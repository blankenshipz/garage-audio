"""Focused synthetic qualification. Never contacts MA/receiver or mounts hardware."""
import json
import math
import os
from pathlib import Path
import stat
import struct
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch

from garage_audio.audio_container import Worker, line_graph
from garage_audio.audio_receiver import AdvertisementCache, qualify_pairing
from garage_audio.audio_scalar import ScalarBank, Meter, WINDOW
from garage_audio.audio_qualification import issue


class ScalarTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def worker(self):
        lease = self.root/'authority.json'
        lease.write_text(json.dumps({'generation':'unit', 'healthy':True,
                         'tick':time.monotonic(), 'expires':time.monotonic()+999}))
        lease.chmod(0o444)
        policy=self.root/'policy'/'attempt.json';store=self.root/'ledger';issue(policy,store)
        w = Worker(self.root/'runtime', self.root/'evidence', 'scalar', lease, 'unit', policy, store)
        self.addCleanup(w.scalar.close)
        self.addCleanup(w.closed.set)
        return w

    def test_requires_generation(self):
        with self.assertRaises(ValueError):
            Worker(self.root/'run', self.root/'ev', 'scalar')

    def test_readers_ready_no_device_policy(self):
        with patch('garage_audio.audio_device.load_policy', side_effect=AssertionError('hardware forbidden')):
            w = self.worker()
        self.assertTrue(w.scalar.healthy())
        self.assertTrue(w.status()['scalar_readers_ready'])
        self.assertIsNone(w.device_lease)
        self.assertTrue(all(stat.S_ISFIFO(p.stat().st_mode) for p in w.scalar.root.glob('*.fifo')))

    def test_unknown_segment_never_file(self):
        w = self.worker()
        with self.assertRaises(RuntimeError):
            w.scalar.writer(9)
        self.assertFalse((w.scalar.root/'9.fifo').exists())
        self.assertFalse(list(w.evidence.glob('*.f32')))

    def test_pinned_fd_survives_path_replacement_without_regular_output(self):
        bank = ScalarBank(self.root, 'race')
        self.addCleanup(bank.close)
        writer = bank.writer(1)
        self.addCleanup(os.close, writer)
        path = bank.root/'1.fifo'
        path.unlink()
        path.write_bytes(b'unchanged sentinel')
        # Camilla uses only this inherited fd. Opening it cannot open the sentinel.
        reopened = os.open(f'/proc/self/fd/{writer}', os.O_WRONLY)
        try:
            self.assertTrue(stat.S_ISFIFO(os.fstat(reopened).st_mode))
            os.write(reopened, b'\0'*48)
        finally:
            os.close(reopened)
        self.assertEqual(path.read_bytes(), b'unchanged sentinel')
        self.assertFalse(bank.healthy())

    def test_reader_mutation_stops_worker(self):
        w = self.worker()
        (w.scalar.root/'1.fifo').unlink()
        self.wait_terminal(w)
        self.assertEqual(w.state, 'off')

    def test_budget_and_finite_dtype(self):
        meter = Meter(self.root/'meter.jsonl', 2, 'unit')
        self.addCleanup(meter.close)
        with self.assertRaises(ValueError):meter.feed(struct.pack('<ff', float('nan'), 0))
        with self.assertRaises(ValueError):meter.feed(b'\0'*8193)

    def test_scalar_frame_accounting(self):
        meter = Meter(self.root/'meter.jsonl', 2, 'unit')
        payload = struct.pack('<ff', .25, -.125)*(2*WINDOW)
        for i in range(0, len(payload), 733):meter.feed(payload[i:i+733])
        meter.close()
        rows = [json.loads(x) for x in (self.root/'meter.jsonl').read_text().splitlines()]
        self.assertEqual([r['frame_start'] for r in rows[:-1]], [0,WINDOW])
        self.assertEqual(rows[0]['peak'], [.25,.125])
        self.assertEqual(rows[-1]['discarded_residual_bytes'], 0)
        self.assertFalse(rows[0]['production_timestamp_proven'])

    def test_full_300_second_scalar_row_budget(self):
        # Conservative scalar-only bound; does not synthesize/store a media payload.
        for channels in (2,12):
            meter=Meter(self.root/f'budget-{channels}.jsonl',channels,'capture-before-dsp-read')
            windows=(48000*300+WINDOW-1)//WINDOW
            for window in range(windows):
                meter.record(dict(stage=meter.provenance,pid=2147483647,observed_ns=9999999999999999999,
                    frame_start=window*WINDOW,frames=WINDOW,channels=channels,rate=48000,format='F32_LE',
                    peak=[3.4028234663852886e38]*channels,sum_squares=[9.485687950320943e80]*channels,
                    nonzero=[WINDOW]*channels,first_byte_read_ns=9999999999999999999,
                    first_nonzero_read_ns=9999999999999999999,first_nonzero_frame=14400000,
                    pending_bytes=47,production_timestamp_proven=False))
                if channels==2:
                    meter.record(dict(stage='capture-forward-complete',pid=2147483647,
                        observed_ns=9999999999999999999,bytes_forwarded=14400000*8,
                        frame_bytes=8,pipe_backpressure_may_delay=True))
            meter.close()
            self.assertLess(meter.log_bytes,meter.log_limit//2)

    def test_deadline_never_reset_by_pause(self):
        w = self.worker()
        w.scalar_started = time.monotonic()-301
        w.halt('unit-pause', paused=True)
        self.wait_terminal(w)
        self.assertEqual(w.state, 'off')

    def wait_terminal(self, w):
        end=time.monotonic()+1
        while not w.scalar_terminal and time.monotonic()<end:time.sleep(.01)
        self.assertTrue(w.scalar_terminal)
        self.assertFalse(w.closed.is_set())

    def test_network_loss_closes_children(self):
        w = self.worker()
        w.baseline = True
        w.network_valid = lambda: False  # Isolated unit fault, never production observer.
        time.sleep(.1)
        self.assertFalse(w.baseline)
        self.assertEqual(w.state, 'off')

    def test_snapshot_does_not_start(self):
        w = self.worker()
        w.network_valid = lambda: True
        event = {'epoch':'unit', 'sequence':1, 'kind':'baseline', 'state':'playing',
                 'active':True, 'logged_in':True, 'volume':20}
        w.event(event)
        self.assertEqual(w.state, 'off')
        self.assertEqual(w.segment, 0)
        self.assertIsNone(w.scalar_started)

    def test_actual_owned_pipeline_stop_resume_exhaustion(self):
        if not Path('/opt/garage-audio/dsp/camilladsp').exists():
            self.skipTest('requires actual Linux cached audio')
        w = self.worker()
        w.network_valid = lambda: True  # Isolated synthetic test authority only.
        popen = subprocess.Popen
        def synthetic_capture(argv, **kwargs):
            if argv[1:4] == ['-m', 'garage_audio.audio_child', 'capture']:
                argv = [argv[0], '-c', 'import os,struct,time;os.write(1,struct.pack("<ff",.25,-.125)*8192);time.sleep(20)']
            return popen(argv, **kwargs)
        base = {'epoch':'test', 'sequence':0, 'kind':'baseline', 'state':'playing',
                'active':True, 'logged_in':True, 'volume':20}
        w.event(base)
        first_started = None
        try:
            with patch('garage_audio.audio_container.subprocess.Popen', side_effect=synthetic_capture):
                for segment in range(1,9):
                    prepared = w.event(dict(base, sequence=segment*3-2, kind='prepare'))
                    w.event(dict(base, sequence=segment*3-1, kind='delta', ticket=prepared['pending_ticket']))
                    if first_started is None:first_started = w.scalar_started
                    self.assertEqual(w.scalar_started, first_started)
                    self.assertEqual(len(w.children), 3)
                    launched = list(w.children)
                    deadline = time.monotonic()+2
                    output = w.scalar.root/f'{segment}.output.jsonl'
                    while output.stat().st_size == 0 and time.monotonic()<deadline:time.sleep(.02)
                    diagnostics = {str(p.name):p.read_text()[-700:] for p in w.evidence.rglob('*.stderr')}
                    diagnostics['exit_codes'] = [p.poll() for p in launched]
                    diagnostics['worker'] = w.status()
                    diagnostics['input_receipt_exists'] = (w.scalar.root/f'{segment}.input.jsonl').exists()
                    self.assertGreater(output.stat().st_size,0, diagnostics)
                    owned = list(w.children)
                    w.event(dict(base, sequence=segment*3, kind='delta', state='paused'))
                    self.assertTrue(all(child.poll() is not None for child in owned))
                    self.assertEqual(w.state,'paused')
                prepared = w.event(dict(base, sequence=25, kind='prepare'))
                with self.assertRaises(RuntimeError):
                    w.event(dict(base, sequence=26, kind='delta', ticket=prepared['pending_ticket']))
                self.wait_terminal(w)
            self.assertFalse(list(w.evidence.glob('*.f32')))
            records = [json.loads(x) for x in (w.scalar.root/'1.input.jsonl').read_text().splitlines()]
            self.assertTrue(any(r.get('bytes_forwarded',0)>0 for r in records))
        finally:w.halt('unit-finished')

    def test_hardware_mount_exclusion(self):
        real_exists = Path.exists
        with patch.object(Path, 'exists', lambda p: str(p)=='/dev/snd' or real_exists(p)):
            with self.assertRaises(PermissionError):self.worker()

    def test_actual_input_wrapper_forwards_all_bytes(self):
        env = dict(os.environ, GARAGE_AUDIO_PARENT_PID=str(os.getpid()))
        path = self.root/'input.jsonl'
        payload = struct.pack('<ff',.25,-.125)*2048
        import sys
        p = subprocess.Popen([sys.executable,'-m','garage_audio.audio_child','scalar-input',str(path)],
                             stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,env=env)
        output, error = p.communicate(payload,timeout=3)
        self.assertEqual(p.returncode,0,error.decode()[:500])
        self.assertEqual(output,payload)
        self.assertTrue(path.exists())

    def test_owned_child_loss_closes_pipeline(self):
        w = self.worker()
        w.network_valid = lambda: True
        w.last_observation = time.monotonic()
        w.state, w.deadline = 'playing', time.monotonic()+10
        a = subprocess.Popen(['sleep','20'])
        b = subprocess.Popen(['sleep','20'])
        w.children = [a,b]
        try:
            a.kill();a.wait(timeout=2)
            deadline = time.monotonic()+1
            while w.state!='off' and time.monotonic()<deadline:time.sleep(.01)
            self.assertEqual(w.state,'off')
            self.assertIsNotNone(b.poll())
            self.assertEqual(w.children,[])
        finally:w.halt('unit');b.kill() if b.poll() is None else None;b.wait(timeout=2)

    def test_parent_death_race_refuses_wrong_parent(self):
        import sys
        env = dict(os.environ,GARAGE_AUDIO_PARENT_PID='2147483647')
        p = subprocess.run([sys.executable,'-m','garage_audio.audio_child','scalar-input',str(self.root/'never.jsonl')],env=env,timeout=3)
        self.assertEqual(p.returncode,125)
        self.assertFalse((self.root/'never.jsonl').exists())

    def test_actual_parent_death_kills_input_meter(self):
        import sys
        path = self.root/'parent-death.jsonl'
        code = '''import os,sys,subprocess,time
r,w=os.pipe()
env=dict(os.environ,GARAGE_AUDIO_PARENT_PID=str(os.getpid()))
p=subprocess.Popen([sys.executable,'-m','garage_audio.audio_child','scalar-input',sys.argv[1]],stdin=r,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,pass_fds=(w,),env=env)
print(p.pid,flush=True)
time.sleep(20)
'''
        parent = subprocess.Popen([sys.executable,'-c',code,str(path)],stdout=subprocess.PIPE)
        try:
            pid = int(parent.stdout.readline())
            deadline = time.monotonic()+2
            while not path.exists() and time.monotonic()<deadline:time.sleep(.01)
            self.assertTrue(path.exists()) # Wrapper reached the actual meter exec.
            parent.kill();parent.wait(timeout=2)
            proc = Path('/proc')/str(pid)/'stat'
            deadline = time.monotonic()+2
            while proc.exists() and proc.read_text().split()[2]!='Z' and time.monotonic()<deadline:time.sleep(.01)
            self.assertTrue(not proc.exists() or proc.read_text().split()[2]=='Z')
            if proc.exists():self.assertEqual(int(proc.read_text().split()[-1]),9)
            # Child inherited its own pipe writer: EOF alone cannot explain this exit.
        finally:
            if parent.poll() is None:parent.kill();parent.wait(timeout=2)
            parent.stdout.close()

    def test_scalar_reader_failure_is_terminal(self):
        w = self.worker()
        w.scalar.failed.set()
        self.wait_terminal(w)
        self.assertEqual(w.state,'off')

    def test_private_fifo_mode_or_directory_mutation_fails_closed(self):
        bank = ScalarBank(self.root,'permissions')
        self.addCleanup(bank.close)
        path = bank.root/'1.fifo'
        path.chmod(0o660)
        self.assertFalse(bank.healthy())
        path.chmod(0o600)
        self.assertFalse(bank.healthy()) # Observed mutation is irrevocable.
        bank.root.chmod(0o750)
        self.assertFalse(bank.healthy())

    def test_actual_camilla_inherited_fifo_graph(self):
        import yaml
        binary = Path('/opt/garage-audio/dsp/camilladsp')
        if not binary.exists():self.skipTest('requires actual cached Linux audio image')
        bank = ScalarBank(self.root, 'camilla')
        self.addCleanup(bank.close)
        writer = bank.writer(1)
        config = self.root/'graph.yaml'
        config.write_text(yaml.safe_dump(line_graph(f'/proc/self/fd/{writer}')))
        env = dict(os.environ, LD_LIBRARY_PATH='/opt/garage-audio/dsp/lib')
        with (self.root/'camilla.stderr').open('wb') as log:
            p = subprocess.Popen([str(binary), str(config)], stdin=subprocess.PIPE,
                                 stdout=subprocess.DEVNULL, stderr=log, pass_fds=(writer,), env=env)
            os.close(writer)
            payload = struct.pack('<ff', .25, -.125)*8192
            p.communicate(payload, timeout=15)
        self.assertEqual(p.returncode, 0, (self.root/'camilla.stderr').read_text()[:500])
        time.sleep(.15)
        bank.close()
        rows = [json.loads(x) for x in (bank.root/'1.output.jsonl').read_text().splitlines() if 'frame_start' in x]
        self.assertTrue(rows)
        for row in rows:
            for c in (0,1,4,5,6,7,8,9,10,11):self.assertEqual(row['nonzero'][c],0)
        self.assertAlmostEqual(rows[0]['peak'][2]/.25, 10**(-6/20), places=6)
        self.assertAlmostEqual(rows[0]['peak'][3]/.125, 10**(-6/20), places=6)
        self.assertFalse(list(self.root.glob('*.f32')))

    def test_actual_fifo_drain_capacity(self):
        bank = ScalarBank(self.root, 'capacity')
        self.addCleanup(bank.close)
        writer = bank.writer(1)
        target = 12*48000*4*3
        block = b'\0'*8192
        total = 0
        started = time.monotonic()
        while total < target:
            total += os.write(writer, block[:min(len(block),target-total)])
        os.close(writer)
        elapsed = time.monotonic()-started
        rate = total/elapsed
        print(json.dumps({'synthetic_fifo_bytes':total,'elapsed':elapsed,'bytes_per_second':rate}))
        self.assertGreater(rate, 2.3*1024*1024)


class DiscoveryTests(unittest.TestCase):
    def test_receiver_birth_replacement_refuses_cached_advertisement(self):
        with patch('garage_audio.audio_receiver.birth',return_value='replacement'):
            with self.assertRaises(PermissionError):
                qualify_pairing({'namespace_inode':10,'pid':20,'birth':'original'},30,10,'192.0.2.1',[41000])

    def test_measured_ttl_expiry_and_removal(self):
        c = AdvertisementCache()
        c.update({'kind':'srv','id':'service','host':'host','port':41000,
                  'remaining_seconds':9,'observed':100},100)
        self.assertEqual(c.ports(100), [])
        c.update({'kind':'address','id':'host','remaining_seconds':3,'observed':100},100)
        self.assertEqual(c.ports(102), [41000])
        self.assertEqual(c.ports(103), [])
        c.update({'kind':'address','id':'host','remaining_seconds':8,'observed':103},103)
        self.assertEqual(c.ports(104), [41000])
        c.update({'kind':'remove','id':'service'},104)
        self.assertEqual(c.ports(104), [])

    def test_expired_queued_record_not_renewed_on_receive(self):
        c = AdvertisementCache()
        c.update({'kind':'address','id':'host','remaining_seconds':2,'observed':100},103)
        self.assertEqual(c.records,{})


if __name__ == '__main__':
    unittest.main(verbosity=2)
