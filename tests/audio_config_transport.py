"""Isolated real HTTP/MCP -> Unix/TCP test bridge -> real Docker worker.

No fake worker or provider. Bridge exists only because Docker Desktop cannot share
Linux Unix sockets with macOS. Container worker/bridge retain UID1002/1000 checks.
Run --serve DIR to retain a visual target; --journey DIR exercises both transports.
"""
import asyncio
import http.cookiejar
from http.cookiejar import CookieJar
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import threading
import time
from urllib.request import Request, build_opener, HTTPCookieProcessor, ProxyHandler

REPO=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(REPO))


def relay(client, upstream):
    with client, upstream:
        data=b''
        while b'\n' not in data:
            part=client.recv(4096)
            if not part or len(data)+len(part)>16384: return
            data+=part
        upstream.sendall(data)
        data=b''
        while b'\n' not in data:
            part=upstream.recv(4096)
            if not part or len(data)+len(part)>16384: return
            data+=part
        client.sendall(data)


def docker_worker():
    evidence=Path('/evidence/worker');evidence.mkdir(exist_ok=True)
    os.chown(evidence,1002,1003)
    code="import os;os.setgroups([1001,1003]);os.setgid(1003);os.setuid(1002);from garage_audio.audio_container import Worker;Worker('/tmp/config-runtime','/evidence/worker','file').run()"
    worker=subprocess.Popen([sys.executable,'-c',code])
    path='/tmp/config-runtime/public/control.sock'
    until=time.monotonic()+10
    while not Path(path).exists():
        if worker.poll() is not None or time.monotonic()>until: raise RuntimeError('worker startup failed')
        time.sleep(.05)
    listener=socket.socket();listener.bind(('0.0.0.0',18742));listener.listen(8)
    os.setgroups([1003]);os.setgid(1003);os.setuid(1000)
    while True:
        client,_=listener.accept()
        upstream=socket.socket(socket.AF_UNIX);upstream.connect(path)
        threading.Thread(target=relay,args=(client,upstream),daemon=True).start()


def serve(root):
    root.mkdir(mode=0o700,parents=True,exist_ok=True)
    token=root/'token'
    if not token.exists(): token.write_text(os.urandom(32).hex());token.chmod(0o600)
    name='garage-config-isolated-'+root.name.lower()+'-'+str(os.getpid())
    volume=root/'worker-volume';volume.mkdir(mode=0o755,exist_ok=True)
    command=['docker','run','--detach','--name',name,'--read-only','--tmpfs','/tmp:rw,nosuid,nodev,size=64m',
             '-p','127.0.0.1::18742','-v',str(REPO/'garage_audio')+':/app/garage_audio:ro',
             '-v',str(Path(__file__).resolve())+':/app/config_transport.py:ro',
             '-v',str(volume)+':/evidence:rw','--entrypoint','python',
             'garage-room-test:gate-room-ready-rcrpgpdk','/app/config_transport.py','--docker-worker']
    subprocess.run(command,check=True,stdout=open(root/'docker-id','w'),stderr=open(root/'docker-start.stderr','w'))
    path=root/'control.sock'
    path.unlink(missing_ok=True)
    listener=socket.socket(socket.AF_UNIX);listener.bind(str(path));listener.listen(8)
    def bridge():
        while True:
            client,_=listener.accept()
            try:
                # Docker Desktop can reassign an ephemeral published port on
                # restart. Resolve BEFORE forwarding each frame; never retry or
                # replay any configuration mutation after it has been sent.
                mapped=subprocess.check_output(['docker','port',name,'18742/tcp'],text=True,timeout=5).strip()
                host,port=mapped.rsplit(':',1)
                if host!='127.0.0.1' or not 1<=int(port)<=65535: raise ValueError('unexpected isolated endpoint')
                with open(root/'endpoint-history.jsonl','a') as log:
                    log.write(json.dumps({'observed':time.time(),'endpoint':mapped})+'\n')
                upstream=socket.create_connection((host,int(port)),timeout=20)
            except (OSError,ValueError,subprocess.SubprocessError): client.close();continue
            threading.Thread(target=relay,args=(client,upstream),daemon=True).start()
    threading.Thread(target=bridge,daemon=True).start()
    os.environ['GARAGE_AUDIO_CONTROL_SOCKET']=str(path)
    os.environ['GARAGE_DEPLOYMENT_MODE']='docker'
    os.environ['GARAGE_APP_TOKEN_FILE']=str(token)
    from garage_audio.application import Application
    from garage_audio.webserver import Server
    app=Application(root/'app-state')
    server=Server(('127.0.0.1',0),app,token.read_text(),REPO/'web')
    ready={'url':f'http://127.0.0.1:{server.server_address[1]}','pid':os.getpid(),'container':name,'token_ref':str(token)}
    (root/'ready.json').write_text(json.dumps(ready));print('READY',ready['url'],flush=True)
    def stop(*_):
        threading.Thread(target=server.shutdown,daemon=True).start()
    signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGINT,stop)
    try: server.serve_forever()
    finally:
        server.server_close();listener.close()
        with open(root/'docker.stdout','wb') as out,open(root/'docker.stderr','wb') as err:
            subprocess.run(['docker','logs',name],stdout=out,stderr=err)
        subprocess.run(['docker','stop',name],stdout=subprocess.DEVNULL,check=True)


async def journey(root):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    ready=json.loads((root/'ready.json').read_text());origin=ready['url']
    attempt=root/('attempt-'+str(time.time_ns()));attempt.mkdir()
    opener=build_opener(ProxyHandler({}),HTTPCookieProcessor(CookieJar()))
    def post(path,body,csrf=''):
        req=Request(origin+path,json.dumps(body).encode(),{'Content-Type':'application/json','Origin':origin,'X-CSRF-Token':csrf})
        with opener.open(req,timeout=25) as response: return json.load(response)
    login=post('/api/login',{'token':Path(ready['token_ref']).read_text()})
    csrf=login['result']['csrf']
    def http(operation,args={}): return post('/api/operations',{'operation':operation,'args':args},csrf)
    # Docker readiness, not a substituted status response.
    until=time.monotonic()+15
    while True:
        initial=http('audio.config.get')
        if initial['ok']: break
        if time.monotonic()>until: raise AssertionError(initial)
        time.sleep(.1)
    assert initial['result']['binding']['wiring']=='declared-virtual'
    assert initial['result']['loaded'] is None
    from garage_audio.audio_config import DEFAULT
    # Resume a retained failed attempt by the same public rollback journey, never
    # deleting state or resetting its accepted revision.
    recovered=False
    if initial['result']['configuration'] != DEFAULT:
        assert initial['result']['previous']==DEFAULT
        recovery=http('audio.config.propose',{'configuration':DEFAULT})['result']
        recovery_args={'proposal_id':recovery['proposal_id']}
        assert http('audio.config.validate',recovery_args)['ok']
        assert http('audio.config.rollback',recovery_args)['ok']
        recovered=True
    spec=dict(DEFAULT,layout='powered-sub-2.1',left=7,right=8,sub=9)
    proposal=http('audio.config.propose',{'configuration':spec})['result']
    assert proposal['diff']['sub']=={'before':None,'after':9}
    args={'proposal_id':proposal['proposal_id']}
    assert not http('audio.config.apply',args)['ok']
    assert http('audio.config.validate',args)['ok']
    accepted=http('audio.config.apply',args)
    assert accepted['ok'],accepted
    revision=accepted['result']['revision']
    assert not http('audio.config.apply',args)['ok']
    for bad in ({'configuration':dict(spec,binding={'kind':'hardware'})},{'configuration':spec,'approval':True}):
        assert not http('audio.config.propose',bad)['ok']
    assert not http('audio.config.apply',dict(args,consent='yes'))['ok']
    assert http('audio.config.get')['result']['hardware_apply_available'] is False
    env={**os.environ,'GARAGE_APP_URL':origin,'GARAGE_APP_TOKEN_FILE':ready['token_ref']}
    with open(attempt/'mcp.stderr','w') as errlog:
      async with stdio_client(StdioServerParameters(command=sys.executable,args=['-m','garage_audio.mcp_server'],env=env),errlog=errlog) as (read,write):
        async with ClientSession(read,write) as client:
            await client.initialize()
            tools=await client.list_tools();assert 'garage_operation' in {t.name for t in tools.tools}
            assert (await client.list_resources()).resources
            async def mcp(operation,args={}):
                value=json.loads((await client.call_tool('garage_operation',{'operation':operation,'args':args})).content[0].text)
                assert value['ok'],value
                return value['result']
            state=await mcp('audio.config.get')
            assert state['revision']==revision and state['configuration']==spec
            assert state['runtime_state']=='not_running' and state['loaded'] is None
            rollback=await mcp('audio.config.propose',{'configuration':state['previous']})
            args={'proposal_id':rollback['proposal_id']}
            await mcp('audio.config.validate',args)
            rolled=await mcp('audio.config.rollback',args)
            assert rolled['configuration']==DEFAULT and rolled['revision']==revision+1
            # Existing operations/resources and disabled runner remain compatible.
            assert (await mcp('capabilities'))['operations']['audio.config.apply']
            assert (await mcp('agent.status'))['inference_enabled'] is False
            session=await mcp('agent.session.create',{})
            run=await mcp('agent.run',{'id':session['id'],'expected_version':session['version'],'message':'Inspect configuration'})
            assert run['runner']=={'state':'inference_disabled','provider_called':False}
    assert http('audio.stop')['ok']
    subprocess.run(['docker','restart',ready['container']],check=True,stdout=open(attempt/'restart.stdout','w'))
    until=time.monotonic()+15
    while True:
        state=http('audio.config.get')
        if state['ok']: break
        if time.monotonic()>until: raise AssertionError(state)
        await asyncio.sleep(.1)
    assert state['result']['revision']==revision+1 and state['result']['configuration']==DEFAULT
    assert state['result']['loaded'] is None
    result={'http_apply_revision':revision,'mcp_same_revision':revision,'mcp_rollback_revision':revision+1,
            'recovered_previous_failed_attempt':recovered,
            'restarted_worker_revision':state['result']['revision'],'loaded':None,'provider_called':False,
            'binding_injection_rejected':True,'consent_injection_rejected':True,'stale_apply_rejected':True,
            'url':origin,'container':ready['container'],'lifetime':'retained until owning --serve process is explicitly stopped'}
    (attempt/'journey-result.json').write_text(json.dumps(result,indent=2))
    (root/'latest-journey.json').write_text(json.dumps(result,indent=2))
    print(json.dumps(result),flush=True)


if __name__=='__main__':
    if sys.argv[1]=='--docker-worker': docker_worker()
    elif sys.argv[1]=='--serve': serve(Path(sys.argv[2]))
    else: asyncio.run(journey(Path(sys.argv[2])))
