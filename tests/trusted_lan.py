"""Real HTTP, synthetic native; no production sessions, approval or audio."""
import http.client,json,time,threading,unittest
from pathlib import Path
from garage_audio import webserver
from garage_audio.speaker_controller import Controller
from tests.speaker_browser_fixture import AppFixture,NativeFixture

class TrustedLanTests(unittest.TestCase):
    def setUp(self):
        self.controller=Controller(NativeFixture());self.previous=webserver.speaker_request
        webserver.speaker_request=self.controller.execute
        self.server=webserver.Server(('0.0.0.0',0),AppFixture(),'ISOLATED-TOKEN',Path(__file__).resolve().parents[1]/'web')
        self.port=self.server.server_address[1];self.lan=f'192.168.68.74:{self.port}';self.local=f'127.0.0.1:{self.port}'
        self.server.authorities.add(self.lan);self.server.trusted_lan_authority=self.lan
        threading.Thread(target=self.server.serve_forever,daemon=True).start()
    def tearDown(self):
        self.server.shutdown();self.server.server_close();webserver.speaker_request=self.previous
    def call(self,path,body=None,cookie='',headers=None):
        h={'Host':self.lan,'Origin':'http://'+self.lan,'Content-Type':'application/json'}
        if cookie:h['Cookie']=cookie
        h.update(headers or {});c=http.client.HTTPConnection('127.0.0.1',self.port,timeout=3)
        c.request('POST' if body is not None else 'GET',path,json.dumps(body) if body is not None else None,h)
        r=c.getresponse();raw=r.read();cookies=r.headers.get_all('Set-Cookie') or [];status=r.status;c.close()
        return status,json.loads(raw) if path.startswith('/api/') else raw.decode(),cookies
    def session(self):
        status,v,h=self.call('/api/session');self.assertEqual(status,200)
        self.assertEqual(v['result']['role'],'automation');self.assertEqual(v['result']['access_mode'],'trusted-lan')
        self.assertIn('HttpOnly; SameSite=Strict',h[0]);return h[0].split(';')[0],v['result']['csrf']
    def test_no_lock_session_reads_and_csrf_mutations(self):
        cookie,csrf=self.session()
        self.assertEqual(self.call('/api/status',cookie=cookie)[0],200)
        self.assertEqual(self.call('/api/operations',{'operation':'profiles.list','args':{}},cookie)[0],403)
        self.assertEqual(self.call('/api/operations',{'operation':'profiles.list','args':{}},cookie,{'X-CSRF-Token':csrf})[0],200)
        html=self.call('/')[1];self.assertIn('data-trusted-lan="true"',html);self.assertIn('<section hidden id="login-panel"',html)
        self.assertNotIn('Request phone access',html)
    def test_token_default_and_localhost_not_auto_admitted(self):
        local={'Host':self.local,'Origin':'http://'+self.local}
        self.assertEqual(self.call('/api/session',headers=local)[0],401)
        cookie,_=self.session();self.assertEqual(self.call('/api/session',cookie=cookie,headers=local)[0],401)
        self.assertEqual(self.call('/api/login',{'token':'ISOLATED-TOKEN'},headers=local)[0],200)
        self.server.trusted_lan_authority=None
        self.assertEqual(self.call('/api/session')[0],401)
        self.assertNotIn('data-trusted-lan="true"',self.call('/')[1])
    def test_host_origin_fetch_and_old_human_denials(self):
        for h in ({'Host':'evil.invalid'},{'Origin':'http://evil.invalid'},{'Sec-Fetch-Site':'cross-site'}):
            self.assertEqual(self.call('/api/session',headers=h)[0],403)
        self.assertEqual(self.call('/api/human/login',{})[0],403)
    def test_setup_stop_available_but_enable_not_promoted(self):
        cookie,csrf=self.session();h={'X-CSRF-Token':csrf}
        status,v,headers=self.call('/api/speakers/session',{},cookie,h);self.assertEqual(status,200)
        self.assertFalse(v['result'].get('paired',False));speaker_csrf=v['result']['csrf']
        combined=cookie+'; '+headers[0].split(';')[0];h['X-Speaker-CSRF']=speaker_csrf
        self.assertEqual(self.call('/api/speakers/enable',{'action':'NOT-A-HUMAN-ACTION'},combined,h)[0],409)
        self.assertEqual(self.call('/api/speakers/setup',{},combined,h)[0],200)
        if self.controller.worker:self.controller.worker.join(2)
        self.assertEqual(self.controller.state,'prepared')
        self.assertEqual(self.call('/api/speakers/action',{},combined,h)[0],409)
        self.assertEqual(self.call('/api/speakers/stop',{},combined,h)[0],200)
        self.assertEqual(self.call('/api/logout',{},combined,h)[0],200)
        self.assertFalse(self.controller.realm.sessions)
        self.assertEqual(self.call('/api/phone/request',{})[0],401)
    def test_admission_bounds_restart_and_reuse(self):
        cookie,_=self.session();self.assertFalse(self.call('/api/session',cookie=cookie)[2])
        self.server.lan_admissions=[time.time()]*60
        self.assertEqual(self.call('/api/session')[0],429)
        self.assertEqual(self.call('/api/session',cookie=cookie)[0],200)
        self.server.lan_admissions=[];self.server.sessions.clear()
        self.assertEqual(self.call('/api/session',cookie=cookie)[0],200)

if __name__=='__main__':unittest.main()
