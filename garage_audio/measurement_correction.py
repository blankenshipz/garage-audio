"""Exact accepted-revision graph rendering before any physical acquisition.

The full listening graph processes stereo excitation; a final isolation mixer
retains only the approved branch. Never merely label unprocessed PCM corrected.
"""
import array
import hashlib
import json
import math
import os
import subprocess
import sys
import time
from . import audio_config, audio_public, measurement_artifacts as artifacts, measurement_route
from .measurement_signal import read_wav

FRESH = ('correction-input.f32','correction-output.f32','correction.stdout','correction.stderr')
EXISTING = ('correction-config.json',)


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()


def snapshot():
    first = audio_public.request('audio.config.get'); status = audio_public.request('audio.status')
    last = audio_public.request('audio.config.get')
    keys = ('binding','revision','configuration','compiler_revision')
    if any(first[key] != last[key] for key in keys): raise ValueError('configuration changed during measurement admission')
    if first['binding']['kind'] != 'hardware' or status['device_mode'] != 'hardware':
        raise PermissionError('physical acquisition requires hardware binding; no FILE-to-device fallback')
    return {**{key:first[key] for key in keys}, 'generation':status['generation']}


def compile_graph(binding, channel, output):
    spec = audio_config.validate_spec(binding['configuration'])
    if binding['compiler_revision'] != audio_config.COMPILER: raise ValueError('correction compiler superseded')
    destination = spec[channel]
    if destination is None: raise ValueError('measurement channel is not routed')
    graph = audio_config.compile_graph(spec,output)
    graph['mixers']['measurement_isolation'] = {'channels':{'in':12,'out':12},
        'mapping':[{'dest':destination-1,'sources':[{'channel':destination-1,'gain':0}]}]}
    graph['pipeline'].append({'type':'Mixer','name':'measurement_isolation'})
    return graph


def prepare(directory, binding, channel, commissioned_output):
    if binding['configuration'][channel] != commissioned_output: raise PermissionError('accepted routing differs from commissioned microphone test branch')
    measurement_route.pinned_binary()
    graph = compile_graph(binding,channel,str(directory/'correction-output.f32'))
    raw = json.dumps(graph,sort_keys=True).encode(); artifacts.write(directory,'correction-config.json',raw)
    return {'binding':binding,'config_sha256':hashlib.sha256(raw).hexdigest(),
            'graph_sha256':digest(compile_graph(binding,channel,'GENERATED_JOB_OUTPUT')),
            'binary_sha256':measurement_route.BINARY_SHA256,'channel':channel,
            'reference':'unfiltered excitation with fixed -12dB safety reserve; graph remains in measured transfer'}


def render(directory, manifest, guard):
    bound = manifest['correction_render']; spec = manifest['signal']; binding = bound['binding']
    raw = artifacts.read(directory,'correction-config.json',65536)
    if hashlib.sha256(raw).hexdigest() != bound['config_sha256'] or json.loads(raw) != compile_graph(binding,manifest['channel'],str(directory/'correction-output.f32')):
        raise ValueError('bound correction graph changed')
    measurement_route.pinned_binary(); artifacts.admit(directory,FRESH,EXISTING)
    reference = read_wav(directory/'excitation.wav',spec['sample_rate'],spec['sample_frames'])
    scale = 10**((manifest['hardware_admission']['policy']['peak_dbfs']-12-spec['peak_dbfs'])/20)
    reference = [value*scale for value in reference]
    channel = manifest['channel']; stereo = array.array('f')
    for value in reference:
        stereo.extend((value if channel in ('left','sub') else 0, value if channel in ('right','sub') else 0))
    if sys.byteorder != 'little': stereo.byteswap()
    artifacts.write(directory,'correction-input.f32',stereo.tobytes())
    # Precreate a private output; only the pinned owned renderer may overwrite it.
    with artifacts.exclusive(directory,'correction-output.f32'): pass
    with artifacts.read_handle(directory,'correction-input.f32') as source, artifacts.exclusive(directory,'correction.stdout') as stdout, artifacts.exclusive(directory,'correction.stderr') as stderr:
        env = dict(os.environ, GARAGE_AUDIO_PARENT_PID=str(os.getpid()),LD_LIBRARY_PATH=str(measurement_route.LIBRARY.parent))
        child = subprocess.Popen([sys.executable,'-m','garage_audio.audio_child','dsp',str(directory/'correction-config.json')],
            stdin=source,stdout=stdout,stderr=stderr,env=env)
        try:
            end = time.monotonic()+5
            while child.poll() is None:
                guard()
                if time.monotonic() >= end: raise TimeoutError('owned correction render deadline')
                time.sleep(.01)
            if child.returncode: raise ValueError('owned correction render failed')
        finally:
            if child.poll() is None: child.kill()
            child.wait(timeout=2)
    data = array.array('f'); data.frombytes(artifacts.read(directory,'correction-output.f32',(spec['sample_frames']+1024)*12*4))
    if sys.byteorder != 'little': data.byteswap()
    if not spec['sample_frames']*12 <= len(data) <= (spec['sample_frames']+1024)*12 or len(data)%12:
        raise ValueError('correction render frame mismatch')
    destination = binding['configuration'][channel]-1; output = bytearray(); peak = 0
    for index,value in enumerate(data[:spec['sample_frames']*12]):
        if not math.isfinite(value) or (index%12 != destination and value != 0): raise ValueError('correction output isolation failed')
        peak = max(peak,abs(value)); output.extend(int(value*8388607).to_bytes(3,'little',signed=True))
    if peak > 10**(manifest['hardware_admission']['policy']['peak_dbfs']/20): raise ValueError('render exceeds commissioned peak; never renormalize away correction')
    evidence = {'binding':binding,'graph_sha256':bound['graph_sha256'],'config_sha256':bound['config_sha256'],
        'binary_sha256':measurement_route.BINARY_SHA256,'native_exit_status':0,'input_sha256':hashlib.sha256(stereo.tobytes()).hexdigest(),
        'emitted_sha256':hashlib.sha256(output).hexdigest(),'frames':spec['sample_frames'],'unused_outputs_zero':True,
        'emitted_peak_dbfs':20*math.log10(max(peak,1e-15))}
    return reference,bytes(output),evidence
