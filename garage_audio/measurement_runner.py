"""Owned file/null worker plus deadline/parent-loss guardian. Never opens audio devices."""
import ctypes
import json
import os
from pathlib import Path
import signal
import select
import subprocess
import sys
import time
import io
import array
import wave
from .measurement_signal import capture_evidence, read_wav, sha, write_wav
from .measurement_route import render
from . import measurement_artifacts as artifacts

def control_live(fd):
    ready,_,_ = select.select([fd],[],[],0)
    if ready and os.read(fd,1) == b'': raise InterruptedError('owner lifetime control pipe closed')

def worker(directory, behavior, expected_parent, control_fd, epoch):
    parent = expected_parent
    if os.getppid() != parent: raise RuntimeError('parent lost before worker admission')
    if sys.platform.startswith('linux'):
        if ctypes.CDLL(None,use_errno=True).prctl(1,signal.SIGKILL,0,0,0): raise RuntimeError('parent death guardian unavailable')
        if os.getppid() != parent: raise RuntimeError('parent lost')
    def guard():
        control_live(control_fd)
        if os.getppid() != parent: raise RuntimeError('parent lost')
    guard()
    manifest = json.loads(artifacts.read(directory,'manifest.json',262144))
    if manifest['server_epoch'] != epoch: raise RuntimeError('epoch mismatch')
    spec = manifest['signal']
    if behavior == 'crash': os._exit(17)
    if behavior in ('hang','slow'):
        until = time.monotonic() + (60 if behavior == 'hang' else 1)
        while time.monotonic() < until: guard(); time.sleep(.02)
    x,native_evidence = render(directory,manifest,guard,behavior)
    # Explicit synthetic LTI fixture, no microphone/hardware or supplied program.
    y = [.8*v + (.1*x[i-24] if i >= 24 else 0) for i,v in enumerate(x)]
    if behavior == 'clip': y[len(y)//2] = 1
    data = array.array('h',(int(max(-1,min(1,v))*32767) for v in y))
    if sys.byteorder != 'little': data.byteswap()
    buffer = io.BytesIO()
    with wave.open(buffer,'wb') as wav:
        wav.setnchannels(1); wav.setsampwidth(2); wav.setframerate(spec['sample_rate']); wav.writeframes(data.tobytes())
    artifacts.write(directory,'capture.wav',buffer.getvalue())
    evidence = capture_evidence(directory/'excitation.wav',directory/'capture.wav',spec,guard)
    evidence['adapter'] = 'file-null'; evidence['hardware_enabled'] = False
    evidence['native_file_render'] = native_evidence
    evidence['file_null_transfer'] = {'direct_gain':.8,'echo_gain':.1,'echo_samples':24,'synthetic':True}
    artifacts.write(directory,'evidence.json',json.dumps(evidence,allow_nan=False).encode())
    return 0 if evidence['supported_ir'] else 18

def guardian(directory, deadline, behavior, expected_parent, control_fd, epoch):
    parent = expected_parent; child = None
    reason = 'worker_exit'; parent_lost = False
    def halted(*_): raise InterruptedError('guardian revoked')
    signal.signal(signal.SIGTERM,halted)
    if sys.platform.startswith('linux'):
        if ctypes.CDLL(None,use_errno=True).prctl(36,1,0,0,0): raise RuntimeError('owned descendant subreaper unavailable')
    try:
        if behavior == 'pre-admission': time.sleep(.3)  # Private lifecycle test seam, never operation args.
        control_live(control_fd)
        if time.monotonic() >= deadline: reason = 'deadline_before_admission'; raise TimeoutError('manifest deadline')
        if json.loads(artifacts.read(directory,'manifest.json',262144))['server_epoch'] != epoch: raise InterruptedError('epoch mismatch')
        if os.getppid() != parent:
            parent_lost = True; reason = 'parent_lost_before_admission'; raise InterruptedError('parent lost')
        child = subprocess.Popen([sys.executable,'-m','garage_audio.measurement_runner','worker',str(directory),behavior,str(os.getpid()),str(control_fd),epoch],
                                 stdin=subprocess.DEVNULL,close_fds=True,pass_fds=(control_fd,))
        end = deadline  # Owner-provided absolute monotonic deadline includes interpreter/setup time.
        while child.poll() is None:
            control_live(control_fd)
            if os.getppid() != parent:
                parent_lost = True; reason = 'parent_lost'; raise InterruptedError('guardian parent lost')
            if time.monotonic() >= end:
                reason = 'deadline'; raise TimeoutError('manifest deadline')
            time.sleep(.02)
        control_live(control_fd)
        if os.getppid() != parent:
            parent_lost = True; reason = 'parent_lost'; raise InterruptedError('owner lost at completion')
        return child.wait()
    except (InterruptedError,TimeoutError):
        if os.getppid() != parent: parent_lost = True
        if reason == 'worker_exit': reason = 'revoked'
        return 124
    finally:
        if child is not None:
            if child.poll() is None: child.kill()
            child.wait(timeout=3)
        reaped = []
        if sys.platform.startswith('linux'):
            until = time.monotonic()+2
            while time.monotonic()<until:
                try: pid,status = os.waitpid(-1,os.WNOHANG)
                except ChildProcessError: break
                if pid: reaped.append({'pid':pid,'exit_status':os.waitstatus_to_exitcode(status)})
                else: time.sleep(.01)
        artifacts.write(directory,'guardian-exit.json',json.dumps({'reason':reason,'parent_lost':parent_lost,
            'owned_worker_exit_verified':True,'worker_spawned':child is not None,
            'worker_exit_status':child.returncode if child is not None else None,'reaped_descendants':reaped}).encode())

def main():
    mode = sys.argv[1]; directory = Path(sys.argv[2])
    if mode == 'worker': code = worker(directory,sys.argv[3],int(sys.argv[4]),int(sys.argv[5]),sys.argv[6])
    else: code = guardian(directory,float(sys.argv[3]),sys.argv[4],int(sys.argv[5]),int(sys.argv[6]),sys.argv[7])
    raise SystemExit(code)

if __name__ == '__main__': main()
