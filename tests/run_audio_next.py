"""Focused executable follow-up; synthetic cases and native config validation labeled."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
sys.path.insert(0, '/app')
sys.path.insert(0, '/fixtures')

suite = unittest.defaultTestLoader.loadTestsFromNames(sys.argv[1:]) if len(sys.argv)>1 else unittest.TestSuite([
    unittest.defaultTestLoader.discover('/fixtures', pattern='audio_pending.py'),
    unittest.defaultTestLoader.discover('/fixtures', pattern='audio_device_identity.py')])
result = unittest.TextTestRunner(verbosity=2).run(suite)
if not result.wasSuccessful():
    raise SystemExit(1)
from garage_audio.audio_device import hardware_graph
from garage_audio.audio_container import line_graph
import yaml
graph = hardware_graph(line_graph('/not-used.f32'), 'hw:CARD=UMC1820,DEV=0')
text = yaml.safe_dump(graph)
config = Path('/tmp/umc-validate-only.yaml')
config.write_text(text)
subprocess.run(['/opt/garage-audio/dsp/camilladsp', '--check', str(config)], check=True,
    env=dict(os.environ, LD_LIBRARY_PATH='/opt/garage-audio/dsp/lib'))
print('PASS native_Camilla_hardware_config_check_NO_DAC_OPEN', hashlib.sha256(text.encode()).hexdigest())
print('COVERAGE synthetic_receiver_MA_identity_failures; actual_Linux_flock_inheritance; native_config_check_only')
