"""Read-only readiness for the owned graph, before policy/player startup.

Type=simple orders process launch, not readiness. Never opens ALSA or loads a
default graph. Unit environment supplies the isolated runtime/name explicitly.
"""
import os
import subprocess
import time

if os.environ.get('PIPEWIRE_REMOTE')!='garage-pipewire':
    raise RuntimeError('explicit project core required')
deadline=time.monotonic()+15
while time.monotonic()<deadline:
    try:result=subprocess.run(['/usr/bin/pw-cli','info','0'],capture_output=True,text=True,timeout=2)
    except subprocess.TimeoutExpired:result=None
    if result and result.returncode==0 and 'name: "garage-pipewire"' in result.stdout:
        print('Project PipeWire core ready (metadata only).')
        break
    time.sleep(.1)
else:raise RuntimeError('project PipeWire core readiness deadline')
