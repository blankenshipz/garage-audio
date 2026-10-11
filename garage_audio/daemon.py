"""Root-owned launcher: raw loopback and policy broker share private network."""
import grp
import json
import os
from pathlib import Path
import signal
import stat
import subprocess
import threading
import time
from camilladsp import CamillaClient
from .broker import Policy, serve
from .control import atomic, connect_guarded, prove_mute
from .hardware import bound_device, validate_identity, load_pinned_capabilities, admit_capabilities, admit_map_capabilities
from .profile import hardware_config

def create_capture_fifo(path, uid, gid):
    """Creation mode is umask-masked; explicitly set and verify final boundary."""
    os.mkfifo(path,0o620)
    os.chmod(path,0o620,follow_symlinks=False)
    info=path.lstat()
    if not stat.S_ISFIFO(info.st_mode) or stat.S_IMODE(info.st_mode)!=0o620 or (info.st_uid,info.st_gid)!=(uid,gid):
        raise PermissionError('capture FIFO ownership/mode is not the required0620 boundary')

def run():
    root=Path('/usr/local/libexec/garage-audio')
    state=Path('/var/lib/garage-audio-dsp')
    runtime=Path('/run/garage-audio')
    identity=json.loads(Path('/etc/garage-audio/identity.json').read_text())
    admin_uid=identity.pop('admin_uid');validate_identity(identity)
    # Root-controlled policy binding is never accepted from local IPC.
    atomic(state/'hardware-identity.json',identity)
    (state/'alsa-device').write_text(f"hw:CARD={identity['alsa_card']},DEV={identity['pcm']}\n")
    capabilities=load_pinned_capabilities(Path('/etc/garage-audio/playback-capabilities.json'),identity)
    admit_map_capabilities(state,capabilities)
    atomic(state/'playback-capabilities.json',capabilities)
    def admit():
        alsa=bound_device(state)  # Keep dedicated UID/mode0600 admission intact.
        admit_capabilities(identity,capabilities)  # Recheck switches/hotplug on each operation.
        return alsa
    admit()  # Refuse mismatched legacy20/S32 before launching any DSP generation.
    fifo=runtime/'capture.fifo'
    if fifo.exists():fifo.unlink()
    create_capture_fifo(fifo,os.geteuid(),grp.getgrnam('garage-stream').gr_gid)
    process=subprocess.Popen([str(root/'camilladsp'),'-a','127.0.0.1','-p','1234',
                               '--wait','--statefile',str(runtime/'camilla-state.yml'),'--no_config','--gain=-60','--mute'],stdin=subprocess.PIPE)
    def close(*_):
        if process.poll() is None:process.terminate()
    signal.signal(signal.SIGTERM,close);signal.signal(signal.SIGINT,close)
    def feed():
        try:
            with fifo.open('rb',buffering=0) as input_stream:
                while process.poll() is None:
                    block=input_stream.read(8192)
                    if not block:break
                    process.stdin.write(block);process.stdin.flush()
        except (OSError,ValueError):pass
        finally:close()  # Producer EOF is a generation-ending event, never resume.
    threading.Thread(target=feed,daemon=True).start()
    policy=Policy(state,lambda:CamillaClient('127.0.0.1',1234),
                  lambda p:hardware_config(p,admit(),capabilities),process,
                  authorize_hardware=admit,hardware_binding=lambda:{'identity':identity,'capabilities':admit_capabilities(identity,capabilities)})
    try:
        ready=False
        for _ in range(40):
            c=CamillaClient('127.0.0.1',1234)
            try:c.connect();prove_mute(c);ready=True;break
            except OSError:time.sleep(.1)
            finally:c.disconnect()
        if not ready:raise RuntimeError('private DSP did not establish startup mute')
        serve(policy,runtime/'control.sock',admin_uid,grp.getgrnam('garage-control').gr_gid)
        if process.returncode:raise RuntimeError('private DSP failed/stopped; new generation required')
    finally:
        close()
        try:process.wait(timeout=3)
        except subprocess.TimeoutExpired:process.kill();process.wait(timeout=3)
