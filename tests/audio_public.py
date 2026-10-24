"""Unit mock socket coverage; --linux runs actual FILE worker/application/HTTP IPC."""
import http.client
import hashlib
import json
import os
from pathlib import Path
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from garage_audio import audio_public
from garage_audio.application import Application

STATUS = {'state':'off','device_mode':'file','device_open':False,'physical_verified':False}


class SocketFixture:
    """Explicit unit mock, not live IPC acceptance."""
    def __init__(self,chunks): self.chunks,self.sent,self.timeouts = list(chunks),[],[]
    def __enter__(self): return self
    def __exit__(self,*args): pass
    def settimeout(self,value): self.timeouts.append(value)
    def connect(self,path): self.path=path
    def sendall(self,data): self.sent.append(data)
    def recv(self,count): return self.chunks.pop(0) if self.chunks else b''
    def getsockopt(self,*args): return struct.pack('3i',1,1002,1003)


class AdapterUnits(unittest.TestCase):
    def invoke(self,data,operation='audio.status'):
        fixture = SocketFixture(data)
        with patch.object(audio_public.socket,'socket',return_value=fixture):
            result=audio_public.request(operation)
        return result,fixture

    def test_exact_messages_bounded_fragmented_reply(self):
        frame=json.dumps({'ok':True,'result':STATUS}).encode()+b'\n'
        for operation in ('audio.status','audio.stop','audio.pause'):
            result,fixture=self.invoke([frame[:4],frame[4:]],operation)
            self.assertEqual(result,STATUS)
            self.assertEqual(json.loads(fixture.sent[0]),{'operation':operation})
            self.assertTrue(all(0<t<=audio_public.TIMEOUT for t in fixture.timeouts))

    def test_missing_socket_and_bad_frames_never_claim_success(self):
        for chunks in ([b''],[b'x'*16385],[b'{"ok":true}'],
                       [b'{"ok":true,"result":NaN}\n'],[b'{}\n{}\n'],
                       [b'{"ok":true,"ok":false,"error":"private"}\n'],
                       [b'{"ok":false,"error":"private path and grant"}\n']):
            with self.assertRaises(audio_public.AudioFailure) as error: self.invoke(chunks)
            self.assertNotIn('private',error.exception.message)
        with patch.object(audio_public.socket,'socket',side_effect=OSError('private path')):
            with self.assertRaises(audio_public.AudioFailure): audio_public.request('audio.status')

    def test_deadline_and_linux_peer_admission(self):
        fixture=SocketFixture([b'{'])
        with patch.object(audio_public.socket,'socket',return_value=fixture), \
             patch.object(audio_public.time,'monotonic',side_effect=[0,3]):
            with self.assertRaises(audio_public.AudioFailure): audio_public.request('audio.status')
        with patch.object(audio_public.socket,'SO_PEERCRED',123,create=True), \
             patch.object(audio_public.socket,'socket',return_value=fixture), \
             patch.object(fixture,'getsockopt',return_value=struct.pack('3i',1,1000,1003)):
            with self.assertRaises(audio_public.AudioFailure) as error: audio_public.request('audio.stop')
            self.assertEqual(error.exception.code,'audio_peer_rejected')

    def test_response_projection_rejects_nested_payloads_and_nonfinite_numbers(self):
        for extra in ({'reason':{'secret':'not forwarded'}},{'receiver_volume':{'grant':'not forwarded'}},
                      {'watchdog_seconds':float('inf')},{'outputs':[1,1]},
                      {'device_mode':'hardware','outputs':[1,2]}):
            frame=json.dumps({'ok':True,'result':{**STATUS,**extra}}).encode()+b'\n'
            with self.assertRaises(audio_public.AudioFailure): self.invoke([frame])
        result,_=self.invoke([json.dumps({'ok':True,'result':{**STATUS,'raw_grant':'not forwarded'}}).encode()+b'\n'])
        self.assertEqual(result,STATUS)
        result,_=self.invoke([json.dumps({'ok':True,'result':{**STATUS,'outputs':[1,2]}}).encode()+b'\n'])
        self.assertEqual(result['outputs'],[1,2])

    def test_extra_caller_fields_and_no_native_fallback(self):
        with tempfile.TemporaryDirectory(dir='/private/tmp/opencode') as root:
            app=Application(root,broker=lambda _: self.fail('Native fallback forbidden'))
            try:
                with patch.dict(os.environ,{'GARAGE_DEPLOYMENT_MODE':'docker'}):
                    self.assertFalse(app.execute('audio.status',{'path':'/anything'})['ok'])
                    with patch.object(audio_public,'request',side_effect=audio_public.AudioFailure('audio_unavailable','Unavailable')):
                        self.assertEqual(app.execute('audio.stop',{})['error']['code'],'audio_unavailable')
                        self.assertFalse(app.execute('setup.status',{})['result']['audio']['available'])
                        self.assertFalse(app.execute('status',{})['result']['audio']['available'])
                    self.assertEqual(app.execute('dsp.volume',{'db':-30})['error']['code'],'docker_audio_operation_unavailable')
            finally: app.agent_harness.close()


def client_journey(root,sock):
    from garage_audio.webserver import Server
    deadline=time.monotonic()+3
    while not Path(sock).exists() and time.monotonic()<deadline: time.sleep(.02)
    assert Path(sock).exists(),'Actual worker socket missing'
    os.environ.update(GARAGE_DEPLOYMENT_MODE='docker',GARAGE_AUDIO_CONTROL_SOCKET=sock)
    app=Application(root,broker=lambda _: (_ for _ in ()).throw(AssertionError('Native fallback forbidden')))
    for operation in ('audio.status','audio.stop','audio.pause'):
        result=app.execute(operation,{})
        assert result['ok'],result
        assert result['result']['state']=='off',result
        assert result['result']['device_mode']=='file',result
        assert result['result']['device_open'] is False,result
    setup=app.execute('setup.status',{})
    assert setup['ok'] and setup['result']['audio']['available'],setup
    assert setup['result']['device']['code']=='physical_device_unavailable',setup
    server=Server(('127.0.0.1',0),app,'fixture-not-a-real-credential-0000',Path(__file__).parents[1]/'web')
    thread=threading.Thread(target=server.serve_forever);thread.start()
    connection=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=3)
    authority=f'http://127.0.0.1:{server.server_port}'
    try:
        connection.request('POST','/api/login',json.dumps({'token':'fixture-not-a-real-credential-0000'}),
                           {'Content-Type':'application/json','Origin':authority})
        response=connection.getresponse(); body=json.loads(response.read()); assert response.status==200
        cookie=response.getheader('Set-Cookie').split(';')[0]
        headers={'Content-Type':'application/json','Origin':authority,'Cookie':cookie,'X-CSRF-Token':body['result']['csrf']}
        # Real HTTP Stop cannot wait behind the application's ordinary busy lock.
        with app.lock:
            connection.request('POST','/api/speakers/stop','{}',headers)
            response=connection.getresponse();body=json.loads(response.read());assert response.status==200 and body['result']['state']=='off',body
        connection.request('GET','/api/speakers/status',headers={'Cookie':cookie})
        response=connection.getresponse();body=json.loads(response.read());assert response.status==200 and body['result']['device_open'] is False,body
        connection.request('POST','/api/speakers/enable',json.dumps({'action':'fabricated'}),headers)
        response=connection.getresponse();body=json.loads(response.read());assert response.status==409,body
        connection.request('POST','/api/speakers/stop','{}',{'Content-Type':'application/json','Origin':authority,'Cookie':cookie})
        response=connection.getresponse();response.read();assert response.status==403
        print('Actual Linux FILE worker: typed status/stop/pause, setup FILE-not-physical readiness, HTTP Stop/status, enable denial and CSRF retained. No model, receiver event, DSP child, DAC or sound.')
    finally:
        connection.close();server.shutdown();thread.join();server.server_close()


def linux_journey():
    assert sys.platform.startswith('linux') and os.getuid()==0
    inventory = ['garage_audio/audio_container.py','garage_audio/audio_public.py',
                 'garage_audio/application.py','garage_audio/setup.py','garage_audio/webserver.py']
    before = {path:hashlib.sha256(Path(path).read_bytes()).hexdigest() for path in inventory}
    print('Observed local source inventory '+json.dumps(before,sort_keys=True),flush=True)
    root=Path(tempfile.mkdtemp(prefix='audio-public-'))
    root.chmod(0o755)
    for name,uid,gid,mode in [('run',1002,1003,0o711),('evidence',1002,1003,0o700),('state',1000,1000,0o700)]:
        path=root/name;path.mkdir(mode=mode);os.chown(path,uid,gid)
    def identity(uid,gid,groups):
        def switch(): os.setgroups(groups);os.setgid(gid);os.setuid(uid)
        return switch
    path=root/'run/public/control.sock'
    # Launch dashboard identity first, then become the worker owner before supervising
    # the new worker. No cross-owner signal or CAP_KILL is needed or attempted.
    client=subprocess.Popen([sys.executable,__file__,'--client',str(root/'state'),str(path)],
        preexec_fn=identity(1000,1000,[1003]))
    identity(1002,1003,[1001,1003])()
    worker=subprocess.Popen([sys.executable,'-m','garage_audio.audio_container','--root',str(root/'run'),'--evidence',str(root/'evidence')],
        stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    try:
        deadline=time.monotonic()+3
        while not path.exists() and time.monotonic()<deadline:
            if worker.poll() is not None: raise AssertionError('Actual worker exited before socket readiness')
            time.sleep(.02)
        assert path.exists(),'Actual worker socket missing'
        assert client.wait(timeout=10)==0,'Actual client journey failed'
    finally:
        worker.terminate()
        stdout,stderr=worker.communicate(timeout=5)
        sys.stdout.buffer.write(stdout);sys.stderr.buffer.write(stderr)
        assert worker.returncode==0,'Actual worker shutdown failed'
        assert before == {path:hashlib.sha256(Path(path).read_bytes()).hexdigest() for path in inventory},'Active source changed during proof'


if __name__=='__main__':
    if '--linux' in sys.argv: linux_journey()
    elif '--client' in sys.argv: client_journey(sys.argv[2],sys.argv[3])
    else: unittest.main(verbosity=2)
