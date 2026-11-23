"""Operator configuration: JSON file plus GARAGE_OPS_MCP_* environment overrides.

Everything site-specific (Music Assistant URL and credential file, CamillaDSP
address, supervisor unit and command, state directory, recovery root, log sources)
comes from here. Unknown keys are rejected so a typo cannot silently drop a
safety setting. Credentials are referenced by file path; their bytes are never
stored in the Config object's public projection or logged.
"""
import copy
import ipaddress
import json
import os
import re
import stat
from pathlib import Path
from urllib.parse import urlsplit

from .errors import ConfigError
from .safety import build_measure_policy, build_policy

ENV_PREFIX = 'GARAGE_OPS_MCP_'
UNIT_PATTERN = re.compile(r'^[A-Za-z0-9@._:-]{1,128}\.service$')
CONTAINER_PATTERN = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$')
LABEL_PATTERN = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$')
CHANNELS = ('left', 'right', 'sub')


def default_state_dir():
    base = os.environ.get('XDG_STATE_HOME') or str(Path.home() / '.local' / 'state')
    return str(Path(base) / 'garage-audio-ops-mcp')


DEFAULT = {
    'allow_write': False,
    'allow_playback': False,
    'state_dir': None,
    'server': {'host': '127.0.0.1', 'port': 8766, 'token_file': None, 'allowed_hosts': []},
    'limits': {},
    'supervisor': {'unit': 'garage-audio-network.service',
                   'status_root': '/var/lib/garage-audio-network',
                   'systemctl': ['systemctl'], 'timeout_seconds': 90,
                   'min_action_interval_seconds': 120},
    'containers': {'docker': ['docker'], 'label': 'garage.audio.generation', 'names': []},
    'logs': {'journalctl': ['journalctl'], 'units': ['garage-audio-network.service'],
             'containers': [], 'max_lines': 200},
    'music_assistant': {'url': 'http://127.0.0.1:8095', 'token_file': None,
                        'timeout_seconds': 8, 'players': []},
    'camilladsp': {'host': '127.0.0.1', 'port': 1234, 'timeout_seconds': 5,
                   'channels': {'left': None, 'right': None, 'sub': None},
                   'backup_retention': 50},
    'room_correction': {'max_boost_db': 0.0, 'max_filters': 6, 'freq_min_hz': 25.0,
                        'freq_max_hz': 300.0, 'smoothing_octaves': 1 / 6,
                        'tilt_db_per_octave': 0.0, 'bass_shelf_db': 0.0, 'bass_shelf_hz': 100.0,
                        'tolerance_db': 1.0, 'sample_rate': 48000, 'history_retention': 200},
    'allow_measure': False,
    'measurement': {
        'backend': 'builtin',          # builtin | rew
        'capture': 'process',          # process | phone (design stub, not implemented)
        'sample_rate': 48000,
        'play_argv': None,             # e.g. ["pw-play", "--target", "<sink>", "{file}"]
        'record_argv': None,           # e.g. ["pw-record", "--target", "<mic>", "--channels", "{channels}", "{file}"]
        'sink_check_argv': None,       # optional read-only probe that exits 0 when the sink exists
        'list_devices_argv': [['arecord', '-l'], ['pw-record', '--list-targets']],
        'output_channels': 2,
        'channel_map': {'left': [0], 'right': [1], 'sub': [0, 1], 'sum': [0, 1]},
        'capture_channels': 1,
        'mic_channel': 0,
        'loopback': None,              # {"output_channel": N, "capture_channel": M}
        'timing_reference': 'left',
        'require_owner_ready': True,
        'sweep_seconds': 6.0,
        'sweep_min_hz': 20.0,
        'sweep_max_hz': 20000.0,
        'target_snr_db': 35.0,
        'min_snr_db': 20.0,
        'smoothing': '1/48',
        'retention': 100,
        'limits': {},
        'rew': {'url': None, 'timeout_seconds': 60, 'protocol': {}},
        'plan': {'radius_m': 0.3, 'positions': 6},
    },
}
MEASURE_CHANNELS = ('left', 'right', 'sub', 'sum')
SMOOTHING = {'none': 0.0, '1/1': 1.0, '1/3': 1 / 3, '1/6': 1 / 6, '1/12': 1 / 12, '1/24': 1 / 24, '1/48': 1 / 48}
PLACEHOLDERS = ('{file}', '{rate}', '{channels}', '{seconds}')
# Playback must go into the stack's sink (PipeWire -> CamillaDSP), never to a raw ALSA device.
DIRECT_DAC = re.compile(r'(?i)hw:|plug:|default:card=|dmix|dsnoop|sysdefault|iec958|front:|surround\d|/dev/snd/'
                        r'|alsa_output\.')
# ALSA players that fall back to the ALSA default device (possibly the DAC) without an explicit -D/--device.
ALSA_PLAYERS = frozenset({'aplay', 'speaker-test'})
# measurement.list_devices_argv: read-only listing programs only, so the "local" tool can never play.
LISTERS = {
    'arecord': lambda a: a in (['-l'], ['-L'], ['--list-devices'], ['--list-pcms']),
    'pw-record': lambda a: a == ['--list-targets'],
    'pw-cli': lambda a: 1 <= len(a) <= 2 and a[0] in ('ls', 'list-objects', 'info', 'i'),
    'pw-dump': lambda a: len(a) <= 2 and all(x in ('-N', '--no-colors') or not x.startswith('-') for x in a),
    'pactl': lambda a: 1 <= len(a) <= 3 and a[0] in ('list', 'info'),
    'wpctl': lambda a: 1 <= len(a) <= 2 and a[0] in ('status', 'inspect'),
}
SAMPLE_RATES = (44100, 48000, 88200, 96000)
_PLACEHOLDER = re.compile(r'\{[^{}]*\}')
SHELLS = frozenset({'sh', 'bash', 'dash', 'zsh', 'ksh', 'fish', 'csh', 'tcsh', 'busybox', 'env', 'sudo', 'doas',
                    'su', 'xargs', 'nohup', 'setsid', 'ssh'})


def _template(name, value, required=('{file}',)):
    """A measurement argv template: no shell, only known placeholders, required ones present."""
    if (not isinstance(value, list) or not 1 <= len(value) <= 16
            or not all(isinstance(v, str) and v and '\x00' not in v and len(v) <= 256 for v in value)):
        raise ConfigError(f'{name} must be a non-empty argv list of strings (no shell)')
    if os.path.basename(value[0]) in SHELLS:
        raise ConfigError(f'{name} must run the player/recorder directly, not through a shell, env or sudo')
    joined = '\n'.join(value)
    unknown = sorted(set(_PLACEHOLDER.findall(joined)) - set(PLACEHOLDERS))
    if unknown:
        raise ConfigError(f'{name} uses unknown placeholder(s) {unknown}; allowed: {list(PLACEHOLDERS)}')
    for item in required:
        if item not in joined:
            raise ConfigError(f'{name} must contain {item}')
    return list(value)


def _alsa_device(args):
    """The -D/--device value of an ALSA player argv (None when absent)."""
    for i, a in enumerate(args):
        if a in ('-D', '--device'):
            return args[i + 1].strip().lower() if i + 1 < len(args) else ''
        if a.startswith('--device='):
            return a[len('--device='):].strip().lower()
        if a.startswith('-D'):
            return a[2:].strip().lower()
    return None


def _int(name, value, low, high):
    return _number(name, value, low, high, integer=True)


def _measurement(data, policy):
    """Validated measurement section (see docs/ops-mcp-measurement.md)."""
    m = data
    out = {}
    if m['backend'] not in ('builtin', 'rew'):
        raise ConfigError('measurement.backend must be "builtin" or "rew"')
    if m['capture'] not in ('process', 'phone'):
        raise ConfigError('measurement.capture must be "process" or "phone"')
    out['backend'], out['capture'] = m['backend'], m['capture']
    if m['sample_rate'] not in SAMPLE_RATES or isinstance(m['sample_rate'], bool):
        raise ConfigError(f'measurement.sample_rate must be one of {list(SAMPLE_RATES)}')
    out['sample_rate'] = m['sample_rate']
    out['play_argv'] = None
    if m['play_argv'] is not None:
        argv = _template('measurement.play_argv', m['play_argv'])
        if any(DIRECT_DAC.search(item) for item in argv):
            raise ConfigError('measurement.play_argv must target the stack sink that feeds CamillaDSP, never a raw '
                              'ALSA/DAC device (hw:, plughw:, plug:, default:CARD=, dmix, /dev/snd, alsa_output.*): '
                              'the supervisor owns the DAC and the DSP limits must apply to measurement signals')
        if os.path.basename(argv[0]) in ALSA_PLAYERS and _alsa_device(argv[1:]) in (None, '', 'default'):
            raise ConfigError('measurement.play_argv runs an ALSA player without an explicit device (-D): it would '
                              'play to the ALSA default, which may be the DAC. Name the stack sink explicitly '
                              '(e.g. -D pipewire) or use pw-play --target <stack sink>.')
        out['play_argv'] = argv
    out['record_argv'] = None if m['record_argv'] is None else _template('measurement.record_argv', m['record_argv'])
    out['sink_check_argv'] = (None if m['sink_check_argv'] is None
                              else _template('measurement.sink_check_argv', m['sink_check_argv'], required=()))
    lists = m['list_devices_argv']
    if not isinstance(lists, list) or len(lists) > 4:
        raise ConfigError('measurement.list_devices_argv must be a list of at most 4 argv lists')
    out['list_devices_argv'] = [_argv('measurement.list_devices_argv[]', item) for item in lists]
    for item in out['list_devices_argv']:
        check = LISTERS.get(os.path.basename(item[0]))
        if check is None or not check(item[1:]):
            raise ConfigError('measurement.list_devices_argv entries must be read-only device listings (arecord '
                              '-l/-L, pw-record --list-targets, pw-cli ls/info, pw-dump, pactl list/info, wpctl '
                              'status/inspect)', argv=item[:3])
    out['output_channels'] = _int('measurement.output_channels', m['output_channels'], 1, 8)
    out['capture_channels'] = _int('measurement.capture_channels', m['capture_channels'], 1, 8)
    out['mic_channel'] = _int('measurement.mic_channel', m['mic_channel'], 0, out['capture_channels'] - 1)
    cmap = m['channel_map']
    if not isinstance(cmap, dict) or not cmap or set(cmap) - set(MEASURE_CHANNELS):
        raise ConfigError(f'measurement.channel_map keys must be among {list(MEASURE_CHANNELS)}')
    out['channel_map'] = {}
    for key, value in cmap.items():
        if (not isinstance(value, list) or not value or len(set(value)) != len(value)
                or not all(type(v) is int and 0 <= v < out['output_channels'] for v in value)):
            raise ConfigError(f'measurement.channel_map.{key} must be a non-empty list of distinct output '
                              f'channel indexes below {out["output_channels"]}')
        out['channel_map'][key] = list(value)
    loop = m['loopback']
    out['loopback'] = None
    if loop is not None:
        if not isinstance(loop, dict) or set(loop) != {'output_channel', 'capture_channel'}:
            raise ConfigError('measurement.loopback must be null or {"output_channel": N, "capture_channel": M}')
        lo = _int('measurement.loopback.output_channel', loop['output_channel'], 0, out['output_channels'] - 1)
        lc = _int('measurement.loopback.capture_channel', loop['capture_channel'], 0, out['capture_channels'] - 1)
        if lc == out['mic_channel']:
            raise ConfigError('measurement.loopback.capture_channel must differ from mic_channel')
        if any(lo in chans for chans in out['channel_map'].values()):
            raise ConfigError('measurement.loopback.output_channel must not be a speaker channel in channel_map')
        out['loopback'] = {'output_channel': lo, 'capture_channel': lc}
    cmap_out = out['channel_map']
    sub_only = set(cmap_out.get('sub', ())) - set(cmap_out.get('left', ())) - set(cmap_out.get('right', ()))
    ref = m['timing_reference']
    if ref not in cmap_out or ref == 'sub' or set(cmap_out[ref]) & sub_only:
        raise ConfigError('measurement.timing_reference must be a full-range channel present in channel_map '
                          '(not the sub, and not a channel that includes a sub-only output)')
    out['timing_reference'] = m['timing_reference']
    if type(m['require_owner_ready']) is not bool:
        raise ConfigError('measurement.require_owner_ready must be true or false')
    out['require_owner_ready'] = m['require_owner_ready']
    out['sweep_seconds'] = _number('measurement.sweep_seconds', m['sweep_seconds'], 0.5, policy.sweep_seconds_max)
    out['sweep_min_hz'] = _number('measurement.sweep_min_hz', m['sweep_min_hz'], policy.sweep_min_hz, 1000)
    out['sweep_max_hz'] = _number('measurement.sweep_max_hz', m['sweep_max_hz'], 1000,
                                  min(policy.sweep_max_hz, 0.48 * out['sample_rate']))
    if out['sweep_min_hz'] * 4 > out['sweep_max_hz']:
        raise ConfigError('measurement sweep range must span at least two octaves')
    out['target_snr_db'] = _number('measurement.target_snr_db', m['target_snr_db'], 10, 80)
    out['min_snr_db'] = _number('measurement.min_snr_db', m['min_snr_db'], 6, out['target_snr_db'])
    if m['smoothing'] not in SMOOTHING:
        raise ConfigError(f'measurement.smoothing must be one of {list(SMOOTHING)}')
    out['smoothing'] = m['smoothing']
    out['retention'] = _int('measurement.retention', m['retention'], 1, 1000)
    rew = m['rew']
    url = rew['url']
    if url is not None:
        parts = urlsplit(url) if isinstance(url, str) else None
        if (parts is None or parts.scheme not in ('http', 'https') or not parts.hostname or parts.username
                or parts.password or parts.query or parts.fragment or parts.path not in ('', '/')):
            raise ConfigError('measurement.rew.url must be http(s)://host[:port] without credentials, path or query')
        url = url.rstrip('/')
    if out['backend'] == 'rew' and url is None:
        raise ConfigError('measurement.backend "rew" needs measurement.rew.url')
    from .measure_rew import validate_protocol  # stdlib-only module
    try:
        protocol = validate_protocol(rew['protocol'])
    except ValueError as error:
        raise ConfigError(str(error)) from None
    out['rew'] = {'url': url, 'timeout_seconds': _number('measurement.rew.timeout_seconds',
                                                          rew['timeout_seconds'], 5, 300),
                  'protocol': protocol}
    plan = m['plan']
    out['plan'] = {'radius_m': _number('measurement.plan.radius_m', plan['radius_m'], 0.1, 1.0),
                   'positions': _int('measurement.plan.positions', plan['positions'], 4, 8)}
    return out


ENV = {
    'ALLOW_WRITE': ('allow_write',), 'ALLOW_PLAYBACK': ('allow_playback',), 'ALLOW_MEASURE': ('allow_measure',),
    'STATE_DIR': ('state_dir',), 'HOST': ('server', 'host'), 'PORT': ('server', 'port'),
    'TOKEN_FILE': ('server', 'token_file'),
    'MA_URL': ('music_assistant', 'url'), 'MA_TOKEN_FILE': ('music_assistant', 'token_file'),
    'CAMILLA_HOST': ('camilladsp', 'host'), 'CAMILLA_PORT': ('camilladsp', 'port'),
    'SUPERVISOR_UNIT': ('supervisor', 'unit'), 'RECOVERY_ROOT': ('supervisor', 'status_root'),
}
INT_FIELDS = {('server', 'port'), ('camilladsp', 'port')}
BOOL_FIELDS = {('allow_write',), ('allow_playback',), ('allow_measure',)}


def _merge(base, override, path=()):
    if not isinstance(override, dict):
        raise ConfigError(f"{'.'.join(path) or 'configuration'} must be an object")
    result = copy.deepcopy(base)
    for key, value in override.items():
        here = path + (key,)
        if key not in base:
            raise ConfigError(f"unknown configuration key {'.'.join(here)}")
        if key in ('limits', 'channels', 'channel_map', 'protocol') or not isinstance(base[key], dict):
            result[key] = copy.deepcopy(value) if key != 'channels' else {**base[key], **(value if isinstance(value, dict) else {'_': value})}
        else:
            result[key] = _merge(base[key], value, here)
    return result


def _parse_bool(name, text):
    lowered = text.strip().lower()
    if lowered in ('1', 'true', 'yes', 'on'):
        return True
    if lowered in ('0', 'false', 'no', 'off', ''):
        return False
    raise ConfigError(f'{name} must be true or false')


def _apply_env(data, environ):
    for suffix, path in ENV.items():
        name = ENV_PREFIX + suffix
        if name not in environ:
            continue
        raw = environ[name]
        if path in BOOL_FIELDS:
            value = _parse_bool(name, raw)
        elif path in INT_FIELDS:
            try:
                value = int(raw)
            except ValueError:
                raise ConfigError(f'{name} must be an integer') from None
        else:
            value = raw
        target = data
        for key in path[:-1]:
            target = target[key]
        target[path[-1]] = value
    return data


def _argv(name, value):
    if (not isinstance(value, list) or not 1 <= len(value) <= 8
            or not all(isinstance(v, str) and v and '\x00' not in v and len(v) <= 256 for v in value)):
        raise ConfigError(f'{name} must be a non-empty argv list of strings (no shell)')
    return list(value)


def _number(name, value, low, high, integer=False):
    if isinstance(value, bool) or (integer and type(value) is not int) or type(value) not in (int, float):
        raise ConfigError(f'{name} must be a {"integer" if integer else "number"}')
    if not low <= value <= high:
        raise ConfigError(f'{name} must be between {low} and {high}')
    return value


def _optional_path(name, value):
    if value is None or value == '':
        return None
    if not isinstance(value, str) or '\x00' in value:
        raise ConfigError(f'{name} must be a path string')
    return value


def _validate_url(value):
    if not isinstance(value, str):
        raise ConfigError('music_assistant.url must be a string')
    parts = urlsplit(value)
    if (parts.scheme not in ('http', 'https') or not parts.hostname or parts.username or parts.password
            or parts.query or parts.fragment or parts.path not in ('', '/')):
        raise ConfigError('music_assistant.url must be http(s)://host[:port] without credentials, path or query')
    return value.rstrip('/')


class Config:
    """Validated effective configuration."""

    def __init__(self, data, source=None):
        self.source = source
        self.allow_write = data['allow_write']
        self.allow_playback = data['allow_playback']
        self.allow_measure = data['allow_measure']
        for key in ('allow_write', 'allow_playback', 'allow_measure'):
            if type(data[key]) is not bool:
                raise ConfigError(f'{key} must be true or false')
        self.state_dir = Path(_optional_path('state_dir', data['state_dir']) or default_state_dir())
        server = data['server']
        if not isinstance(server['host'], str) or not server['host']:
            raise ConfigError('server.host must be a host name or address')
        self.host = server['host']
        self.port = _number('server.port', server['port'], 1, 65535, integer=True)
        self.token_file = _optional_path('server.token_file', server['token_file'])
        hosts = server['allowed_hosts']
        if not isinstance(hosts, list) or not all(isinstance(h, str) and h and len(h) <= 255 for h in hosts):
            raise ConfigError('server.allowed_hosts must be a list of host[:port] strings')
        self.allowed_hosts = list(hosts)
        self.policy = build_policy(data['limits'])

        sup = data['supervisor']
        if not isinstance(sup['unit'], str) or not UNIT_PATTERN.fullmatch(sup['unit']):
            raise ConfigError('supervisor.unit must be a systemd .service unit name')
        self.unit = sup['unit']
        self.status_root = Path(_optional_path('supervisor.status_root', sup['status_root']) or '/nonexistent')
        self.systemctl = _argv('supervisor.systemctl', sup['systemctl'])
        self.supervisor_timeout = _number('supervisor.timeout_seconds', sup['timeout_seconds'], 1, 600)
        self.min_action_interval = _number('supervisor.min_action_interval_seconds',
                                           sup['min_action_interval_seconds'], 0, 86400)

        cont = data['containers']
        self.docker = _argv('containers.docker', cont['docker'])
        if cont['label'] is not None and (not isinstance(cont['label'], str) or not LABEL_PATTERN.fullmatch(cont['label'])):
            raise ConfigError('containers.label must be a label key or null')
        self.container_label = cont['label']
        self.container_names = self._names('containers.names', cont['names'])

        logs = data['logs']
        self.journalctl = _argv('logs.journalctl', logs['journalctl'])
        units = logs['units']
        if not isinstance(units, list) or not all(isinstance(u, str) and UNIT_PATTERN.fullmatch(u) for u in units):
            raise ConfigError('logs.units must be a list of .service unit names')
        self.log_units = list(units)
        self.log_containers = self._names('logs.containers', logs['containers'])
        self.max_log_lines = _number('logs.max_lines', logs['max_lines'], 1, 2000, integer=True)

        ma = data['music_assistant']
        self.ma_url = _validate_url(ma['url'])
        self.ma_token_file = _optional_path('music_assistant.token_file', ma['token_file'])
        self.ma_timeout = _number('music_assistant.timeout_seconds', ma['timeout_seconds'], 1, 60)
        players = ma['players']
        if not isinstance(players, list) or not all(isinstance(p, str) and 0 < len(p) <= 256 for p in players):
            raise ConfigError('music_assistant.players must be a list of player ids')
        self.ma_players = list(players)

        dsp = data['camilladsp']
        if not isinstance(dsp['host'], str) or not dsp['host']:
            raise ConfigError('camilladsp.host must be a host name or address')
        self.camilla_host = dsp['host']
        self.camilla_port = _number('camilladsp.port', dsp['port'], 1, 65535, integer=True)
        self.camilla_timeout = _number('camilladsp.timeout_seconds', dsp['timeout_seconds'], 1, 60)
        channels = dsp['channels']
        if set(channels) - set(CHANNELS):
            raise ConfigError('camilladsp.channels accepts only left, right and sub')
        self.channels = {}
        for name in CHANNELS:
            value = channels.get(name)
            if value is not None:
                _number(f'camilladsp.channels.{name}', value, 0, 63, integer=True)
            self.channels[name] = value
        used = [v for v in self.channels.values() if v is not None]
        if len(set(used)) != len(used):
            raise ConfigError('camilladsp.channels must map to distinct pipeline channels')
        self.backup_retention = _number('camilladsp.backup_retention', dsp['backup_retention'], 5, 1000, integer=True)

        room = data['room_correction']
        self.room = {
            'max_boost_db': _number('room_correction.max_boost_db', room['max_boost_db'], 0, self.policy.max_boost_db),
            'max_filters': _number('room_correction.max_filters', room['max_filters'], 1,
                                   self.policy.max_filters_per_channel, integer=True),
            'freq_min_hz': _number('room_correction.freq_min_hz', room['freq_min_hz'],
                                   self.policy.freq_min_hz, self.policy.freq_max_hz),
            'freq_max_hz': _number('room_correction.freq_max_hz', room['freq_max_hz'],
                                   self.policy.freq_min_hz, self.policy.freq_max_hz),
            'smoothing_octaves': _number('room_correction.smoothing_octaves', room['smoothing_octaves'], 0, 1),
            'tilt_db_per_octave': _number('room_correction.tilt_db_per_octave', room['tilt_db_per_octave'], -3, 3),
            'bass_shelf_db': _number('room_correction.bass_shelf_db', room['bass_shelf_db'], -12, 12),
            'bass_shelf_hz': _number('room_correction.bass_shelf_hz', room['bass_shelf_hz'], 20, 500),
            'tolerance_db': _number('room_correction.tolerance_db', room['tolerance_db'], 0.1, 10),
            'sample_rate': _number('room_correction.sample_rate', room['sample_rate'], 8000, 384000, integer=True),
            'history_retention': _number('room_correction.history_retention', room['history_retention'], 1, 5000, integer=True),
        }
        if self.room['freq_min_hz'] >= self.room['freq_max_hz']:
            raise ConfigError('room_correction.freq_min_hz must be below freq_max_hz')
        if not isinstance(data['measurement'], dict):
            raise ConfigError('measurement must be an object')
        self.measure_policy = build_measure_policy(data['measurement']['limits'])
        self.measurement = _measurement(data['measurement'], self.measure_policy)

    @staticmethod
    def _names(label, value):
        if not isinstance(value, list) or not all(isinstance(n, str) and CONTAINER_PATTERN.fullmatch(n) for n in value):
            raise ConfigError(f'{label} must be a list of container names')
        return list(value)

    def loopback_only(self):
        if self.host == 'localhost':
            return True
        try:
            return ipaddress.ip_address(self.host).is_loopback
        except ValueError:
            return False

    def public(self):
        """Effective configuration for display: paths are shown, credential bytes never exist here."""
        return {
            'source': str(self.source) if self.source else None,
            'allow_write': self.allow_write, 'allow_playback': self.allow_playback,
            'state_dir': str(self.state_dir),
            'server': {'host': self.host, 'port': self.port, 'token_file_configured': bool(self.token_file),
                       'allowed_hosts': self.allowed_hosts, 'loopback_only': self.loopback_only()},
            'limits': self.policy.as_dict(),
            'supervisor': {'unit': self.unit, 'status_root': str(self.status_root), 'systemctl': self.systemctl,
                           'timeout_seconds': self.supervisor_timeout,
                           'min_action_interval_seconds': self.min_action_interval},
            'containers': {'docker': self.docker, 'label': self.container_label, 'names': self.container_names},
            'logs': {'units': self.log_units, 'containers': self.log_containers, 'max_lines': self.max_log_lines},
            'music_assistant': {'url': self.ma_url, 'token_file_configured': bool(self.ma_token_file),
                                'players': self.ma_players},
            'camilladsp': {'host': self.camilla_host, 'port': self.camilla_port, 'channels': self.channels,
                           'backup_retention': self.backup_retention},
            'room_correction': dict(self.room),
            'allow_measure': self.allow_measure,
            'measurement': {**copy.deepcopy(self.measurement), 'limits': self.measure_policy.as_dict()},
        }


def load_config(path=None, environ=None):
    """Load configuration from ``path`` (or GARAGE_OPS_MCP_CONFIG) and apply env overrides."""
    environ = os.environ if environ is None else environ
    path = path or environ.get(ENV_PREFIX + 'CONFIG')
    data = copy.deepcopy(DEFAULT)
    if path:
        try:
            raw = Path(path).read_text(encoding='utf-8')
        except OSError:
            raise ConfigError('configuration file cannot be read', path=str(path)) from None
        try:
            value = json.loads(raw)
        except ValueError:
            raise ConfigError('configuration file is not valid JSON', path=str(path)) from None
        if isinstance(value, dict):
            value = {k: v for k, v in value.items() if not k.startswith('_comment')}
        data = _merge(data, value)
    data = _apply_env(data, environ)
    return Config(data, source=path)


def read_private_secret(path, label):
    """Read a credential from a private regular file owned by this user (mode 0600-style).

    Mirrors the repository's ``application.private_token`` convention. Errors never
    include the file contents.
    """
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    except OSError:
        raise ConfigError(f'{label} file cannot be opened (missing, symlink or unreadable)') from None
    with os.fdopen(fd, 'r', encoding='utf-8', errors='strict') as handle:
        info = os.fstat(handle.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) & 0o077:
            raise ConfigError(f'{label} file must be a regular file owned by this user with no group/other permissions (chmod 600)')
        if info.st_size > 8192:
            raise ConfigError(f'{label} file is too large')
        try:
            value = handle.read(8193).strip()
        except UnicodeError:
            raise ConfigError(f'{label} file is not text') from None
    return value


def _valid_token(value):
    return isinstance(value, str) and 32 <= len(value) <= 4096 and value.isascii() and value.isprintable() and not any(c.isspace() for c in value)


def load_bearer_token(config, environ=None):
    """HTTP bearer token: token file (preferred) or GARAGE_OPS_MCP_TOKEN. None if unset."""
    environ = os.environ if environ is None else environ
    path = config.token_file
    if path:
        token = read_private_secret(path, 'server token')
    elif environ.get(ENV_PREFIX + 'TOKEN'):
        token = environ[ENV_PREFIX + 'TOKEN'].strip()
    else:
        return None
    if not _valid_token(token):
        raise ConfigError('server token must be 32-4096 printable ASCII characters without whitespace')
    return token
