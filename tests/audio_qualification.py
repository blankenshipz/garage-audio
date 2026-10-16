"""Synthetic lifecycle/ledger checks on real Linux files, no receiver or PCM."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from garage_audio.audio_qualification import Qualification, atomic, issue
from garage_audio.audio_container import Worker


class Ledger(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.root.chmod(0o755)
        self.store = self.root/'ledger'
        self.policy = self.root/'policy'/'r15.json'
        self.attempt = issue(self.policy, self.store)
        self.q = Qualification(self.policy, self.store)

    def tearDown(self):
        self.temp.cleanup()

    def worker(self, mode):
        lease = self.root/'lease'
        lease.write_text('{}');lease.chmod(0o444)
        w = Worker(self.root/'run', self.root/'evidence', mode, lease, 'synthetic', self.policy, self.store)
        w.network_valid = lambda: True
        self.addCleanup(w.closed.set)
        self.addCleanup(w.halt, 'synthetic_shutdown')
        if w.scalar:self.addCleanup(w.scalar.close)
        return w

    def test_first_admission_pause_restart_and_expiry(self):
        self.assertIsNone(self.q.read()['started'])
        first = self.q.admit_segment()['started']
        q2 = Qualification(self.policy, self.store)
        self.assertEqual(q2.admit_segment()['started'], first)
        with patch('garage_audio.audio_qualification.time.monotonic', return_value=first+301):
            self.assertFalse(Qualification(self.policy, self.store).valid())
            with self.assertRaises(PermissionError):q2.admit_segment()
            q2.consume()
        self.assertFalse(Qualification(self.policy, self.store).valid())

    def test_actual_worker_uid_primary_gid_can_persist(self):
        code='import os;os.setgid(1003);os.setuid(1002);from garage_audio.audio_qualification import Qualification;q=Qualification('+repr(str(self.policy))+','+repr(str(self.store))+');q.admit_segment();q.consume()'
        p=subprocess.run(['python','-c',code],capture_output=True)
        self.assertEqual(p.returncode,0,p.stderr.decode()[:500])
        self.assertEqual(self.q.read()['segments'],1)
        self.assertTrue(self.q.read()['terminal'])

    def test_consumed_across_worker_namespace_candidate_and_modes(self):
        self.q.consume()
        w = self.worker('standby')
        self.assertTrue(w.status()['qualification_terminal'])
        self.assertEqual(w.status()['owned_children'], [])
        self.assertFalse(w.status()['device_open'])
        with patch('garage_audio.audio_container.subprocess.Popen', side_effect=AssertionError('no PCM')):
            with self.assertRaises(PermissionError):w.start()
            with self.assertRaises(PermissionError):w.start_owned(None)
            event = dict(epoch='future', sequence=1, kind='baseline', state='playing', active=True, logged_in=True, volume=100)
            w.event(event)
            for kind in ('prepare', 'delta'):
                with self.assertRaises(PermissionError):w.event(dict(event, sequence=2, kind=kind))
        self.assertIsNone(w.pending_ticket)
        self.assertEqual(w.state, 'off')
        self.assertFalse(w.closed.is_set())
        with self.assertRaises(PermissionError):
            Worker(self.root/'other', self.root/'ev2', 'file', qualification_policy=self.policy, qualification_store=self.store)
        with self.assertRaises(PermissionError):
            Worker(self.root/'other', self.root/'ev2', 'hardware', self.root/'lease', 'other', self.policy, self.store)
        scalar = self.worker('scalar')
        self.assertTrue(scalar.scalar_terminal)
        with self.assertRaises(RuntimeError):scalar.start()
        self.assertFalse(scalar.closed.is_set())

    def test_expiry_keeps_service_alive_and_halts_before_persistence(self):
        self.q.admit_segment()
        w = self.worker('scalar')
        entered = threading.Event();release = threading.Event()
        def held():
            entered.set();release.wait(2)
        with patch.object(w.qualification, 'consume', held):
            w.scalar_started = time.monotonic()-301
            self.assertTrue(entered.wait(1))
            self.assertTrue(w.scalar_terminal)
            self.assertEqual(w.state, 'off')
            self.assertFalse(w.closed.is_set())
            self.assertIsNone(w.pending_ticket)
            self.assertEqual(w.status()['owned_children'], [])
            release.set()

    def test_held_durable_admission_does_not_block_terminal_halt(self):
        w=self.worker('scalar');w.last_observation=time.monotonic()
        entered=threading.Event();release=threading.Event();errors=[]
        original=w.qualification.admit_segment
        def held():
            saved=original();entered.set();release.wait(2);return saved
        def start():
            try:w.start()
            except RuntimeError:errors.append('revoked')
        with patch.object(w.qualification,'admit_segment',held),patch('garage_audio.audio_container.subprocess.Popen',side_effect=AssertionError('must not launch')):
            thread=threading.Thread(target=start);thread.start()
            self.assertTrue(entered.wait(1))
            w.scalar_started=time.monotonic()-301
            end=time.monotonic()+1
            while not w.scalar_terminal and time.monotonic()<end:time.sleep(.01)
            self.assertTrue(w.scalar_terminal)
            self.assertEqual(w.state,'off')
            release.set();thread.join(2)
        self.assertFalse(thread.is_alive());self.assertEqual(errors,['revoked'])

    def test_reboot_missing_corrupt_and_observer_write_denied(self):
        with patch('garage_audio.audio_qualification.boot_id', return_value='new-boot'):
            self.assertFalse(Qualification(self.policy, self.store).valid())
        denied = subprocess.run(['python','-c', 'import os;os.setgid(1001);os.setuid(1001);open("'+str(self.q.path)+'","w")'], capture_output=True)
        self.assertNotEqual(denied.returncode, 0)
        self.assertIn(b'PermissionError', denied.stderr)
        atomic(self.q.path, {'invalid':True}, 1002)
        with self.assertRaises(PermissionError):Qualification(self.policy, self.store)
        self.q.path.unlink()
        with self.assertRaises(FileNotFoundError):Qualification(self.policy, self.store)

    def test_no_implicit_reissue_and_segment_budget(self):
        with self.assertRaises(FileExistsError):issue(self.policy, self.store)
        with self.assertRaises(PermissionError):self.worker('standby')
        for _ in range(8):self.q.admit_segment()
        self.assertTrue(self.q.valid())
        with self.assertRaises(PermissionError):self.q.admit_segment()
        self.q.consume()
        original = self.q.path.read_bytes()
        new_policy = self.root/'policy'/'new.json'
        issue(new_policy, self.store)
        self.assertEqual(self.q.path.read_bytes(), original)
        self.assertIsNone(Qualification(new_policy, self.store).read()['started'])


if __name__ == '__main__':
    unittest.main()
