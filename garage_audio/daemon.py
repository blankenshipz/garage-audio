"""Root-owned launcher: raw loopback and policy broker share private network."""
import grp
import json
import os
from pathlib import Path
import signal
import subprocess
import threading
import time
from camilladsp import CamillaClient
from .broker import Policy, serve
from .control import atomic, connect_guarded, prove_mute
from .hardware import bound_device, validate_identity
from .profile import hardware_config

def run():
    root=Path('/usr/local/libexec/garage-audio')
    state=Path('/var/lib/garage-audio-dsp')
    runtime=Path('/run/garage-audio')
    identity=json.loads(Path('/etc/garage-audio/identity.json').read_text())
    admin_uid=identity.pop('admin_uid');validate_identity(identity)
    # Root-controlled policy binding is never accepted from local IPC.
    atomic(state/'hardware-identity.json',identity)
    (state/'alsa-device').write_text(f"hw:CARD={identity['alsa_card']},DEV={identity['pcm']}\n")
    fifo=runtime/'capture.fifo'
    if fifo.exists():fifo.unlink()
    os.mkfifo(fifo,0o620)
    process=subprocess.Popen([str(root/'camilladsp'),'-a','127.0.0.1','-p','1234',
                               '--wait','--no_config','--gain=-60','--mute'],stdin=subprocess.PIPE)
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
                  lambda p:hardware_config(p,bound_device(state)),process,
                  authorize_hardware=lambda:bound_device(state))
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
