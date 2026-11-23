"""Speaker and amplifier protection limits enforced in code.

``HARD`` values are code constants. Operator configuration may only tighten them:
a configured value that is looser than its hard limit is rejected at load time
(fail closed), never silently clamped. Every DSP write and every room-correction
proposal is checked against the effective ``Policy``.
"""
import math
from dataclasses import dataclass, fields

from .errors import ConfigError, SafetyError

# Absolute limits. Changing these requires a code change and review.
HARD = {
    'max_boost_db': 6.0,             # highest positive PEQ/shelf gain
    'min_gain_db': -24.0,            # deepest PEQ/shelf cut
    'freq_min_hz': 20.0,
    'freq_max_hz': 20000.0,
    'q_min': 0.1,
    'q_max': 20.0,
    'delay_max_ms': 50.0,
    'channel_gain_max_db': 6.0,      # Gain filter / level trim ceiling
    'channel_gain_min_db': -60.0,
    'volume_ceiling_db': 0.0,        # CamillaDSP main volume never above this
    'max_filters_per_channel': 10,   # room-correction proposal size
    'ma_volume_max': 100,            # Music Assistant player volume (0-100)
}
# 'max' keys may only be lowered by configuration; 'min' keys may only be raised.
DIRECTION = {
    'max_boost_db': 'max', 'min_gain_db': 'min', 'freq_min_hz': 'min', 'freq_max_hz': 'max',
    'q_min': 'min', 'q_max': 'max', 'delay_max_ms': 'max', 'channel_gain_max_db': 'max',
    'channel_gain_min_db': 'min', 'volume_ceiling_db': 'max', 'max_filters_per_channel': 'max',
    'ma_volume_max': 'max',
}
# Conservative shipped defaults (tighter than HARD).
DEFAULTS = {
    'max_boost_db': 3.0, 'min_gain_db': -15.0, 'freq_min_hz': 20.0, 'freq_max_hz': 20000.0,
    'q_min': 0.3, 'q_max': 10.0, 'delay_max_ms': 20.0, 'channel_gain_max_db': 0.0,
    'channel_gain_min_db': -40.0, 'volume_ceiling_db': -10.0, 'max_filters_per_channel': 8,
    'ma_volume_max': 50,
}
VOLUME_MIN_DB = -100.0
SHELF_SLOPE = (1.0, 12.0)
SPEED_OF_SOUND_M_S = 343.0
INTEGER_KEYS = {'max_filters_per_channel', 'ma_volume_max'}


@dataclass(frozen=True)
class Policy:
    max_boost_db: float
    min_gain_db: float
    freq_min_hz: float
    freq_max_hz: float
    q_min: float
    q_max: float
    delay_max_ms: float
    channel_gain_max_db: float
    channel_gain_min_db: float
    volume_ceiling_db: float
    max_filters_per_channel: int
    ma_volume_max: int

    def as_dict(self):
        return {f.name: getattr(self, f.name) for f in fields(self)}


def _finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def build_policy(overrides=None):
    """Effective policy = DEFAULTS updated by overrides; any loosening is a ConfigError."""
    overrides = {} if overrides is None else overrides
    if not isinstance(overrides, dict):
        raise ConfigError('limits must be an object')
    unknown = set(overrides) - set(HARD)
    if unknown:
        raise ConfigError('unknown limit keys', keys=sorted(unknown))
    values = dict(DEFAULTS)
    for key, value in overrides.items():
        if key in INTEGER_KEYS:
            if type(value) is not int:
                raise ConfigError(f'limit {key} must be an integer')
        elif not _finite(value):
            raise ConfigError(f'limit {key} must be a finite number')
        hard = HARD[key]
        if (DIRECTION[key] == 'max' and value > hard) or (DIRECTION[key] == 'min' and value < hard):
            raise ConfigError(f'limit {key}={value} would loosen the hard limit {hard}; configuration may only tighten',
                              key=key, hard_limit=hard)
        values[key] = float(value) if key not in INTEGER_KEYS else value
    checks = [
        (values['max_boost_db'] >= 0, 'max_boost_db must be >= 0'),
        (values['min_gain_db'] <= 0, 'min_gain_db must be <= 0'),
        (values['freq_min_hz'] < values['freq_max_hz'], 'freq_min_hz must be below freq_max_hz'),
        (values['q_min'] < values['q_max'], 'q_min must be below q_max'),
        (values['delay_max_ms'] >= 0, 'delay_max_ms must be >= 0'),
        (values['channel_gain_min_db'] < values['channel_gain_max_db'], 'channel gain range is empty'),
        (values['volume_ceiling_db'] >= VOLUME_MIN_DB, f'volume_ceiling_db must be >= {VOLUME_MIN_DB}'),
        (values['max_filters_per_channel'] >= 1, 'max_filters_per_channel must be >= 1'),
        (values['ma_volume_max'] >= 0, 'ma_volume_max must be >= 0'),
    ]
    for ok, message in checks:
        if not ok:
            raise ConfigError(message)
    return Policy(**values)


def check_range(name, value, low, high):
    if not _finite(value):
        raise SafetyError(f'{name} must be a finite number', parameter=name)
    if not low <= value <= high:
        raise SafetyError(f'{name}={value} is outside the allowed range {low}..{high}',
                          parameter=name, minimum=low, maximum=high)
    return float(value)


def check_gain(policy, value, name='gain'):
    return check_range(name, value, policy.min_gain_db, policy.max_boost_db)


def check_freq(policy, value, name='freq'):
    return check_range(name, value, policy.freq_min_hz, policy.freq_max_hz)


def check_q(policy, value, name='q'):
    return check_range(name, value, policy.q_min, policy.q_max)


def check_delay_ms(policy, value, name='delay_ms'):
    return check_range(name, value, 0.0, policy.delay_max_ms)


def check_channel_gain(policy, value, name='gain_db'):
    return check_range(name, value, policy.channel_gain_min_db, policy.channel_gain_max_db)


def check_volume(policy, value, name='volume_db'):
    return check_range(name, value, VOLUME_MIN_DB, policy.volume_ceiling_db)


def check_ma_volume(policy, value):
    if type(value) is not int:
        raise SafetyError('volume level must be an integer 0-100', parameter='level')
    return int(check_range('level', value, 0, policy.ma_volume_max))


def check_peq(policy, params):
    """Validate a Peaking biquad parameter dict {freq, q, gain}."""
    return {'type': 'Peaking', 'freq': check_freq(policy, params.get('freq')),
            'q': check_q(policy, params.get('q')), 'gain': check_gain(policy, params.get('gain'))}


def delay_ms(parameters, samplerate):
    """Convert a CamillaDSP Delay parameter block to milliseconds, or None if unknown."""
    value = parameters.get('delay')
    unit = parameters.get('unit', 'ms')
    if not _finite(value):
        return None
    if unit == 'ms':
        return float(value)
    if unit == 'mm':
        return float(value) / SPEED_OF_SOUND_M_S
    if unit == 'samples' and _finite(samplerate) and samplerate > 0:
        return float(value) * 1000.0 / samplerate
    return None


# Filter types whose effect on level the audit can bound. Anything else (Biquad Free,
# LinkwitzTransform, Conv, DiffEq, Loudness, BiquadCombo EQ shapes, unknown types) is
# reported as an "unbounded" violation, so a write path can never introduce one.
GAIN_BIQUADS = ('Peaking', 'Highshelf', 'Lowshelf', 'HighshelfFO', 'LowshelfFO')
GAINLESS_BIQUADS = ('Highpass', 'Lowpass', 'HighpassFO', 'LowpassFO', 'Notch', 'Bandpass', 'Allpass', 'AllpassFO')
Q_BIQUADS = ('Peaking', 'Notch', 'Bandpass', 'Allpass')
SHELVES = ('Highshelf', 'Lowshelf')  # second-order shelves: q or slope (SHELF_SLOPE, dB/octave)
CROSSOVER_COMBOS = ('ButterworthHighpass', 'ButterworthLowpass', 'LinkwitzRileyHighpass', 'LinkwitzRileyLowpass')
LEVEL_NEUTRAL_TYPES = ('Dither', 'Limiter')  # never raise level
# Top-level sections and pipeline steps the audit understands. ``processors`` is listed so
# it is not reported twice; each of its entries is reported as unbounded individually.
KNOWN_SECTIONS = ('title', 'description', 'devices', 'filters', 'mixers', 'processors', 'pipeline')
KNOWN_PIPELINE_STEPS = ('Filter', 'Mixer')
# A Volume filter is bounded only on the Main fader (capped by volume_ceiling_db); Aux faders
# are not controlled or capped by this server.


def gain_db(value, scale='dB'):
    """A Gain/mixer gain in dB, or None when it cannot be interpreted. Linear 0 (silence) is -inf."""
    if not _finite(value):
        return None
    if scale in (None, 'dB'):
        return float(value)
    if scale == 'linear':
        return 20.0 * math.log10(abs(value)) if value else -math.inf
    return None


def audit_filter(policy, name, definition, samplerate):
    """Return a list of human-readable limit violations for one filter definition."""
    problems = []
    if not isinstance(definition, dict):
        return [f'{name}: filter definition is not an object']
    kind = definition.get('type')
    params = definition.get('parameters') if isinstance(definition.get('parameters'), dict) else {}

    def outside(label, value, low, high):
        if not _finite(value) or not low <= value <= high:
            problems.append(f'{name}: {label}={value!r} outside {low}..{high}')

    def unbounded(what):
        problems.append(f'{name}: unbounded filter type {what!r}; the server cannot verify its level, '
                        'so it may not be introduced or restored')

    if kind == 'Biquad':
        subtype = params.get('type')
        if subtype not in GAIN_BIQUADS + GAINLESS_BIQUADS:
            unbounded(f'Biquad {subtype}')
            return problems
        outside('freq', params.get('freq'), policy.freq_min_hz, policy.freq_max_hz)
        if subtype in GAIN_BIQUADS:
            outside('gain', params.get('gain'), policy.min_gain_db, policy.max_boost_db)
        if subtype in Q_BIQUADS and 'q' in params:
            outside('q', params['q'], policy.q_min, policy.q_max)
        if subtype in SHELVES:
            # A second-order shelf takes either q or slope (dB/octave); a high q overshoots
            # beyond the stated gain, so both are range-checked and one must be present.
            if 'q' in params:
                outside('q', params['q'], policy.q_min, policy.q_max)
            if 'slope' in params:
                outside('slope', params['slope'], *SHELF_SLOPE)
            if 'q' not in params and 'slope' not in params:
                problems.append(f'{name}: shelf needs q or slope')
    elif kind == 'BiquadCombo':
        subtype = params.get('type')
        if subtype not in CROSSOVER_COMBOS:
            unbounded(f'BiquadCombo {subtype}')
            return problems
        outside('freq', params.get('freq'), policy.freq_min_hz, policy.freq_max_hz)
    elif kind == 'Delay':
        value = delay_ms(params, samplerate)
        if value is None:
            problems.append(f'{name}: delay cannot be interpreted')
        else:
            outside('delay_ms', value, 0.0, policy.delay_max_ms)
    elif kind == 'Gain':
        value = gain_db(params.get('gain'), params.get('scale', 'dB'))
        if value != -math.inf:  # linear 0 is silence
            outside('gain_db', value, policy.channel_gain_min_db, policy.channel_gain_max_db)
    elif kind == 'Volume':
        if params.get('fader', 'Main') != 'Main':
            unbounded(f"Volume fader {params.get('fader')}")
    elif kind not in LEVEL_NEUTRAL_TYPES:
        unbounded(kind)
    return problems


def audit_config(policy, config):
    """All limit violations in a CamillaDSP config.

    Checks filters and mixer source gains. Anything the audit does not understand is an
    "unbounded" violation: every ``processors`` entry (e.g. a Compressor's makeup_gain),
    every ``Processor`` pipeline step, any other pipeline step type and any top-level
    section other than KNOWN_SECTIONS.
    """
    if not isinstance(config, dict):
        return ['configuration is not an object']
    devices = config.get('devices') if isinstance(config.get('devices'), dict) else {}
    samplerate = devices.get('samplerate')
    problems = []
    for section in sorted(str(key) for key in config if key not in KNOWN_SECTIONS):
        problems.append(f'section {section}: unbounded configuration section; the server cannot verify its level, '
                        'so it may not be introduced or restored')
    processors = config.get('processors')
    if isinstance(processors, dict):
        for name in sorted(processors):
            kind = processors[name].get('type') if isinstance(processors[name], dict) else None
            problems.append(f'processor {name}: unbounded processor type {kind!r}; the server cannot verify its '
                            'level, so it may not be introduced or restored')
    elif processors is not None:
        problems.append('processors: unbounded processors section (not an object)')
    pipeline = config.get('pipeline')
    for step in pipeline if isinstance(pipeline, list) else []:
        kind = step.get('type') if isinstance(step, dict) else None
        if kind not in KNOWN_PIPELINE_STEPS:
            # Keyed by type and name, not position, so unrelated pipeline edits do not make a
            # pre-existing step look new.
            label = step.get('name') if isinstance(step, dict) else None
            problems.append(f'pipeline step {kind!r} {label!r}: unbounded pipeline step type; the server cannot '
                            'verify its level, so it may not be introduced or restored')
    if pipeline is not None and not isinstance(pipeline, list):
        problems.append('pipeline: not a list')
    filters = config.get('filters') or {}
    if isinstance(filters, dict):
        for name in sorted(filters):
            problems.extend(audit_filter(policy, name, filters[name], samplerate))
    mixers = config.get('mixers') or {}
    if isinstance(mixers, dict):
        for name in sorted(mixers):
            for mapping in (mixers[name] or {}).get('mapping', []) if isinstance(mixers[name], dict) else []:
                for source in mapping.get('sources', []) if isinstance(mapping, dict) else []:
                    gain = source.get('gain', 0) if isinstance(source, dict) else None
                    value = gain_db(gain, source.get('scale', 'dB')) if isinstance(source, dict) else None
                    if value is None or value > policy.channel_gain_max_db:
                        problems.append(f'mixer {name}: source gain {gain!r} above {policy.channel_gain_max_db}')
    return problems


MEASURE_HARD = {
    'sweep_level_max_dbfs': -6.0,    # peak digital level of any measurement signal (sweep, tone)
    'start_level_dbfs': -30.0,       # first level-check tone is never louder than this
    'level_step_max_db': 6.0,        # largest increase per level-check step
    'sub_sweep_max_hz': 250.0,       # upper bound of any sweep or tone sent to the sub channel
    'sweep_seconds_max': 20.0,       # longest single sweep
    'max_duration_seconds': 60.0,    # longest audible time of one measurement action
    'sweep_min_hz': 10.0,            # lowest sweep frequency
    'sweep_max_hz': 22000.0,         # highest sweep frequency (also below the Nyquist frequency)
}
MEASURE_DIRECTION = {
    'sweep_level_max_dbfs': 'max', 'start_level_dbfs': 'max', 'level_step_max_db': 'max',
    'sub_sweep_max_hz': 'max', 'sweep_seconds_max': 'max', 'max_duration_seconds': 'max',
    'sweep_min_hz': 'min', 'sweep_max_hz': 'max',
}
MEASURE_DEFAULTS = {
    'sweep_level_max_dbfs': -18.0, 'start_level_dbfs': -40.0, 'level_step_max_db': 3.0,
    'sub_sweep_max_hz': 200.0, 'sweep_seconds_max': 10.0, 'max_duration_seconds': 30.0,
    'sweep_min_hz': 15.0, 'sweep_max_hz': 20000.0,
}
MEASURE_LEVEL_FLOOR_DBFS = -90.0
CAPTURE_CLIP_DBFS = -0.5           # a capture peak at or above this counts as clipped
CAPTURE_CLIP_RISK_DBFS = -6.0      # the level check stops raising the level above this capture peak


@dataclass(frozen=True)
class MeasurePolicy:
    sweep_level_max_dbfs: float
    start_level_dbfs: float
    level_step_max_db: float
    sub_sweep_max_hz: float
    sweep_seconds_max: float
    max_duration_seconds: float
    sweep_min_hz: float
    sweep_max_hz: float

    def as_dict(self):
        return {f.name: getattr(self, f.name) for f in fields(self)}


def build_measure_policy(overrides=None):
    """Effective measurement limits = MEASURE_DEFAULTS updated by overrides; loosening is a ConfigError."""
    overrides = {} if overrides is None else overrides
    if not isinstance(overrides, dict):
        raise ConfigError('measurement.limits must be an object')
    unknown = set(overrides) - set(MEASURE_HARD)
    if unknown:
        raise ConfigError('unknown measurement limit keys', keys=sorted(unknown))
    values = dict(MEASURE_DEFAULTS)
    for key, value in overrides.items():
        if not _finite(value):
            raise ConfigError(f'measurement limit {key} must be a finite number')
        hard = MEASURE_HARD[key]
        if (MEASURE_DIRECTION[key] == 'max' and value > hard) or (MEASURE_DIRECTION[key] == 'min' and value < hard):
            raise ConfigError(f'measurement limit {key}={value} would loosen the hard limit {hard}; configuration '
                              'may only tighten', key=key, hard_limit=hard)
        values[key] = float(value)
    checks = [
        (values['sweep_level_max_dbfs'] >= MEASURE_LEVEL_FLOOR_DBFS,
         f'sweep_level_max_dbfs must be >= {MEASURE_LEVEL_FLOOR_DBFS}'),
        (MEASURE_LEVEL_FLOOR_DBFS <= values['start_level_dbfs'] <= values['sweep_level_max_dbfs'],
         'start_level_dbfs must be between -90 and sweep_level_max_dbfs'),
        (values['level_step_max_db'] >= 0.5, 'level_step_max_db must be >= 0.5'),
        (values['sweep_min_hz'] < values['sub_sweep_max_hz'], 'sub_sweep_max_hz must be above sweep_min_hz'),
        (values['sweep_min_hz'] * 4 <= values['sweep_max_hz'], 'sweep range must span at least two octaves'),
        (values['sweep_seconds_max'] >= 0.5, 'sweep_seconds_max must be >= 0.5'),
        (values['max_duration_seconds'] >= values['sweep_seconds_max'],
         'max_duration_seconds must be >= sweep_seconds_max'),
    ]
    for ok, message in checks:
        if not ok:
            raise ConfigError(message)
    return MeasurePolicy(**values)


def check_measure_level(policy, value, name='level_dbfs'):
    """A requested measurement signal level (dBFS peak) must be inside the floor..cap range."""
    return check_range(name, value, MEASURE_LEVEL_FLOOR_DBFS, policy.sweep_level_max_dbfs)


def hard_policy():
    """The absolute caps as a Policy (no operator tightening)."""
    return build_policy(HARD)


def audit_whole_config(config):
    """Violations of the hard caps anywhere in ``config``, including unbounded filter types,
    processors, pipeline steps and sections (see ``audit_config``).

    Used for writes that push a configuration not derived from the live one (restore):
    such a config must be entirely boundable and inside the hard caps.
    """
    return audit_config(hard_policy(), config)
