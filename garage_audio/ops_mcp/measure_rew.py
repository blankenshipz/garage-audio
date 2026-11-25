"""Optional REW (Room EQ Wizard) REST API backend for ``measure_sweep`` (default off).

REW 5.40+ exposes an HTTP API (start REW with ``-api`` or enable it in Preferences;
default ``http://127.0.0.1:4735``). Automated measurement through the API (starting a sweep
and reading results back) REQUIRES A REW PRO LICENCE; without it REW refuses the measure
command and this backend reports a ``rew_error``.

Every endpoint path, command name and payload field used here lives in ``PROTOCOL`` below and
can be overridden per key with ``measurement.rew.protocol`` (validated, see config.py). They are
UNVERIFIED AGAINST A LIVE REW: they follow the published API description as far as it could be
read, tests use a fake, and the installed REW build's own description (``GET /`` on the API
port serves an OpenAPI document) is authoritative.

REW plays through the output device configured *inside REW*. That output must be the stack
sink (PipeWire -> CamillaDSP), never the DAC; the ops server cannot verify REW's routing
and says so in every result. Level checks and noise checks are not provided through this
backend; the sweep level sent to REW is still capped by the measurement policy.

Behaviour: REW's blocking mode is read first, switched on for the measurement and restored
afterwards. The blocking measure call runs in a worker thread; an abort or the deadline sends
the cancel command and returns without waiting for REW. Measurement indices are 1-based.
"""
import base64
import json
import math
import re
import struct
import threading
import time
import urllib.error
import urllib.request

from .errors import OpsError

# One table of everything protocol-specific. ALL UNVERIFIED against a live REW (see docstring);
# override per key with measurement.rew.protocol. Methods are fixed (GET = read, POST = change).
PROTOCOL = {
    'path.version': '/version',
    'path.blocking': '/application/blocking',          # GET -> bool, POST bool
    'path.level': '/measure/level',
    'path.sweep_config': '/measure/sweep/configuration',
    'path.naming': '/measure/naming',
    'path.command': '/measure/command',
    'path.measurements': '/measurements',                # keyed by measurement index
    'path.frequency_response': '/measurements/{id}/frequency-response',
    'path.impulse_response': '/measurements/{id}/impulse-response',
    'command.measure': 'Measure',                        # REW also has an "SPL" command; Measure = sweep
    'command.cancel': 'Cancel',
    'field.command': 'command',
    'field.sweep_start': 'startFreq',
    'field.sweep_end': 'endFreq',
    'field.sweep_length': 'length',
    'index_base': 1,                                     # REW numbers measurements from 1
}
CALLS = {
    'version': ('GET', 'path.version'),
    'blocking_get': ('GET', 'path.blocking'),
    'blocking_set': ('POST', 'path.blocking'),
    'level': ('POST', 'path.level'),
    'sweep_config': ('POST', 'path.sweep_config'),
    'naming': ('POST', 'path.naming'),
    'command': ('POST', 'path.command'),
    'measurements': ('GET', 'path.measurements'),
    'frequency_response': ('GET', 'path.frequency_response'),
    'impulse_response': ('GET', 'path.impulse_response'),
}
_PATH = re.compile(r'^/[A-Za-z0-9_./-]{0,120}(\{id\}[A-Za-z0-9_./-]{0,60})?$')
_NAME = re.compile(r'^[A-Za-z][A-Za-z0-9_]{0,40}$')
MAX_RESPONSE = 32 * 1024 * 1024
POLL_S = 0.1
RESULT_WAIT_S = 5.0     # after the blocking call returns: how long to look for the new measurement
CANCEL_JOIN_S = 2.0     # after cancel: how long to wait for the worker before returning anyway


def validate_protocol(overrides):
    """Validated copy of PROTOCOL with ``overrides`` applied (raises ValueError with a plain message)."""
    if not isinstance(overrides, dict):
        raise ValueError('measurement.rew.protocol must be an object')
    out = dict(PROTOCOL)
    for key, value in overrides.items():
        if key not in PROTOCOL:
            raise ValueError(f'measurement.rew.protocol: unknown key {key!r} (known: {sorted(PROTOCOL)})')
        if key == 'index_base':
            if value not in (0, 1) or isinstance(value, bool):
                raise ValueError('measurement.rew.protocol.index_base must be 0 or 1')
        elif key.startswith('path.'):
            if not isinstance(value, str) or not _PATH.fullmatch(value) or '..' in value:
                raise ValueError(f'measurement.rew.protocol.{key} must be a plain absolute API path')
            if ('{id}' in PROTOCOL[key]) != ('{id}' in value):
                raise ValueError(f'measurement.rew.protocol.{key} must {"" if "{id}" in PROTOCOL[key] else "not "}'
                                 'contain {id}')
        elif not isinstance(value, str) or not _NAME.fullmatch(value):
            raise ValueError(f'measurement.rew.protocol.{key} must be a plain identifier')
        out[key] = value
    return out


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def _urllib_transport(method, url, body, timeout):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(url, data=data, method=method,
                                     headers={'Content-Type': 'application/json', 'Accept': 'application/json'})
    try:
        with opener.open(request, timeout=timeout) as response:
            status, raw = response.status, response.read(MAX_RESPONSE + 1)
    except urllib.error.HTTPError as error:
        return error.code, None
    if len(raw) > MAX_RESPONSE:
        raise OpsError('rew_error', 'REW returned an oversized response')
    if not raw.strip():
        return status, None
    try:
        return status, json.loads(raw)
    except ValueError:
        raise OpsError('rew_error', 'REW returned invalid JSON') from None


class RewClient:
    def __init__(self, url, timeout, transport=None, protocol=None):
        self.url = url.rstrip('/')
        self.timeout = timeout
        self.transport = transport or _urllib_transport
        self.protocol = dict(protocol or PROTOCOL)

    def call(self, key, body=None, timeout=None, **params):
        method, path_key = CALLS[key]
        path = self.protocol[path_key]
        path = path.format(**{k: urllib.request.quote(str(v), safe='') for k, v in params.items()})
        try:
            status, value = self.transport(method, self.url + path, body, timeout or self.timeout)
        except OpsError:
            raise
        except (OSError, ValueError):
            raise OpsError('rew_unreachable', 'the REW API is not reachable at measurement.rew.url (is REW running '
                           'with the API enabled?)') from None
        if not 200 <= int(status) < 300:
            raise OpsError('rew_error', f'REW API {method} {path} answered HTTP {status}', http_status=status)
        return value

    def command(self, which, timeout=None):
        p = self.protocol
        return self.call('command', {p['field.command']: p[f'command.{which}']}, timeout=timeout)


def decode_floats(text):
    """REW encodes arrays as base64 big-endian float32."""
    try:
        raw = base64.b64decode(text, validate=True)
    except (ValueError, TypeError):
        raise OpsError('rew_error', 'REW returned an undecodable array') from None
    if len(raw) % 4:
        raise OpsError('rew_error', 'REW array length is not a multiple of 4 bytes')
    values = list(struct.unpack(f'>{len(raw) // 4}f', raw))
    if not all(math.isfinite(v) for v in values):
        raise OpsError('rew_error', 'REW array has non-finite values')
    return values


def _log_resample(freqs, values, grid):
    out, j = [], 0
    for f in grid:
        while j < len(freqs) - 2 and freqs[j + 1] < f:
            j += 1
        f0, f1 = freqs[j], freqs[j + 1]
        t = 0.0 if f1 == f0 else (math.log(f) - math.log(f0)) / (math.log(f1) - math.log(f0))
        out.append(values[j] + (values[j + 1] - values[j]) * min(1.0, max(0.0, t)))
    return out


class RewBackend:
    name = 'rew'

    def __init__(self, client):
        self.client = client
        self._cancel_requested = False
        self.notes = []

    def stop(self):
        """Best effort: ask REW to cancel the running measurement. REW owns its own playback."""
        self._cancel_requested = True
        try:
            self.client.command('cancel', timeout=min(5.0, self.client.timeout))
        except OpsError:
            return 0
        return 1

    def _blocking(self):
        """REW's current blocking mode (None if it cannot be read)."""
        try:
            value = self.client.call('blocking_get')
        except OpsError:
            return None
        if isinstance(value, dict):  # tolerate {"blocking": bool} as well as a bare bool
            value = value.get('blocking', value.get('value'))
        return value if isinstance(value, bool) else None

    def sweep(self, *, f1, f2, seconds, rate, level_dbfs, title, should_stop, deadline):
        p = self.client.protocol
        before = set(self._ids())
        previous = self._blocking()
        if previous is None:
            self.notes.append("REW's blocking mode could not be read; it is left enabled after the measurement.")
        self.client.call('blocking_set', True)
        try:
            self.client.call('level', {'value': round(level_dbfs, 2), 'unit': 'dBFS'})
            length = 1 << max(16, min(22, int(math.log2(max(1.0, seconds * rate)))))
            self.client.call('sweep_config', {p['field.sweep_start']: f1, p['field.sweep_end']: f2,
                                              p['field.sweep_length']: length})
            self.client.call('naming', {'title': title[:60]})
            outcome = self._measure(should_stop, deadline)
            if outcome is not None:
                return outcome
            new = self._new_id(before, should_stop, deadline)
            if isinstance(new, dict):
                return new
            return {'status': 'ok', 'rew_measurement_id': new, **self._fetch(new, f1, f2)}
        finally:
            if previous is False:  # it was off before: switch it back off
                try:
                    self.client.call('blocking_set', previous)
                except OpsError:
                    self.notes.append("REW's blocking mode could not be restored.")

    def _measure(self, should_stop, deadline):
        """Run the blocking measure command in a worker; None when it finished, else an aborted/timeout result."""
        box = {}
        done = threading.Event()

        def work():
            try:
                self.client.command('measure', timeout=max(1.0, deadline - time.monotonic()))
            except BaseException as error:  # noqa: BLE001 - handed to the caller thread
                box['error'] = error
            finally:
                done.set()

        worker = threading.Thread(target=work, name='rew-measure', daemon=True)
        worker.start()
        while not done.wait(POLL_S):
            status = None
            if should_stop() or self._cancel_requested:
                status = 'aborted'
            elif time.monotonic() >= deadline:
                status = 'timeout'
            if status:
                self.stop()
                done.wait(CANCEL_JOIN_S)
                return {'status': status}
        if self._cancel_requested or should_stop():
            return {'status': 'aborted'}
        if 'error' in box:
            raise box['error']
        return None

    def _new_id(self, before, should_stop, deadline):
        until = min(deadline, time.monotonic() + RESULT_WAIT_S)
        while True:
            added = [i for i in self._ids() if i not in before]
            if added:
                return added[-1]
            if should_stop() or self._cancel_requested:
                return {'status': 'aborted'}
            if time.monotonic() >= until:
                raise OpsError('rew_error', 'REW finished the measure command but no new measurement appeared (is a '
                               'REW Pro licence active? see measurement.rew in docs/ops-mcp-measurement.md)')
            time.sleep(POLL_S)

    def _ids(self):
        value = self.client.call('measurements')
        base = self.client.protocol['index_base']
        if isinstance(value, dict):
            return [str(k) for k in value]
        if isinstance(value, list):
            return [str(item.get('id', i)) if isinstance(item, dict) else str(item)
                    for i, item in enumerate(value, start=base)]
        raise OpsError('rew_error', 'REW measurement list has an unexpected shape')

    def _fetch(self, ident, f1, f2):
        from .room import log_grid
        fr = self.client.call('frequency_response', id=ident)
        if not isinstance(fr, dict) or 'magnitude' not in fr or 'startFreq' not in fr:
            raise OpsError('rew_error', 'REW frequency response has an unexpected shape')
        mags = decode_floats(fr['magnitude'])
        phases = decode_floats(fr['phase']) if fr.get('phase') else [0.0] * len(mags)
        start = float(fr['startFreq'])
        if fr.get('ppo'):
            freqs = [start * 2 ** (i / float(fr['ppo'])) for i in range(len(mags))]
        else:
            step = float(fr.get('freqStep') or 0)
            if step <= 0:
                raise OpsError('rew_error', 'REW frequency response has neither ppo nor freqStep')
            freqs = [start + i * step for i in range(len(mags))]
        if len(freqs) < 2 or freqs[0] <= 0:
            raise OpsError('rew_error', 'REW frequency response is too short')
        lo, hi = max(f1, freqs[0]), min(f2, freqs[-1])
        if hi <= lo * 2:
            raise OpsError('rew_error', 'REW frequency response does not cover the requested range')
        grid = log_grid(lo, hi)
        unwrapped, offset, prev = [], 0.0, None
        for p in phases:
            if prev is not None:
                while p + offset - prev > 180:
                    offset -= 360
                while p + offset - prev < -180:
                    offset += 360
            prev = p + offset
            unwrapped.append(prev)
        out = {'grid': grid, 'magnitude_db': _log_resample(freqs, mags, grid),
               'phase_rad': [math.radians(p) for p in _log_resample(freqs, unwrapped, grid)],
               'timing': {'timing_source': 'rew', 'timing_confidence': 'low', 'arrival_ms': None,
                          'note': 'timing depends on the REW timing reference setup; not verified here'}}
        ir = self.client.call('impulse_response', id=ident)
        if isinstance(ir, dict) and ir.get('data'):
            samples = decode_floats(ir['data'])
            rate = int(ir.get('sampleRate') or 48000)
            peak = max(range(len(samples)), key=lambda i: abs(samples[i])) if samples else 0
            start_t = float(ir.get('startTime') or 0.0)
            out['timing']['arrival_ms'] = round((start_t + peak / rate) * 1000.0, 3)
            lo_i = max(0, peak - int(0.05 * rate))
            out.update({'impulse': samples[lo_i:peak + rate], 'impulse_peak_offset': peak - lo_i, 'impulse_rate': rate})
        return out
