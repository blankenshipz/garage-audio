"""Built-in room measurement engine (tools ``measure_*``); see docs/ops-mcp-measurement.md.

How a measurement interacts with the running stack
--------------------------------------------------
The test signal is written to a WAV file and played by the operator-configured player
argv into the *stack sink* (the PipeWire sink that feeds CamillaDSP), exactly like music.
The DAC stays owned by the supervisor-managed stack, and CamillaDSP's volume ceiling,
limits and crossovers apply to every signal. Configuration refuses player argv that name
a raw ALSA/DAC device. Before anything is played the engine refuses (never auto-fixes)
when the owner is not ``ready``, CamillaDSP is unreachable/not running or muted, Music
Assistant is playing, or the optional sink probe fails.

Safety (code-enforced; configuration can only tighten, see safety.MEASURE_HARD):
the level of every generated signal is capped; the first level-check tone starts at a
gentle level and rises in bounded steps, stopping at the capture headroom limit, the
target SNR or the cap; a sweep may not be louder than the level confirmed by the level
check (or the gentle start level), and a confirmation expires (TTL) and is bound to the
main volume, mute, routing, capture device and calibration it was made with; signals on
the sub or on any sub-only output are band-limited; sweep length and total
audible time are bounded and enforced by a watchdog; one measurement runs at a time
(thread lock + state-dir flock); ``measure_abort`` stops only the children this server
started. All of it needs allow_write AND allow_measure.
"""
import contextlib
import fcntl
import hashlib
import json
import math
import os
import re
import shutil
import statistics
import threading
import time
from pathlib import Path

from .. import calibration
from . import health, safety
from .backups import Journal, _ensure_private_dir, atomic_write_bytes, atomic_write_json, new_id
from .clients import run_argv
from .config import SMOOTHING
from .errors import OpsError, SafetyError
from .measure_io import PhoneCaptureBackend, ProcessCapture, list_capture_devices

ARTIFACT_DIR = 'measurement-files'
CAL_DIR = 'mic-calibrations'
WORK_DIR = 'measure-work'
LOCK_FILE = '.measure.lock'
ABORT_FILE = '.measure.abort'
CAL_RETENTION = 20
PREROLL_S = 0.5          # recorder runs alone first: background-noise estimate
LEAD_S = 0.1             # silence at the start of every generated file
TAIL_S = 1.5             # recording continues after playback (latency + room decay)
NOISE_SECONDS = 3.0
TONE_SECONDS = 1.0
TONE_TAIL_S = 0.3
TONE_NOISE_S = (0.03, 0.35)
REF_SECONDS = 0.5        # acoustic timing chirp on the reference speaker
REF_GAP_S = 0.5
REF_BAND_HZ = (200.0, 8000.0)
WATCHDOG_SLACK_S = 5.0
WINDOW_MS = 500.0
NOISY_FLOOR_DBFS = -45.0
SILENT_DBFS = -110.0
MAX_LEVEL_STEPS = 8
STALE_TMP_S = 600.0
MAX_RECORDING_BYTES = 64 * 1024 * 1024
MISSING = ('the measurement extra is not installed: install the optional dependency with '
           'pip install "garage-audio[measurement]" (numpy==2.2.6) in the ops server environment; '
           'all non-measurement tools keep working without it')


# ---- optional numpy ----------------------------------------------------------------------
def math_module():
    """The numpy-backed signal module, or a clear ``measurement_extra_missing`` error."""
    try:
        from . import measure_math
    except ImportError as error:
        if (getattr(error, 'name', None) or '').split('.')[0] == 'numpy' or 'numpy' in str(error):
            raise OpsError('measurement_extra_missing', MISSING) from None
        raise
    return measure_math


def extra_status():
    try:
        mm = math_module()
    except OpsError:
        return {'installed': False, 'message': MISSING}
    return {'installed': True, 'numpy': mm.np.__version__}


# ---- runtime: one measurement at a time, abort -------------------------------------------
class Runtime:
    def __init__(self):
        self.lock = threading.Lock()
        self.abort = threading.Event()
        self.active = None
        self.backend = None
        self.levels = {}   # channel -> {'level_dbfs', 'at', 'reason'} confirmed by the level check


_RUNTIME_LOCK = threading.Lock()


def runtime(ctx):
    with _RUNTIME_LOCK:
        rt = getattr(ctx, '_measure_runtime', None)
        if rt is None:
            rt = ctx._measure_runtime = Runtime()
        return rt


def _unlink(path):
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass


@contextlib.contextmanager
def _measuring(ctx, kind, channel=None, session_id=None):
    rt = runtime(ctx)
    if not rt.lock.acquire(blocking=False):
        raise OpsError('busy', 'a measurement is already running (one at a time); wait for it or call measure_abort',
                       active=rt.active)
    fd = None
    try:
        root = _ensure_private_dir(Path(ctx.config.state_dir))
        fd = os.open(root / LOCK_FILE, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise OpsError('busy', 'another ops server process is measuring (one measurement at a time)') from None
        _unlink(root / ABORT_FILE)
        rt.abort.clear()
        rt.active = {'kind': kind, 'channel': channel, 'session_id': session_id, 'started_at': round(ctx.clock(), 3)}
        abort_file = root / ABORT_FILE
        rt.should_stop = lambda: rt.abort.is_set() or abort_file.exists()
        yield rt
    finally:
        rt.active = None
        rt.backend = None
        if fd is not None:
            os.close(fd)
        rt.lock.release()


def abort(ctx, _args):
    """Stop the running measurement promptly. Only children started by the measuring server are signalled."""
    rt = runtime(ctx)
    active = rt.active
    if active is not None:
        rt.abort.set()
        backend = rt.backend
        signalled = backend.stop() if backend is not None else 0
        return {'aborted': True, 'scope': 'this server', 'active': active, 'children_signalled': signalled,
                'note': 'Playback/recording children started by this server were stopped; nothing else was touched.'}
    root = Path(ctx.config.state_dir)
    try:
        fd = os.open(root / LOCK_FILE, os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC)
    except OSError:
        return {'aborted': False, 'note': 'no measurement is running'}
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            atomic_write_json(root / ABORT_FILE, {'requested_at': round(ctx.clock(), 3)})
            return {'aborted': True, 'scope': 'another ops server process',
                    'note': 'abort requested; the measuring server stops its own player/recorder within ~0.1 s'}
        fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)
    return {'aborted': False, 'note': 'no measurement is running'}


# ---- configuration-derived pieces --------------------------------------------------------
def _capture(ctx):
    injected = getattr(ctx, 'measure_capture', None)  # tests inject a simulated room here
    if injected is not None:
        return injected
    m = ctx.config.measurement
    if m['capture'] == 'phone':
        return PhoneCaptureBackend()
    return ProcessCapture(m['play_argv'], m['record_argv'])


def _rew_backend(ctx):
    from .measure_rew import RewBackend, RewClient
    m = ctx.config.measurement
    transport = getattr(ctx, 'rew_transport', None)  # tests inject a fake REW here
    return RewBackend(RewClient(m['rew']['url'], m['rew']['timeout_seconds'], transport, m['rew']['protocol']))


def _preflight(ctx, channel, audible=True):
    """Refuse (never auto-fix) unless the stack can carry a measurement signal. Returns (mm, stack, notes)."""
    mm = math_module()
    m = ctx.config.measurement
    if m['backend'] == 'builtin':
        if m['capture'] == 'phone':
            PhoneCaptureBackend().record()
        need = ['record_argv'] + (['play_argv'] if audible else [])
        missing = [k for k in need if not m[k]]
        if missing and getattr(ctx, 'measure_capture', None) is None:
            raise OpsError('measurement_unconfigured', f'measurement.{" and measurement.".join(missing)} not '
                           'configured; see docs/ops-mcp-measurement.md (player into the stack sink, recorder '
                           'from the measurement microphone)', missing=missing)
    if channel is not None and channel not in m['channel_map']:
        raise OpsError('invalid_argument', f'channel {channel!r} is not in measurement.channel_map',
                       channels=sorted(m['channel_map']))
    stack, notes = {}, []
    if not audible:
        return mm, stack, notes
    if m['require_owner_ready']:
        state = health.recovery_status(ctx).get('state')
        stack['owner_state'] = state
        if state != 'ready':
            raise OpsError('stack_not_ready', f'the stack owner reports {state!r}, not "ready". Measurement plays '
                           'through the stack sink (PipeWire -> CamillaDSP) and needs the owner-managed stack up; '
                           'check get_status/diagnose first.', owner_state=state)
    cam = ctx.clients.camilla
    try:
        dsp_state = cam.get_state()
        mute = cam.get_mute()
    except OpsError as error:
        raise OpsError('stack_not_ready', 'CamillaDSP is not reachable: measurement signals must pass through it '
                       '(volume ceiling, limits, crossover)', cause=error.code) from None
    stack['camilladsp_state'] = dsp_state
    if str(dsp_state) not in ('Running', 'Paused'):
        raise OpsError('stack_not_ready', f'CamillaDSP state is {dsp_state!r}; it must be Running (or Paused '
                       'waiting for signal)', camilladsp_state=dsp_state)
    if mute is True:
        raise OpsError('stack_muted', 'CamillaDSP main output is muted. Measurement never unmutes; unmute '
                       'deliberately (dsp_set_mute) and retry.')
    stack['mute'] = mute
    try:
        stack['main_volume_db'] = cam.get_volume()
    except OpsError:
        stack['main_volume_db'] = None
    stack['ops_delays_ms'], stack['dsp_mixers_sha256'] = _dsp_snapshot(cam)
    try:
        players = ctx.clients.ma.call('players/all')
    except OpsError as error:
        if error.code != 'ma_not_configured':
            # Fail closed: with Music Assistant configured but unreachable nobody can tell whether music is
            # playing into the same sink, and a sweep must not mix with programme material.
            raise OpsError('ma_unreachable', 'Music Assistant is configured but not reachable, so it cannot be '
                           'confirmed that nothing else is playing; a measurement must not mix with music. Check '
                           'Music Assistant (get_status) and retry. On a host without Music Assistant, leave '
                           'music_assistant.token_file unset.', cause=error.code) from None
        notes.append('Music Assistant is not configured (no token file): other playback was not checked.')
        players = []
    allowed = set(ctx.config.ma_players)
    busy = [p.get('display_name') or p.get('player_id') for p in players if isinstance(p, dict)
            and p.get('state') == 'playing' and (not allowed or p.get('player_id') in allowed)]
    if busy:
        raise OpsError('stack_busy', f'Music Assistant is playing on {busy}; pause it first (a measurement must not '
                       'mix with programme material)', players=busy)
    if m['sink_check_argv']:
        code, _out, _err = run_argv(m['sink_check_argv'], 5)
        if code != 0:
            raise OpsError('sink_unavailable', 'the configured sink probe (measurement.sink_check_argv) failed: the '
                           'stack sink is not available, so there is nothing safe to play into', exit_status=code)
    return mm, stack, notes


def _dsp_snapshot(cam):
    """(alignment delays active while measuring, digest of the DSP mixer routing/gains).

    The delays (ops_<channel>_delay) let alignment be made absolute; the mixer digest binds a
    confirmed level to the channel routing it was measured with. Both are None if unreadable.
    """
    try:
        config = cam.get_config()
    except OpsError:
        return None, None
    if not isinstance(config, dict):
        return None, None
    filters = config.get('filters') or {}
    rate = (config.get('devices') or {}).get('samplerate')
    out = {}
    for channel in ('left', 'right', 'sub'):
        item = filters.get(f'ops_{channel}_delay')
        if not isinstance(item, dict):
            out[channel] = 0.0
            continue
        out[channel] = safety.delay_ms(item.get('parameters') or {}, rate)
    mixers = json.dumps(config.get('mixers'), sort_keys=True, default=str)
    return out, hashlib.sha256(mixers.encode()).hexdigest()


@contextlib.contextmanager
def _workdir(ctx):
    root = _ensure_private_dir(Path(ctx.config.state_dir))
    path = _ensure_private_dir(root / WORK_DIR) / new_id(ctx.clock)
    path.mkdir(mode=0o700)
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


def _read_recording(mm, path, rate, need_channels):
    try:
        size = os.lstat(path).st_size
    except FileNotFoundError:
        raise OpsError('capture_failed', 'the recorder wrote no file (check measurement.record_argv uses {file})') from None
    if size > MAX_RECORDING_BYTES:
        raise OpsError('capture_failed', 'the recording exceeds the size limit')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    with os.fdopen(fd, 'rb') as handle:
        data = handle.read(MAX_RECORDING_BYTES + 1)
    try:
        got_rate, frames = mm.read_wav(data)
    except mm.WavError as error:
        raise OpsError('capture_failed', f'the recording could not be read: {error}') from None
    if got_rate != rate:
        raise OpsError('capture_failed', f'the recorder wrote {got_rate} Hz but measurement.sample_rate is {rate}; '
                       'pass {rate} to the recorder argv')
    if frames.shape[1] < need_channels:
        raise OpsError('capture_failed', f'the recording has {frames.shape[1]} channel(s); measurement needs '
                       f'{need_channels} (mic_channel/loopback capture_channel); pass {{channels}} to the recorder')
    if frames.shape[0] < int(0.25 * rate):
        raise OpsError('capture_failed', 'the recording is (almost) empty')
    return frames


def _play_record(ctx, rt, mm, frames, play_seconds, tail, workdir):
    m = ctx.config.measurement
    rate = m['sample_rate']
    play_file, rec_file = workdir / 'signal.wav', workdir / 'capture.wav'
    atomic_write_bytes(play_file, mm.wav_bytes(frames, rate, 3), limit=MAX_RECORDING_BYTES)
    backend = _capture(ctx)
    rt.backend = backend
    deadline = time.monotonic() + PREROLL_S + play_seconds + tail + WATCHDOG_SLACK_S
    outcome = backend.play_and_record(play_file=str(play_file), record_file=str(rec_file), rate=rate,
                                      out_channels=m['output_channels'], capture_channels=m['capture_channels'],
                                      play_seconds=play_seconds, preroll=PREROLL_S, tail=tail,
                                      should_stop=rt.should_stop, deadline=deadline)
    if outcome['status'] != 'ok':
        _stopped(outcome['status'])
    return _read_recording(mm, rec_file, rate, _needed_channels(m))


def _needed_channels(m):
    need = m['mic_channel'] + 1
    if m['loopback']:
        need = max(need, m['loopback']['capture_channel'] + 1)
    return need


def _stopped(status):
    if status == 'aborted':
        raise OpsError('measurement_aborted', 'the measurement was aborted (measure_abort); playback stopped')
    raise OpsError('measurement_timeout', 'the measurement exceeded its time budget; the watchdog stopped '
                   'playback and recording')


def _peak_check(mm, frames, policy):
    """Defence in depth: the generated file itself must respect the level cap."""
    peak = float(mm.np.max(mm.np.abs(frames))) if frames.size else 0.0
    if peak > 0 and 20 * math.log10(peak) > policy.sweep_level_max_dbfs + 0.01:
        raise SafetyError('generated signal would exceed the measurement level cap')


def level_snapshot(ctx, channel, stack, calibration_id):
    """What a confirmed level is bound to: a change of any of these needs a new level check."""
    m = ctx.config.measurement
    capture = json.dumps({'record_argv': m['record_argv'], 'capture': m['capture'], 'mic_channel': m['mic_channel'],
                          'capture_channels': m['capture_channels']}, sort_keys=True)
    return {'main_volume_db': stack.get('main_volume_db'), 'mute': stack.get('mute'),
            'outputs': list(m['channel_map'][channel]), 'dsp_mixers_sha256': stack.get('dsp_mixers_sha256'),
            'capture_sha256': hashlib.sha256(capture.encode()).hexdigest(), 'calibration_id': calibration_id}


def _snapshot_usable(snapshot):
    return snapshot.get('main_volume_db') is not None and snapshot.get('dsp_mixers_sha256') is not None


def _confirmed_level(ctx, channel, session, snapshot):
    """(level, None) for a level check that is still valid for ``snapshot``, else (None, why not).

    A confirmation is valid for policy.level_ttl_seconds and only while the main volume, mute,
    channel outputs, DSP mixer routing, capture device and calibration are unchanged.
    """
    ttl = ctx.config.measure_policy.level_ttl_seconds
    now = ctx.clock()
    why = 'no level check yet'
    for entry in ((session or {}).get('levels', {}).get(channel), runtime(ctx).levels.get(channel)):
        if entry is None:
            continue
        if not isinstance(entry, dict) or not isinstance(entry.get('snapshot'), dict):
            why = 'the level check predates expiry tracking; run it again'
            continue
        age = now - float(entry.get('at', 0.0))
        if not 0.0 <= age <= ttl:
            why = f'the level check expired (valid for {ttl:g} s)'
            continue
        if not _snapshot_usable(snapshot):
            why = 'the DSP main volume or configuration could not be read, so the level check cannot be matched'
            continue
        changed = sorted(k for k in snapshot if entry['snapshot'].get(k) != snapshot[k])
        if changed:
            why = f'the level check no longer matches the stack (changed: {", ".join(changed)})'
            continue
        return float(entry['level_dbfs']), None
    return None, why


def _allowed_level(ctx, channel, session, snapshot):
    policy = ctx.config.measure_policy
    confirmed, why = _confirmed_level(ctx, channel, session, snapshot)
    if confirmed is None:
        return policy.start_level_dbfs, f'start_level ({why})'
    return min(confirmed, policy.sweep_level_max_dbfs), 'level_check'


def _sub_only_outputs(m):
    cmap = m['channel_map']
    mains = set(cmap.get('left', ())) | set(cmap.get('right', ()))
    return set(cmap.get('sub', ())) - mains


def sub_limited(m, channel):
    """True when the sub frequency cap applies to ``channel``: the sub itself, or any channel whose
    outputs include a sub-only output (mapped to the sub and to neither main), e.g. a sum that
    includes a dedicated sub input."""
    return channel == 'sub' or bool(set(m['channel_map'].get(channel, ())) & _sub_only_outputs(m))


def _tone_freq(m, channel, policy):
    return min(60.0, policy.sub_sweep_max_hz / 2.0) if sub_limited(m, channel) else 1000.0


# ---- noise floor -----------------------------------------------------------------------
def noise_check(ctx, args, session=None):
    m = ctx.config.measurement
    if m['backend'] == 'rew':
        raise OpsError('not_supported', 'the REW backend has no noise-check API here; check the noise floor in REW '
                       '(RTA) or switch measurement.backend to "builtin"')
    mm, _stack, _notes = _preflight(ctx, None, audible=False)
    seconds = args.get('seconds', NOISE_SECONDS)
    rate = m['sample_rate']
    sid = (session or {}).get('session_id')
    with _measuring(ctx, 'noise_check', None, sid) as rt, _workdir(ctx) as workdir:
        backend = _capture(ctx)
        rt.backend = backend
        rec_file = workdir / 'noise.wav'
        outcome = backend.record(record_file=str(rec_file), rate=rate, capture_channels=m['capture_channels'],
                                 seconds=seconds, should_stop=rt.should_stop,
                                 deadline=time.monotonic() + seconds + WATCHDOG_SLACK_S)
        if outcome['status'] != 'ok':
            _stopped(outcome['status'])
        frames = _read_recording(mm, rec_file, rate, _needed_channels(m))
    mic = frames[int(0.1 * rate):, m['mic_channel']]
    report = mm.noise_report(mic, rate, 20.0, min(20000.0, 0.45 * rate))
    clipped = mm.clipped_count(mic, safety.CAPTURE_CLIP_DBFS)
    if report['rms_dbfs'] < SILENT_DBFS:
        verdict, advice = 'suspiciously_silent', ('The microphone input is digitally silent: check that the mic is '
                                                  'connected, the capture device/channel is right and its gain is up.')
    elif report['noise_floor_dbfs'] > NOISY_FLOOR_DBFS or clipped:
        verdict, advice = 'too_noisy', ('The room is too noisy for a clean measurement: ask the user to switch off '
                                        'fans/HVAC/appliances, close doors and keep quiet, then re-run the check.')
    else:
        verdict, advice = 'ok', 'Background noise is acceptable.'
    return {'verdict': verdict, 'advice': advice, 'seconds': seconds, 'clipped_samples': clipped, **report,
            'threshold_noise_floor_dbfs': NOISY_FLOOR_DBFS}


# ---- level check -----------------------------------------------------------------------
def level_check(ctx, args, session=None):
    channel = args['channel']
    m = ctx.config.measurement
    policy = ctx.config.measure_policy
    if m['backend'] == 'rew':
        raise OpsError('not_supported', 'the REW backend sets the sweep level directly (capped); use measure_sweep '
                       'with level_dbfs at or below the start level, or the builtin backend for stepwise checks')
    start = args.get('start_level_dbfs', policy.start_level_dbfs)
    safety.check_measure_level(policy, start, 'start_level_dbfs')
    if start > policy.start_level_dbfs:
        raise SafetyError(f'start_level_dbfs={start} is above the gentle start level {policy.start_level_dbfs}',
                          maximum=policy.start_level_dbfs)
    mm, stack, notes = _preflight(ctx, channel)
    cal_id, cal_source = resolve_calibration(ctx, args, session)
    if cal_id and cal_source not in ('explicit', 'session'):
        notes.append(f'calibration {cal_id} ({cal_source}) applied automatically')
    if cal_id:
        get_calibration(ctx, cal_id)  # must exist: the confirmation is bound to it
    snapshot = level_snapshot(ctx, channel, stack, cal_id)
    rate = m['sample_rate']
    freq = _tone_freq(m, channel, policy)
    chans = m['channel_map'][channel]
    lead = int(LEAD_S * rate)
    length = lead + int(TONE_SECONDS * rate) + int(0.05 * rate)
    noise = slice(int(TONE_NOISE_S[0] * rate), int(TONE_NOISE_S[1] * rate))
    step_seconds = PREROLL_S + length / rate + TONE_TAIL_S
    target = m['target_snr_db']
    level, steps, chosen, reason = float(start), [], None, 'max_steps'
    began, planned = time.monotonic(), 0.0
    sid = (session or {}).get('session_id')
    with _measuring(ctx, 'level_check', channel, sid) as rt, ctx.exclusive():
        for _ in range(MAX_LEVEL_STEPS):
            # Budget = the larger of the planned audible time and the elapsed wall time.
            if max(planned, time.monotonic() - began) + step_seconds > policy.max_duration_seconds:
                reason = 'max_duration'
                break
            planned += step_seconds
            if rt.should_stop():
                _stopped('aborted')
            frames = mm.program(length, m['output_channels'], [(lead, mm.tone(freq, TONE_SECONDS, rate, level), chans)])
            _peak_check(mm, frames, policy)
            with _workdir(ctx) as workdir:
                rec = _play_record(ctx, rt, mm, frames, length / rate, TONE_TAIL_S, workdir)
            mic = rec[:, m['mic_channel']]
            stats = mm.capture_stats(mic)
            tone = mm.tone_level(mic, rate, freq, noise)
            clipped = mm.clipped_count(mic, safety.CAPTURE_CLIP_DBFS)
            steps.append({'level_dbfs': round(level, 2), 'capture_peak_dbfs': stats['peak_dbfs'],
                          'clipped_samples': clipped, **tone})
            peak = stats['peak_dbfs']
            if clipped or peak >= safety.CAPTURE_CLIP_DBFS:
                chosen, reason = max(safety.MEASURE_LEVEL_FLOOR_DBFS, level - 12.0), 'capture_clipped'
                break
            if peak >= safety.CAPTURE_CLIP_RISK_DBFS:
                chosen, reason = level, 'capture_headroom'
                break
            if tone['snr_db'] >= target:
                chosen, reason = level, 'target_snr'
                break
            if level >= policy.sweep_level_max_dbfs:
                chosen, reason = level, 'level_cap'
                break
            increase = min(policy.level_step_max_db, safety.CAPTURE_CLIP_RISK_DBFS - peak, target - tone['snr_db'] + 3.0)
            if increase < 0.5:
                chosen, reason = level, 'capture_headroom'
                break
            level = min(policy.sweep_level_max_dbfs, round(level + increase, 2))
    if chosen is None:
        chosen = steps[-1]['level_dbfs'] if steps else None
    confirmation = None
    if chosen is not None:
        chosen = round(min(chosen, policy.sweep_level_max_dbfs), 2)
        confirmation = {'level_dbfs': chosen, 'at': round(ctx.clock(), 3), 'reason': reason,
                        'valid_for_seconds': policy.level_ttl_seconds, 'snapshot': snapshot}
        runtime(ctx).levels[channel] = confirmation
        if not _snapshot_usable(snapshot):
            notes.append('The DSP main volume or configuration could not be read: this level check cannot be '
                         'matched later, so sweeps stay at the start level until it can.')
    last = steps[-1] if steps else {}
    ok = reason in ('target_snr', 'capture_headroom') or (reason == 'level_cap' and last.get('snr_db', 0) >= m['min_snr_db'])
    advice = {
        'target_snr': 'Level found: the test tone is comfortably above the background noise.',
        'capture_headroom': 'Level limited by microphone headroom; that is fine.',
        'capture_clipped': 'The microphone input clipped: the level was backed off 12 dB. Reduce the mic/interface '
                           'gain if this repeats.',
        'level_cap': 'Reached the configured level cap before the target SNR: the room may be noisy or the '
                     'mic gain low. Raise the mic/interface gain or the main volume deliberately, or accept a '
                     'lower SNR.',
        'max_duration': 'Stopped at the time budget; run the check again to continue stepping up.',
        'max_steps': 'Stopped after the maximum number of steps; run the check again to continue stepping up.',
    }[reason]
    return {'channel': channel, 'tone_hz': freq, 'chosen_level_dbfs': chosen, 'stop_reason': reason, 'ok': ok,
            'advice': advice, 'steps': steps, 'target_snr_db': target, 'level_cap_dbfs': policy.sweep_level_max_dbfs,
            'confirmation': confirmation, 'stack': stack, 'notes': notes,
            'expiry': (f'The confirmed level is valid for {policy.level_ttl_seconds:g} s and only while the main '
                       'volume, mute, channel routing, capture device and calibration stay unchanged; otherwise '
                       'sweeps fall back to the start level until the level check is repeated.')}


# ---- sweep -----------------------------------------------------------------------------
def _sweep_range(ctx, channel, args):
    m, policy, rate = ctx.config.measurement, ctx.config.measure_policy, ctx.config.measurement['sample_rate']
    f1 = float(args.get('freq_min_hz', m['sweep_min_hz']))
    f2 = float(args.get('freq_max_hz', m['sweep_max_hz']))
    if f1 < policy.sweep_min_hz:
        raise SafetyError(f'freq_min_hz={f1} is below the sweep floor {policy.sweep_min_hz} Hz',
                          minimum=policy.sweep_min_hz)
    top = min(policy.sweep_max_hz, 0.48 * rate)
    if f2 > top:
        raise SafetyError(f'freq_max_hz={f2} is above the sweep ceiling {top} Hz', maximum=top)
    limited = None
    if sub_limited(m, channel) and f2 > policy.sub_sweep_max_hz:
        f2 = policy.sub_sweep_max_hz
        limited = (f'{channel} sweep upper frequency limited to {policy.sub_sweep_max_hz} Hz'
                   + ('' if channel == 'sub' else ' (its outputs include a sub-only output)'))
    if f2 < 4 * f1:
        raise OpsError('invalid_argument', 'the sweep must span at least two octaves')
    seconds = float(args.get('sweep_seconds', m['sweep_seconds']))
    if seconds > policy.sweep_seconds_max:
        raise SafetyError(f'sweep_seconds={seconds} exceeds the limit {policy.sweep_seconds_max}',
                          maximum=policy.sweep_seconds_max)
    return f1, f2, seconds, limited


def sweep(ctx, args, session=None):
    """One log sweep on one channel; analysed, quality-checked and stored. Returns a summary."""
    session = session or {}
    channel = args['channel']
    m, policy = ctx.config.measurement, ctx.config.measure_policy
    f1, f2, seconds, limited = _sweep_range(ctx, channel, args)
    if 'level_dbfs' in args:
        safety.check_measure_level(policy, float(args['level_dbfs']))
    smoothing = args.get('smoothing', m['smoothing'])
    cal_id, cal_source = resolve_calibration(ctx, args, session)
    cal = get_calibration(ctx, cal_id) if cal_id else None
    mm, stack, notes = _preflight(ctx, channel)
    if cal_id and cal_source not in ('explicit', 'session'):
        notes.append(f'calibration {cal_id} ({cal_source}) applied automatically')
    # The confirmed level is matched against the stack as it is now (volume, routing, capture, calibration).
    allowed, basis = _allowed_level(ctx, channel, session, level_snapshot(ctx, channel, stack, cal_id))
    level = float(args.get('level_dbfs', allowed))
    safety.check_measure_level(policy, level)
    if level > allowed + 1e-9:
        raise SafetyError(f'level_dbfs={level} is above the level allowed for {channel} ({allowed} dBFS, from '
                          f'{basis}); run measure_level_check first: it raises the level stepwise within the cap',
                          maximum=allowed)
    if limited:
        notes.append(limited)
    if m['backend'] == 'rew':
        return _rew_sweep(ctx, args, session, channel, f1, f2, seconds, level, smoothing, cal, stack, notes)
    rate = m['sample_rate']
    lead = int(LEAD_S * rate)
    sw = mm.log_sweep(f1, f2, seconds, rate, level)
    inv = mm.inverse_filter(sw, f1, f2, seconds, rate)
    chans = list(m['channel_map'][channel])
    loop = m['loopback']
    ref, placements, main_start = None, [], lead
    ref_channel = m['timing_reference']
    if loop:
        placements.append((lead, sw, chans + [loop['output_channel']]))
    elif channel != ref_channel:
        # The chirp is never louder than the target sweep, nor than the reference speaker's own (still valid)
        # confirmed level; without one it is held at the gentle start level.
        ref_confirmed, _why = _confirmed_level(ctx, ref_channel, session,
                                               level_snapshot(ctx, ref_channel, stack, cal_id))
        ref_level = min(level, policy.start_level_dbfs if ref_confirmed is None else ref_confirmed)
        ref_lo, top = REF_BAND_HZ[0], min(REF_BAND_HZ[1], policy.sweep_max_hz, 0.48 * rate)
        if sub_limited(m, ref_channel):  # defence in depth: configuration refuses such a timing reference
            top = min(top, policy.sub_sweep_max_hz)
            ref_lo = min(ref_lo, top / 8.0)  # keeps a usable inverse-filter band (2*lo .. top/2)
        rsw = mm.log_sweep(ref_lo, top, REF_SECONDS, rate, ref_level)
        ref = {'start': lead, 'sweep': rsw, 'inverse': mm.inverse_filter(rsw, ref_lo, top, REF_SECONDS, rate),
               'level_dbfs': ref_level, 'band_hz': [ref_lo, top]}
        placements.append((lead, rsw, list(m['channel_map'][ref_channel])))
        main_start = lead + len(rsw) + int(REF_GAP_S * rate)
        placements.append((main_start, sw, chans))
    else:
        placements.append((lead, sw, chans))
    length = main_start + len(sw) + int(0.05 * rate)
    planned = PREROLL_S + length / rate + TAIL_S
    if planned > policy.max_duration_seconds:
        raise SafetyError(f'this measurement would run {planned:.1f} s, above max_duration_seconds '
                          f'{policy.max_duration_seconds}; use a shorter sweep', maximum=policy.max_duration_seconds)
    frames = mm.program(length, m['output_channels'], placements)
    _peak_check(mm, frames, policy)
    with _measuring(ctx, 'sweep', channel, session.get('session_id')) as rt:
        with ctx.exclusive(), _workdir(ctx) as workdir:
            rec = _play_record(ctx, rt, mm, frames, length / rate, TAIL_S, workdir)
        result = mm.analyze_sweep(rec, rate=rate, mic_channel=m['mic_channel'], layout={'main_start': main_start},
                                  sweep=sw, inverse=inv, f1=f1, f2=f2, seconds=seconds, window_ms=WINDOW_MS,
                                  smoothing=SMOOTHING[smoothing], noise_slice=None,
                                  loop_channel=loop['capture_channel'] if loop else None, ref=ref,
                                  clip_dbfs=safety.CAPTURE_CLIP_DBFS)
    if result['timing'] is None:
        result['timing'] = {'timing_source': 'acoustic_reference', 'timing_confidence': 'low', 'arrival_ms': 0.0,
                            'note': f'{channel} is the timing reference speaker (arrival 0 by definition)'}
    elif ref is not None:
        result['timing']['reference_channel'] = ref_channel
        result['timing']['reference_level_dbfs'] = round(ref['level_dbfs'], 2)
        result['timing']['reference_band_hz'] = [round(f, 2) for f in ref['band_hz']]
    grid = [float(f) for f in result['grid']]
    mags = [float(d) for d in result['magnitude_db']]
    if cal is not None:
        offsets = calibration_offsets(cal['points'], grid)
        mags = [d - o for d, o in zip(mags, offsets)]
    quality = assess(result, m)
    settings = {'level_dbfs': round(level, 2), 'level_basis': basis, 'freq_min_hz': f1, 'freq_max_hz': f2,
                'sweep_seconds': seconds, 'smoothing': smoothing, 'sample_rate': rate, 'window_ms': WINDOW_MS,
                'output_channels': chans, 'backend': 'builtin',
                'loopback': bool(loop)}
    return _store(ctx, mm, channel=channel, args=args, session=session, settings=settings, stack=stack, notes=notes,
                  grid=grid, mags=mags, phase=[float(p) for p in result['phase_rad']],
                  group_delay=[float(g) for g in result['group_delay_ms']], impulse=result['impulse'],
                  impulse_offset=result['impulse_peak_offset'], rate=rate, timing=result['timing'],
                  quality=quality, cal=cal, metrics={
                      'raw_arrival_ms': result['raw_arrival_ms'], 'snr_by_octave_db': result['snr_by_octave_db'],
                      'capture': result['capture'], 'clipped_samples': result['clipped_samples'],
                      'noise': result['noise'], 'ir_peak_to_tail_db': result['ir_peak_to_tail_db']})


def assess(result, m):
    """Quality verdict for one sweep: ok | warn | redo, with plain-language reasons."""
    issues, warnings = [], []
    cap = result.get('capture')
    if cap is None:
        warnings.append({'code': 'quality_unverified', 'message': 'backend returned no capture metrics; inspect '
                         'the response before using it'})
        return {'status': 'warn', 'issues': issues, 'warnings': warnings}
    if result.get('clipped_samples') or cap['peak_dbfs'] >= safety.CAPTURE_CLIP_DBFS:
        issues.append({'code': 'clipping', 'message': 'the microphone capture clipped; redo at a lower level '
                       '(run measure_level_check again or pass a lower level_dbfs) or reduce the mic gain'})
    if cap['rms_dbfs'] < SILENT_DBFS or cap['peak_dbfs'] < -80.0:
        issues.append({'code': 'no_signal', 'message': 'the microphone recorded (almost) nothing: is the mic '
                       'connected, the capture device/channel right, and is the speaker playing?'})
    snr = result.get('snr_by_octave_db') or {}
    if snr:
        median, low = statistics.median(snr.values()), min(snr.values())
        if median < m['min_snr_db']:
            issues.append({'code': 'low_snr', 'message': f'signal-to-noise {median:.0f} dB is below the minimum '
                           f'{m["min_snr_db"]:.0f} dB: reduce background noise or raise the level (level check)'})
        elif low < m['min_snr_db']:
            bands = [k for k, v in snr.items() if v < m['min_snr_db']]
            warnings.append({'code': 'low_snr_band', 'message': f'low SNR in octave band(s) {bands} Hz'})
        elif median < m['target_snr_db']:
            warnings.append({'code': 'below_target_snr', 'message': f'SNR {median:.0f} dB is below the target '
                             f'{m["target_snr_db"]:.0f} dB'})
    if result.get('ir_peak_to_tail_db', 99.0) < 20.0:
        issues.append({'code': 'no_clear_impulse', 'message': 'no clear impulse response: the capture looks like '
                       'noise (mic disconnected, wrong capture channel, or the speaker did not play)'})
    if abs(cap.get('dc_offset', 0.0)) > 0.05:
        warnings.append({'code': 'dc_offset', 'message': 'large DC offset on the microphone input'})
    if (result.get('noise') or {}).get('short_estimate'):
        warnings.append({'code': 'noise_estimate_short', 'message': 'little pre-roll silence; SNR is approximate'})
    status = 'redo' if issues else ('warn' if warnings else 'ok')
    return {'status': status, 'issues': issues, 'warnings': warnings}


# ---- storage and exports ---------------------------------------------------------------
def _wrap_deg(rad):
    deg = math.degrees(rad)
    return (deg + 180.0) % 360.0 - 180.0


def export_text(grid, mags, phase, header_lines):
    rows = [f'{f:.4f} {d:.3f} {_wrap_deg(p):.2f}' for f, d, p in zip(grid, mags, phase)]
    head = [f'* {line}' for line in header_lines]
    return '\n'.join(head + rows) + '\n'


def _artifacts_root(ctx):
    return _ensure_private_dir(_ensure_private_dir(Path(ctx.config.state_dir)) / ARTIFACT_DIR)


def _prune_artifacts(root, keep, retention):
    names = sorted(p.name for p in root.iterdir() if p.is_dir() and not p.name.startswith('.'))
    others = [n for n in names if n != keep]
    for name in others[:max(0, len(others) + 1 - retention)]:
        shutil.rmtree(root / name, ignore_errors=True)
    # Leftovers of an interrupted store; only old ones, a concurrent store may be writing a fresh one.
    now = time.time()
    for stale in root.iterdir():
        try:
            old = now - stale.stat().st_mtime > STALE_TMP_S
        except OSError:
            continue
        if stale.name.startswith('.tmp-') and old:
            shutil.rmtree(stale, ignore_errors=True)


def _store(ctx, mm, *, channel, args, session, settings, stack, notes, grid, mags, phase, group_delay, impulse,
           impulse_offset, rate, timing, quality, cal, metrics):
    m = ctx.config.measurement
    label = args.get('label') or f'{channel} {session.get("position_id") or ""}'.strip()
    artifact_id = new_id(ctx.clock)
    root = _artifacts_root(ctx)
    units = 'dB re digital transfer (0 dB = capture level equals play level)'
    header = [f'garage-audio ops measurement {artifact_id}', f'channel {channel}; label {label}',
              f'log sweep {settings["freq_min_hz"]:g}-{settings["freq_max_hz"]:g} Hz, smoothing {settings["smoothing"]}',
              'mic calibration ' + (f'{cal["name"]} applied' if cal else 'not applied'), units,
              'Freq(Hz) SPL(dB) Phase(degrees)']
    temp = root / f'.tmp-{artifact_id}'
    temp.mkdir(mode=0o700)
    try:
        text = export_text(grid, mags, phase, header)
        atomic_write_bytes(temp / 'response-rew.txt', text.encode())
        atomic_write_bytes(temp / 'response.frd', export_text(grid, mags, phase, []).encode())
        impulse_info = None
        if impulse is not None and len(impulse):
            peak = float(mm.np.max(mm.np.abs(impulse))) or 1.0
            atomic_write_bytes(temp / 'impulse.wav', mm.wav_bytes(mm.np.asarray(impulse) / peak * 0.99, rate, 4),
                               limit=MAX_RECORDING_BYTES)
            impulse_info = {'file': 'impulse.wav', 'format': 'mono 32-bit PCM', 'sample_rate': rate,
                            'scale': round(peak / 0.99, 9), 'peak_offset_samples': int(impulse_offset)}
        meta = {'artifact_id': artifact_id, 'channel': channel, 'label': label, 'settings': settings,
                'position_id': session.get('position_id'), 'session_id': session.get('session_id'),
                'calibration': ({'id': cal['id'], 'name': cal['name'], 'orientation': cal['orientation']}
                                if cal else None),
                'timing': timing, 'quality': quality, 'stack': stack, 'notes': notes, 'units': units,
                'impulse': impulse_info, 'metrics': metrics, 'measured_at': round(ctx.clock(), 3),
                'group_delay_ms': [round(g, 4) for g in group_delay],
                'files': ['response-rew.txt', 'response.frd'] + (['impulse.wav'] if impulse_info else [])}
        atomic_write_json(temp / 'measurement.json', meta)
        os.rename(temp, root / artifact_id)
    except BaseException:
        shutil.rmtree(temp, ignore_errors=True)
        raise
    try:
        record = _save_record(ctx, channel, label, notes, text, header, artifact_id, session, cal, timing, quality,
                              settings, units, stack, grid, mags, phase)
    except BaseException:
        shutil.rmtree(root / artifact_id, ignore_errors=True)  # no orphan files without a history record
        raise
    _prune_artifacts(root, artifact_id, m['retention'])
    return {'measurement_id': record['id'], 'artifact_id': artifact_id, 'channel': channel, 'label': label,
            'quality': quality, 'timing': timing, 'settings': settings,
            'summary': summarize(grid, mags), 'snr_by_octave_db': metrics.get('snr_by_octave_db'),
            'capture': metrics.get('capture'), 'calibration_applied': bool(cal), 'notes': notes,
            'files': str(root / artifact_id), 'next': 'measure_get for details; room_compare_to_target / '
                                                      'room_propose_peq with measurement_id; '
                                                      'room_propose_alignment with measurement_ids.'}


def _save_record(ctx, channel, label, notes, text, header, artifact_id, session, cal, timing, quality, settings,
                 units, stack, grid, mags, phase):
    return ctx.history.save({
        'kind': 'measurement', 'source': settings['backend'], 'channel': channel, 'label': label,
        'distance_m': None, 'notes': '; '.join(notes)[:500], 'sha256': hashlib.sha256(text.encode()).hexdigest(),
        'headers': header[:20], 'artifact_id': artifact_id, 'position_id': session.get('position_id'),
        'session_id': session.get('session_id'), 'calibration_id': cal['id'] if cal else None,
        'timing': timing, 'quality': quality['status'], 'settings': settings, 'units': units,
        'ops_delays_ms': stack.get('ops_delays_ms'),
        'points': [{'frequency_hz': round(f, 4), 'db': round(d, 3), 'phase_deg': round(math.degrees(p), 2)}
                   for f, d, p in zip(grid, mags, phase)]})


def summarize(grid, mags):
    """Levels at octave centres (nearest grid point) for a compact tool result."""
    out = {}
    for centre in (31.5, 63, 125, 250, 500, 1000, 2000, 4000, 8000, 16000):
        if grid[0] <= centre <= grid[-1]:
            i = min(range(len(grid)), key=lambda k: abs(math.log(grid[k] / centre)))
            out[str(centre)] = round(mags[i], 1)
    return {'freq_range_hz': [round(grid[0], 2), round(grid[-1], 2)], 'points': len(grid), 'octave_levels_db': out}


def get_measurement(ctx, args):
    record = ctx.history.get(args['measurement_id'])
    out = {k: v for k, v in record.items() if k != 'points'}
    points = record.get('points') or []
    if points:
        out['summary'] = summarize([p['frequency_hz'] for p in points], [p['db'] for p in points])
    if args.get('include_points'):
        out['points'] = points
    artifact = record.get('artifact_id')
    if isinstance(artifact, str) and re.fullmatch(r'[0-9]{8}T[0-9]{6}\.[0-9]{6}Z-[0-9a-f]{8}', artifact):
        path = Path(ctx.config.state_dir) / ARTIFACT_DIR / artifact
        try:
            from .backups import read_json
            meta = read_json(path / 'measurement.json')
            out['artifacts'] = {'directory': str(path), 'files': meta.get('files'), 'metrics': meta.get('metrics'),
                                'impulse': meta.get('impulse')}
        except (OSError, ValueError):
            out['artifacts'] = {'directory': str(path), 'missing': True, 'note': 'pruned by retention or removed'}
    return out


# ---- microphone calibration ------------------------------------------------------------
def calibrations(ctx):
    from . import measure_cal  # the calibration journal never prunes an active calibration
    return measure_cal.journal(ctx)


def resolve_calibration(ctx, args, session):
    """(calibration_id, source) for a measurement: explicit argument, else the session's pinned calibration, else
    (no session) the active calibration for the configured default orientation."""
    if args.get('calibration_id'):
        return args['calibration_id'], 'explicit'
    if session and session.get('session_id'):
        return session.get('calibration_id'), 'session'
    from . import measure_cal
    orientation = ctx.config.measurement['calibration']['default_orientation']
    ident = measure_cal.active_id(ctx, orientation)
    return ident, (f'active {orientation}-degree calibration' if ident else None)


_NUM = r'([-+]?(?:\d+(?:\.\d*)?|\.\d+))'  # UMIK files may write "-.7dB"
_SENS = re.compile(r'(?i)sens(?:itivity)?\s*factor\s*=\s*' + _NUM + r'\s*dB')
_SENS_ALT = re.compile(r'(?i)^"?sensitivity\s+' + _NUM)
_AGAIN = re.compile(r'(?i)again\s*=\s*' + _NUM + r'\s*dB')


def parse_calibration(text, name, orientation=None):
    """Parse a UMIK/miniDSP-style or generic freq/dB calibration file (stdlib)."""
    try:
        parsed = calibration.parse(text, 'mic-calibration')
    except ValueError as error:
        raise OpsError('calibration_invalid', f'calibration file could not be parsed: {error}') from None
    sens = again = None
    for line in parsed['headers']:
        match = _SENS.search(line) or _SENS_ALT.search(line)
        if match and sens is None:
            sens = float(match.group(1))
        match = _AGAIN.search(line)
        if match and again is None:
            again = float(match.group(1))
    if orientation is None:
        lowered = (name + ' ' + ' '.join(parsed['headers'])).lower()
        orientation = '90' if re.search(r'(?<!\d)90(?:\s*deg|°|_deg|deg)?(?!\d)', lowered) else (
            '0' if re.search(r'(?<!\d)0(?:\s*deg|°|_deg|deg)(?!\d)', lowered) else 'unknown')
    points = [[p['frequency_hz'], p['db']] for p in parsed['points']]
    if points[0][0] > 100 or points[-1][0] < 5000:
        raise OpsError('calibration_invalid', 'calibration must cover at least 100 Hz to 5 kHz')
    if max(abs(p[1]) for p in points) > 40:
        raise OpsError('calibration_invalid', 'calibration corrections above 40 dB are not plausible')
    return {'kind': 'mic_calibration', 'name': name, 'orientation': orientation, 'sensitivity_factor_db': sens,
            'again_db': again, 'sha256': parsed['sha256'], 'points': points}


def import_calibration(ctx, args):
    from . import measure_cal
    return measure_cal.import_calibration(ctx, args)


def orientation_advice(orientation):
    if orientation == '90':
        return 'Point the microphone straight up at the ceiling (90-degree calibration).'
    if orientation == '0':
        return 'Point the microphone horizontally at the speakers (0-degree calibration).'
    return ('Orientation unknown: if both are available, prefer the 90-degree file with the mic pointing straight '
            'up; with a 0-degree file point it at the speakers.')


def get_calibration(ctx, ident):
    record = calibrations(ctx).get(ident)
    if record.get('kind') != 'mic_calibration' or not isinstance(record.get('points'), list):
        raise OpsError('unreadable', 'calibration record is malformed')
    return record


def list_calibrations(ctx, _args):
    from . import measure_cal
    out = []
    journal = calibrations(ctx)
    active = {o: measure_cal.active_id(ctx, o) for o in measure_cal.ORIENTATIONS}
    for ident in journal.list(ctx.config.measurement['calibration']['retention']):
        try:
            rec = journal.get(ident)
        except OpsError:
            continue
        out.append({'calibration_id': ident, 'name': rec.get('name'), 'orientation': rec.get('orientation'),
                    'sensitivity_factor_db': rec.get('sensitivity_factor_db'), 'points': len(rec.get('points') or []),
                    'model': rec.get('model'), 'serial': rec.get('serial'), 'provider': rec.get('provider'),
                    'active_for': [o for o, i in active.items() if i == ident]})
    return {'calibrations': out, 'active': active}


def calibration_offsets(points, grid):
    """Calibration dB at each grid frequency: log-frequency interpolation, edge values held."""
    freqs = [p[0] for p in points]
    out, j = [], 0
    for f in grid:
        if f <= freqs[0]:
            out.append(points[0][1])
            continue
        if f >= freqs[-1]:
            out.append(points[-1][1])
            continue
        while freqs[j + 1] < f:
            j += 1
        (f0, d0), (f1, d1) = points[j], points[j + 1]
        t = (math.log(f) - math.log(f0)) / (math.log(f1) - math.log(f0))
        out.append(d0 + (d1 - d0) * t)
    return out


# ---- REW backend sweep -----------------------------------------------------------------
def _rew_sweep(ctx, args, session, channel, f1, f2, seconds, level, smoothing, cal, stack, notes):
    mm = math_module()
    backend = _rew_backend(ctx)
    m = ctx.config.measurement
    notes.append('REW backend: REW plays through its own configured output; that output must be the stack sink '
                 '(never the DAC directly). The requested channel routing cannot be verified from here.')
    policy = ctx.config.measure_policy
    if PREROLL_S + seconds + TAIL_S > policy.max_duration_seconds:
        raise SafetyError(f'this measurement would run {PREROLL_S + seconds + TAIL_S:.1f} s, above '
                          f'max_duration_seconds {policy.max_duration_seconds}', maximum=policy.max_duration_seconds)
    with _measuring(ctx, 'sweep', channel, session.get('session_id')) as rt:
        rt.backend = backend
        with ctx.exclusive():
            # REW cannot be force-stopped from here: on the deadline the backend asks REW to cancel.
            result = backend.sweep(f1=f1, f2=f2, seconds=seconds, rate=m['sample_rate'], level_dbfs=level,
                                   title=args.get('label') or f'ops {channel}', should_stop=rt.should_stop,
                                   deadline=time.monotonic() + policy.max_duration_seconds)
    notes.extend(backend.notes)
    notes.append('REW API names used here are unverified against a live REW (measurement.rew.protocol overrides '
                 'them); automated REW sweeps need a REW Pro licence.')
    if result['status'] != 'ok':
        _stopped(result['status'])
    grid, mags = result['grid'], result['magnitude_db']
    if cal is not None:
        mags = [d - o for d, o in zip(mags, calibration_offsets(cal['points'], grid))]
    settings = {'level_dbfs': round(level, 2), 'level_basis': 'requested (REW)', 'freq_min_hz': f1,
                'freq_max_hz': f2, 'sweep_seconds': seconds, 'smoothing': smoothing, 'sample_rate': m['sample_rate'],
                'backend': 'rew', 'rew_measurement_id': result.get('rew_measurement_id'),
                'output_channels': m['channel_map'][channel], 'loopback': None}
    return _store(ctx, mm, channel=channel, args=args, session=session, settings=settings, stack=stack, notes=notes,
                  grid=grid, mags=mags, phase=result['phase_rad'], group_delay=[], impulse=result.get('impulse'),
                  impulse_offset=result.get('impulse_peak_offset', 0), rate=result.get('impulse_rate', m['sample_rate']),
                  timing=result['timing'], quality=assess({}, m), cal=cal, metrics={})


# ---- read-only status and devices ------------------------------------------------------
def status(ctx, _args):
    m = ctx.config.measurement
    rt = runtime(ctx)
    ttl, now = ctx.config.measure_policy.level_ttl_seconds, ctx.clock()
    levels = {c: {'level_dbfs': v['level_dbfs'], 'reason': v.get('reason'),
                  'expires_in_seconds': round(max(0.0, v['at'] + ttl - now), 1),
                  'note': 'also invalid once volume, mute, routing, capture or calibration change'}
              for c, v in rt.levels.items()}
    return {'allow_write': ctx.config.allow_write, 'allow_measure': ctx.config.allow_measure,
            'extra': extra_status(), 'backend': m['backend'], 'capture': m['capture'],
            'player_configured': bool(m['play_argv']), 'recorder_configured': bool(m['record_argv']),
            'channels': sorted(m['channel_map']), 'loopback': m['loopback'], 'timing_reference': m['timing_reference'],
            'limits': ctx.config.measure_policy.as_dict(), 'active': rt.active,
            'confirmed_levels': levels, 'target_snr_db': m['target_snr_db'], 'min_snr_db': m['min_snr_db'],
            'how_it_plays': 'player argv -> stack sink (PipeWire) -> CamillaDSP (limits, crossover, volume ceiling) '
                            '-> DAC owned by the supervisor-managed stack'}


def list_devices(ctx, _args):
    argvs = ctx.config.measurement['list_devices_argv']
    if not argvs:
        return {'devices': [], 'note': 'measurement.list_devices_argv is empty'}
    runner = getattr(ctx, 'measure_runner', None) or run_argv
    return {'devices': list_capture_devices(argvs, runner),
            'note': 'Raw listings from the configured read-only commands; put the chosen device into '
                    'measurement.record_argv (and capture_channels/mic_channel).'}
