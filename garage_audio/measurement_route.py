"""Dedicated disposable CamillaDSP FILE measurement compiler. Never production DSP."""
import array
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import ctypes
import signal
import fcntl
import io
import wave
from contextlib import ExitStack
from .measurement_signal import read_wav, sha
from . import measurement_artifacts as artifacts

VERSION = 'CamillaDSP 4.1.3 (05e9cfc)'
BINARY_SHA256 = 'e69090287d9af87eeb5720d59cf8ebf8cab1ed3adbe24de1dd60b66a47f5385b'
BINARY = Path('/opt/garage-audio/dsp/camilladsp')
LIBRARY = Path('/opt/garage-audio/dsp/lib/libasound.so.2')
LIBRARY_SHA256 = '9dabddc79cc29b4f69058ac7d94ac11e82a4d6d8cc8c9148cb8377705790c7ea'
MASTER_DB = -60
HEADROOM_DB = -6
CHANNELS = {'left':0,'right':1,'sub':2}
OUTPUT_FD = 64
RENDER_FRESH = ('native-input.raw','native-output.raw','native.stdout','native.stderr','native-process.json','native.exit','capture.wav','evidence.json')
RENDER_EXISTING = ('excitation.wav','native-config.json','native-version.stdout','native-version.stderr')

def encode(value): return json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()

def pinned_binary():
    path = BINARY
    if path.is_symlink() or not path.is_file() or not os.access(path,os.X_OK) or path.stat().st_uid != 0 or path.stat().st_mode & 0o022:
        raise ValueError('Pinned executable must be a regular non-writable-by-others file')
    if sha(path) != BINARY_SHA256: raise ValueError('CamillaDSP binary hash mismatch')
    if LIBRARY.is_symlink() or not LIBRARY.is_file() or LIBRARY.stat().st_uid != 0 or LIBRARY.stat().st_mode & 0o022 or sha(LIBRARY) != LIBRARY_SHA256:
        raise ValueError('Pinned CamillaDSP FILE library mismatch')
    version = subprocess.run([str(path),'--version'],capture_output=True,timeout=2,check=True,env={'LD_LIBRARY_PATH':str(LIBRARY.parent)})
    if version.stdout.decode().strip() != VERSION: raise ValueError('CamillaDSP version mismatch')
    return path,version

def readiness():
    try: pinned_binary(); return {'camilla_file_available':True,'camilla_file_unavailable_reason':None}
    except (ValueError,OSError,subprocess.SubprocessError):
        return {'camilla_file_available':False,'camilla_file_unavailable_reason':'Operator-pinned CamillaDSP4.1.3 binary/hash/version is missing or invalid; no render fallback'}

def compile_route(channel, output):
    selected = CHANNELS[channel]
    return {'title':'DISPOSABLE isolated measurement FILE route v1; never hardware',
        'devices':{'samplerate':48000,'chunksize':1024,'volume_limit':-20,'volume_ramp_time':0,
                   'capture':{'type':'Stdin','channels':1,'format':'F32_LE'},
                   'playback':{'type':'File','channels':23,'format':'F32_LE','filename':f'/proc/self/fd/{OUTPUT_FD}'}},
        'filters':{'headroom':{'type':'Gain','parameters':{'gain':HEADROOM_DB}}},
        'mixers':{'isolate':{'channels':{'in':1,'out':3},
                            'mapping':[{'dest':selected,'sources':[{'channel':0,'gain':0}]}]},
                  'physical_and_probes':{'channels':{'in':3,'out':23},'mapping':[
                       {'dest':destination,'sources':[{'channel':logical,'gain':0}]}
                       for logical in range(3) for destination in (2+logical,20+logical)]}},
        'pipeline':[{'type':'Filter','channels':[0],'names':['headroom']},
                    {'type':'Mixer','name':'isolate'},{'type':'Mixer','name':'physical_and_probes'}]}

def prepare(directory, channel, spec):
    artifacts.admit(directory,RENDER_FRESH+('native-config.json','native-version.stdout','native-version.stderr'),('excitation.wav',))
    if spec['sample_rate'] != 48000: raise ValueError('Pinned measurement FILE route requires48000Hz; create matching plan')
    binary,version = pinned_binary()
    config = compile_route(channel,directory/'native-output.raw')
    raw = encode(config); path = directory/'native-config.json'; artifacts.write(directory,path.name,raw)
    artifacts.write(directory,'native-version.stdout',version.stdout)
    artifacts.write(directory,'native-version.stderr',version.stderr)
    route = {'version':1,'binary_version':VERSION,'binary_sha256':BINARY_SHA256,'library_sha256':LIBRARY_SHA256,'config_sha256':hashlib.sha256(raw).hexdigest(),
             'version_stdout_sha256':hashlib.sha256(version.stdout).hexdigest(),'version_stderr_sha256':hashlib.sha256(version.stderr).hexdigest(),
             'route_sha256':hashlib.sha256(encode(compile_route(channel,'GENERATED_JOB_OUTPUT'))).hexdigest(),
             'channel':channel,'software_index':CHANNELS[channel],'physical_index':2+CHANNELS[channel],
             'physical_channels':20,'software_probe_channels':3,'output_channels':23,'format':'F32_LE',
             'output_artifact':'native-output.raw','minimum_output_frames':spec['sample_frames'],'maximum_output_frames':spec['sample_frames']+1024,
             'output_fd_role':OUTPUT_FD,
             'master_db':MASTER_DB,'headroom_db':HEADROOM_DB,'volume_ceiling_db':-20,
             'production_mutated':False,'hardware_opened':False,
             'synthetic_reference_normalization_db':66}
    return route

def render(directory, manifest, guard=lambda:None, test_behavior='normal'):
    artifacts.admit(directory,RENDER_FRESH,RENDER_EXISTING)
    route = manifest['native_file_route']; spec = manifest['signal']
    if route['binary_sha256'] != BINARY_SHA256 or route['binary_version'] != VERSION:
        raise ValueError('Manifest native pin mismatch')
    config_path = directory/'native-config.json'
    config_bytes = artifacts.read(directory,config_path.name,65536)
    if hashlib.sha256(config_bytes).hexdigest() != route['config_sha256'] or json.loads(config_bytes) != compile_route(route['channel'],directory/'native-output.raw'):
        raise ValueError('Disposable route changed; no execution')
    for name in ('stdout','stderr'):
        if hashlib.sha256(artifacts.read(directory,'native-version.'+name,65536)).hexdigest() != route['version_'+name+'_sha256']:
            raise ValueError('Native version evidence changed')
    guard()
    excitation_bytes = artifacts.read(directory,'excitation.wav',400000)
    if hashlib.sha256(excitation_bytes).hexdigest() != spec['excitation_sha256']: raise ValueError('Excitation changed')
    with wave.open(io.BytesIO(excitation_bytes),'rb') as wav:
        if (wav.getnchannels(),wav.getsampwidth(),wav.getframerate(),wav.getnframes()) != (1,2,spec['sample_rate'],spec['sample_frames']): raise ValueError('Excitation format changed')
        raw_samples = array.array('h'); raw_samples.frombytes(wav.readframes(spec['sample_frames']))
    if sys.byteorder != 'little': raw_samples.byteswap()
    samples = [v/32767 for v in raw_samples]
    binary,_ = pinned_binary()
    pcm = array.array('f',samples)
    if sys.byteorder != 'little': pcm.byteswap()
    files = ExitStack()
    try:
        input_path = directory/'native-input.raw'; input_file = files.enter_context(artifacts.exclusive(directory,input_path.name,'w+b'))
        input_file.write(pcm.tobytes()); input_file.flush(); input_file.seek(0)
        reserved = files.enter_context(artifacts.exclusive(directory,'native-output.raw','w+b'))
        output_fd = fcntl.fcntl(reserved.fileno(),fcntl.F_DUPFD,OUTPUT_FD); files.callback(os.close,output_fd)
        if output_fd != OUTPUT_FD: raise ValueError('Reserved output descriptor unavailable')
        config_fd = os.memfd_create('approved-measurement-config',os.MFD_ALLOW_SEALING); files.callback(os.close,config_fd)
        os.write(config_fd,config_bytes); os.lseek(config_fd,0,0)
        fcntl.fcntl(config_fd,fcntl.F_ADD_SEALS,fcntl.F_SEAL_WRITE|fcntl.F_SEAL_GROW|fcntl.F_SEAL_SHRINK|fcntl.F_SEAL_SEAL)
    except BaseException:
        files.close(); raise
    child = None
    parent = os.getpid()
    def parent_death():
        if not sys.platform.startswith('linux') or ctypes.CDLL(None,use_errno=True).prctl(1,signal.SIGKILL,0,0,0): os._exit(125)
        if os.getppid() != parent: os._exit(125)
    try:
        with input_file as inp,artifacts.exclusive(directory,'native.stdout') as out,artifacts.exclusive(directory,'native.stderr') as err:
            argv = [str(binary),'--gain=-60',f'/proc/self/fd/{config_fd}']
            if test_behavior == 'native-failure': argv.append('--isolated-invalid-test-option')
            child = subprocess.Popen(argv,stdin=subprocess.PIPE if test_behavior == 'native-hang' else inp,stdout=out,stderr=err,close_fds=True,
                                     env={'LD_LIBRARY_PATH':str(LIBRARY.parent)},preexec_fn=parent_death,pass_fds=(OUTPUT_FD,config_fd))
            artifacts.write(directory,'native-process.json',encode({'pid':child.pid,'parent_pid':parent,'binary_sha256':BINARY_SHA256}))
            # Worker owns/reaps only this disposable child. Guardian group also owns it.
            while child.poll() is None:
                guard()
                import time
                time.sleep(.02)
            code = child.wait(timeout=2)
        if code: raise ValueError('Disposable CamillaDSP FILE render failed')
    finally:
        if child is not None:
            if child.poll() is None: child.kill()
            child.wait(timeout=2)
            if child.stdin is not None: child.stdin.close()
        try:
            if child is not None: artifacts.write(directory,'native.exit',(str(child.returncode)+'\n').encode())
            reserved.seek(0); output_bytes = reserved.read((spec['sample_frames']+1024)*23*4+1)
        finally:
            files.close()
    output = directory/'native-output.raw'
    if len(output_bytes) > (spec['sample_frames']+1024)*23*4: raise ValueError('Bounded native output required')
    values = array.array('f'); values.frombytes(output_bytes)
    if sys.byteorder != 'little': values.byteswap()
    if len(values)%23: raise ValueError('Native channel/frame count invalid')
    frames = len(values)//23
    if not spec['sample_frames'] <= frames <= spec['sample_frames']+1024 or not all(math.isfinite(v) for v in values):
        raise ValueError('Native render length/finite check failed')
    streams = [values[i::23] for i in range(23)]
    selected = route['physical_index']; probe = 20+route['software_index']
    peaks = [max(map(abs,s),default=0) for s in streams]
    rms = [math.sqrt(sum(v*v for v in s)/max(1,len(s))) for s in streams]
    if any(peaks[i] != 0 for i in range(23) if i not in (selected,probe)):
        raise ValueError('Unrequested software/physical output nonzero')
    attenuation = 10**((MASTER_DB+HEADROOM_DB)/20)
    if not 1e-10 < peaks[selected] <= .01*attenuation*1.001 or streams[selected] != streams[probe]:
        raise ValueError('Requested branch level/probe mismatch')
    if any(v != 0 for stream in streams for v in stream[spec['sample_frames']:]):
        raise ValueError('Native EOF padding not exactly silent')
    evidence = {'binary_version':VERSION,'binary_sha256':BINARY_SHA256,'library_sha256':LIBRARY_SHA256,'input_frames':spec['sample_frames'],
        'kind':'disposable-electrical-native-FILE-render','source':'synthetic','measured_acoustically':False,'output_channels':23,
        'output_frames':frames,'physical_peaks':peaks[:20],'software_peaks':peaks[20:],
        'physical_rms':rms[:20],'software_rms':rms[20:],'requested_physical_index':selected,
        'requested_software_index':route['software_index'],'all_other_outputs_exact_zero':True,
        'input_sha256':hashlib.sha256(pcm.tobytes()).hexdigest(),'output_sha256':hashlib.sha256(output_bytes).hexdigest(),'config_sha256':hashlib.sha256(config_bytes).hexdigest(),
        'exit_status':code,'master_db':MASTER_DB,'headroom_db':HEADROOM_DB,'volume_ceiling_db':-20,
        'native_child_exit_verified':True,'native_pid':child.pid,
        'synthetic_reference_normalization_db':66,'hardware_opened':False,'production_mutated':False}
    # Explicit synthetic virtual-microphone normalization of known disposable DSP
    # attenuation only. Never calibration correction or physical signal authority.
    return [float(v)/attenuation for v in streams[selected][:spec['sample_frames']]],evidence
