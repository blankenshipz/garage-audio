"""Isolated CLI/phone barrier regression; no credentials, systemd or audio."""
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from garage_audio import human_playback as helper
from garage_audio.speaker_controller import NativeChain

class BarrierTests(unittest.TestCase):
    def test_installed_powered_off_readback_requires_exact_idle_identity(self):
        stopped={'power':0,'player_connected':1,'player_name':'Garage Speakers','waitingToPlay':0}
        with patch.object(helper,'ma_request',side_effect=[None,stopped]):helper.stop_pending_music()
        for bad in ({**stopped,'mode':'play'},{**stopped,'waitingToPlay':1},{**stopped,'player_connected':0},{**stopped,'player_name':'Other'},{'power':0},{}):
            with patch.object(helper,'ma_request',side_effect=[None,bad]):
                with self.assertRaises(RuntimeError):helper.stop_pending_music()
    def verifier(self):
        return SimpleNamespace(authenticate=lambda *a:SimpleNamespace(session='ISOLATED'),
                               current=lambda *a,**k:True,revoke=lambda *a:None)
    def test_cli_rejects_pending_play_before_native_or_capture_start(self):
        replies=[{'players_loop':[{'playerid':'02:00:00:00:21:01','name':'Garage Speakers','connected':1}]},None,{'mode':'play'}]
        with patch.object(helper,'review_operation'),patch.object(helper,'HumanVerifier',return_value=self.verifier()),patch.object(helper,'password',return_value='ISOLATED-NOT-A-CREDENTIAL'),patch.object(helper,'user_command') as commands,patch.object(helper,'ma_request',side_effect=replies) as ma,patch.object(helper,'user_units') as units,patch.object(helper.subprocess,'run') as system:
            with self.assertRaises(RuntimeError):helper.listen(SimpleNamespace())
            self.assertEqual(commands.call_args.args[0],['/usr/bin/systemctl','--user','start','garage-player.service'])
            self.assertNotIn('start',[c.args[0] for c in units.call_args_list])
            self.assertFalse(any('start' in c.args[0] for c in system.call_args_list))
            self.assertEqual(ma.call_args_list[1].args,('02:00:00:00:21:01',['stop']))
            self.assertEqual(ma.call_args_list[2].args,('02:00:00:00:21:01',['status',0,1]))
    def test_cli_shared_barrier_precedes_native_start(self):
        order=[]
        def barrier():order.append('MA-stop/readback')
        def system(argv,**k):
            if 'start' in argv:
                order.append('native-start');raise InterruptedError('ISOLATED stop before device')
        with patch.object(helper,'review_operation'),patch.object(helper,'HumanVerifier',return_value=self.verifier()),patch.object(helper,'password',return_value='ISOLATED-NOT-A-CREDENTIAL'),patch.object(helper,'prepare_transport',side_effect=barrier),patch.object(helper,'user_units'),patch.object(helper.subprocess,'run',side_effect=system):
            with self.assertRaises(InterruptedError):helper.listen(SimpleNamespace())
        self.assertEqual(order,['MA-stop/readback','native-start'])
    def test_phone_uses_same_barrier_before_native_start(self):
        class Cancel:
            def is_set(self):return False
        order=[]
        def system(*a,**k):order.append('native-start');raise InterruptedError('ISOLATED')
        with patch('garage_audio.speaker_controller.prepare_transport',side_effect=lambda cancel:order.append('MA-stop/readback')),patch('garage_audio.speaker_controller.subprocess.run',side_effect=system):
            with self.assertRaises(InterruptedError):NativeChain().start(Cancel())
        self.assertEqual(order,['MA-stop/readback','native-start'])

if __name__=='__main__':unittest.main()
