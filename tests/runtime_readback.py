"""Synthetic status-only IPC fixture; no worker update, devices or sound."""
import tempfile
import unittest
from unittest.mock import patch
from garage_audio.application import Application
from garage_audio import audio_public


class Readback(unittest.TestCase):
    def test_status_only_is_unknown_configuration_and_readable_lifecycle(self):
        status={'device_mode':'hardware','state':'paused','outputs':[3,4],'headroom_db':-6}
        calls=[]
        def legacy(operation,args=None):
            calls.append(operation)
            if operation=='audio.config.get':raise audio_public.AudioFailure('audio_operation_rejected','SYNTHETIC allowlist rejection')
            if operation=='audio.status':return status
            raise AssertionError('No mutation permitted')
        with tempfile.TemporaryDirectory() as root,patch.object(audio_public,'request',side_effect=legacy):
            app=Application(root);sid=app.execute('calibration.plan',{'name':'SYNTHETIC legacy readback','channels':['left']})['result']['id']
            result=app.execute('audio.config.get',{})
            self.assertTrue(result['ok'],result);value=result['result']
            self.assertFalse(value['configuration_readback_available'])
            for field in ('configuration','revision','desired','accepted_for_next_play','loaded'):self.assertIsNone(value[field])
            self.assertEqual(value['observed_status'],status)
            result=app.execute('calibration.correction.get',{'id':sid})
            self.assertTrue(result['ok'],result);self.assertFalse(result['result']['runtime']['configuration_readback_available'])
            self.assertEqual(calls,['audio.config.get','audio.status']*2)
    def test_transport_or_peer_failure_not_hidden_as_legacy(self):
        from garage_audio.runtime_readback import configuration
        for code in ('audio_peer_rejected','audio_unavailable'):
            with patch.object(audio_public,'request',side_effect=audio_public.AudioFailure(code,'SYNTHETIC')) as request:
                with self.assertRaises(audio_public.AudioFailure):configuration()
                self.assertEqual(request.call_count,1)


if __name__=='__main__':unittest.main()
