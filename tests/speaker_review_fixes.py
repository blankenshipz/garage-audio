"""Targeted four-finding regressions; synthetic except separate FILE tests."""
import threading
import json
import socket
import time
import unittest
from unittest.mock import patch
from garage_audio.speaker_controller import Controller,NativeChain
from garage_audio.speaker_realm import digest

class ReviewFixTests(unittest.TestCase):
    def test_setup_rejected_until_stalled_stop_finishes(self):
        entered=threading.Event();release=threading.Event()
        class Native:
            def stop(self):entered.set();release.wait(3)
        c=Controller(Native());original=c.cancel
        thread=threading.Thread(target=c.stop);thread.start();self.assertTrue(entered.wait(1))
        try:
            with self.assertRaises(PermissionError):c.setup()
            self.assertIs(c.cancel,original);self.assertTrue(original.is_set());self.assertEqual(c.shutdowns,1)
        finally:release.set();thread.join(2)
        self.assertEqual(c.shutdowns,0);self.assertEqual(c.state,'stopped')
    def test_logout_revokes_only_own_browser_and_owned_window(self):
        stops=[]
        class Native:
            def stop(self):stops.append(True)
        c=Controller(Native());owner=c.realm.create();other=c.realm.create()
        c.realm.pair(owner['code']);c.state='listening';c.owner=digest(owner['cookie']);c.setup_owner=digest(other['cookie'])
        c.realm.action(owner['cookie'],owner['csrf'],{'generation':'ISOLATED'})
        c.revoke(other['cookie']);self.assertEqual(stops,[]);self.assertEqual(c.state,'listening')
        c.revoke(owner['cookie']);self.assertEqual(stops,[True]);self.assertEqual(c.state,'stopped')
        with self.assertRaises(PermissionError):c.realm.current(owner['cookie'])
    def test_failed_logout_stop_keeps_shutdown_barrier_and_revoke(self):
        class Native:
            def stop(self):raise RuntimeError('isolated unavailable')
        c=Controller(Native());b=c.realm.create();c.owner=digest(b['cookie']);c.state='listening'
        with self.assertRaises(RuntimeError):c.revoke(b['cookie'])
        self.assertEqual(c.state,'stop_unverified')
        with self.assertRaises(PermissionError):c.setup()
        with self.assertRaises(PermissionError):c.realm.current(b['cookie'])
    def test_bounded_poll_accepts_only_stable_zero_generation(self):
        now=[0];calls=[]
        class Cancel:
            def is_set(self):return False
            def wait(self,seconds):now[0]+=seconds;return False
        statuses=[(False,'a'),(False,'a'),(True,'b'),(True,'c'),(True,'c')]
        def exchange(*args):
            zero,g=statuses[len(calls)];calls.append(True)
            return {'muted':True,'volume_db':-60,'generation':g,'preparation_zero':zero}
        with patch('garage_audio.speaker_controller.exchange',exchange):
            result=NativeChain().wait_zero(None,Cancel(),timeout=1,clock=lambda:now[0])
        self.assertEqual(result['generation'],'c');self.assertEqual(len(calls),5)
    def test_bounded_poll_deadline_and_stop_cancellation(self):
        now=[0]
        class Cancel:
            cancelled=False
            def is_set(self):return self.cancelled
            def wait(self,seconds):now[0]+=seconds;return self.cancelled
        status={'muted':True,'volume_db':-60,'generation':'a','preparation_zero':False}
        cancel=Cancel()
        with patch('garage_audio.speaker_controller.exchange',return_value=status):
            with self.assertRaises(TimeoutError):NativeChain().wait_zero(None,cancel,timeout=.3,clock=lambda:now[0])
            cancel.cancelled=True
            with self.assertRaises(InterruptedError):NativeChain().wait_zero(None,cancel,timeout=1,clock=lambda:now[0])
    def test_delayed_status_reply_uses_remaining_budget_not_idle_timeout(self):
        client,peer=socket.socketpair();self.addCleanup(client.close);client.settimeout(.03)
        def responder():
            with peer:
                stream=peer.makefile('rb')
                for delay in (.15,0):
                    if not stream.readline():return
                    time.sleep(delay)
                    result={'muted':True,'volume_db':-60,'generation':'delayed','preparation_zero':True}
                    peer.sendall(json.dumps({'ok':True,'result':result}).encode()+b'\n')
        thread=threading.Thread(target=responder);thread.start()
        result=NativeChain().wait_zero(client,threading.Event(),timeout=1)
        thread.join(1);self.assertEqual(result['generation'],'delayed');self.assertEqual(client.gettimeout(),2)

if __name__=='__main__':unittest.main()
