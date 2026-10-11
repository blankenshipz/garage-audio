"""Real Camilla FILE/null process; isolated synthetic phone peer, no hardware."""
import threading
import unittest
import os
from pathlib import Path
from unittest.mock import patch
import native_playback as fixtures
fixtures.ROOT=Path(os.environ['GARAGE_FILE_TEST_PROJECT'])
fixtures.ENV={**os.environ,'LD_LIBRARY_PATH':str(fixtures.ROOT/'tools/libs/usr/lib/x86_64-linux-gnu')}

class PhoneGuardianTests(unittest.TestCase):
    def fixture(self):
        fixture=fixtures.NativeFileTests();fixture.setUp();self.addCleanup(fixture.doCleanups)
        fixture.peer.realm='phone'
        generation=fixture.policy.generation()
        fixture.policy.execute({'cmd':'phone-start','generation':generation},fixture.peer)
        status=fixture.policy.execute({'cmd':'initialize','generation':generation})
        self.assertTrue(status['preparation_zero']);self.assertTrue(status['muted']);self.assertEqual(status['volume_db'],-60)
        return fixture,status['generation']
    def test_prepare_controller_death_stops_actual_file_process(self):
        f,g=self.fixture();f.policy.execute({'cmd':'phone-prepare','generation':g},f.peer)
        # Dead helper guardian is independent even when DSP readback stalls.
        f.policy.client_factory=lambda:threading.Event().wait(10)
        f.helper.kill();f.helper.wait(timeout=2);f.process.wait(timeout=2)
    def test_phone_listen_deadline_stops_actual_file_process(self):
        f,g=self.fixture()
        with patch('garage_audio.broker.SECONDS',2):
            f.policy.execute({'cmd':'phone-prepare','generation':g},f.peer)
            result=f.policy.execute({'cmd':'phone-open','generation':g},f.peer)
            self.assertEqual(result['outputs'],[3,4]);self.assertEqual(result['ceiling_db'],-60)
            f.process.wait(timeout=3)
    def test_stop_bypasses_held_policy_lock(self):
        f,g=self.fixture();f.policy.execute({'cmd':'phone-prepare','generation':g},f.peer)
        held=threading.Event();release=threading.Event()
        def holder():
            with f.policy.lock:held.set();release.wait(5)
        thread=threading.Thread(target=holder);thread.start();self.assertTrue(held.wait(1))
        try:self.assertEqual(f.policy.execute({'cmd':'speaker-stop'}),{'stopped':True})
        finally:release.set();thread.join(2)
    def test_cli_command_cannot_use_phone_realm(self):
        f,g=self.fixture();f.policy.execute({'cmd':'phone-prepare','generation':g},f.peer)
        with self.assertRaises(PermissionError):f.policy.execute({'cmd':'stereo-open','generation':g},f.peer)

    def test_owner_death_during_initialize_reaps_real_file_dsp_before_lock_release(self):
        f=fixtures.NativeFileTests();f.setUp();self.addCleanup(f.doCleanups);f.peer.realm='phone'
        g=f.policy.generation();f.policy.execute({'cmd':'phone-start','generation':g},f.peer)
        entered=threading.Event();release=threading.Event();failures=[]
        from garage_audio.control import apply_graph
        def stalled(*args,**kwargs):
            apply_graph(*args,**kwargs) # actual zero-output FILE graph active
            entered.set();release.wait(5);raise InterruptedError('isolated initialization cancelled')
        def initialize():
            try:f.policy.execute({'cmd':'initialize','generation':g})
            except Exception as e:failures.append(type(e).__name__)
        with patch('garage_audio.broker.apply_graph',stalled):
            thread=threading.Thread(target=initialize);thread.start();self.assertTrue(entered.wait(2))
            try:
                f.helper.kill();f.helper.wait(timeout=2);f.process.wait(timeout=2)
                self.assertTrue(thread.is_alive()) # native guardian did not need init lock
            finally:release.set();thread.join(3)
        self.assertTrue(failures)
    def test_early_start_deadline_reaps_real_file_process_without_prepare(self):
        f=fixtures.NativeFileTests();f.setUp();self.addCleanup(f.doCleanups);f.peer.realm='phone'
        with patch('garage_audio.broker.SECONDS',.4):
            f.policy.execute({'cmd':'phone-start','generation':f.policy.generation()},f.peer)
        f.process.wait(timeout=2)

if __name__=='__main__':unittest.main()
