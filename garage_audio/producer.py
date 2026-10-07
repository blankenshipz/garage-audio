"""User-owned PCM producer: identity-bound pidfd stop and control-loss watchdog.

Linux pidfds bind signals to a process lifetime, not a reusable numeric PID.
Registration/start and persistent fail-close latch share one project lock.
No host process enumeration, name-based kill, process-group kill or DAC opens.
"""
import fcntl
import ctypes
import json
import os
from pathlib import Path
import select
import shutil
import signal
import stat
import subprocess
import time
import uuid
from contextlib import contextmanager
from .control import ROOT, atomic
from .ipc import request

@contextmanager
def locked(state):
    state.mkdir(exist_ok=True)
    with (state/'producer.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        yield

def identity(pid, marker):
    proc=Path('/proc')/str(pid)
    fields=(proc/'stat').read_text().rsplit(')',1)[1].split()
    if fields[0]=='Z':raise ProcessLookupError('producer already exited')
    if proc.stat().st_uid!=os.getuid():raise PermissionError('producer UID differs')
    if f'GARAGE_AUDIO_PRODUCER={marker}'.encode() not in (proc/'environ').read_bytes().split(b'\0'):
        raise PermissionError('not this registered project producer instance')
    return {'pid':pid,'uid':proc.stat().st_uid,'starttime':fields[19],
            'boot':Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
            'exe':str((proc/'exe').resolve(strict=True)),
            'cwd':str((proc/'cwd').resolve(strict=True)),
            'argv':(proc/'cmdline').read_bytes().hex(),'marker':marker}

def terminate(record):
    try:fd=os.pidfd_open(record['pid'])
    except ProcessLookupError:return 'already_exited'
    try:
        try:current=identity(record['pid'],record['marker'])
        except (FileNotFoundError,ProcessLookupError):return 'already_exited'
        if current!=record:raise PermissionError('producer lifetime/ownership/command identity mismatch; no signal sent')
        signal.pidfd_send_signal(fd,signal.SIGKILL)
        ready,_,_=select.select([fd],[],[],3)
        if not ready:raise RuntimeError('owned producer exit not confirmed')
        return 'exit_verified'
    finally:os.close(fd)

def stop(state, manager=('systemctl','--user','stop','garage-player','garage-dsp'), timeout=2):
    # Independent stop FIRST: never wait on a broken user manager for silence.
    with locked(state):
        atomic(state/'producer-stop.json',{'stopped_at':time.time(),'reason':'mute could not be proved'})
        path=state/'producer.json'
        if path.exists():
            info=path.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_uid!=os.getuid():raise PermissionError('untrusted producer record')
            result=terminate(json.loads(path.read_text()))
        else:result='no_registered_producer_under_start_lock'
    try:
        reply=subprocess.run(manager,check=False,timeout=timeout,capture_output=True)
        manager_status=f'exit{reply.returncode}'
    except subprocess.TimeoutExpired:manager_status='timeout'
    except OSError:manager_status='unavailable'
    return {'producer':result,'manager':manager_status,'restart_latched':True}

def register(state,command,output):
    """Launch exactly one project PCM child; caller retains and reaps it."""
    with locked(state):
        if (state/'producer-stop.json').exists():raise RuntimeError('producer fail-close latch: explicit muted rearm required')
        path=state/'producer.json'
        if path.exists():
            old=json.loads(path.read_text())
            try:identity(old['pid'],old['marker'])
            except (FileNotFoundError,ProcessLookupError):pass
            else:raise RuntimeError('a producer is already registered; refusing replacement')
        marker=uuid.uuid4().hex
        parent=os.getpid()
        def parent_death():
            # Also close PCM if the supervisor is killed/crashes before finally.
            if ctypes.CDLL(None,use_errno=True).prctl(1,signal.SIGKILL,0,0,0)!=0:os._exit(125)
            if os.getppid()!=parent:os._exit(125)
        child=subprocess.Popen(command,stdout=output,env=dict(os.environ,GARAGE_AUDIO_PRODUCER=marker),cwd=ROOT,preexec_fn=parent_death)
        try:
            record=identity(child.pid,marker)
            atomic(path,record)
        except Exception:
            child.kill();child.wait(timeout=3);raise
        return child,record

def supervise(state,command,output,initialize=lambda:request({'cmd':'initialize'}),
              heartbeat=lambda:request({'cmd':'status'},timeout=2),interval=.5):
    child,record=register(state,command,output)
    previous=signal.getsignal(signal.SIGTERM)
    def halted(*_):raise InterruptedError('producer supervisor stopped')
    signal.signal(signal.SIGTERM,halted)
    try:
        initialize()
        generation=heartbeat()['generation']
        while child.poll() is None:
            if (state/'producer-stop.json').exists():raise RuntimeError('producer stop latched')
            if heartbeat()['generation']!=generation:raise RuntimeError('DSP generation changed; producer stopped')
            time.sleep(interval)
        child.wait(timeout=3)
        if child.returncode:raise RuntimeError('project PCM child failed')
    except BaseException:
        with locked(state):atomic(state/'producer-stop.json',{'stopped_at':time.time(),'reason':'producer lifecycle/control loss'})
        raise
    finally:
        # Owned subprocess object remains bound to our child; wait verifies exit.
        if child.poll() is None:child.kill()
        child.wait(timeout=3)
        signal.signal(signal.SIGTERM,previous)

def main():
    import argparse
    ap=argparse.ArgumentParser();ap.add_argument('--rearm',action='store_true');args=ap.parse_args()
    state=ROOT/'state'
    if args.rearm:
        status=request({'cmd':'status'},timeout=2)
        if not status['muted'] or status['volume_db']!=-60:raise RuntimeError('rearm requires proved muted/-60 policy DSP')
        with locked(state):(state/'producer-stop.json').unlink(missing_ok=True)
        print('Producer rearmed while DSP muted/-60; start remains a fresh muted initialization.');return
    fifo=Path('/run/garage-audio/capture.fifo')
    import pwd
    info=fifo.lstat()
    if not stat.S_ISFIFO(info.st_mode) or stat.S_IMODE(info.st_mode)!=0o620 or info.st_uid!=pwd.getpwnam('garage-dsp').pw_uid:
        raise PermissionError('required isolated capture FIFO absent or wrong permissions')
    # Nonblocking open fails closed if dedicated reader absent; then PCM writes
    # use blocking mode. Does not open any ALSA hardware.
    fd=os.open(fifo,os.O_WRONLY|os.O_NONBLOCK|os.O_NOFOLLOW)
    os.set_blocking(fd,True)
    try:
        supervise(state,[shutil.which('pw-record') or 'pw-record','--raw','--target','garage_music',
            '--properties=stream.capture.sink=true node.dont-fallback=true node.dont-move=true',
            '--rate','48000','--channels','2','--format','f32','-'],fd)
    finally:os.close(fd)

if __name__=='__main__':main()
