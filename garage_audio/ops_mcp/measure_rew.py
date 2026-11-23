"""Optional REW (Room EQ Wizard) REST API backend for ``measure_sweep`` (default off).

REW 5.40+ exposes an HTTP API (start REW with ``-api`` or enable it in Preferences;
default ``http://127.0.0.1:4735``). The endpoint paths and payload shapes below are kept
in one table so they can be checked against the API documentation of the installed REW
build (``GET /`` on the API port serves an OpenAPI description). They have NOT been
verified against a live REW instance in this repository: tests use a fake. Some features
(e.g. automation of measurements) may need a REW Pro licence.

REW plays through the output device configured *inside REW*. That output must be the stack
sink (PipeWire -> CamillaDSP), never the DAC; the ops server cannot verify REW's routing
and says so in every result. Level checks and noise checks are not provided through this
backend; the sweep level sent to REW is still capped by the measurement policy.
"""
import base64
import json
import math
import struct
import time
import urllib.error
import urllib.request

from .errors import OpsError

# key -> (method, path). Unverified against a live REW; see module docstring.
ENDPOINTS = {
    'version': ('GET', '/version'),
    'blocking': ('POST', '/application/blocking'),
    'level': ('POST', '/measure/level'),
    'sweep_config': ('POST', '/measure/sweep/configuration'),
    'naming': ('POST', '/measure/naming'),
    'command': ('POST', '/measure/command'),
    'measurements': ('GET', '/measurements'),
    'frequency_response': ('GET', '/measurements/{id}/frequency-response'),
    'impulse_response': ('GET', '/measurements/{id}/impulse-response'),
}
MAX_RESPONSE = 32 * 1024 * 1024
POLL_S = 0.2


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
    def __init__(self, url, timeout, transport=None):
        self.url = url.rstrip('/')
        self.timeout = timeout
        self.transport = transport or _urllib_transport

    def call(self, key, body=None, **params):
        method, path = ENDPOINTS[key]
        path = path.format(**{k: urllib.request.quote(str(v), safe='') for k, v in params.items()})
        try:
            status, value = self.transport(method, self.url + path, body, self.timeout)
        except OpsError:
            raise
        except (OSError, ValueError):
            raise OpsError('rew_unreachable', 'the REW API is not reachable at measurement.rew.url (is REW running '
                           'with the API enabled?)') from None
        if not 200 <= int(status) < 300:
            raise OpsError('rew_error', f'REW API {method} {path} answered HTTP {status}', http_status=status)
        return value


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

    def stop(self):
        """Best effort: ask REW to cancel the running measurement. REW owns its own playback."""
        self._cancel_requested = True
        try:
            self.client.call('command', {'command': 'Cancel'})
        except OpsError:
            return 0
        return 1

    def sweep(self, *, f1, f2, seconds, rate, level_dbfs, title, should_stop, deadline):
        before = self._ids()
        self.client.call('blocking', True)
        self.client.call('level', {'value': round(level_dbfs, 2), 'unit': 'dBFS'})
        length = 1 << max(16, min(22, int(math.log2(max(1.0, seconds * rate)))))
        self.client.call('sweep_config', {'startFreq': f1, 'endFreq': f2, 'length': length})
        self.client.call('naming', {'title': title[:60]})
        self.client.call('command', {'command': 'Measure'})
        new = None
        while new is None:
            if should_stop() or self._cancel_requested:
                self.stop()
                return {'status': 'aborted'}
            if time.monotonic() >= deadline:
                self.stop()
                return {'status': 'timeout'}
            added = [i for i in self._ids() if i not in before]
            if added:
                new = added[-1]
                break
            time.sleep(POLL_S)
        return {'status': 'ok', 'rew_measurement_id': new, **self._fetch(new, f1, f2)}

    def _ids(self):
        value = self.client.call('measurements')
        if isinstance(value, dict):
            return [str(k) for k in value]
        if isinstance(value, list):
            return [str(item.get('id', i)) if isinstance(item, dict) else str(item) for i, item in enumerate(value)]
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
