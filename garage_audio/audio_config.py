"""Worker-owned declarative configuration. No paths or device bindings from clients.

FILE acceptance is software-only. Hardware mutation is deliberately unavailable until
an independent exact-proposal human approval channel exists.
"""
import copy
import hashlib
import json
import math
import os
import subprocess
import threading
from pathlib import Path

COMPILER = 'layout-cut-peq-v2'
DEFAULT = {'layout': 'stereo', 'left': 3, 'right': 4, 'sub': None,
           'crossover_hz': 80, 'headroom_db': -6}
SPEC = {'type': 'object', 'additionalProperties': False, 'required': list(DEFAULT),
        'properties': {'layout': {'enum': ['stereo', 'powered-sub-2.1']},
                       **{k: {'type': 'integer', 'minimum': 1, 'maximum': 12} for k in ('left', 'right')},
                       'sub': {'anyOf': [{'type': 'null'}, {'type': 'integer', 'minimum': 1, 'maximum': 12}]},
                       'crossover_hz': {'type': 'number', 'minimum': 40, 'maximum': 160},
                        'headroom_db': {'type': 'number', 'minimum': -60, 'maximum': -6}}}
EQ_SPEC = {'type': 'object', 'additionalProperties': False, 'required': ['freq', 'q', 'gain'],
           'properties': {'freq': {'type': 'number', 'minimum': 20, 'maximum': 1000},
                          'q': {'type': 'number', 'minimum': .5, 'maximum': 10},
                          'gain': {'type': 'number', 'minimum': -6, 'maximum': 0}}}
# Optional for compatibility with existing routing clients and accepted v1 state.
# This is an electrical filter declaration, never measured-source provenance.
SPEC['properties']['correction'] = {'type': 'object', 'additionalProperties': False,
    'required': ['left', 'right', 'sub'], 'properties': {
        channel: {'type': 'array', 'maxItems': 4, 'items': copy.deepcopy(EQ_SPEC)}
        for channel in ('left', 'right', 'sub')}}
SPEC['properties']['correction']['properties']['sub']['items']['properties']['freq']['maximum'] = 300
IDENTITY = {'type': 'string', 'pattern': '^[a-f0-9]{64}$'}
SCHEMAS = {'audio.config.get': ([], []), 'audio.config.propose': (['configuration'], []),
           **{f'audio.config.{op}': (['proposal_id'], []) for op in ('validate', 'apply', 'rollback')}}
OPERATION_SCHEMAS = {op: {'type': 'object', 'additionalProperties': False,
    'required': fields[0], 'properties': {k: SPEC if k == 'configuration' else IDENTITY for k in fields[0]}}
    for op, fields in SCHEMAS.items()}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def validate_spec(spec):
    if not isinstance(spec, dict) or set(spec) - {'correction'} != set(DEFAULT): raise ValueError('exact configuration fields required')
    if spec['layout'] not in ('stereo', 'powered-sub-2.1'): raise ValueError('unknown layout')
    for key in ('left', 'right'):
        if type(spec[key]) is not int or not 1 <= spec[key] <= 12: raise ValueError('invalid output')
    if spec['sub'] is not None and (type(spec['sub']) is not int or not 1 <= spec['sub'] <= 12): raise ValueError('invalid sub output')
    for key, lo, hi in [('crossover_hz', 40, 160), ('headroom_db', -60, -6)]:
        if type(spec[key]) not in (int, float) or not math.isfinite(spec[key]) or not lo <= spec[key] <= hi: raise ValueError('invalid filter or level')
    outputs = [spec['left'], spec['right']]
    if spec['layout'] == 'powered-sub-2.1':
        if spec['sub'] is None: raise ValueError('explicit powered sub output required')
        outputs.append(spec['sub'])
    elif spec['sub'] is not None: raise ValueError('stereo has no sub output')
    if len(set(outputs)) != len(outputs): raise ValueError('output routes must be unique')
    if 'correction' in spec:
        correction = spec['correction']
        if not isinstance(correction, dict) or set(correction) != {'left', 'right', 'sub'}:
            raise ValueError('exact correction channels required')
        for channel, filters in correction.items():
            if not isinstance(filters, list) or len(filters) > 4: raise ValueError('maximum four cut filters per channel')
            if channel == 'sub' and spec['sub'] is None and filters: raise ValueError('sub correction requires routed sub')
            for eq in filters:
                if not isinstance(eq, dict) or set(eq) != {'freq', 'q', 'gain'}: raise ValueError('exact PEQ fields required')
                for key, lo, hi in [('freq', 20, 300 if channel == 'sub' else 1000), ('q', .5, 10), ('gain', -6, 0)]:
                    value = eq[key]
                    if type(value) not in (int, float) or not math.isfinite(value) or not lo <= value <= hi:
                        raise ValueError('invalid cut-only correction')
    return copy.deepcopy(spec)


def compile_graph(spec, output):
    from .audio_container import line_graph
    spec = validate_spec(spec)
    graph = line_graph(output)
    graph['title'] = 'Garage declarative ' + spec['layout']
    graph['filters']['headroom']['parameters']['gain'] = spec['headroom_db']
    mapping = [{'dest': spec[k]-1, 'sources': [{'channel': i, 'gain': 0}]} for i, k in enumerate(('left', 'right'))]
    if spec['sub'] is not None:
        mapping.append({'dest': spec['sub']-1, 'sources': [{'channel': i, 'gain': -6.020599913279624} for i in (0, 1)]})
    graph['mixers']['route']['mapping'] = mapping
    if spec['sub'] is not None:
        for name, kind in [('hp', 'Highpass'), ('lp', 'Lowpass')]:
            graph['filters'][name] = {'type': 'Biquad', 'parameters': {'type': kind, 'freq': spec['crossover_hz'], 'q': math.sqrt(.5)}}
        graph['pipeline'] += [{'type': 'Filter', 'channels': [spec['left']-1, spec['right']-1], 'names': ['hp', 'hp']},
                               {'type': 'Filter', 'channels': [spec['sub']-1], 'names': ['lp', 'lp']}]
    for channel, filters in spec.get('correction', {}).items():
        names = []
        for index, eq in enumerate(filters):
            name = f'{channel}_correction_{index}'
            graph['filters'][name] = {'type': 'Biquad', 'parameters': {'type': 'Peaking', **eq}}
            names.append(name)
        if names: graph['pipeline'].append({'type': 'Filter', 'channels': [spec[channel]-1], 'names': names})
    return graph


class Configurations:
    def __init__(self, worker):
        self.worker = worker
        self.lock = threading.Lock()
        self.path = worker.evidence / 'accepted-configuration.json'
        self.binding = {'revision': 'file-12-v1' if worker.mode == 'file' else 'hardware-unverified-v1',
                        'kind': worker.mode, 'wiring': 'declared-virtual' if worker.mode == 'file' else 'unverified'}
        self.state = {'revision': 0, 'configuration': copy.deepcopy(DEFAULT), 'previous': None}
        if self.path.exists():
            saved = json.loads(self.path.read_text())
            validate_spec(saved['configuration'])
            if saved.get('previous') is not None: validate_spec(saved['previous'])
            if saved['binding'] != self.binding: raise ValueError('saved binding changed')
            self.state = saved
        self.proposals = {}

    def get(self):
        return copy.deepcopy({**self.state, 'binding': self.binding, 'compiler_revision': COMPILER,
                'capability_revision': 1, 'desired': self.state['configuration'],
                'accepted_for_next_play': self.state['configuration'],
                'loaded': copy.deepcopy(self.worker.loaded_configuration),
                'load_verification': 'owned-process-launched-not-dsp-readback' if self.worker.loaded_configuration else 'not_running',
                'runtime_state': 'not_running' if not self.worker.children else self.worker.state,
                'physically_verified': False, 'hardware_apply_available': False,
                'correction_verification': 'electrical-declaration-only-not-acoustic-proof'})

    def propose(self, spec):
        if len(self.proposals) >= 64: raise ValueError('proposal capacity reached; restart isolated worker')
        spec = validate_spec(spec)
        proposal = {'base_revision': self.state['revision'], 'configuration': spec,
                    'compiler_revision': COMPILER, 'binding': self.binding, 'capability_revision': 1,
                     'diff': {k: {'before': self.state['configuration'].get(k), 'after': spec.get(k)}
                              for k in sorted(set(spec) | set(self.state['configuration']))
                              if spec.get(k) != self.state['configuration'].get(k)},
                    'approval_requirement': 'none-file-only' if self.worker.mode == 'file' else 'independent-human-exact-proposal-required'}
        identity = digest(proposal)
        self.proposals[identity] = {'proposal': proposal, 'validated': False}
        return {'proposal_id': identity, **copy.deepcopy(proposal), 'validated': False}

    def execute(self, operation, args):
        if operation not in SCHEMAS or not isinstance(args, dict) or set(args) != set(SCHEMAS[operation][0]):
            raise ValueError('exact operation arguments required')
        if 'proposal_id' in args and (not isinstance(args['proposal_id'], str) or len(args['proposal_id']) != 64 or any(c not in 'abcdef0123456789' for c in args['proposal_id'])):
            raise ValueError('invalid proposal identity')
        if operation == 'audio.config.get':
            with self.lock: return self.get()
        if operation == 'audio.config.propose':
            with self.lock: return self.propose(args['configuration'])
        identity = args['proposal_id']
        with self.lock:
            record = copy.deepcopy(self.proposals[identity])
            proposal = record['proposal']
            if proposal['base_revision'] != self.state['revision']: raise ValueError('stale proposal')
            if operation == 'audio.config.rollback' and proposal['configuration'] != self.state['previous']:
                raise ValueError('rollback requires a fresh validated proposal matching previous configuration')
        if self.worker.mode != 'file': raise PermissionError('independent human authority and verified hardware binding required')
        with self.worker.children_lock:
            generation = self.worker.stop_generation
        if operation == 'audio.config.validate':
            path = self.worker.evidence / (identity + '.yaml')
            # JSON is a strict YAML subset and needs no additional worker dependency.
            path.write_text(json.dumps(compile_graph(proposal['configuration'], self.worker.evidence / 'configuration-validation.f32'), allow_nan=False))
            # Pinned container executable, no shell, client paths or DSP fragments.
            result = subprocess.run(['/opt/garage-audio/dsp/camilladsp', '--check', str(path)],
                                    capture_output=True, timeout=10,
                                    env=dict(os.environ, LD_LIBRARY_PATH='/opt/garage-audio/dsp/lib'))
            (self.worker.evidence / (identity + '.validation.stdout')).write_bytes(result.stdout)
            (self.worker.evidence / (identity + '.validation.stderr')).write_bytes(result.stderr)
            if result.returncode: raise ValueError('Camilla validation failed; retained private diagnostics')
            with self.lock:
                if proposal['base_revision'] != self.state['revision']: raise ValueError('stale proposal')
                with self.worker.children_lock:
                    if self.worker.stop_generation != generation: raise ValueError('validation cancelled by Stop')
                self.proposals[identity]['validated'] = True
            return {'proposal_id': identity, 'validated': True, 'validator': 'pinned-container-camilladsp', 'physical_verified': False}
        if operation not in ('audio.config.apply', 'audio.config.rollback') or not record['validated']: raise ValueError('validate exact proposal before apply')
        # Quiesce owned children only; discovery, queue and enrollment remain untouched.
        with self.worker.command_lock:
            return self.commit(proposal, identity, generation)

    def commit(self, proposal, identity, generation):
        self.worker.halt('configuration_apply')
        with self.lock:
            if proposal['base_revision'] != self.state['revision']: raise ValueError('stale proposal')
            saved = {'revision': self.state['revision'] + 1, 'configuration': proposal['configuration'],
                     'previous': self.state['configuration'], 'binding': self.binding, 'proposal_id': identity}
            temporary = self.path.with_suffix('.next')
            with open(temporary, 'w') as stream:
                os.chmod(temporary, 0o600)
                json.dump(saved, stream, allow_nan=False)
                stream.flush()
                os.fsync(stream.fileno())
            # Slow staging/fsync must not block emergency Stop's state lock.
            # Only atomic publication is inside the short generation transition.
            with self.worker.children_lock:
                if self.worker.stop_generation != generation + 1: raise ValueError('configuration commit cancelled by Stop')
                os.replace(temporary, self.path)
                self.state = saved
            return self.get()
