"""Actual quiet Camilla FILE preflight code; synthetic DeviceLease/network only, no ALSA."""
import array,json,os
from pathlib import Path
from unittest.mock import patch
from garage_audio import audio_zero_check

class SyntheticLease:
 def __init__(self):self.fd=os.open('/dev/null',os.O_RDONLY)
 def verify(self):return 'FILE_FIXTURE_NO_DEVICE'
 def close(self):os.close(self.fd)
def file_graph(graph,_):
 graph['devices']['playback']['filename']='/evidence/zero-output.f32'
 return graph
os.environ.update(GARAGE_AUDIO_NETWORK_LEASE='/not-a-live-lease',GARAGE_AUDIO_NETWORK_GENERATION='synthetic-file-preflight')
with patch('garage_audio.audio_device.DeviceLease',SyntheticLease),patch('garage_audio.audio_device.hardware_graph',file_graph),patch('garage_audio.audio_zero_check.network_valid',return_value=True):
 audio_zero_check.main()
v=json.loads(Path('/evidence/zero-preflight.json').read_text())
assert v['sound_authority'] is False and v['muted'] is True and v['volume_db']==-60
raw=Path('/evidence/zero-output.f32').read_bytes()
assert raw and not any(raw)
print('PASS actual Camilla config/mute/-60/readback/exit; synthetic DeviceLease/network; no hardware',flush=True)
