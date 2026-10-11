"""Cheap policy checks; synthetic monotonic clock, not physical evidence."""
import unittest
from garage_audio.playback_lease import Lease,listening_scope,LISTEN_POLICY

class NormalPolicyTests(unittest.TestCase):
    def test_closed_scope_enums(self):
        for hours in (1,2,4):
            self.assertEqual(listening_scope(hours,LISTEN_POLICY),(hours*3600,0))
        for h,c in ((True,LISTEN_POLICY),(2.0,LISTEN_POLICY),(3,LISTEN_POLICY),(0,LISTEN_POLICY),(5,LISTEN_POLICY),(2,-40),(2,-30),(2,-20),(2,None),(2,'fosi-line-v0')):
            with self.assertRaises(PermissionError):listening_scope(h,c)
    def test_absolute_deadline_watchdog_and_idle_no_renewal(self):
        class Peer:
            uid=0
            def alive(self):return True
        now=[10.];binding={'boot':'FILE-only','generation':'FILE-generation','hardware':'FILE','graph':'FILE','outputs':[3,4]}
        lease=Lease(Peer(),binding,clock=lambda:now[0],hours=2,policy=LISTEN_POLICY)
        deadline=lease.deadline;now[0]+=9;lease.heartbeat(binding);self.assertEqual(lease.deadline,deadline)
        now[0]+=10;self.assertFalse(lease.valid(binding))
        with self.assertRaises(PermissionError):lease.heartbeat(binding)
        now[0]=10;lease=Lease(Peer(),binding,clock=lambda:now[0],hours=4,policy=LISTEN_POLICY)
        lease.observe('playing');lease.observe('paused');idle=lease.idle_deadline
        for _ in range(599):now[0]+=1;lease.heartbeat(binding);lease.observe('paused')
        self.assertEqual(lease.idle_deadline,idle);now[0]+=1;self.assertFalse(lease.valid(binding))
    def test_resume_keeps_deadline_and_stop_never_restores(self):
        class Peer:
            uid=0
            def alive(self):return True
        now=[0.];binding={'boot':'FILE'};lease=Lease(Peer(),binding,clock=lambda:now[0],hours=1,policy=LISTEN_POLICY)
        deadline=lease.deadline;lease.observe('idle');self.assertTrue(lease.valid(binding));lease.observe('playing');lease.observe('paused');lease.observe('playing')
        self.assertEqual(lease.deadline,deadline);self.assertIsNone(lease.idle_deadline)
        lease.observe('idle');self.assertFalse(lease.valid(binding));lease.observe('playing');self.assertFalse(lease.valid(binding))
        self.assertFalse(lease.valid({'boot':'changed'}))

if __name__=='__main__':unittest.main()
