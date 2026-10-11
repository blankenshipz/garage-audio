"""Actual isolated HTTP negative boundaries; synthetic controller only."""
import http.client
import json
import unittest

class HttpTests(unittest.TestCase):
    def call(self,path,body=None,headers=None):
        c=http.client.HTTPConnection('127.0.0.1',18798,timeout=5)
        h={'Origin':'http://127.0.0.1:18798','Content-Type':'application/json',**(headers or {})}
        c.request('GET' if body is None else 'POST',path,body=None if body is None else json.dumps(body),headers=h)
        response=c.getresponse();result=(response.status,json.loads(response.read()),response.getheader('Set-Cookie'));c.close();return result
    def test_real_handler_denials(self):
        status,login,cookie=self.call('/api/login',{'token':'ISOLATED-BROWSER-FIXTURE-TOKEN'})
        self.assertEqual(status,200);self.assertEqual(login['result']['role'],'automation')
        headers={'Cookie':cookie.split(';')[0],'X-CSRF-Token':login['result']['csrf']}
        self.assertEqual(self.call('/api/speakers/session',{},dict(headers,Origin='http://other.invalid'))[0],403)
        self.assertEqual(self.call('/api/speakers/session',{},dict(headers,Host='192.168.68.74:8098'))[0],403)
        self.assertEqual(self.call('/api/speakers/session',{},dict(headers,**{'X-CSRF-Token':'FAKE'}))[0],403)
        status,session,speaker_cookie=self.call('/api/speakers/session',{},headers)
        self.assertEqual(status,200,(session.get('error') or {}).get('code'))
        s=session['result'];self.assertNotIn('cookie',s)
        headers['Cookie']+='; '+speaker_cookie.split(';')[0];headers['X-Speaker-CSRF']=s['csrf']
        self.assertEqual(self.call('/api/speakers/action',{},headers)[0],409)
        self.assertEqual(self.call('/api/speakers/enable',{'action':s['code']},headers)[0],409)
        self.assertEqual(self.call('/api/speakers/pair',{'code':s['code']},headers)[0],400)
        self.assertEqual(self.call('/api/speakers/setup',{'role':'human'},headers)[0],400)
        duplicate=dict(headers,Cookie=headers['Cookie']+'; '+speaker_cookie.split(';')[0])
        self.assertEqual(self.call('/api/speakers/status',headers=duplicate)[0],409)
        self.assertEqual(self.call('/api/human/login',{'user':'operator','password':'ISOLATED-FIXTURE-PASSWORD'},headers)[0],403)
        self.assertEqual(self.call('/api/speakers/stop',{},headers)[0],200)

if __name__=='__main__':unittest.main()
