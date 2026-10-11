"""Bounded text imports and electrical-only profile previews. No acoustic claims."""
import cmath
import hashlib
import math
import re
from .profile import validate

MAX_TEXT = 262144

def parse(text, kind):
    if kind not in ('rew-response', 'mic-calibration'):
        raise ValueError('supported formats: rew-response, mic-calibration')
    if not isinstance(text, str) or len(text.encode('utf-8')) > MAX_TEXT or '\x00' in text:
        raise ValueError('invalid or oversized UTF-8 text')
    points = []
    headers = []
    # Preserve submitted text (including BOM) for provenance; remove prefix only
    # from the parsing view, so a BOM before the first numeric row loses no data.
    for line in text.removeprefix('\ufeff').splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith(('*','#',';','//')) or re.match(
                r'^(?:"?Sens Factor\s*=|Sensitivity\s+|SPL Meter Calibration data$|(?:Frequency|Freq)(?:\s|\(|,)|(?:Microphone|Mic|Soundcard) calibration(?: data)?$)',line,re.I):
            headers.append(line[:512])
            continue
        fields = re.split(r'[\s,]+', line)
        if len(fields) not in (2, 3):
            raise ValueError('expected frequency, dB, optional phase')
        values = list(map(float, fields))
        if not all(math.isfinite(v) for v in values):
            raise ValueError('non-finite measurement')
        f, db = values[:2]
        if not 0 < f <= 192000 or not -300 <= db <= 300:
            raise ValueError('measurement outside supported range')
        if points and f <= points[-1]['frequency_hz']:
            raise ValueError('frequencies must be strictly increasing')
        point = {'frequency_hz': f, 'db': db}
        if len(values) == 3:
            if abs(values[2]) > 360000:
                raise ValueError('phase outside supported range')
            point['phase_deg'] = values[2]
        points.append(point)
        if len(points) > 20000:
            raise ValueError('too many measurement points')
    if len(points) < 2:
        raise ValueError('at least two numeric rows required')
    return {'format': kind, 'points': points, 'headers': headers[:100],
            'sha256': hashlib.sha256(text.encode('utf-8')).hexdigest(),
            'interpretation': 'measured response' if kind == 'rew-response' else 'microphone response; not an acoustic measurement',
            'calibration_applied': False}

def metadata(value):
    if not isinstance(value, dict) or set(value) - {'room','position','channel','profile','sample_rate','orientation','timing_reference','notes','source','calibration_sha256','calibration_applied','microphone_serial','timing_quality','clock_drift_ppm'}:
        raise ValueError('invalid metadata fields')
    for key, item in value.items():
        if key == 'sample_rate':
            if type(item) is not int or item not in (44100,48000,88200,96000,176400,192000):
                raise ValueError('unsupported sample rate')
        elif key == 'calibration_applied':
            if type(item) is not bool: raise ValueError('calibration status must be boolean')
        elif key == 'clock_drift_ppm':
            if type(item) not in (int,float) or not math.isfinite(item) or abs(item)>1000: raise ValueError('invalid clock drift')
        elif not isinstance(item, str) or len(item) > 2000:
            raise ValueError('metadata must be bounded text')
        if key == 'channel' and item not in ('left','right','sub','combined','unknown'):
            raise ValueError('unknown channel')
        if key == 'orientation' and item not in ('0','90','unknown'):
            raise ValueError('orientation must be 0, 90 or unknown')
    return dict(value)

def preview(profile):
    p = validate(profile)
    result = {}
    for label, channel in p['channels'].items():
        points = []
        for i in range(241):
            f = 20 * (1000 ** (i / 240))
            # Digital LR4 = two identical second-order Butterworth sections.
            w = 2 * math.pi * f / 48000
            wc = 2 * math.pi * p['crossover_hz'] / 48000
            alpha = math.sin(wc) / math.sqrt(2)
            c = math.cos(wc)
            b = ((1-c)/2, 1-c, (1-c)/2) if label == 'sub' else ((1+c)/2, -(1+c), (1+c)/2)
            a = (1+alpha, -2*c, 1-alpha)
            z = cmath.exp(-1j*w)
            h = ((b[0]+b[1]*z+b[2]*z*z)/(a[0]+a[1]*z+a[2]*z*z)) ** 2
            if not p['peq_bypass']:
                for eq in channel['peq']:
                    we = 2*math.pi*eq['freq']/48000
                    A = 10**(eq['gain']/40)
                    al = math.sin(we)/(2*eq['q'])
                    ce = math.cos(we)
                    h *= ((1+al*A)-2*ce*z+(1-al*A)*z*z)/((1+al/A)-2*ce*z+(1-al/A)*z*z)
            gain = -6 + channel['gain'] + (p['sub_trim'] if label == 'sub' else 0)
            db = max(-300, 20*math.log10(max(abs(h),1e-15))+gain)
            points.append({'frequency_hz': f, 'db': db})
        result[label] = {'points': points, 'muted': channel['mute'], 'delay_ms':channel['delay_ms'], 'invert':channel['invert']}
    return {'kind':'electrical-only', 'sample_rate':48000, 'channels':result,
            'includes':'LR4 crossover, channel gain, sub trim, PEQ and -6 dB headroom; excludes master gain, summing and room/speaker/microphone',
            'acoustic_prediction':False, 'measured':False}
