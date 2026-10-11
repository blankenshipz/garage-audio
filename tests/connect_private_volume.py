"""Actual root-owned metadata-file DAC test, synthetic integer/private path only."""
import json,os,tempfile
from pathlib import Path
from garage_audio import connect_port

assert os.geteuid()==0
root=Path(tempfile.mkdtemp(prefix='garage-private-volume-test.',dir='/root'))
connect_port.VOLUME_PATH=root/'volume.json' # isolated fixture, never production state
assert connect_port.saved_volume() is None
connect_port.save_volume(37)
assert connect_port.saved_volume()==37
assert connect_port.VOLUME_PATH.stat().st_mode&0o777==0o600
for invalid in (True,-1,101,37.0,'37'):
    try:connect_port.save_volume(invalid)
    except PermissionError:pass
    else:raise AssertionError('unbounded/noninteger metadata admitted')
connect_port.VOLUME_PATH.chmod(0o644)
try:connect_port.saved_volume()
except PermissionError:pass
else:raise AssertionError('nonprivate metadata accepted')
print(json.dumps({'root_owned_private_metadata_roundtrip':True,'invalid_metadata_denied':True,'wrong_DAC_denied':True,'value':'synthetic integer, not user choice','production_config_or_volume_changed':False,'receipt':str(root)}))
