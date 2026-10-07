"""Restricted, unmeasured profile schema and CamillaDSP graph compiler."""
import copy
import json
import math
import re

DEFAULT = {'name': 'uncalibrated', 'channels': {
    c: {'gain': 0.0, 'mute': False, 'delay_ms': 0.0, 'invert': False, 'peq': []}
    for c in ('left', 'right', 'sub')}, 'sub_trim': 0.0, 'peq_bypass': False, 'crossover_hz': 80.0}

def number(x, lo, hi):
    if isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x) or not lo <= x <= hi:
        raise ValueError(f'expected finite number in [{lo}, {hi}]')

def validate(p):
    # Version-one files compiled an invariant 80 Hz crossover. Upgrade only
    # that known schema, never arbitrary missing/extra fields.
    if set(p) == set(DEFAULT) - {'crossover_hz'}:
        p = copy.deepcopy(p); p['crossover_hz'] = 80.0
    if set(p) != set(DEFAULT) or not isinstance(p['name'], str) or len(p['name']) > 80:
        raise ValueError('invalid profile fields')
    if set(p['channels']) != {'left', 'right', 'sub'}:
        raise ValueError('exactly left/right/sub required')
    number(p['sub_trim'], -30, 0)
    number(p['crossover_hz'], 40, 160)
    if type(p['peq_bypass']) is not bool:
        raise ValueError('invalid bypass')
    for c in p['channels'].values():
        if set(c) != {'gain', 'mute', 'delay_ms', 'invert', 'peq'}:
            raise ValueError('invalid channel fields')
        number(c['gain'], -60, 0)
        number(c['delay_ms'], 0, 50)
        if type(c['mute']) is not bool or type(c['invert']) is not bool:
            raise ValueError('invalid boolean')
        if not isinstance(c['peq'], list) or len(c['peq']) > 10:
            raise ValueError('maximum ten PEQs per channel')
        for eq in c['peq']:
            if set(eq) != {'freq', 'q', 'gain'}:
                raise ValueError('invalid PEQ')
            number(eq['freq'], 20, 20000)
            number(eq['q'], 0.1, 20)
            number(eq['gain'], -20, 0)  # cut-only: no unaccounted boost
    return copy.deepcopy(p)

def load(path):
    with open(path) as f:
        return validate(json.load(f))

def compile_profile(p, capture, playback, hardware=False):
    p = validate(p)
    devices = {'samplerate': 48000, 'chunksize': 1024, 'volume_limit': -20,
               'volume_ramp_time': 400, 'capture': capture, 'playback': playback}
    if hardware:
        devices.update(enable_rate_adjust=True, capture_samplerate=48000,
                       resampler={'type': 'AsyncSinc', 'profile': 'Balanced'},
                       target_level=2048, adjust_period=5)
    filters = {'headroom': {'type': 'Gain', 'parameters': {'gain': -6.0}},
               'hp': {'type': 'BiquadCombo', 'parameters': {'type': 'LinkwitzRileyHighpass', 'freq': p['crossover_hz'], 'order': 4}},
               'lp': {'type': 'BiquadCombo', 'parameters': {'type': 'LinkwitzRileyLowpass', 'freq': p['crossover_hz'], 'order': 4}}}
    pipeline = [{'type': 'Filter', 'channels': [0, 1], 'names': ['headroom']},
                {'type': 'Mixer', 'name': 'route'}]
    for label, index in [('left', 2), ('right', 3), ('sub', 4)]:
        c = p['channels'][label]
        filters[label + '_gain'] = {'type': 'Gain', 'parameters': {
            'gain': c['gain'] + (p['sub_trim'] if label == 'sub' else 0),
            'inverted': c['invert'], 'mute': c['mute']}}
        filters[label + '_delay'] = {'type': 'Delay', 'parameters': {
            'delay': c['delay_ms'], 'unit': 'ms', 'subsample': True}}
        names = ['lp' if label == 'sub' else 'hp', label + '_gain', label + '_delay']
        if not p['peq_bypass']:
            for i, eq in enumerate(c['peq']):
                name = f'{label}_peq_{i}'
                filters[name] = {'type': 'Biquad', 'parameters': {
                    'type': 'Peaking', 'freq': eq['freq'], 'q': eq['q'], 'gain': eq['gain']}}
                names.append(name)
        pipeline.append({'type': 'Filter', 'channels': [index], 'names': names})
    mapping = [{'dest': 2, 'sources': [{'channel': 0, 'gain': 0}]},
               {'dest': 3, 'sources': [{'channel': 1, 'gain': 0}]},
               {'dest': 4, 'sources': [{'channel': 0, 'gain': 0.5, 'scale': 'linear'},
                                       {'channel': 1, 'gain': 0.5, 'scale': 'linear'}]}]
    return {'title': 'Garage 2.1 — UNCALIBRATED', 'devices': devices, 'filters': filters,
            'mixers': {'route': {'channels': {'in': 2, 'out': playback['channels']}, 'mapping': mapping}},
            'pipeline': pipeline}

def hardware_config(p, alsa):
    if not re.fullmatch(r'hw:CARD=[A-Za-z0-9_]+,DEV=[0-9]+',alsa):
        raise ValueError('explicit bound hw:CARD/DEV identity required; no default/plughw')
    return compile_profile(p, {'type': 'Stdin', 'channels': 2, 'format': 'F32_LE'},
                           {'type': 'Alsa', 'channels': 20, 'device': alsa, 'format': 'S32_LE'}, True)
