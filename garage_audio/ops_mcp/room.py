"""Room-correction helpers: REW import, target comparison, cut-biased PEQ proposals, alignment, YAML preview, apply.

The math is deliberately conservative: correction is fitted on a smoothed,
log-spaced grid inside a bounded frequency range (room modes by default), peaks
are cut before dips are considered, boosts are capped (default 0 dB = cut-only)
and never fill more than half of a dip, and the filter count is bounded. Every
proposal is re-validated against the speaker-protection policy when applied.
"""
import cmath
import json
import math
import re
import statistics

from .. import calibration
from . import dsp, safety
from .backups import Journal
from .config import CHANNELS
from .errors import OpsError, SafetyError

GRID_PER_OCTAVE = 48
RC_PREFIX = 'ops_rc_'
PROPOSAL_DIR = 'room-proposals'


def proposals(ctx):
    return Journal(ctx.config.state_dir, PROPOSAL_DIR, ctx.config.room['history_retention'], ctx.clock)


# ---- math ----------------------------------------------------------------------------
def log_grid(f_lo, f_hi, per_octave=GRID_PER_OCTAVE):
    count = max(2, int(math.ceil(math.log2(f_hi / f_lo) * per_octave)) + 1)
    step = math.log2(f_hi / f_lo) / (count - 1)
    grid = [f_lo * 2 ** (i * step) for i in range(count)]
    grid[0], grid[-1] = f_lo, f_hi  # exact endpoints: no float drift outside the measured range
    return grid


def _toward_zero(value, places=1):
    """Round toward zero so a rounded gain never exceeds the cap it was clamped to."""
    scale = 10 ** places
    return math.trunc(value * scale) / scale


def interpolate(points, grid):
    """Linear interpolation of dB in log-frequency. ``points`` are (freq, dB) ascending."""
    freqs = [p[0] for p in points]
    out = []
    j = 0
    for f in grid:
        if f < freqs[0] or f > freqs[-1]:
            raise OpsError('measurement_range', f'measurement does not cover {f:.1f} Hz')
        while j < len(freqs) - 2 and freqs[j + 1] < f:
            j += 1
        f0, d0 = points[j]
        f1, d1 = points[j + 1]
        t = 0.0 if f1 == f0 else (math.log(f) - math.log(f0)) / (math.log(f1) - math.log(f0))
        out.append(d0 + (d1 - d0) * min(1.0, max(0.0, t)))
    return out


def smooth(values, octaves, per_octave=GRID_PER_OCTAVE):
    half = int(round(octaves * per_octave / 2))
    if half <= 0:
        return list(values)
    out = []
    for i in range(len(values)):
        window = values[max(0, i - half):i + half + 1]
        out.append(sum(window) / len(window))
    return out


def target_db(f, tilt_db_per_octave=0.0, bass_shelf_db=0.0, bass_shelf_hz=100.0):
    """Flat target (0 dB) with optional tilt around 1 kHz and a smooth bass shelf (half gain at shelf_hz)."""
    value = tilt_db_per_octave * math.log2(f / 1000.0)
    if bass_shelf_db:
        value += bass_shelf_db / (1.0 + (f / bass_shelf_hz) ** 2)
    return value


def peaking_db(f, f0, q, gain, fs):
    """Magnitude (dB) of an RBJ-cookbook peaking biquad (as CamillaDSP Biquad Peaking) at frequency f."""
    a = 10 ** (gain / 40.0)
    w0 = 2 * math.pi * f0 / fs
    alpha = math.sin(w0) / (2 * q)
    b = (1 + alpha * a, -2 * math.cos(w0), 1 - alpha * a)
    den = (1 + alpha / a, -2 * math.cos(w0), 1 - alpha / a)
    z1 = cmath.exp(-1j * 2 * math.pi * f / fs)
    h = (b[0] + b[1] * z1 + b[2] * z1 * z1) / (den[0] + den[1] * z1 + den[2] * z1 * z1)
    return 20 * math.log10(max(abs(h), 1e-12))


def q_from_bandwidth(octaves):
    return 1.0 / (2.0 * math.sinh(math.log(2) / 2.0 * octaves))


def rms(values):
    return math.sqrt(sum(v * v for v in values) / len(values)) if values else 0.0


def propose(points, *, f_lo, f_hi, target, smoothing, tolerance, max_filters, max_boost, min_gain,
            q_min, q_max, fs):
    """Greedy cut-biased PEQ fit. Returns filters and before/after statistics. Pure function."""
    grid = log_grid(f_lo, f_hi)
    measured = smooth(interpolate(points, grid), smoothing)
    goal = [target_db(f, **target) for f in grid]
    offset = statistics.median(m - g for m, g in zip(measured, goal))
    residual = [m - g - offset for m, g in zip(measured, goal)]
    before = list(residual)
    filters = []
    for _ in range(max_filters):
        peak_i = max(range(len(residual)), key=lambda i: residual[i])
        dip_i = min(range(len(residual)), key=lambda i: residual[i])
        peak = residual[peak_i]
        dip = -residual[dip_i]
        cut_score = peak if peak >= tolerance else 0.0
        boost_amount = min(dip / 2.0, max_boost) if max_boost > 0 and dip >= tolerance else 0.0
        # Cut bias: a boost must be twice as valuable as the best cut to be chosen.
        boost_score = boost_amount * 0.5
        if cut_score <= 0 and boost_score <= 0:
            break
        if cut_score >= boost_score:
            index, sign, height = peak_i, 1, peak
            gain = -min(peak, -min_gain)
        else:
            index, sign, height = dip_i, -1, dip
            gain = boost_amount
        lo = hi = index
        while lo > 0 and sign * residual[lo - 1] > height / 2:
            lo -= 1
        while hi < len(residual) - 1 and sign * residual[hi + 1] > height / 2:
            hi += 1
        bandwidth = max(math.log2(grid[hi] / grid[lo]), 2.0 / GRID_PER_OCTAVE)
        q = min(q_max, max(q_min, q_from_bandwidth(bandwidth)))
        f0 = min(f_hi, max(f_lo, grid[index]))
        candidate = {'freq': min(f_hi, max(f_lo, round(f0, 1))),
                     'q': min(q_max, max(q_min, round(q, 2))), 'gain': _toward_zero(gain)}
        if candidate['gain'] == 0:
            break
        # The filter adds its response to the output, so it adds to the residual (a cut lowers a peak).
        trial = [r + peaking_db(f, candidate['freq'], candidate['q'], candidate['gain'], fs)
                 for r, f in zip(residual, grid)]
        if rms(trial) >= rms(residual):
            break
        residual = trial
        filters.append(candidate)
    return {
        'filters': sorted(filters, key=lambda x: x['freq']),
        'reference_offset_db': round(offset, 2),
        'rms_deviation_before_db': round(rms(before), 2), 'rms_deviation_after_db': round(rms(residual), 2),
        'max_peak_before_db': round(max(before), 2), 'max_peak_after_db': round(max(residual), 2),
        'max_dip_before_db': round(min(before), 2), 'max_dip_after_db': round(min(residual), 2),
        'grid_points': len(grid),
    }


def alignment(distances_m=None, impulse_peaks_ms=None, levels_db=None):
    """Delay (ms, >= 0) and level trims (dB, <= 0) that align every channel to the latest/quietest one."""
    delays, gains, method = {}, {}, None
    if impulse_peaks_ms:
        latest = max(impulse_peaks_ms.values())
        delays = {ch: round(latest - t, 3) for ch, t in impulse_peaks_ms.items()}
        method = 'impulse_peaks'
    elif distances_m:
        far = max(distances_m.values())
        delays = {ch: round((far - d) / safety.SPEED_OF_SOUND_M_S * 1000.0, 3) for ch, d in distances_m.items()}
        method = 'distances'
    if levels_db:
        quiet = min(levels_db.values())
        gains = {ch: round(quiet - level, 2) for ch, level in levels_db.items()}
    return {'delay_ms': delays, 'gain_db': gains, 'delay_method': method}


# ---- tool handlers ---------------------------------------------------------------------
def _points(record):
    points = record.get('points')
    if not isinstance(points, list) or len(points) < 2:
        raise OpsError('unreadable', 'measurement has no points')
    return [(p['frequency_hz'], p['db']) for p in points]


def import_measurement(ctx, args):
    try:
        parsed = calibration.parse(args['text'], 'rew-response')
    except ValueError as error:
        raise OpsError('measurement_invalid', f'REW export could not be parsed: {error}') from None
    points = parsed['points']
    record = ctx.history.save({
        'kind': 'measurement', 'channel': args['channel'], 'label': args.get('label', ''),
        'distance_m': args.get('distance_m'), 'notes': args.get('notes', ''), 'sha256': parsed['sha256'],
        'headers': parsed['headers'][:20], 'points': points})
    dbs = [p['db'] for p in points]
    return {'measurement_id': record['id'], 'channel': args['channel'], 'points': len(points),
            'freq_range_hz': [points[0]['frequency_hz'], points[-1]['frequency_hz']],
            'level_range_db': [round(min(dbs), 2), round(max(dbs), 2)], 'sha256': parsed['sha256'],
            'note': 'Stored in the measurement history. Microphone calibration is not applied by this import.'}


def list_measurements(ctx, args):
    out = []
    for ident in ctx.history.list(args.get('limit', 50)):
        try:
            rec = ctx.history.get(ident)
        except OpsError:
            continue
        pts = rec.get('points') or []
        out.append({'id': ident, 'channel': rec.get('channel'), 'label': rec.get('label'),
                    'saved_at': rec.get('saved_at'), 'points': len(pts), 'distance_m': rec.get('distance_m'),
                    'freq_range_hz': [pts[0]['frequency_hz'], pts[-1]['frequency_hz']] if pts else None})
    return {'measurements': out, 'directory': str(ctx.history.directory)}


def _settings(ctx, args, points):
    room = ctx.config.room
    policy = ctx.config.policy
    f_lo = args.get('freq_min_hz', room['freq_min_hz'])
    f_hi = args.get('freq_max_hz', room['freq_max_hz'])
    safety.check_freq(policy, f_lo, 'freq_min_hz')
    safety.check_freq(policy, f_hi, 'freq_max_hz')
    f_lo = max(f_lo, points[0][0])
    f_hi = min(f_hi, points[-1][0])
    if f_hi / f_lo < 2 ** (1 / 6):
        raise OpsError('measurement_range', 'the measurement and requested range overlap by less than 1/6 octave')
    ceiling = min(room['max_boost_db'], policy.max_boost_db)
    max_boost = args.get('max_boost_db', ceiling)
    if max_boost > ceiling:
        raise SafetyError(f'max_boost_db={max_boost} exceeds the configured room-correction boost cap {ceiling}',
                          maximum=ceiling)
    max_filters = args.get('max_filters', room['max_filters'])
    if max_filters > room['max_filters']:
        raise SafetyError(f"max_filters={max_filters} exceeds the configured cap {room['max_filters']}",
                          maximum=room['max_filters'])
    target = args.get('target') or {}
    return {'f_lo': f_lo, 'f_hi': f_hi, 'max_boost': max_boost, 'max_filters': max_filters,
            'smoothing': args.get('smoothing_octaves', room['smoothing_octaves']),
            'tolerance': args.get('tolerance_db', room['tolerance_db']),
            'target': {'tilt_db_per_octave': target.get('tilt_db_per_octave', room['tilt_db_per_octave']),
                       'bass_shelf_db': target.get('bass_shelf_db', room['bass_shelf_db']),
                       'bass_shelf_hz': target.get('bass_shelf_hz', room['bass_shelf_hz'])}}


def compare_to_target(ctx, args):
    record = ctx.history.get(args['measurement_id'])
    points = _points(record)
    s = _settings(ctx, args, points)
    grid = log_grid(s['f_lo'], s['f_hi'], 12)
    measured = smooth(interpolate(points, grid), s['smoothing'], 12)
    goal = [target_db(f, **s['target']) for f in grid]
    offset = statistics.median(m - g for m, g in zip(measured, goal))
    deviation = [round(m - g - offset, 2) for m, g in zip(measured, goal)]
    worst = sorted(range(len(grid)), key=lambda i: -abs(deviation[i]))[:5]
    return {'measurement_id': args['measurement_id'], 'channel': record.get('channel'),
            'range_hz': [round(s['f_lo'], 1), round(s['f_hi'], 1)], 'target': s['target'],
            'reference_offset_db': round(offset, 2), 'rms_deviation_db': round(rms(deviation), 2),
            'largest_deviations': [{'freq_hz': round(grid[i], 1), 'deviation_db': deviation[i]} for i in sorted(worst)],
            'curve': [{'freq_hz': round(f, 1), 'deviation_db': d} for f, d in zip(grid, deviation)],
            'note': 'Positive deviation = louder than target (cut candidates); negative = dips (often room nulls '
                    'that EQ cannot fix).'}


def propose_peq(ctx, args):
    record = ctx.history.get(args['measurement_id'])
    channel = args.get('channel') or record.get('channel')
    if channel not in CHANNELS:
        raise OpsError('invalid_argument', 'channel must be left, right or sub')
    points = _points(record)
    s = _settings(ctx, args, points)
    policy = ctx.config.policy
    result = propose(points, f_lo=s['f_lo'], f_hi=s['f_hi'], target=s['target'], smoothing=s['smoothing'],
                     tolerance=s['tolerance'], max_filters=s['max_filters'], max_boost=s['max_boost'],
                     min_gain=policy.min_gain_db, q_min=policy.q_min, q_max=policy.q_max,
                     fs=ctx.config.room['sample_rate'])
    for item in result['filters']:
        _check_filter(ctx, item, s['max_boost'])
    saved = proposals(ctx).save({'kind': 'peq', 'channel': channel, 'measurement_id': args['measurement_id'],
                                 'settings': s, **result})
    return {'proposal_id': saved['id'], 'channel': channel, 'settings': s, **result,
            'next': 'room_preview_yaml, then room_apply_correction (write-gated, backup first).'}


def _check_filter(ctx, item, max_boost):
    checked = safety.check_peq(ctx.config.policy, item)
    if checked['gain'] > max_boost:
        raise SafetyError(f"proposed gain {checked['gain']} exceeds boost cap {max_boost}")
    return checked


def propose_alignment(ctx, args):
    distances = args.get('distances_m')
    peaks = args.get('impulse_peaks_ms')
    levels = args.get('levels_db')
    if not (distances or peaks or levels):
        raise OpsError('invalid_argument', 'provide distances_m, impulse_peaks_ms and/or levels_db')
    result = alignment(distances, peaks, levels)
    policy = ctx.config.policy
    for channel, value in result['delay_ms'].items():
        safety.check_delay_ms(policy, value, f'{channel} delay_ms')
    for channel, value in result['gain_db'].items():
        safety.check_channel_gain(policy, value, f'{channel} gain_db')
    saved = proposals(ctx).save({'kind': 'alignment', **result, 'inputs': {'distances_m': distances,
                                 'impulse_peaks_ms': peaks, 'levels_db': levels}})
    return {'proposal_id': saved['id'], **result,
            'note': 'Impulse peaks (from the same loopback-timed REW sweep) include electronic latency and are '
                    'preferred over distances. Level trims are cut-only, aligning to the quietest channel.'}


def _yaml_scalar(value):
    if isinstance(value, bool):
        return 'true' if value else 'false'
    if value is None:
        return 'null'
    if isinstance(value, (int, float)):
        return repr(value)
    if re.fullmatch(r'[A-Za-z_][A-Za-z0-9_.-]*', value) and value not in ('true', 'false', 'null', 'yes', 'no'):
        return value
    return json.dumps(value)


def to_yaml(value, indent=0):
    pad = '  ' * indent
    lines = []
    if isinstance(value, dict):
        for key, item in value.items():
            if isinstance(item, (dict, list)) and item:
                lines.append(f'{pad}{_yaml_scalar(key)}:')
                lines.append(to_yaml(item, indent + 1))
            else:
                lines.append(f'{pad}{_yaml_scalar(key)}: {_yaml_scalar(item) if not isinstance(item, (dict, list)) else ("{}" if isinstance(item, dict) else "[]")}')
    elif isinstance(value, list):
        for item in value:
            if isinstance(item, dict) and item:
                inner = to_yaml(item, indent + 1).splitlines()
                lines.append(f'{pad}- ' + inner[0].lstrip())
                lines.extend(inner[1:])
            elif isinstance(item, list):
                lines.append(f'{pad}- [' + ', '.join(_yaml_scalar(x) for x in item) + ']')
            else:
                lines.append(f'{pad}- {_yaml_scalar(item)}')
    return '\n'.join(lines)


def _fragment(ctx, proposal):
    filters, steps = {}, []
    if proposal['kind'] == 'peq':
        channel = proposal['channel']
        names = []
        for i, item in enumerate(proposal['filters']):
            name = f'{RC_PREFIX}{channel}_{i}'
            filters[name] = {'type': 'Biquad', 'parameters': {'type': 'Peaking', **item}}
            names.append(name)
        if names:
            steps.append({'type': 'Filter', 'channels': [ctx.config.channels.get(channel)], 'names': names})
    else:
        for channel, value in proposal['delay_ms'].items():
            name = f'{dsp.OPS_PREFIX}{channel}_delay'
            filters[name] = {'type': 'Delay', 'parameters': {'delay': value, 'unit': 'ms', 'subsample': False}}
            steps.append({'type': 'Filter', 'channels': [ctx.config.channels.get(channel)], 'names': [name]})
        for channel, value in proposal['gain_db'].items():
            name = f'{dsp.OPS_PREFIX}{channel}_gain'
            filters[name] = {'type': 'Gain', 'parameters': {'gain': value, 'inverted': False, 'scale': 'dB'}}
            steps.append({'type': 'Filter', 'channels': [ctx.config.channels.get(channel)], 'names': [name]})
    return {'filters': filters, 'pipeline': steps}


def preview_yaml(ctx, args):
    proposal = proposals(ctx).get(args['proposal_id'])
    fragment = _fragment(ctx, proposal)
    unmapped = sorted({ch for ch in CHANNELS if ctx.config.channels.get(ch) is None
                       and any(f'_{ch}_' in n for n in fragment['filters'])})
    return {'proposal_id': args['proposal_id'], 'kind': proposal['kind'], 'yaml': to_yaml(fragment) + '\n',
            'unmapped_channels': unmapped,
            'note': 'Fragment to merge into the CamillaDSP config. room_apply_correction replaces earlier ops_rc_* '
                    'filters for the channel and appends pipeline steps at the end (output channel indexes).'}


def apply(ctx, args):
    proposal = proposals(ctx).get(args['proposal_id'])
    policy = ctx.config.policy
    if proposal.get('kind') == 'peq':
        channel = proposal['channel']
        boost = min(proposal.get('settings', {}).get('max_boost', 0.0), policy.max_boost_db,
                    ctx.config.room['max_boost_db'])
        items = [_check_filter(ctx, item, boost) for item in proposal['filters']]
        if len(items) > min(policy.max_filters_per_channel, ctx.config.room['max_filters']):
            raise SafetyError('proposal has more filters than allowed')

        def mutate(config):
            index = dsp.channel_index(ctx, channel)
            old = [n for n in (config.get('filters') or {}) if n.startswith(f'{RC_PREFIX}{channel}_')]
            dsp.remove_filters(config, old)
            names = []
            for i, item in enumerate(items):
                name = f'{RC_PREFIX}{channel}_{i}'
                config.setdefault('filters', {})[name] = {'type': 'Biquad', 'parameters': item}
                dsp.ensure_in_pipeline(config, name, index)
                names.append(name)
            return names + old
    elif proposal.get('kind') == 'alignment':
        delays = {ch: safety.check_delay_ms(policy, v, f'{ch} delay_ms') for ch, v in proposal['delay_ms'].items()}
        gains = {ch: safety.check_channel_gain(policy, v, f'{ch} gain_db') for ch, v in proposal['gain_db'].items()}

        def mutate(config):
            names = []
            filters = config.setdefault('filters', {})
            for ch, value in delays.items():
                name = f'{dsp.OPS_PREFIX}{ch}_delay'
                filters[name] = {'type': 'Delay', 'parameters': {'delay': value, 'unit': 'ms', 'subsample': False}}
                dsp.ensure_in_pipeline(config, name, dsp.channel_index(ctx, ch))
                names.append(name)
            for ch, value in gains.items():
                name = f'{dsp.OPS_PREFIX}{ch}_gain'
                filters[name] = {'type': 'Gain', 'parameters': {'gain': value, 'inverted': False, 'scale': 'dB'}}
                dsp.ensure_in_pipeline(config, name, dsp.channel_index(ctx, ch))
                names.append(name)
            return names
    else:
        raise OpsError('unreadable', 'unknown proposal kind')
    result = dsp.apply_config(ctx, 'room_apply_correction', f"room correction {args['proposal_id']}", mutate)
    result['proposal_id'] = args['proposal_id']
    result['next'] = 'Re-measure the same positions; if worse, dsp_restore_backup with backup_id.'
    return result
