"""Isolated synthetic policy fixtures; HTTP itself runs as a real local server."""
import copy
import http.client
import json
from pathlib import Path
import socket
import tempfile
import threading
import unittest
from unittest.mock import patch
from garage_audio.application import Application, private_token
from garage_audio.profile import DEFAULT
from garage_audio.webserver import Server, validate_authority
from garage_audio import ipc

class Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.calls = []
        def broker(p):
            self.calls.append(p)
            return {'muted':True,'generation':'synthetic'}
        self.app = Application(self.tmp.name,broker)

    def tearDown(self): self.tmp.cleanup()
    def runop(self,op,**args): return self.app.execute(op,args)

    def test_sessions_import_persistence_compare(self):
        sid = self.runop('sessions.create',name='Synthetic')['result']['id']
        text = '* Freq(Hz) SPL(dB) Phase(degrees)\n20 70 0\n100 75 -20\n'
        f = self.runop('measurements.import',session_id=sid,filename='synthetic.txt',format='rew-response',text=text)['result']
        import hashlib
        self.assertEqual(f['sha256'],hashlib.sha256(text.encode()).hexdigest())
        self.assertEqual(f['original_text'],text)
        self.assertEqual(Application(self.tmp.name).execute('sessions.get',{'id':sid})['result']['files'][0],f)
        self.assertTrue(self.runop('measurements.compare',session_id=sid,before_id=f['id'],after_id=f['id'])['ok'])
        mic = self.runop('measurements.import',session_id=sid,filename='mic.cal',format='mic-calibration',text='20 0\n100 1')['result']
        self.assertFalse(self.runop('measurements.compare',session_id=sid,before_id=f['id'],after_id=mic['id'])['ok'])
        other = self.runop('sessions.create',name='Other')['result']['id']
        self.assertFalse(self.runop('measurements.compare',session_id=other,before_id=f['id'],after_id=f['id'])['ok'])

    def test_import_validation(self):
        sid = self.runop('sessions.create',name='Fixture')['result']['id']
        for text in ('20 nan\n100 0','100 0\n20 0','20 0','20 0\n20 1','x'*262145):
            self.assertFalse(self.runop('measurements.import',session_id=sid,filename='x.txt',format='rew-response',text=text)['ok'])
        self.assertFalse(self.runop('sessions.get',id='../secret')['ok'])
        self.assertFalse(self.runop('measurements.import',session_id=sid,filename='../secret',format='rew-response',text='20 0\n100 0')['ok'])

    def test_consent_mute_and_profiles(self):
        for op in ('dsp.unmute','commissioning.start','playback.play','playback.next','playback.clear'):
            args = {'player_id':'arbitrary'} if op.startswith('playback.') else {}
            self.assertFalse(self.app.execute(op,args)['ok'])
        self.assertFalse(self.runop('dsp.mute',attended='PRESENT-GAINS-LOW')['ok'])
        self.assertTrue(self.runop('dsp.volume',db=-30)['ok'])
        self.assertEqual(self.calls,[{'cmd':'mute'},{'cmd':'volume','db':-30}])
        self.calls.clear()
        def failure(p): self.calls.append(p);raise OSError()
        self.app.broker=failure
        self.assertFalse(self.runop('dsp.volume',db=-30)['ok'])
        self.assertEqual(self.calls,[{'cmd':'mute'}])
        p = copy.deepcopy(DEFAULT); p['channels']['left']['peq']=[{'freq':100,'q':1,'gain':1}]
        self.assertFalse(self.runop('profiles.save',profile=p)['ok'])
        record = self.runop('profiles.save',profile=DEFAULT)['result']
        preview = self.runop('profiles.preview',id=record['id'])['result']
        self.assertFalse(preview['measured']);self.assertFalse(preview['acoustic_prediction'])
        self.assertFalse(self.runop('profiles.import_rew',id=record['id'],channel='left',text='Filter 1: ON PK Fc 100 Hz Gain 2 dB Q 1')['ok'])

    def test_partial_ipc_eof(self):
        path = str(Path(self.tmp.name)/'sock')
        sock = socket.socket(socket.AF_UNIX);sock.bind(path);sock.listen()
        def serve():
            c,_ = sock.accept();c.recv(4096);c.sendall(b'{"ok":');c.close();sock.close()
        t=threading.Thread(target=serve);t.start()
        with self.assertRaises(OSError):ipc.request({'cmd':'status'},path,timeout=1)
        t.join()

    def test_discoverable_schemas_play_url_and_offline_edits(self):
        capabilities=self.runop('capabilities')['result']
        schema=capabilities['operations']['playback.play_url']['input_schema']
        self.assertEqual(schema['required'],['player_id','url'])
        self.assertFalse(schema['additionalProperties'])
        self.assertEqual(schema['properties']['url']['type'],'string')
        result=self.runop('playback.play_url',player_id='garage',url='https://example.invalid/media.flac')
        self.assertEqual(result['error']['code'],'attended_authority_required')
        for url in ('file:///etc/passwd','http://user:pass@example.invalid/a','http://example.invalid/a b'):
            self.assertEqual(self.runop('playback.play_url',player_id='garage',url=url)['error']['code'],'invalid_arguments')
        self.assertFalse(self.runop('playback.play_url',player_id='garage',url='https://example.invalid/a',attended='PRESENT-GAINS-LOW')['ok'])
        record=self.runop('profiles.save',profile=DEFAULT)['result']
        edit=self.runop('profiles.edit',id=record['id'],channel='left',delay_ms=1.25,invert=True,mute=True,crossover_hz=90,peq=[{'freq':100,'q':1,'gain':-3}])['result']
        self.assertNotEqual(edit['id'],record['id'])
        self.assertEqual(edit['profile']['channels']['left']['delay_ms'],1.25)
        self.assertTrue(edit['profile']['channels']['left']['invert'])
        self.assertFalse(edit['delay_inferred'])
        self.assertEqual(self.runop('profiles.get',id=record['id'])['result'],record)
        for args in ({'delay_ms':2},{'channel':'left','delay_ms':51},{'channel':'left','invert':'yes'},{'attended':'present'},{'channel':'left','peq':[{'freq':100,'q':1,'gain':1}]},{}):
            self.assertFalse(self.runop('profiles.edit',id=record['id'],**args)['ok'])
        self.assertEqual(self.calls,[])

    def test_bom_and_nonfinite_rows(self):
        sid=self.runop('sessions.create',name='BOM fixture')['result']['id']
        text='\ufeff20 70\n100 75'
        result=self.runop('measurements.import',session_id=sid,filename='bom.txt',format='rew-response',text=text)['result']
        import hashlib
        self.assertEqual(result['original_text'],text)
        self.assertEqual(result['sha256'],hashlib.sha256(text.encode('utf-8')).hexdigest())
        self.assertEqual([p['frequency_hz'] for p in result['points']],[20,100])
        for kind in ('rew-response','mic-calibration'):
            for token in ('NaN','Inf','+Inf','-Inf','Infinity','+Infinity','-Infinity'):
                for column in range(3):
                    fields=['50','70','0'];fields[column]=token
                    body='20 70 0\n'+' '.join(fields)+'\n100 75 0'
                    self.assertFalse(self.runop('measurements.import',session_id=sid,filename='bad.txt',format=kind,text=body)['ok'],(kind,token,column))
            self.assertFalse(self.runop('measurements.import',session_id=sid,filename='bad.txt',format=kind,text='unknown arbitrary header\n20 0\n100 1')['ok'])
            self.assertTrue(self.runop('measurements.import',session_id=sid,filename='comment.txt',format=kind,text='* REW exported response\n# recognized comment\n20 0\n100 1')['ok'])

    def test_lightweight_manifests_and_storage_limits(self):
        sid=self.runop('sessions.create',name='Bounded')['result']['id']
        imported=self.runop('measurements.import',session_id=sid,filename='curve.txt',format='rew-response',text='20 70\n100 75')['result']
        listing=self.runop('sessions.list')['result']
        file=listing[0]['files'][0]
        self.assertNotIn('original_text',file);self.assertNotIn('points',file)
        self.assertEqual(file['point_count'],2);self.assertEqual(file['sha256'],imported['sha256'])
        original_read=self.app.read
        def no_detail(collection,identifier):
            self.assertNotIn(collection,('sessions','profiles'))
            return original_read(collection,identifier)
        with patch.object(self.app,'read',side_effect=no_detail):
            self.assertTrue(self.runop('sessions.list')['ok'])
            self.assertEqual(self.runop('status')['result']['calibration']['sessions'],1)
        total=lambda:sum(p.stat().st_size for p in Path(self.tmp.name).glob('*/*.json'))
        with patch('garage_audio.application.MAX_STORAGE_BYTES',total()+16384):
            self.assertTrue(self.runop('profiles.save',profile=DEFAULT)['ok'])
        with patch('garage_audio.application.MAX_STORAGE_BYTES',total()+10):
            result=self.runop('measurements.import',session_id=sid,filename='another.txt',format='rew-response',text='20 1\n100 2')
            self.assertEqual(result['error']['code'],'storage_limit')
        self.assertEqual(len(self.runop('sessions.get',id=sid)['result']['files']),1)
        with patch('garage_audio.application.MAX_PROFILES',1):
            self.assertEqual(self.runop('profiles.save',profile=DEFAULT)['error']['code'],'storage_limit')
        with patch('garage_audio.application.MAX_RECORD_BYTES',100):
            self.assertEqual(self.runop('sessions.get',id=sid)['error']['code'],'storage_limit')
        with patch.object(self.app,'read',side_effect=no_detail):
            self.assertTrue(self.runop('profiles.list')['ok'])

    def test_credentials_and_concurrent_state(self):
        token=Path(self.tmp.name)/'token';token.write_text('x'*32);token.chmod(0o644)
        with self.assertRaises(ValueError):private_token(token)
        token.chmod(0o600);self.assertEqual(private_token(token),'x'*32)
        link=Path(self.tmp.name)/'link';link.symlink_to(token)
        with self.assertRaises(ValueError):private_token(link)
        sid=self.runop('sessions.create',name='Concurrent')['result']['id']
        def add(i):
            other=Application(self.tmp.name)
            result=other.execute('measurements.import',{'session_id':sid,'filename':f'{i}.txt','format':'rew-response','text':'20 0\n100 1'})
            self.assertTrue(result['ok'])
        threads=[threading.Thread(target=add,args=(i,)) for i in range(8)]
        for t in threads:t.start()
        for t in threads:t.join()
        self.assertEqual(len(self.runop('sessions.get',id=sid)['result']['files']),8)

    def test_http_boundaries(self):
        server=Server(('127.0.0.1',0),self.app,'t'*32,self.tmp.name)
        t=threading.Thread(target=server.serve_forever,daemon=True);t.start()
        port=server.server_address[1];host=f'127.0.0.1:{port}'
        def request(path,body=None,headers=None):
            c=http.client.HTTPConnection('127.0.0.1',port)
            h={'Host':host,'Content-Type':'application/json',**(headers or {})}
            c.request('POST' if body is not None else 'GET',path,json.dumps(body) if body is not None else None,h)
            r=c.getresponse();data=json.loads(r.read());cookie=r.getheader('Set-Cookie');status=r.status;c.close()
            return status,data,cookie
        try:
            self.assertEqual(request('/api/session')[0],401)
            for origin in (None,'null','http://evil'):
                h={} if origin is None else {'Origin':origin}
                self.assertEqual(request('/api/login',{'token':'t'*32},h)[0],403)
            self.assertEqual(request('/api/login',{'token':'t'*32},{'Host':'evil','Origin':'http://evil'})[0],403)
            code,data,cookie=request('/api/login',{'token':'t'*32},{'Origin':'http://'+host})
            self.assertEqual(code,200)
            self.assertEqual(request('/api/login',{'token':'é'},{'Origin':'http://'+host})[0],401)
            self.assertEqual(request('/api/login',{'token':'\ud800'},{'Origin':'http://'+host})[0],401)
            h={'Origin':'http://'+host,'Cookie':cookie.split(';')[0]}
            self.assertEqual(request('/api/operations',{'operation':'status','args':{}},h)[0],403)
            h['X-CSRF-Token']='é'
            self.assertEqual(request('/api/operations',{'operation':'status','args':{}},h)[0],403)
            h['X-CSRF-Token']=data['result']['csrf']
            self.assertTrue(request('/api/operations',{'operation':'sessions.create','args':{'name':'HTTP fixture'}},h)[1]['ok'])
            self.assertEqual(request('/api/logout',{},h)[0],200)
            self.assertEqual(request('/api/session',headers=h)[0],401)
        finally:server.shutdown();server.server_close();t.join()

    def test_private_lan_authority_configuration(self):
        for authority in ('192.168.68.74:8098','127.0.0.1:8098','localhost:8098','10.0.0.1:18098','172.16.0.1:8098','172.31.255.254:65535'):
            validate_authority(authority)
        for authority in ('*','*:8098','0.0.0.0:8098','8.8.8.8:8098','169.254.1.1:8098','100.64.0.1:8098','172.32.0.1:8098','127.0.0.2:8098','garage-server:8098','evil.example:8098','localhost.evil:8098','LOCALHOST:8098','user@192.168.68.74:8098','192.168.68.74:8098/path','192.168.68.74:8098?x=1','192.168.68.74:8098#x','http://192.168.68.74:8098','192.168.68.74','192.168.68.74:0','192.168.68.74:08098','192.168.68.74:65536','192.168.068.74:8098','192.168.68.999:8098','[::1]:8098','192.168.68.74:8098 ','192.168.68.74:8098\n'):
            with self.subTest(authority=authority),self.assertRaises(ValueError):
                Server(('127.0.0.1',0),self.app,'t'*32,self.tmp.name,[authority])

    def test_exact_lan_host_origin_guards(self):
        lan='192.168.68.74:8098'
        server=Server(('127.0.0.1',0),self.app,'t'*32,self.tmp.name,['127.0.0.1:8098','localhost:8098',lan])
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        def request(path,body=None,headers=None):
            c=http.client.HTTPConnection('127.0.0.1',server.server_address[1])
            h={'Host':lan,'Content-Type':'application/json',**(headers or {})}
            c.request('POST' if body is not None else 'GET',path,json.dumps(body) if body is not None else None,h)
            r=c.getresponse();data=json.loads(r.read());cookie=r.getheader('Set-Cookie');status=r.status;c.close()
            return status,data,cookie
        try:
            self.assertEqual(request('/api/status')[0],401)
            for host in ('192.168.68.75:8098','192.168.68.74:8099','evil.example:8098'):
                self.assertEqual(request('/api/login',{'token':'t'*32},{'Host':host,'Origin':'http://'+host,'X-Forwarded-Host':lan,'X-Forwarded-Proto':'http'})[0],403)
            for origin in (None,'null','http://evil.example','http://localhost:8098','https://'+lan):
                headers={} if origin is None else {'Origin':origin}
                self.assertEqual(request('/api/login',{'token':'t'*32},headers)[0],403)
            code,data,cookie=request('/api/login',{'token':'t'*32},{'Origin':'http://'+lan})
            self.assertEqual(code,200)
            h={'Origin':'http://'+lan,'Cookie':cookie.split(';')[0]}
            body={'operation':'status','args':{}}
            self.assertEqual(request('/api/operations',body,h)[0],403)
            h['X-CSRF-Token']=data['result']['csrf']
            self.assertTrue(request('/api/operations',body,h)[1]['ok'])
            h['Origin']='http://localhost:8098'
            self.assertEqual(request('/api/operations',body,h)[0],403)
        finally:server.shutdown();server.server_close();thread.join()

if __name__=='__main__':unittest.main()
