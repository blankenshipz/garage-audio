"""Real Application/runtime journal + pinned validator; response fixtures SYNTHETIC.

The transport bridge is isolated unit integration, not hardware/socket acceptance.
"""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from garage_audio.application import Application
from garage_audio.audio_container import Worker
from garage_audio import audio_public
from measurement_workflow import fixture


class Lifecycle(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); root = Path(self.tmp.name)
        self.app = Application(root/'app'); self.worker = Worker(root/'worker',root/'evidence')
        self.bridge = patch.object(audio_public,'request',side_effect=lambda op,args=None:self.worker.configurations.execute(op,args or {})); self.bridge.start()
        self.sid = self.op('calibration.plan',name='SYNTHETIC lifecycle',channels=['left'])['result']['id']
        self.assertTrue(self.op('measurements.import',session_id=self.sid,**fixture())['ok'])
        self.assertTrue(self.op('calibration.analyze',id=self.sid)['ok'])
        self.assertTrue(self.op('calibration.propose',id=self.sid)['ok'])
    def tearDown(self):
        self.bridge.stop(); self.worker.closed.set(); self.worker.halt('test_exit'); self.tmp.cleanup()
    def op(self,op,**args): return self.app.execute(op,args)
    def prepare(self):
        result = self.op('calibration.correction.prepare',id=self.sid)
        self.assertTrue(result['ok'],result); self.assertEqual(result['result']['source'],'synthetic')
        return result
    def test_persistent_apply_next_play_and_rollback_after_inputs_change(self):
        self.prepare()
        result = self.op('calibration.correction.apply',id=self.sid); self.assertTrue(result['ok'],result)
        self.assertEqual(result['result']['transaction']['state'],'accepted_for_next_play')
        self.assertIsNone(result['result']['runtime']['loaded']); self.assertEqual(self.worker.children,[])
        self.assertFalse(self.op('calibration.correction.apply',id=self.sid)['ok'])
        denied = self.op('calibration.correction.verify',id=self.sid)
        self.assertEqual(denied['error']['code'],'paired_capture_required')
        reopened = Application(self.app.root)
        self.assertEqual(reopened.execute('calibration.correction.get',{'id':self.sid})['result']['transaction']['state'],'accepted_for_next_play')
        self.op('measurements.import',session_id=self.sid,**fixture())
        self.assertFalse(self.op('calibration.correction.get',id=self.sid)['result']['inputs_current'])
        self.assertTrue(self.op('calibration.cancel',id=self.sid)['ok'])
        rolled = self.op('calibration.correction.rollback',id=self.sid)
        self.assertTrue(rolled['ok'],rolled)
        self.assertNotIn('correction',rolled['result']['runtime']['configuration'])
    def test_post_commit_journal_failure_can_rollback_without_replay(self):
        self.prepare(); save = self.app.save; count = [0]
        def interrupted(*args):
            count[0] += 1
            if count[0] == 2: raise OSError('SYNTHETIC post-commit journal failure')
            return save(*args)
        with patch.object(self.app,'save',side_effect=interrupted):
            self.assertFalse(self.op('calibration.correction.apply',id=self.sid)['ok'])
        self.assertEqual(self.worker.configurations.state['revision'],1)
        self.assertEqual(self.op('calibration.correction.get',id=self.sid)['result']['transaction']['state'],'apply_requested')
        self.assertFalse(self.op('calibration.correction.apply',id=self.sid)['ok'])
        self.assertTrue(self.op('calibration.correction.rollback',id=self.sid)['ok'])
    def test_hardware_preparation_and_synthetic_capture_promotion_denied(self):
        actual = self.worker.configurations.get()
        with patch.object(audio_public,'request',return_value=dict(actual,binding={'kind':'hardware'})):
            denied = self.op('calibration.correction.prepare',id=self.sid)
        self.assertEqual(denied['error']['code'],'hardware_authority_required')
        with patch.object(self.app.jobs,'_read',return_value={'state':'completed','adapter':'file-null','manifest':{},'evidence':{'source':'synthetic'}}):
            denied = self.op('calibration.capture.import',job_id='b'*32)
        self.assertEqual(denied['error']['code'],'qualified_capture_required')


if __name__ == '__main__': unittest.main()
