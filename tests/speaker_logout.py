"""Actual HTTP logout handler, isolated synthetic native windows only."""
import http.client
import json
from pathlib import Path
import threading
import unittest
from unittest.mock import patch
from garage_audio import webserver
from garage_audio.speaker_controller import Controller
from garage_audio.speaker_realm import digest
from speaker_browser_fixture import AppFixture

class LogoutTests(unittest.TestCase):
    def exercise(self,fail_stop):
        stops=[]
        class Native:
            def stop(self):
                stops.append(True)
                if fail_stop:raise RuntimeError('isolated stop unavailable')
        controller=Controller(Native())
        server=webserver.Server(('127.0.0.1',0),AppFixture(),'ISOLATED-LOGOUT-FIXTURE',Path(__file__).resolve().parents[1]/'web')
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        self.addCleanup(server.server_close);self.addCleanup(server.shutdown)
        port=server.server_address[1]
        def call(path,body=None,headers=None):
            c=http.client.HTTPConnection('127.0.0.1',port,timeout=3)
            h={'Content-Type':'application/json','Origin':f'http://127.0.0.1:{port}',**(headers or {})}
            c.request('GET' if body is None else 'POST',path,None if body is None else json.dumps(body),h)
            r=c.getresponse();value=json.loads(r.read());result=(r.status,value,r.headers.get_all('Set-Cookie',[]));c.close();return result
        with patch('garage_audio.webserver.speaker_request',controller.execute):
            _,login,cookies=call('/api/login',{'token':'ISOLATED-LOGOUT-FIXTURE'})
            headers={'Cookie':cookies[0].split(';')[0],'X-CSRF-Token':login['result']['csrf']}
            _,browser,cookies=call('/api/speakers/session',{},headers)
            possession=cookies[0].split(';')[0].split('=',1)[1]
            controller.realm.pair(browser['result']['code'])
            controller.realm.action(possession,browser['result']['csrf'],{'generation':'ISOLATED'})
            controller.owner=digest(possession);controller.state='listening'
            # Browser narrow cookie intentionally NOT sent to /api/logout.
            status,value,cleared=call('/api/logout',{},headers)
            self.assertEqual(stops,[True])
            with self.assertRaises(PermissionError):controller.realm.current(possession)
            if fail_stop:
                self.assertEqual(status,409);self.assertEqual(cleared,[])
                self.assertEqual(call('/api/session',headers=headers)[0],200)
                self.assertEqual(controller.state,'stop_unverified')
            else:
                self.assertEqual(status,200);self.assertEqual(controller.state,'stopped')
                self.assertEqual(len(cleared),2);self.assertTrue(all('Max-Age=0' in c for c in cleared))
                self.assertEqual(call('/api/session',headers=headers)[0],401)
    def test_logout_listening_revoke_before_lock_and_clear_both_cookies(self):self.exercise(False)
    def test_failed_revocation_retains_automation_session_for_stop(self):self.exercise(True)

if __name__=='__main__':unittest.main()
