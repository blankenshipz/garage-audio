"""Enrollment boundary checks; synthetic MA/client and real local HTTP, no real login."""
import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from garage_audio import ma_enrollment as enrollment,webserver

FIXTURE_TOKEN='synthetic-fixture-token-not-production-12345'
REQUEST={'username':'fixture','password':'synthetic-password','request_id':'fixture-request-0001','confirm':True}

class FixtureMA:
    calls=[]
    def call(self,command,args=None,bearer=None):
        self.calls.append(command)
        if command=='auth/login':return {'success':True,'access_token':FIXTURE_TOKEN}
        if command=='auth/me':return {'role':'admin'}
        if command=='auth/scopes':return {'admin':list(enrollment.SCOPES)}
        if command=='auth/token/create':return FIXTURE_TOKEN
        return []

class EnrollmentTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name).resolve()
        self.root.chmod(0o700);enrollment.ATTEMPTS.clear();FixtureMA.calls=[]
    def tearDown(self):self.temp.cleanup()
    def test_issuance_redaction_and_repeat_no_mint(self):
        state={'state':'setup_required','code':'ma_credential_required','scope_verified':False}
        def installer(payload):
            if payload['op']=='install':state.update(state='ready',code=None,enrollment_id=payload['enrollment_id'],scope_verified=True)
            return dict(state)
        with patch.object(enrollment,'MA',FixtureMA),patch.object(enrollment,'installer',installer):
            body=dict(REQUEST);result=enrollment.enroll(self.root,body)
            self.assertEqual(body,{});self.assertEqual(result['state'],'ready')
            enrollment.enroll(self.root,dict(REQUEST))
        self.assertEqual(FixtureMA.calls.count('auth/token/create'),1)
        stored=(self.root/'ma-enrollment/request.json').read_text()
        for secret in (FIXTURE_TOKEN,REQUEST['password'],REQUEST['username']):self.assertNotIn(secret,stored.replace(REQUEST['request_id'],''))
        self.assertNotIn(FIXTURE_TOKEN,json.dumps(result))
    def test_unknown_create_and_new_id_do_not_blind_mint(self):
        class Unknown(FixtureMA):
            def call(self,command,args=None,bearer=None):
                if command=='auth/token/create':raise TimeoutError()
                return super().call(command,args,bearer)
        with patch.object(enrollment,'MA',Unknown),patch.object(enrollment,'status',return_value={'state':'setup_required'}):
            for request_id in (REQUEST['request_id'],'different-request-0002'):
                with self.assertRaises(enrollment.EnrollmentFailure) as error:
                    enrollment.enroll(self.root,{**REQUEST,'request_id':request_id})
                self.assertEqual(error.exception.code,'ma_enrollment_interrupted')
        self.assertEqual(FixtureMA.calls.count('auth/login'),1)
    def test_installer_unavailable_precedes_login(self):
        with patch.object(enrollment,'MA',FixtureMA),patch.object(enrollment,'status',return_value={'state':'unavailable','code':'ma_installer_unavailable'}):
            with self.assertRaises(enrollment.EnrollmentFailure):enrollment.enroll(self.root,dict(REQUEST))
        self.assertEqual(FixtureMA.calls,[])
    def test_schema_scope_rate_and_serialization(self):
        for body in ({**REQUEST,'url':'http://evil'}, {**REQUEST,'confirm':False},{**REQUEST,'request_id':'../secret'}):
            with self.assertRaises(enrollment.EnrollmentFailure):enrollment.enroll(self.root,body)
        enrollment.LOCK.acquire()
        try:
            with self.assertRaises(enrollment.EnrollmentFailure) as error:enrollment.enroll(self.root,dict(REQUEST))
            self.assertEqual(error.exception.status,429)
        finally:enrollment.LOCK.release()
        class Guest(FixtureMA):
            def call(self,command,args=None,bearer=None):
                return {'role':'guest'} if command=='auth/me' else super().call(command,args,bearer)
        with self.assertRaises(enrollment.EnrollmentFailure):enrollment.verify(Guest(),FIXTURE_TOKEN)
    def test_mcp_typed_operation_cannot_enroll_or_echo_credentials(self):
        from garage_audio.application import Application,SCHEMAS
        self.assertNotIn('setup.ma.enroll',SCHEMAS)
        result=Application.execute(object.__new__(Application),'setup.ma.enroll',dict(REQUEST))
        self.assertFalse(result['ok'])
        for secret in (REQUEST['password'],REQUEST['username']):self.assertNotIn(secret,json.dumps(result))
    def test_rate_limit_before_login(self):
        import time
        enrollment.ATTEMPTS[:]=[time.monotonic()]*5
        with patch.object(enrollment,'MA',FixtureMA),patch.object(enrollment,'status',return_value={'state':'setup_required'}):
            with self.assertRaises(enrollment.EnrollmentFailure) as error:enrollment.enroll(self.root,dict(REQUEST))
        self.assertEqual(error.exception.code,'ma_enrollment_rate_limited');self.assertEqual(FixtureMA.calls,[])
    def test_actual_ma_admin_wildcard_is_supported_not_invented_downscope(self):
        class Admin(FixtureMA):
            def call(self,command,args=None,bearer=None):
                return {'admin':['*']} if command=='auth/scopes' else super().call(command,args,bearer)
        self.assertTrue(enrollment.verify(Admin(),FIXTURE_TOKEN)['scope_verified'])

class HTTPTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.app=SimpleNamespace(root=self.temp.name)
        self.server=webserver.Server(('127.0.0.1',0),self.app,'BOOTSTRAP',Path(__file__).parents[1]/'web')
        self.port=self.server.server_address[1];self.lan=f'192.168.68.74:{self.port}'
        self.server.authorities.add(self.lan);self.server.trusted_lan_authority=self.lan
        threading.Thread(target=self.server.serve_forever,daemon=True).start()
    def tearDown(self):self.server.shutdown();self.server.server_close();self.temp.cleanup()
    def call(self,path,body=None,headers=None):
        h={'Host':self.lan,'Origin':'http://'+self.lan,'Content-Type':'application/json'};h.update(headers or {})
        c=http.client.HTTPConnection('127.0.0.1',self.port,timeout=3)
        c.request('POST' if body is not None else 'GET',path,json.dumps(body) if isinstance(body,dict) else body,h)
        r=c.getresponse();result=json.loads(r.read());status=r.status;cookie=r.getheader('Set-Cookie');c.close()
        return status,result,cookie
    def session(self):
        _,v,c=self.call('/api/session');return {'Cookie':c.split(';')[0],'X-CSRF-Token':v['result']['csrf']}
    def test_reject_before_credential_processing(self):
        with patch.object(enrollment,'enroll') as enroll:
            self.assertEqual(self.call('/api/setup/ma/enroll',REQUEST)[0],401)
            session=self.session()
            for headers in ({**session,'Origin':'http://evil.invalid'},
                            {**session,'Host':'evil.invalid'},
                            {**session,'X-CSRF-Token':'wrong'},
                            {**session,'Sec-Fetch-Site':'cross-site'}):
                self.assertIn(self.call('/api/setup/ma/enroll',REQUEST,headers)[0],(401,403))
            enroll.assert_not_called()
    def test_success_only_safe_response_and_duplicate_json_rejection(self):
        safe={'state':'ready','code':None,'scope_verified':True,'enrollment_id':REQUEST['request_id']}
        with patch.object(enrollment,'enroll',return_value=safe) as enroll:
            headers=self.session()
            status,result,_=self.call('/api/setup/ma/enroll',REQUEST,headers)
            self.assertEqual(status,200);self.assertNotIn(FIXTURE_TOKEN,json.dumps(result));self.assertNotIn('password',json.dumps(result))
            self.assertEqual(self.call('/api/setup/ma/enroll','{"password":"a","password":"b"}',headers)[0],400)
            self.assertEqual(enroll.call_count,1)
    def test_bootstrap_session_cannot_enroll(self):
        _,v,c=self.call('/api/login',{'token':'BOOTSTRAP'})
        h={'Cookie':c.split(';')[0],'X-CSRF-Token':v['result']['csrf']}
        with patch.object(enrollment,'enroll') as enroll:
            self.assertEqual(self.call('/api/setup/ma/enroll',REQUEST,h)[0],401);enroll.assert_not_called()
    def test_expired_dashboard_session_before_credentials(self):
        headers=self.session()
        for session in self.server.sessions.values():session['expires']=0
        with patch.object(enrollment,'enroll') as enroll:
            self.assertEqual(self.call('/api/setup/ma/enroll',REQUEST,headers)[0],401);enroll.assert_not_called()

if __name__=='__main__':unittest.main()
