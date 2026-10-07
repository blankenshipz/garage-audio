"""Inside a nonprivileged --network none test container: FILE/null only."""
import json
from pathlib import Path
import subprocess
import threading
import time
import yaml
from camilladsp import CamillaClient
from garage_audio.broker import Policy,serve
from garage_audio.control import atomic
from garage_audio.profile import DEFAULT,compile_profile

state=Path('/test');factory=lambda p:compile_profile(p,{'type':'Stdin','channels':2,'format':'F32_LE'},
    {'type':'File','channels':20,'format':'F32_LE','filename':'/dev/null'})
config=state/'file.yaml';config.write_text(yaml.safe_dump(factory(DEFAULT)))
process=subprocess.Popen(['/camilladsp','-a','127.0.0.1','-p','1234','--gain=-60','--mute',str(config)],stdin=subprocess.PIPE)
def feed():
    try:
        while process.poll() is None:process.stdin.write(b'\0'*8192);process.stdin.flush();time.sleep(.0213)
    except (OSError,ValueError):pass
threading.Thread(target=feed,daemon=True).start()
try:
    ready=False
    for _ in range(30):
        c=CamillaClient('127.0.0.1',1234)
        try:c.connect();ready=True;break
        except OSError:time.sleep(.1)
        finally:c.disconnect()
    if not ready:raise RuntimeError('private test DSP unavailable')
    atomic(state/'profile-store.json',{'active':DEFAULT,'known_good':DEFAULT})
    policy=Policy(state,lambda:CamillaClient('127.0.0.1',1234),factory,process)
    serve(policy,state/'control.sock',1000)
finally:
    if process.poll() is None:process.kill()
    process.wait(timeout=3)
