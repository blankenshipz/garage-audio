"""Synthetic fakes for the ops MCP tests: no systemd, Docker, Music Assistant or CamillaDSP access."""
import copy
import json
import os
import tempfile
from pathlib import Path

from garage_audio.ops_mcp.clients import Clients
from garage_audio.ops_mcp.config import DEFAULT, Config, _merge
from garage_audio.ops_mcp.context import Context
from garage_audio.ops_mcp.errors import OpsError

TOKEN = 'synthetic-test-token-' + 'x' * 32


class Clock:
    """Monotonically increasing fake wall clock (1 ms per call) so ids sort chronologically."""

    def __init__(self, start=1_800_000_000.0):
        self.now = start

    def __call__(self):
        self.now += 0.001
        return self.now

    def advance(self, seconds):
        self.now += seconds


class FakeSystem:
    def __init__(self, active='active', sub='running', action_exit=0):
        self.active, self.sub, self.action_exit = active, sub, action_exit
        self.actions = []
        self.log_lines = ['2026-11-22T10:00:00 owner started', '2026-11-22T10:00:01 ERROR guard failed',
                          '2026-11-22T10:00:02 ok']
        self.container_list = [{'name': 'ma', 'status': 'running', 'running': True, 'health': 'healthy'},
                               {'name': 'squeezelite', 'status': 'running', 'running': True, 'health': None}]
        self.log_calls = []

    def unit_state(self, unit=None):
        return {'unit': unit or 'garage-audio-network.service', 'ActiveState': self.active, 'SubState': self.sub,
                'NRestarts': '0'}

    def unit_action(self, action):
        self.actions.append(action)
        return {'command': ['systemctl', action, '--no-block', 'garage-audio-network.service'],
                'exit_status': self.action_exit, 'stderr': []}

    def containers(self):
        return copy.deepcopy(self.container_list)

    def unit_logs(self, unit, lines, since=None):
        self.log_calls.append(('unit', unit, lines, since))
        return self.log_lines[-lines:]

    def container_logs(self, name, lines, since=None):
        self.log_calls.append(('container', name, lines, since))
        return self.log_lines[-lines:]


class FakeMA:
    def __init__(self, reachable=True):
        self.reachable = reachable
        self.calls = []
        self.players = [
            {'player_id': 'player-a', 'display_name': 'Garage', 'state': 'idle', 'volume_level': 20,
             'available': True, 'current_media': {'uri': 'library://track/1', 'title': 'Song', 'secret': 'x'},
             'unexpected': 'dropped'},
            {'player_id': 'player-b', 'display_name': 'Bench', 'state': 'playing', 'volume_level': 30}]

    def info(self):
        if not self.reachable:
            raise OpsError('ma_unreachable', 'Music Assistant is not reachable at the configured URL')
        return {'server_version': '2.5.0', 'schema_version': 27}

    def call(self, command, args=None):
        self.calls.append((command, args))
        if not self.reachable:
            raise OpsError('ma_unreachable', 'Music Assistant is not reachable at the configured URL')
        if command == 'players/all':
            return copy.deepcopy(self.players)
        if command == 'player_queues/all':
            return [{'queue_id': 'player-a', 'display_name': 'Garage', 'state': 'idle', 'items': 2,
                     'current_index': 0, 'current_item': {'media_item': {'uri': 'library://track/1', 'name': 'Song'}}}]
        if command == 'player_queues/items':
            return [{'queue_item_id': 'q1', 'name': 'Song', 'duration': 200,
                     'media_item': {'uri': 'library://track/1', 'name': 'Song',
                                    'artists': [{'name': 'Artist', 'uri': 'x'}]}}]
        if command == 'music/search':
            return {'tracks': [{'uri': 'library://track/1', 'name': 'Song', 'item_id': '1', 'provider': 'library',
                                'media_type': 'track'}], 'albums': [], 'unrelated': 'scalar'}
        return None


def base_config():
    """A valid CamillaDSP v2/v3-style config with two mains and a sub on outputs 0, 1, 2."""
    return {
        'title': 'synthetic', 'devices': {'samplerate': 48000, 'capture': {'channels': 2},
                                          'playback': {'channels': 4}},
        'filters': {'sub_lowpass': {'type': 'Biquad', 'parameters': {'type': 'Lowpass', 'freq': 80, 'q': 0.707}},
                    'peq_left_1': {'type': 'Biquad', 'parameters': {'type': 'Peaking', 'freq': 120, 'q': 2.0,
                                                                     'gain': -3.0}},
                    'not_peq': {'type': 'Gain', 'parameters': {'gain': -1.0}}},
        'mixers': {'to4': {'channels': {'in': 2, 'out': 4}, 'mapping': [
            {'dest': 0, 'sources': [{'channel': 0, 'gain': 0}]},
            {'dest': 1, 'sources': [{'channel': 1, 'gain': 0}]},
            {'dest': 2, 'sources': [{'channel': 0, 'gain': -6}, {'channel': 1, 'gain': -6}]}]}},
        'pipeline': [{'type': 'Mixer', 'name': 'to4'},
                     {'type': 'Filter', 'channels': [0], 'names': ['peq_left_1']},
                     {'type': 'Filter', 'channels': [2], 'names': ['sub_lowpass']}],
    }


class FakeCamilla:
    def __init__(self, config=None, validate='ok', reachable=True):
        self.config = copy.deepcopy(config if config is not None else base_config())
        self.validate = validate  # 'ok' | 'unsupported' | 'reject'
        self.reachable = reachable
        self.volume, self.mute = -20.0, False
        self.events = []
        self.fail_set = False

    def _up(self):
        if not self.reachable:
            raise OpsError('camilladsp_unreachable', 'CamillaDSP websocket is not reachable at the configured address')

    def get_config(self):
        self._up()
        self.events.append('get_config')
        return copy.deepcopy(self.config)

    def validate_config(self, config):
        self._up()
        self.events.append('validate')
        json.dumps(config)
        if self.validate == 'unsupported':
            raise OpsError('camilladsp_unsupported', 'CamillaDSP does not support ValidateConfigJson')
        if self.validate == 'reject':
            raise OpsError('camilladsp_rejected', 'CamillaDSP rejected ValidateConfigJson: Error', reason='bad')

    def set_config(self, config):
        self._up()
        self.events.append('set_config')
        if self.fail_set:
            raise OpsError('camilladsp_rejected', 'CamillaDSP rejected SetConfigJson: Error')
        self.config = copy.deepcopy(json.loads(json.dumps(config)))

    def get_volume(self):
        self._up()
        return self.volume

    def set_volume(self, value):
        self._up()
        self.events.append(('set_volume', value))
        self.volume = value

    def get_mute(self):
        self._up()
        return self.mute

    def set_mute(self, value):
        self._up()
        self.events.append(('set_mute', value))
        self.mute = value

    def get_signal_levels(self):
        self._up()
        return {'playback_rms': [-30.0, -31.0, -28.0, -100.0], 'playback_peak': [-12.0, -13.0, -10.0, -100.0]}

    def get_peaks_since_start(self):
        self._up()
        return {'playback': [-3.0, -3.5, -2.0, -100.0]}

    def get_clipped_samples(self):
        self._up()
        return 0

    def get_state(self):
        self._up()
        return 'Running'

    def get_processing_load(self):
        self._up()
        return 7.5


class Harness:
    """Temporary state dir + status root, config from a dict, fakes wired into a Context."""

    def __init__(self, testcase, overrides=None, system=None, ma=None, camilla=None):
        temp = tempfile.TemporaryDirectory()
        testcase.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.state_dir = self.root / 'state'
        self.status_root = self.root / 'status'
        self.status_root.mkdir()
        data = {'state_dir': str(self.state_dir),
                'supervisor': {'status_root': str(self.status_root), 'min_action_interval_seconds': 120},
                'camilladsp': {'channels': {'left': 0, 'right': 1, 'sub': 2}}}
        for key, value in (overrides or {}).items():
            if isinstance(value, dict) and isinstance(data.get(key), dict):
                data[key] = {**data[key], **value}
            else:
                data[key] = value
        self.config = Config(_merge(DEFAULT, data))
        self.system = system or FakeSystem()
        self.ma = ma or FakeMA()
        self.camilla = camilla or FakeCamilla()
        self.clock = Clock()
        self.ctx = Context(self.config, Clients(system=self.system, ma=self.ma, camilla=self.camilla), self.clock)

    def recovery_state(self, state, **extra):
        (self.status_root / 'recovery-state.json').write_text(json.dumps({'schema': 1, 'state': state, **extra}))


def write_private(path, text, mode=0o600):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    with os.fdopen(fd, 'w') as handle:
        handle.write(text)
    os.chmod(path, mode)
    return str(path)


def rew_export(points, header=True):
    lines = ['* Measurement data measured by REW (synthetic)', '* Freq(Hz) SPL(dB) Phase(degrees)'] if header else []
    lines += [f'{f:.3f} {db:.3f} 0.0' for f, db in points]
    return '\n'.join(lines) + '\n'


def synthetic_response(peaks=(), dips=(), f_lo=15.0, f_hi=20000.0, per_octave=48, level=75.0):
    """Log-spaced response: flat ``level`` plus Gaussian (in octaves) peaks/dips of (freq, dB, width_oct)."""
    import math
    count = int(math.log2(f_hi / f_lo) * per_octave) + 1
    out = []
    for i in range(count):
        f = f_lo * 2 ** (i / per_octave)
        db = level
        for f0, height, width in list(peaks) + [(f0, -depth, w) for f0, depth, w in dips]:
            db += height * math.exp(-0.5 * (math.log2(f / f0) / width) ** 2)
        out.append((f, db))
    return out
