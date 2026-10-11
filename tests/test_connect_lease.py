"""Synthetic clock/peer tests, not acoustic or real receiver evidence."""
import unittest
from garage_audio.playback_lease import Lease

class Peer:
    uid=0
    live=True
    def alive(self):return self.live

class Tests(unittest.TestCase):
    def setUp(self):
        self.now=1.;self.peer=Peer();self.binding={'generation':'zero1'}
        self.lease=Lease(self.peer,self.binding,clock=lambda:self.now,connect=True)
    def test_fixed_receiver_scope(self):
        self.assertTrue(self.lease.connect);self.assertEqual(self.lease.ceiling,0)
        with self.assertRaises(PermissionError):Lease(self.peer,self.binding,connect=True,hours=4)
        with self.assertRaises(PermissionError):Lease(self.peer,self.binding,connect=True,policy='other')
    def test_no_four_hour_ui_deadline(self):
        self.lease.observe('playing')
        for _ in range(14401):
            self.now+=1;self.lease.heartbeat(self.binding)
        self.assertTrue(self.lease.valid(self.binding));self.assertEqual(self.lease.deadline,self.now+10)
    def test_expired_lease_never_renews(self):
        self.now+=10
        with self.assertRaises(PermissionError):self.lease.heartbeat(self.binding)
    def test_paused_lease_keeps_ten_minute_bound(self):
        self.lease.observe('playing');self.lease.observe('paused')
        for _ in range(599):self.now+=1;self.lease.heartbeat(self.binding)
        self.now+=1
        self.assertFalse(self.lease.valid(self.binding))
    def test_resume_does_not_extend_expired_pause(self):
        self.lease.observe('playing');self.lease.observe('paused');self.now+=600
        with self.assertRaises(PermissionError):self.lease.heartbeat(self.binding)
    def test_stop_off_death_and_graph_change_close(self):
        for state in ('idle','off'):
            self.setUp();self.lease.observe('playing');self.lease.observe(state)
            self.assertFalse(self.lease.valid(self.binding))
        self.setUp();self.peer.live=False;self.assertFalse(self.lease.valid(self.binding))
        self.setUp();self.assertFalse(self.lease.valid({'generation':'other'}))

if __name__=='__main__':unittest.main()
