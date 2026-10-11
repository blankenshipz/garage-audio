"""Synthetic muted-review polling fixtures, never credentials/devices/helper launch."""
import unittest
from unittest.mock import patch
from garage_audio.human_playback import approve_listening

class TTY:
    def __init__(self,answer='LISTEN\n'):self.answer=answer;self.output=''
    def write(self,value):self.output+=value
    def flush(self):pass
    def readline(self,size):return self.answer

class MutedReview(unittest.TestCase):
    def test_idle_pause_polls_status_before_operator_reply(self):
        tty=TTY();connection=object()
        with patch('garage_audio.human_playback.select.select',side_effect=[([],[],[]),([],[],[]),([tty],[],[])]), \
             patch('garage_audio.human_playback.time.monotonic',side_effect=[0,1,2]), \
             patch('garage_audio.human_playback.exchange',return_value={'muted':True,'volume_db':-60,'generation':'FIXTURE'}) as exchange:
            approve_listening(tty,connection,'FIXTURE')
            self.assertEqual(exchange.call_count,2)
            self.assertEqual(exchange.call_args.args,(connection,{'cmd':'status'}))
        self.assertIn('at most120 seconds',tty.output)
    def test_muted_review_deadline_denies_without_authorizing(self):
        with patch('garage_audio.human_playback.select.select',return_value=([],[],[])), \
             patch('garage_audio.human_playback.time.monotonic',side_effect=[0,121]), \
             patch('garage_audio.human_playback.exchange') as exchange:
            with self.assertRaises(PermissionError):approve_listening(TTY(),object(),'FIXTURE')
            exchange.assert_not_called()
    def test_mute_volume_and_generation_changes_deny(self):
        for change in ({'muted':False},{'volume_db':-20},{'generation':'OTHER'}):
            status={'muted':True,'volume_db':-60,'generation':'FIXTURE',**change}
            with self.subTest(change=change), \
                 patch('garage_audio.human_playback.select.select',return_value=([],[],[])), \
                 patch('garage_audio.human_playback.exchange',return_value=status):
                with self.assertRaises(PermissionError):approve_listening(TTY(),object(),'FIXTURE')
    def test_readback_failure_and_operator_cancel_deny(self):
        with patch('garage_audio.human_playback.select.select',return_value=([],[],[])), \
             patch('garage_audio.human_playback.exchange',side_effect=TimeoutError('fixture')):
            with self.assertRaises(TimeoutError):approve_listening(TTY(),object(),'FIXTURE')
        tty=TTY('\n')
        with patch('garage_audio.human_playback.select.select',return_value=([tty],[],[])):
            with self.assertRaises(PermissionError):approve_listening(tty,object(),'FIXTURE')

if __name__=='__main__':unittest.main(verbosity=2)
