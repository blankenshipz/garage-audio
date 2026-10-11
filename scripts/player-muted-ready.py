"""User-service startup only: exact supported mute/−60 IPC, no sound grant.

Do not forward argparse globals/legacy attendance parameters to native policy.
This helper accepts no arguments and has no unmute/profile/authority operation.
"""
from pathlib import Path
import sys

if len(sys.argv)!=1:raise ValueError('no readiness arguments allowed')
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from garage_audio.ipc import request

status=request({'cmd':'status'})
if not status['muted'] or status['volume_db']!=-60:
    raise RuntimeError('player startup requires already-muted/-60 native DSP')
request({'cmd':'mute'})
request({'cmd':'volume','db':-60})
status=request({'cmd':'status'})
if not status['muted'] or status['volume_db']!=-60:
    raise RuntimeError('player mute/-60 readback failed')
print('Native player startup muted/-60 verified; no sound authority.')
