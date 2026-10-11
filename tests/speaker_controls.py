"""Isolated synthetic authority/control tests. Never pair a production browser."""
import threading
import unittest
from garage_audio.speaker_realm import BrowserRealm
from garage_audio.speaker_controller import Controller

class RealmTests(unittest.TestCase):
    def setUp(self):
        self.now=[10];self.realm=BrowserRealm(lambda:self.now[0]);self.browser=self.realm.create()
        self.binding={'generation':'ISOLATED','outputs':[3,4],'ceiling_db':-60,'seconds':120}
    def test_code_is_not_possession_and_unpaired_cannot_enable(self):
        b=self.browser
        with self.assertRaises(PermissionError):self.realm.status(b['code'])
        with self.assertRaises(PermissionError):self.realm.action(b['cookie'],b['csrf'],self.binding)
        self.assertFalse(self.realm.status(b['cookie'])['paired'])
    def test_pair_no_sound_action_one_use_and_stale_generation_denied(self):
        b=self.browser
        self.assertEqual(self.realm.pair(b['code']),{'paired':True,'sound_granted':False})
        action=self.realm.action(b['cookie'],b['csrf'],self.binding)
        self.assertTrue(self.realm.consume(b['cookie'],b['csrf'],action,self.binding))
        with self.assertRaises(PermissionError):self.realm.consume(b['cookie'],b['csrf'],action,self.binding)
        action=self.realm.action(b['cookie'],b['csrf'],self.binding)
        with self.assertRaises(PermissionError):self.realm.consume(b['cookie'],b['csrf'],action,dict(self.binding,generation='OTHER'))
    def test_csrf_action_expiry_and_restart(self):
        b=self.browser;self.realm.pair(b['code'])
        with self.assertRaises(PermissionError):self.realm.action(b['cookie'],'WRONG-CSRF-'*4,self.binding)
        action=self.realm.action(b['cookie'],b['csrf'],self.binding);self.now[0]+=31
        with self.assertRaises(PermissionError):self.realm.consume(b['cookie'],b['csrf'],action,self.binding)
        with self.assertRaises(PermissionError):BrowserRealm().status(b['cookie'])
    def test_reload_preserves_pairing_rotates_csrf_invalidates_action(self):
        b=self.browser;self.realm.pair(b['code']);action=self.realm.action(b['cookie'],b['csrf'],self.binding)
        resumed=self.realm.resume(b['cookie']);self.assertTrue(resumed['paired'])
        with self.assertRaises(PermissionError):self.realm.consume(b['cookie'],resumed['csrf'],action,self.binding)
    def test_session_capacity_and_deadline(self):
        for _ in range(31):self.realm.create()
        with self.assertRaises(PermissionError):self.realm.create()
        self.now[0]+=601
        with self.assertRaises(PermissionError):self.realm.status(self.browser['cookie'])
        self.realm.create()

class ControllerTests(unittest.TestCase):
    def test_stop_during_stalled_start_not_falsely_acknowledged(self):
        entered=threading.Event();release=threading.Event();stops=[]
        class FixtureNative:
            def start(self,cancel):entered.set();release.wait(3);raise InterruptedError('fixture cancelled')
            def stop(self):stops.append(True)
        c=Controller(FixtureNative());self.assertEqual(c.setup()['state'],'setting_up');self.assertTrue(entered.wait(1))
        self.assertEqual(c.stop()['state'],'stopping');self.assertTrue(stops)
        release.set();c.worker.join(2);self.assertEqual(c.status()['state'],'stopped');self.assertGreaterEqual(len(stops),2)
    def test_agent_role_fields_code_pairing_and_unpaired_enable_rejected(self):
        c=Controller();b=c.execute({'cmd':'session'})
        for p in ({'cmd':'pair','code':b['code']},{'cmd':'enable','role':'human','cookie':b['cookie'],'csrf':b['csrf'],'action':'FAKE-'*10}):
            with self.assertRaises(ValueError):c.execute(p)
        c.state='prepared';c.generation='ISOLATED';c.registered=True
        with self.assertRaises(PermissionError):c.execute({'cmd':'action','cookie':b['cookie'],'csrf':b['csrf']})
    def test_enable_only_queues_after_pairing_consumes_exact_action(self):
        c=Controller();b=c.execute({'cmd':'session'});c.state='prepared';c.generation='ISOLATED';c.registered=True
        c.realm.pair(b['code']) # isolated fixture admin, not production
        action=c.execute({'cmd':'action','cookie':b['cookie'],'csrf':b['csrf']})['action']
        self.assertEqual(c.execute({'cmd':'enable','cookie':b['cookie'],'csrf':b['csrf'],'action':action})['state'],'enabling')
        with self.assertRaises(PermissionError):c.execute({'cmd':'enable','cookie':b['cookie'],'csrf':b['csrf'],'action':action})

if __name__=='__main__':unittest.main()
