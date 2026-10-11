"""Isolated real HTTP handler + synthetic native chain for headless UI test.

Never started on production ports; root pairing socket admission replaced with
fixture owner UID only inside this process. No systemd, PCM or real credentials.
"""
import json
import os
from pathlib import Path
import socket
import threading
from types import SimpleNamespace
from garage_audio import webserver
from garage_audio.speaker_controller import Controller,serve_socket,request

class NativeFixture:
    def __init__(self):self.number=0
    def registration(self):return True # synthetic fixture, never real MA proof
    def start(self,cancel):
        self.number+=1;generation='ISOLATED-BROWSER-'+str(self.number)
        c,peer=socket.socketpair();c.settimeout(2)
        def responder():
            listening=False
            with peer:
                stream=peer.makefile('rb')
                while True:
                    data=stream.readline()
                    if not data:return
                    p=json.loads(data)
                    if p['cmd']=='phone-open':listening=True
                    result={'generation':generation,'muted':not listening,'volume_db':-60,'preparation_zero':not listening,'seconds':120}
                    try:peer.sendall(json.dumps({'ok':True,'result':result}).encode()+b'\n')
                    except OSError:return
        threading.Thread(target=responder,daemon=True).start()
        return c,{'generation':generation,'muted':True,'volume_db':-60,'preparation_zero':True}
    def stop(self):return True

class AppFixture:
    def __init__(self):self.jobs=SimpleNamespace(invalidate_all=lambda reason:None)
    def execute(self,operation,args):
        if operation=='status':result={'dsp':{'available':False},'playback':{'configured':False}}
        elif operation in ('profiles.list','sessions.list','playback.status'):result=[]
        elif operation=='capabilities':result={'operations':{}}
        else:return {'ok':False,'result':None,'error':{'code':'fixture_only','message':'Outside isolated speaker scope','requirements':[]}}
        return {'ok':True,'result':result,'error':None}

def main():
    import argparse
    p=argparse.ArgumentParser();p.add_argument('--directory',type=Path,required=True);p.add_argument('--port',type=int,required=True);p.add_argument('--bind',default='127.0.0.1',choices=['127.0.0.1','0.0.0.0']);p.add_argument('--trusted-lan-authority');a=p.parse_args()
    a.directory.mkdir(parents=True,exist_ok=True)
    controller=Controller(NativeFixture());websock=a.directory/'web.sock';pairsock=a.directory/'pair.sock'
    for path,pair in ((websock,False),(pairsock,True)):
        threading.Thread(target=serve_socket,args=(controller,path,os.getuid()),kwargs={'pair':pair},daemon=True).start()
    webserver.speaker_request=lambda payload:request(payload,websock)
    allowed=[f'127.0.0.1:{a.port}',f'localhost:{a.port}']
    if a.trusted_lan_authority:allowed.append(a.trusted_lan_authority)
    app=AppFixture();server=webserver.Server((a.bind,a.port),app,'ISOLATED-BROWSER-FIXTURE-TOKEN',Path(__file__).resolve().parents[1]/'web',allowed,trusted_lan_authority=a.trusted_lan_authority)
    print('ISOLATED fixture ready',flush=True);server.serve_forever()

if __name__=='__main__':main()
