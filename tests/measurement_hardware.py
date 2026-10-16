"""SYNTHETIC microphone/executable fixtures only; no sound or device opens.

Actual job guardian, lifetime pipe, processes and reaping are exercised on Linux.
Synthetic fixtures are installed only inside this test's private subprocesses.
"""
import array
import contextlib
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from garage_audio import measurement_hardware as hardware
from garage_audio.measurement_signal import read_wav
from garage_audio.measurement_acoustics import analyze
from garage_audio import measurement_correction, audio_config

CAL = b'20 0\n1000 0\n'
BOUND = {'policy': {'microphone': {'serial': 'SYNTHETIC-MIC', 'alsa_card': 'SYNTHETIC', 'channels': 1, 'channel': 0, 'orientation': '90'},
                   'outputs': {'left': 3}, 'peak_dbfs': -60, 'calibration_sha256': hashlib.sha256(CAL).hexdigest()},
         'fixture': 'SYNTHETIC-NO-HARDWARE'}
SELF = str(Path(__file__).resolve())
REAL_POPEN = subprocess.Popen
SNAPSHOT = {'binding':{'kind':'hardware','revision':'SYNTHETIC-binding'},'revision':0,
            'configuration':dict(audio_config.DEFAULT),'compiler_revision':audio_config.COMPILER,'generation':'a'*32}


class FixtureApplication:
    """Minimal synthetic app store, with real Jobs and HumanVerifier."""
    human_approval_available = True
    human_approval_unavailable_reason = None
    def __init__(self, root, verifier):
        from garage_audio.human_authority import HumanVerifier
        from garage_audio.measurement_jobs import Jobs
        self.root = root; root.mkdir(mode=0o700)
        self.humans = HumanVerifier(root, reference=str(verifier)); self.jobs = Jobs(self, self.humans)
        self.session = {'id':'a'*32,'state':'planned','plan': {'target':'server-umik-umc', 'sample_rate':48000,
            'orientation':'90','positions':['seat'],'channels':['left']}}
    def read(self, collection, identifier):
        if collection != 'sessions' or identifier != self.session['id']: raise ValueError('fixture session mismatch')
        return self.session
    def execute(self, operation, args):
        from garage_audio.measurement_jobs import JobFailure
        try:
            if operation == 'calibration.plan': value = self.session
            else: value = getattr(self.jobs, operation.split('.')[1])(**args) if operation == 'jobs.request' else getattr(self.jobs,operation.split('.')[1])(args['id'])
            return {'ok':True, 'result':value}
        except JobFailure as error: return {'ok':False,'error':{'code':error.code}}


def provision(directory):
    directory.mkdir(mode=0o700)
    password = 'synthetic-fixture-'+os.urandom(16).hex(); salt = os.urandom(16)
    value = {'user':'operator','realm':'synthetic-fixture','version':1,'salt':salt.hex(),'iterations':200000,
             'password_hash':hashlib.pbkdf2_hmac('sha256',password.encode(),salt,200000).hex()}
    path = directory/'verifier.json'; path.write_text(json.dumps(value)); path.chmod(0o600)
    return path,password


class FixtureLease:
    def __init__(self): self.fd = os.open('/dev/null', os.O_RDONLY)
    def verify(self): return 'hw:CARD=SYNTHETIC,DEV=0'
    def close(self): os.close(self.fd)


def fixture_spawn(argv, *args, **kwargs):
    if len(argv) > 2 and argv[1:3] == ['-m', 'garage_audio.measurement_runner']:
        argv = [sys.executable, SELF, '--fixture', *argv[3:]]
    elif argv[0] in ('/usr/bin/arecord', '/usr/bin/aplay'):
        argv = [sys.executable, SELF, '--recorder' if argv[0].endswith('arecord') else '--player', sys.argv[2]]
    return REAL_POPEN(argv, *args, **kwargs)


def fixture_main():
    mode = sys.argv[1]
    if mode == '--fixture':
        sys.argv = [sys.argv[0], *sys.argv[2:]]
        from garage_audio import measurement_runner
        def synthetic_analysis(*args, **kwargs):
            result = analyze(*args, **kwargs)
            result['source'] = 'synthetic'
            result['warnings'].append('SYNTHETIC executable fixture; no real microphone or sound')
            return result
        # Guardian dispatch remains real. Only physical endpoints are fixtures.
        with patch('subprocess.Popen', side_effect=fixture_spawn), patch.object(hardware, 'admission', return_value=BOUND), patch.object(measurement_correction,'snapshot',return_value=SNAPSHOT), patch.object(hardware, 'analyze', side_effect=synthetic_analysis), patch('garage_audio.audio_device.DeviceLease', FixtureLease):
            measurement_runner.main()
    elif mode == '--player':
        sys.stdin.buffer.read(); time.sleep(.4)
    elif mode == '--recorder':
        directory = Path(sys.argv[2]); manifest = json.loads((directory/'manifest.json').read_text()); spec = manifest['signal']
        raw = (directory/'output.raw').read_bytes()
        emitted = [int.from_bytes(raw[i+6:i+9],'little',signed=True)/8388608 for i in range(0,len(raw),36)]
        values = [0.]*9600+[v*.5 for v in emitted]+[0.]*14400
        data = array.array('i', (int(v*2147483648) for v in values))
        if sys.byteorder != 'little': data.byteswap()
        raw = data.tobytes()
        for start in range(0,len(raw),4096):
            sys.stdout.buffer.write(raw[start:start+4096]); sys.stdout.buffer.flush(); time.sleep(.015)


class HardwareTests(unittest.TestCase):
    def test_missing_policy_is_readonly_prerequisite(self):
        with patch.object(hardware, 'POLICY', Path('/nonexistent-measurement-policy')):
            self.assertFalse(hardware.readiness()['hardware_capture_available'])

    def test_changed_binding_never_acquires_devices_or_starts_output(self):
        with patch.object(hardware, 'admission', return_value={'changed': True}), patch('garage_audio.audio_device.DeviceLease') as lease, patch('subprocess.Popen') as spawn:
            with self.assertRaisesRegex(ValueError, 'binding changed'):
                hardware.capture(Path('/never-opened'), {'hardware_admission': BOUND, 'signal': {}}, lambda: None)
            lease.assert_not_called(); spawn.assert_not_called()

    def test_latency_calibration_and_rejection(self):
        import math
        rate = 48000
        # Short swept waveform with silence, delayed by 2400 samples.
        x = [0.]*9600+[.001*math.sin(2*math.pi*(30*i/rate+400*(i/rate)**2)) for i in range(12000)]+[0.]*2400
        y = [0.]*2400+[v*.5 for v in x]+[0.]*2400
        result = analyze(x,y,rate,[30,200],[{'frequency_hz':20,'db':2},{'frequency_hz':1000,'db':2}])
        self.assertTrue(result['supported_magnitude'],result['checks'])
        self.assertEqual(result['latency_samples_estimate'],2400)
        self.assertFalse(result['timing_verified']); self.assertIsNone(result['clock_drift_ppm'])
        self.assertAlmostEqual(result['points'][60]['db'], -8.0206, delta=.2)
        silent = analyze(x,[0.]*len(y),rate,[30,200],[{'frequency_hz':20,'db':0},{'frequency_hz':1000,'db':0}])
        self.assertFalse(silent['supported_magnitude'])

    @unittest.skipUnless(sys.platform.startswith('linux'), 'Linux real guardian fixture required')
    def test_exact_approval_real_children_capture_cancel_and_deadline(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); verifier,password = provision(root/'human')
            app = FixtureApplication(root/'state',verifier)
            sid = app.execute('calibration.plan', {'name':'SYNTHETIC hardware-adapter test','channels':['left']})['result']['id']
            with patch.object(hardware,'admission',return_value=BOUND), patch.object(hardware,'immutable',return_value=CAL), patch.object(measurement_correction,'snapshot',return_value=SNAPSHOT), patch('subprocess.Popen',side_effect=fixture_spawn):
                for action in ('complete', 'cancel', 'deadline'):
                    app.jobs.test_deadline = .35 if action == 'deadline' else None
                    result = app.execute('jobs.request', {'session_id':sid,'position':'seat','channel':'left','adapter':'hardware'})
                    self.assertTrue(result['ok'],result); job = result['result']; jid = job['id']
                    self.assertFalse(app.execute('jobs.execute',{'id':jid})['ok'])
                    identity = app.humans.authenticate('operator',password,'synthetic-'+action)
                    app.jobs.approve(jid,job['manifest_sha256'],identity)
                    self.assertTrue(app.execute('jobs.execute',{'id':jid})['ok'])
                    if action == 'cancel': time.sleep(.25); app.jobs.cancel(jid)
                    until = time.monotonic()+28
                    while jid in app.jobs.children and time.monotonic()<until: time.sleep(.02)
                    self.assertNotIn(jid,app.jobs.children)
                    done = app.jobs.get(jid)
                    reference = os.environ.get('GARAGE_TEST_RECEIPT_DIR')
                    if reference:
                        from measurement_job_receipts import retain_jobs
                        retain_jobs(app,'synthetic-hardware-'+action)
                    self.assertTrue(done['owned_child_exit_verified'],done)
                    self.assertTrue(done['descendant_reaping']['owned_group_children_reaped'])
                    directory = app.jobs.directory(jid)
                    if action == 'complete':
                        self.assertEqual(done['state'],'completed',done)
                        self.assertTrue(done['evidence']['supported_magnitude'])
                        self.assertEqual(done['evidence']['source'], 'synthetic')
                        raw = (directory/'output.raw').read_bytes()
                        self.assertTrue(any(raw[6::36]))
                        self.assertTrue(all(raw[i] == 0 for i in range(len(raw)) if i%36 not in (6,7,8)))
                    elif action == 'cancel': self.assertEqual(done['state'],'cancelled')
                    else: self.assertEqual(done['state'],'failed')
                    self.assertFalse(app.execute('jobs.execute',{'id':jid})['ok'])


if __name__ == '__main__':
    if len(sys.argv)>1 and sys.argv[1].startswith('--'): fixture_main()
    else: unittest.main()
