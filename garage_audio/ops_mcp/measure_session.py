"""Guided multi-position measurement sessions and response averaging (stdlib).

A session is a small JSON state machine persisted atomically in the private state
directory, so it survives restarts and can be resumed from any MCP client:

    plan -> for each position: await_placement --(user confirms)--> await_noise_check
         --(noise ok)--> await_sweeps --(every channel swept)--> position_review
         --(advance | redo_channel | redo_position | skip_position)--> next position ...
         -> ready_to_finish --(finish: power/vector average per channel)--> finished

Audible steps are never taken by this module on its own: each one is a separate tool call
(``measure_noise_check``/``measure_level_check``/``measure_sweep`` with ``session_id``) that
the agent makes after the user confirmed, and the session refuses sweeps before the mic
placement was confirmed and the background noise checked.
"""
import cmath
import contextlib
import fcntl
import math
import os
import statistics
import threading
from pathlib import Path

from . import measure, room_model
from .backups import ID_PATTERN, _ensure_private_dir, atomic_write_json, new_id, read_json
from .errors import OpsError
from .room import GRID_PER_OCTAVE, interpolate, log_grid

SESSION_DIR = 'measure-sessions'
LOCK_FILE = '.sessions.lock'
RETENTION = 20
AVERAGE_GRID_PER_OCTAVE = GRID_PER_OCTAVE
_THREAD_LOCK = threading.Lock()


# ---- storage ---------------------------------------------------------------------------
def _dir(ctx):
    return _ensure_private_dir(_ensure_private_dir(Path(ctx.config.state_dir)) / SESSION_DIR)


@contextlib.contextmanager
def _locked(ctx):
    directory = _dir(ctx)
    with _THREAD_LOCK:
        fd = os.open(directory / LOCK_FILE, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield directory
        finally:
            os.close(fd)


def _ids(directory):
    names = [p.name[:-5] for p in directory.iterdir() if p.name.endswith('.json')]
    return sorted(n for n in names if ID_PATTERN.fullmatch(n))


def _load(directory, ident):
    if not isinstance(ident, str) or not ID_PATTERN.fullmatch(ident):
        raise OpsError('invalid_id', 'session id is not valid')
    try:
        value = read_json(directory / f'{ident}.json')
    except FileNotFoundError:
        raise OpsError('not_found', 'measurement session not found', session_id=ident) from None
    except (OSError, ValueError):
        raise OpsError('unreadable', 'measurement session cannot be read', session_id=ident) from None
    if not isinstance(value, dict) or value.get('session_id') != ident:
        raise OpsError('unreadable', 'measurement session is malformed', session_id=ident)
    return value


def _save(ctx, directory, session):
    session['updated_at'] = round(ctx.clock(), 3)
    session['events'] = session.get('events', [])[-50:]
    atomic_write_json(directory / f"{session['session_id']}.json", session)
    ids = [i for i in _ids(directory) if i != session['session_id']]
    for ident in ids[:max(0, len(ids) + 1 - RETENTION)]:
        try:
            (directory / f'{ident}.json').unlink()
        except OSError:
            pass


def _active(directory):
    for ident in reversed(_ids(directory)):
        try:
            session = _load(directory, ident)
        except OpsError:
            continue
        if session.get('status') == 'active':
            return session
    return None


def _event(ctx, session, text):
    session.setdefault('events', []).append({'at': round(ctx.clock(), 3), 'event': text})


# ---- view ------------------------------------------------------------------------------
def _current(session):
    index = session['current']
    return session['plan']['positions'][index] if index < len(session['plan']['positions']) else None


def _pending(session):
    pos = _current(session)
    if pos is None:
        return []
    done = session['positions'][pos['id']].get('channels', {})
    return [c for c in session['channels'] if c not in done]


def instruction(session):
    status, phase = session['status'], session['phase']
    if status != 'active':
        return f'Session is {status}.'
    pos = _current(session)
    if phase == 'await_placement':
        return (f"Position {pos['id']} ({session['current'] + 1} of {len(session['plan']['positions'])}): ask the user to "
                f"put the mic {pos['description']} [{pos['description_imperial']}], "
                f"{session['plan']['mic_orientation']}, on a stand (not hand-held), cable and body clear of the mic. "
                'Wait for the user to confirm, then call measure_session_confirm_placement.')
    if phase == 'await_noise_check':
        return ('Ask everyone to be quiet, then call measure_noise_check with this session_id (records ~3 s, plays '
                'nothing).')
    if phase == 'await_sweeps':
        pending = _pending(session)
        unlevelled = [c for c in pending if c not in session.get('levels', {})]
        text = f'Channels still to sweep at {pos["id"]}: {pending}. '
        if unlevelled:
            text += (f'No confirmed level yet for {unlevelled}: tell the user test tones will play, then call '
                     'measure_level_check (user_confirmed=true) for each. ')
        return text + ('Before each measure_sweep tell the user a sweep is about to play (a rising tone of a few '
                       'seconds) and wait for their go-ahead; pass user_confirmed=true only after that.')
    if phase == 'position_review':
        bad = {c: v['quality'] for c, v in session['positions'][pos['id']]['channels'].items() if v['quality'] != 'ok'}
        if bad:
            return (f'Quality at {pos["id"]}: {bad}. Redo flagged channels (measure_session_advance '
                    'action=redo_channel) or the whole position (redo_position); advance with accept_quality=true '
                    'only if the user accepts.')
        return 'All channels look good here. Call measure_session_advance action=next.'
    if phase == 'ready_to_finish':
        return 'All positions are done. Call measure_session_finish to average them and hand off to the EQ tools.'
    return ''


def _view(session):
    pos = _current(session)
    positions = []
    for p in session['plan']['positions']:
        state = session['positions'][p['id']]
        positions.append({'id': p['id'], 'state': state['state'],
                          'noise': (state.get('noise') or {}).get('verdict'),
                          'channels': {c: {'measurement_id': v['measurement_id'], 'quality': v['quality']}
                                       for c, v in state.get('channels', {}).items()}})
    return {'session_id': session['session_id'], 'status': session['status'], 'phase': session['phase'],
            'current_position': pos['id'] if pos else None, 'channels': session['channels'],
            'average': session['average'], 'calibration_id': session.get('calibration_id'),
            'levels': session.get('levels', {}), 'positions': positions, 'result': session.get('result'),
            'next_step': instruction(session)}


# ---- tools: lifecycle ------------------------------------------------------------------
def start(ctx, args):
    m = ctx.config.measurement
    channels = args.get('channels') or [c for c in ('left', 'right', 'sub') if c in m['channel_map']]
    unknown = [c for c in channels if c not in m['channel_map']]
    if unknown or len(set(channels)) != len(channels):
        raise OpsError('invalid_argument', f'channels must be distinct entries of measurement.channel_map '
                       f'{sorted(m["channel_map"])}', unknown=unknown)
    cal_id = args.get('calibration_id')
    orientation = args.get('mic_orientation')
    if cal_id:
        cal = measure.get_calibration(ctx, cal_id)
        orientation = orientation or (cal['orientation'] if cal['orientation'] in ('0', '90') else None)
    profile = room_model.load(ctx)
    listening = args.get('listening_position', 'main')
    the_plan = room_model.plan(profile, count=args.get('positions', m['plan']['positions']),
                               radius=args.get('radius_m', m['plan']['radius_m']), listening=listening,
                               ear_height=args.get('ear_height_m'), orientation=orientation or '90')
    with _locked(ctx) as directory:
        old = _active(directory)
        if old is not None:
            if not args.get('replace'):
                raise OpsError('session_active', 'another measurement session is active; resume it '
                               '(measure_session_status) or pass replace=true to cancel it',
                               session_id=old['session_id'])
            old['status'], old['phase'] = 'cancelled', 'cancelled'
            _event(ctx, old, 'cancelled (replaced by a new session)')
            _save(ctx, directory, old)
        ident = new_id(ctx.clock)
        session = {'session_id': ident, 'status': 'active', 'phase': 'await_placement', 'current': 0,
                   'created_at': round(ctx.clock(), 3), 'channels': channels, 'calibration_id': cal_id,
                   'average': args.get('average', 'power'), 'plan': the_plan, 'levels': {},
                   'positions': {p['id']: {'state': 'pending', 'channels': {}} for p in the_plan['positions']},
                   'profile_missing': room_model.missing_fields(profile)}
        _event(ctx, session, f'started: {len(the_plan["positions"])} positions, channels {channels}')
        _save(ctx, directory, session)
    view = _view(session)
    view['plan'] = the_plan
    if not cal_id:
        view['warning'] = ('no calibration_id: responses are uncorrected for the microphone (import one with '
                           'measure_import_calibration for a calibrated mic)')
    return view


def status(ctx, args):
    with _locked(ctx) as directory:
        if args.get('session_id'):
            session = _load(directory, args['session_id'])
        else:
            session = _active(directory)
            if session is None:
                ids = _ids(directory)
                if not ids:
                    return {'session': None, 'note': 'no measurement session yet; start one with measure_session_start'}
                session = _load(directory, ids[-1])
    view = _view(session)
    if args.get('include_plan'):
        view['plan'] = session['plan']
    view['events'] = session.get('events', [])[-10:]
    return view


def confirm_placement(ctx, args):
    with _locked(ctx) as directory:
        session = _load(directory, args['session_id'])
        _require(session, ('await_placement',))
        pos = _current(session)
        if args['position_id'] != pos['id']:
            raise OpsError('session_state', f"the current position is {pos['id']}, not {args['position_id']}",
                           current_position=pos['id'])
        session['positions'][pos['id']]['state'] = 'placed'
        session['phase'] = 'await_noise_check'
        _event(ctx, session, f"{pos['id']} placement confirmed by the user")
        _save(ctx, directory, session)
    return _view(session)


def advance(ctx, args):
    action = args['action']
    with _locked(ctx) as directory:
        session = _load(directory, args['session_id'])
        pos = _current(session)
        state = session['positions'][pos['id']] if pos else None
        if action == 'next':
            _require(session, ('position_review',))
            bad = {c: v['quality'] for c, v in state['channels'].items() if v['quality'] == 'redo'}
            if bad and not args.get('accept_quality'):
                raise OpsError('session_quality', f'channels {sorted(bad)} need a redo; redo them or pass '
                               'accept_quality=true if the user accepts the result', channels=sorted(bad))
            state['state'] = 'done'
            _event(ctx, session, f"{pos['id']} done" + (' (quality accepted by user)' if bad else ''))
            _next(session)
        elif action == 'redo_channel':
            _require(session, ('await_sweeps', 'position_review'))
            channel = args.get('channel')
            if channel not in state['channels']:
                raise OpsError('invalid_argument', f'channel {channel!r} has no sweep at {pos["id"]} to redo')
            state.setdefault('superseded', []).append(state['channels'].pop(channel)['measurement_id'])
            session['phase'] = 'await_sweeps'
            _event(ctx, session, f"{pos['id']} redo {channel}")
        elif action == 'redo_position':
            _require(session, ('await_noise_check', 'await_sweeps', 'position_review'))
            state.setdefault('superseded', []).extend(v['measurement_id'] for v in state['channels'].values())
            state.update({'state': 'pending', 'channels': {}, 'noise': None})
            session['phase'] = 'await_placement'
            _event(ctx, session, f"{pos['id']} redo position")
        elif action == 'skip_position':
            _require(session, ('await_placement', 'await_noise_check', 'await_sweeps', 'position_review'))
            if pos['role'] == 'main':
                raise OpsError('session_state', 'the main listening position cannot be skipped')
            state['state'] = 'skipped'
            _event(ctx, session, f"{pos['id']} skipped")
            _next(session)
        _save(ctx, directory, session)
    return _view(session)


def _next(session):
    session['current'] += 1
    session['phase'] = ('ready_to_finish' if session['current'] >= len(session['plan']['positions'])
                        else 'await_placement')


def cancel(ctx, args):
    aborted = None
    rt = measure.runtime(ctx)
    if rt.active and rt.active.get('session_id') == args['session_id']:
        aborted = measure.abort(ctx, {})
    with _locked(ctx) as directory:
        session = _load(directory, args['session_id'])
        if session['status'] == 'active':
            session['status'], session['phase'] = 'cancelled', 'cancelled'
            _event(ctx, session, 'cancelled')
            _save(ctx, directory, session)
    return {**_view(session), 'aborted_running_measurement': aborted,
            'note': 'Stored measurements stay in the history; nothing was deleted.'}


def _require(session, phases):
    if session['status'] != 'active':
        raise OpsError('session_state', f"the session is {session['status']}")
    if session['phase'] not in phases:
        raise OpsError('session_state', f"this step is not possible in phase {session['phase']!r} "
                       f'(expected {list(phases)}). {instruction(session)}', phase=session['phase'])


# ---- tools: session-aware measurement steps --------------------------------------------
def _require_confirmation(args, what):
    if args.get('user_confirmed') is not True:
        raise OpsError('confirmation_required', f'{what} is audible: tell the user what will play and pass '
                       'user_confirmed=true only after they agreed')


def plan_tool(ctx, args):
    m = ctx.config.measurement
    profile = room_model.load(ctx)
    out = room_model.plan(profile, count=args.get('positions', m['plan']['positions']),
                          radius=args.get('radius_m', m['plan']['radius_m']),
                          listening=args.get('listening_position', 'main'), ear_height=args.get('ear_height_m'),
                          orientation=args.get('mic_orientation', '90'))
    out['profile_missing'] = room_model.missing_fields(profile)
    return out


def _attach(ctx, session_id, position_id, update):
    """Re-load the session after a measurement and apply ``update`` if it is still at the same step."""
    with _locked(ctx) as directory:
        session = _load(directory, session_id)
        pos = _current(session)
        if session['status'] != 'active' or pos is None or pos['id'] != position_id:
            return None, 'the session changed while measuring (cancelled or moved on); the result was stored but ' \
                         'not attached to the session'
        update(session, session['positions'][pos['id']])
        _save(ctx, directory, session)
        return session, None


def _begin(ctx, session_id, phases, channel=None):
    with _locked(ctx) as directory:
        session = _load(directory, session_id)
        _require(session, phases)
        if channel is not None and channel not in session['channels']:
            raise OpsError('invalid_argument', f'channel {channel!r} is not part of this session {session["channels"]}')
        return session, _current(session)


def noise_check_tool(ctx, args):
    sid = args.get('session_id')
    if not sid:
        return measure.noise_check(ctx, args)
    session, pos = _begin(ctx, sid, ('await_noise_check', 'await_sweeps', 'position_review'))
    result = measure.noise_check(ctx, args, session)
    accept = bool(args.get('accept_noisy')) and result['verdict'] == 'too_noisy'

    def update(s, state):
        state['noise'] = {'verdict': result['verdict'], 'noise_floor_dbfs': result['noise_floor_dbfs'],
                          'accepted_by_user': accept}
        if s['phase'] == 'await_noise_check' and (result['verdict'] == 'ok' or accept):
            s['phase'] = 'await_sweeps'
            state['state'] = 'measuring'
        _event(ctx, s, f"{pos['id']} noise check: {result['verdict']}" + (' (accepted by user)' if accept else ''))

    updated, note = _attach(ctx, sid, pos['id'], update)
    return {**result, 'session': _view(updated) if updated else None, **({'session_note': note} if note else {})}


def level_check_tool(ctx, args):
    _require_confirmation(args, 'the level check')
    sid = args.get('session_id')
    if not sid:
        return measure.level_check(ctx, args)
    session, pos = _begin(ctx, sid, ('await_sweeps', 'position_review'), args['channel'])
    result = measure.level_check(ctx, args, session)

    def update(s, _state):
        if result['chosen_level_dbfs'] is not None:
            s.setdefault('levels', {})[args['channel']] = result['chosen_level_dbfs']
        _event(ctx, s, f"level check {args['channel']}: {result['chosen_level_dbfs']} dBFS ({result['stop_reason']})")

    updated, note = _attach(ctx, sid, pos['id'], update)
    return {**result, 'session': _view(updated) if updated else None, **({'session_note': note} if note else {})}


def sweep_tool(ctx, args):
    _require_confirmation(args, 'the sweep')
    sid = args.get('session_id')
    if not sid:
        return measure.sweep(ctx, args)
    session, pos = _begin(ctx, sid, ('await_sweeps', 'position_review'), args['channel'])
    context = {'session_id': sid, 'position_id': pos['id'], 'calibration_id': session.get('calibration_id'),
               'levels': session.get('levels', {})}
    result = measure.sweep(ctx, args, context)

    def update(s, state):
        channel = args['channel']
        if channel in state['channels']:
            state.setdefault('superseded', []).append(state['channels'][channel]['measurement_id'])
        state['channels'][channel] = {'measurement_id': result['measurement_id'],
                                      'quality': result['quality']['status'],
                                      'arrival_ms': (result.get('timing') or {}).get('arrival_ms')}
        s['phase'] = 'position_review' if not _pending(s) else 'await_sweeps'
        _event(ctx, s, f"{pos['id']} sweep {channel}: {result['quality']['status']}")

    updated, note = _attach(ctx, sid, pos['id'], update)
    return {**result, 'session': _view(updated) if updated else None, **({'session_note': note} if note else {})}


# ---- averaging -------------------------------------------------------------------------
def _unwrap_deg(values):
    out, offset, prev = [], 0.0, None
    for v in values:
        if prev is not None:
            while v + offset - prev > 180:
                offset -= 360
            while v + offset - prev < -180:
                offset += 360
        prev = v + offset
        out.append(prev)
    return out


def average_records(records, method='power'):
    """Average measurement records on a common log grid. Returns (points, spread_summary)."""
    if len(records) < 2:
        raise OpsError('invalid_argument', 'averaging needs at least two measurements')
    curves = []
    for rec in records:
        points = rec.get('points') or []
        if len(points) < 2:
            raise OpsError('unreadable', 'a measurement has no points', id=rec.get('id'))
        curves.append(points)
    f_lo = max(c[0]['frequency_hz'] for c in curves)
    f_hi = min(c[-1]['frequency_hz'] for c in curves)
    if f_hi / f_lo < 2:
        raise OpsError('measurement_range', 'the measurements overlap by less than an octave')
    grid = log_grid(f_lo, f_hi, AVERAGE_GRID_PER_OCTAVE)
    mags = [interpolate([(p['frequency_hz'], p['db']) for p in c], grid) for c in curves]
    if method == 'vector':
        if not all('phase_deg' in p for c in curves for p in c):
            raise OpsError('invalid_argument', 'vector averaging needs phase in every measurement (built-in sweeps '
                           'have it; imported magnitude-only files do not): use method=power')
        phases = [interpolate(list(zip([p['frequency_hz'] for p in c], _unwrap_deg([p['phase_deg'] for p in c]))),
                              grid) for c in curves]
        points = []
        for i, f in enumerate(grid):
            total = sum(cmath.rect(10 ** (m[i] / 20.0), math.radians(ph[i])) for m, ph in zip(mags, phases))
            mean = total / len(curves)
            points.append({'frequency_hz': round(f, 4), 'db': round(20 * math.log10(max(abs(mean), 1e-15)), 3),
                           'phase_deg': round(math.degrees(cmath.phase(mean)), 2)})
    else:
        points = [{'frequency_hz': round(f, 4),
                   'db': round(10 * math.log10(sum(10 ** (m[i] / 10.0) for m in mags) / len(mags)), 3)}
                  for i, f in enumerate(grid)]
    spread = [statistics.pstdev([m[i] for m in mags]) for i in range(len(grid))]
    low = [s for f, s in zip(grid, spread) if f <= 300] or spread
    return points, {'median_std_db': round(statistics.median(spread), 2),
                    'median_std_below_300hz_db': round(statistics.median(low), 2),
                    'max_std_db': round(max(spread), 2)}


def _save_average(ctx, records, method, channel, label, extra):
    points, spread = average_records(records, method)
    record = ctx.history.save({
        'kind': 'measurement', 'source': f'{method}_average', 'channel': channel, 'label': label[:100],
        'distance_m': None, 'notes': f'{method} average of {len(records)} measurements', 'sha256': None,
        'headers': [], 'inputs': [r['id'] for r in records], 'spread': spread, **extra, 'points': points})
    return record, spread


def average_tool(ctx, args):
    method = args.get('method', 'power')
    records = [ctx.history.get(i) for i in args['measurement_ids']]
    channels = {r.get('channel') for r in records}
    channel = args.get('channel')
    if channel is None:
        if len(channels) != 1:
            raise OpsError('invalid_argument', f'the measurements are for different channels {sorted(map(str, channels))}; '
                           'pass channel to label the average explicitly')
        channel = channels.pop()
    record, spread = _save_average(ctx, records, method, channel,
                                   args.get('label') or f'{channel} {method} average ({len(records)})', {})
    return {'measurement_id': record['id'], 'channel': channel, 'method': method, 'inputs': len(records),
            'spread': spread, 'points': len(record['points']),
            'next': 'room_compare_to_target / room_propose_peq with this measurement_id'}


def finish(ctx, args):
    with _locked(ctx) as directory:
        session = _load(directory, args['session_id'])
        if session['status'] != 'active':
            raise OpsError('session_state', f"the session is {session['status']}")
        done = [p['id'] for p in session['plan']['positions'] if session['positions'][p['id']]['state'] == 'done']
        remaining = [p['id'] for p in session['plan']['positions']
                     if session['positions'][p['id']]['state'] not in ('done', 'skipped')]
        if not done:
            raise OpsError('session_state', 'no position is complete yet')
        if remaining and not args.get('allow_partial'):
            raise OpsError('session_state', f'positions {remaining} are not complete; finish them or pass '
                           'allow_partial=true', remaining=remaining)
        method = args.get('method', session['average'])
        results, main_ids = {}, {}
        for channel in session['channels']:
            ids = [session['positions'][p]['channels'][channel]['measurement_id'] for p in done
                   if channel in session['positions'][p]['channels']]
            if 'P0' in done and channel in session['positions']['P0']['channels']:
                main_ids[channel] = session['positions']['P0']['channels'][channel]['measurement_id']
            if not ids:
                continue
            records = [ctx.history.get(i) for i in ids]
            if len(records) == 1:
                results[channel] = {'measurement_id': ids[0], 'positions': 1, 'method': 'single'}
                continue
            record, spread = _save_average(ctx, records, method, channel,
                                           f'{channel} session average ({len(records)} positions)',
                                           {'session_id': session['session_id']})
            results[channel] = {'measurement_id': record['id'], 'positions': len(records), 'method': method,
                                'spread': spread}
        session['result'] = {'averages': results, 'main_position_measurement_ids': main_ids,
                             'partial': bool(remaining), 'positions_used': done}
        session['status'], session['phase'] = 'finished', 'finished'
        _event(ctx, session, f'finished ({method} average over {len(done)} positions)')
        _save(ctx, directory, session)
    timed = {c: i for c, i in main_ids.items() if c in ('left', 'right', 'sub')}
    return {**_view(session),
            'next': ['room_compare_to_target with each averaged measurement_id (see result.averages)',
                     'room_propose_peq per channel (cut-biased; nulls are not boosted)',
                     f'room_propose_alignment with measurement_ids={list(timed.values())} (main-position sweeps; '
                     'the DSP delays active while measuring are removed automatically)' if len(timed) >= 2 else
                     'room_propose_alignment needs main-position sweeps of at least two channels',
                     'room_preview_yaml, then room_apply_correction (write-gated, backup first)']}
