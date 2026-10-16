"""Real worker/Camilla FILE render; synthetic capture process, no device or network.

Transaction fault injection below is isolated unit coverage, not live hardware proof.
"""
import array
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from garage_audio.audio_config import DEFAULT
from garage_audio.audio_container import Worker

ROOT = Path(os.environ.get('CONFIG_EVIDENCE', '/tmp'))


class RenderTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix='config-render-', dir=ROOT))
        self.worker = Worker(self.root, self.root/'worker')
        self.store = self.worker.configurations

    def tearDown(self):
        self.worker.closed.set()
        self.worker.halt('test_exit')

    def proposal(self, **changes):
        result = self.store.execute('audio.config.propose', {'configuration': dict(DEFAULT, **changes)})
        return {'proposal_id': result['proposal_id']}

    def validated(self, **changes):
        args = self.proposal(**changes)
        self.store.execute('audio.config.validate', args)
        return args

    def render(self, stop=True):
        # Explicit synthetic capture executable replaces only pw-record in this
        # isolated process PATH. Worker child ownership and real Camilla are intact.
        fixture = self.root/'pw-record'
        fixture.write_text('#!' + sys.executable + '\nimport math,struct,sys\n'
            'data=bytearray()\nfor n in range(48000):\n'
            ' s=sum(math.sin(2*math.pi*f*n/48000) for f in (20,80,320))\n'
            ' data.extend(struct.pack("ff",.04*s,.02*s+.03*math.sin(2*math.pi*640*n/48000)))\n'
            'while True:\n sys.stdout.buffer.write(data)\n sys.stdout.buffer.flush()\n')
        fixture.chmod(0o700)
        with patch.dict(os.environ, {'PATH': str(self.root)+':'+os.environ['PATH']}):
            self.worker.last_observation = time.monotonic()
            self.worker.start()
        output = self.worker.evidence/f'{self.worker.generation}-{self.worker.segment}.f32'
        deadline = time.monotonic()+2.5
        while (not output.exists() or output.stat().st_size < 60000*12*4) and time.monotonic()<deadline:
            time.sleep(.01)
        self.assertTrue(output.exists(), 'real DSP output missing')
        diagnostic = {p.name: p.read_text()[:4000] for p in self.worker.evidence.glob('*.stderr')}
        self.assertGreaterEqual(output.stat().st_size, 60000*12*4,
                                f'owned child exits={[p.poll() for p in self.worker.children]}; diagnostics={diagnostic}')
        self.assertEqual(self.store.execute('audio.config.get', {})['loaded'], self.store.state['configuration'])
        if stop: self.worker.halt('render_collected')
        data = array.array('f')
        data.frombytes(output.read_bytes()[:60000*12*4])
        if sys.byteorder != 'little': data.byteswap()
        return [data[12000*12+c:60000*12:12] for c in range(12)]

    def amplitude(self, samples, frequency):
        re = sum(v*math.cos(2*math.pi*frequency*n/48000) for n,v in enumerate(samples))
        im = sum(v*math.sin(2*math.pi*frequency*n/48000) for n,v in enumerate(samples))
        return 2*math.hypot(re,im)/len(samples)

    def test_actual_stereo_and_21_response(self):
        gain = 10**(-6/20)
        stereo = self.render()
        for c in set(range(12))-{2,3}: self.assertEqual(max(map(abs, stereo[c])), 0)
        for c, scale in [(2,.04),(3,.02)]:
            for f in (20,80,320): self.assertAlmostEqual(self.amplitude(stereo[c],f),scale*gain,delta=1e-6)
        self.assertLess(self.amplitude(stereo[2],640),1e-6)
        self.assertAlmostEqual(self.amplitude(stereo[3],640),.03*gain,delta=1e-6)
        args = self.validated(layout='powered-sub-2.1', left=7, right=8, sub=9)
        self.store.execute('audio.config.apply',args)
        self.assertEqual(self.worker.status()['outputs'],[7,8,9])
        from garage_audio.audio_public import _status
        self.assertEqual(_status(self.worker.status())['outputs'],[7,8,9])
        rendered = self.render()
        for c in set(range(12))-{6,7,8}: self.assertEqual(max(map(abs, rendered[c])),0)
        measurements = []
        for f in (20,80,320):
            r = math.tan(math.pi*f/48000)/math.tan(math.pi*80/48000)
            hp, lp = r**4/(1+r**4), 1/(1+r**4)
            for c, expected in [(6,.04*gain*hp),(7,.02*gain*hp),(8,.03*gain*lp)]:
                measured = self.amplitude(rendered[c],f)
                error_db = 20*math.log10(measured/expected)
                self.assertLess(abs(error_db),.08)
                measurements.append({'output':c+1,'hz':f,'amplitude':measured,'expected':expected,'error_db':error_db})
        (self.root/'measurements.json').write_text(json.dumps(measurements,indent=2))
        print('REAL_FILE_MEASUREMENTS',json.dumps(measurements),flush=True)
        print('RETAINED_FILE_ARTIFACTS',self.root,flush=True)

    def test_stop_during_validation_and_failed_validation(self):
        args = self.proposal(layout='powered-sub-2.1',sub=5)
        def stopped(*a, **k):
            self.worker.halt('public_silence_only')
            return subprocess.CompletedProcess(a,0,b'',b'')
        with patch('garage_audio.audio_config.subprocess.run',side_effect=stopped):
            with self.assertRaisesRegex(ValueError,'cancelled by Stop'): self.store.execute('audio.config.validate',args)
        self.assertFalse(self.store.proposals[args['proposal_id']]['validated'])
        with patch('garage_audio.audio_config.subprocess.run',return_value=subprocess.CompletedProcess([],1,b'',b'injected validator failure')):
            with self.assertRaisesRegex(ValueError,'validation failed'): self.store.execute('audio.config.validate',args)
        self.assertEqual(self.store.state['revision'],0)
        self.assertEqual(self.worker.children,[])

    def test_actual_cut_correction_next_start_restart_and_rollback(self):
        args = self.validated(layout='powered-sub-2.1', sub=5)
        self.store.execute('audio.config.apply', args)
        baseline = self.render()
        correction = {'left': [{'freq': 80, 'q': 2, 'gain': -6}], 'right': [],
                      'sub': [{'freq': 80, 'q': 2, 'gain': -6}]}
        args = self.validated(layout='powered-sub-2.1', sub=5, correction=correction)
        self.store.execute('audio.config.apply', args)
        from garage_audio.audio_config import Configurations
        self.worker.configurations = self.store = Configurations(self.worker)
        rendered = self.render()
        ratio = self.amplitude(rendered[2], 80)/self.amplitude(baseline[2], 80)
        self.assertAlmostEqual(20*math.log10(ratio), -6, delta=.08)
        sub_ratio = self.amplitude(rendered[4], 80)/self.amplitude(baseline[4], 80)
        self.assertAlmostEqual(20*math.log10(sub_ratio), -6, delta=.08)
        for c in set(range(12))-{2,3,4}: self.assertEqual(max(map(abs, rendered[c])), 0)
        self.assertAlmostEqual(self.amplitude(rendered[3], 80), self.amplitude(baseline[3], 80), delta=1e-6)
        args = self.validated(layout='powered-sub-2.1', sub=5)
        self.store.execute('audio.config.rollback', args)
        rolled = self.render()
        self.assertAlmostEqual(self.amplitude(rolled[2], 80), self.amplitude(baseline[2], 80), delta=1e-6)

    def test_stop_during_slow_commit(self):
        args = self.validated(layout='powered-sub-2.1',sub=5)
        entered, release = threading.Event(), threading.Event()
        errors = []
        real_fsync = os.fsync
        def slow(fd):
            entered.set()
            if not release.wait(3): raise RuntimeError('test barrier timeout')
            real_fsync(fd)
        def apply():
            try: self.store.execute('audio.config.apply',args)
            except Exception as error: errors.append(error)
        with patch('garage_audio.audio_config.os.fsync',side_effect=slow):
            thread = threading.Thread(target=apply)
            thread.start()
            self.assertTrue(entered.wait(2))
            start = time.monotonic()
            self.worker.halt('public_silence_only')
            elapsed = time.monotonic()-start
            release.set()
            thread.join(3)
        self.assertFalse(thread.is_alive())
        self.assertLess(elapsed,.2)
        self.assertRegex(str(errors[0]),'cancelled by Stop')
        self.assertEqual(self.store.state['revision'],0)
        self.assertFalse(self.store.path.exists())
        self.assertEqual(self.worker.children,[])
        print('STOP_DURING_COMMIT_SECONDS',elapsed,flush=True)

    def test_failed_install_and_durable_commit_leave_previous_silent(self):
        args = self.validated(headroom_db=-9)
        self.store.execute('audio.config.apply',args)
        previous = self.store.path.read_bytes()
        for target in ('os.fsync','os.replace'):
            args = self.validated(layout='powered-sub-2.1',sub=5)
            self.render(stop=False)
            owned = list(self.worker.children)
            self.assertTrue(owned)
            with patch('garage_audio.audio_config.'+target,side_effect=OSError('injected commit failure')):
                with self.assertRaises(OSError): self.store.execute('audio.config.apply',args)
            self.assertEqual(self.store.path.read_bytes(),previous)
            self.assertEqual(self.store.state['configuration'],dict(DEFAULT,headroom_db=-9))
            self.assertEqual(self.worker.children,[])
            self.assertTrue(all(child.poll() is not None for child in owned))
            self.assertEqual(self.worker.state,'off')


if __name__ == '__main__': unittest.main()
