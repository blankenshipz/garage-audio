"""Real SSH target setup→Soloist choice→terms→abort, never consent/key/music.

Cached images only, isolated internal network/private fresh volumes, retained logs/state.
Not production onboarding, Spotify authentication, acoustic or full release proof.
"""
import asyncio
import hashlib
import io
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tarfile
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

SOURCE=Path(__file__).resolve().parents[1]
HOST='zach@192.168.68.74'
APP_IMAGE='sha256:44038332e66f37ddca6a49e049a9c818e3392c06b8f073e29cedaed3e28b82f6'
WORKER_IMAGE='sha256:ece60bd70b5dbfe21d20254988c25485d9b1229d24c44955e9ab52b663865d7a'
MA_IMAGE='ghcr.io/music-assistant/server:2.10.5@sha256:28023f8c0d96ca2496391f3218a6d70d7ef3ba84db846b3a3f3cd9ce7ef8aedc'
P=Path(sys.argv[1]);NAME='gaudio-setup-'+P.name.rsplit('.',1)[-1]
REMOTE='/home/zach/.local/state/garage-audio-checks/'+NAME
APP=NAME+'-app';WORKER=NAME+'-file';MA=NAME+'-ma'
started=[]


def save(name,value): (P/name).write_text(json.dumps(value,sort_keys=True,indent=2)+'\n')
def run(label,argv,input=None,check=True):
    result=subprocess.run(argv,input=input,text=True,capture_output=True,timeout=120)
    (P/(label+'.stdout')).write_text(result.stdout);(P/(label+'.stderr')).write_text(result.stderr)
    (P/(label+'.exit')).write_text(str(result.returncode)+'\n')
    if check and result.returncode: raise RuntimeError(label+' failed; private receipt retained')
    return result.stdout
def ssh(label,args,input=None,check=True):
    return run(label,['ssh','-T','-o','BatchMode=yes','-o','ConnectTimeout=8',HOST,shlex.join(args)],input,check)
def docker(label,args,check=True): return ssh(label,['docker',*args],check=check)
def target_python(label,code): return json.loads(ssh(label,['python3','-'],code))


def inventory():
    paths=[]
    for folder in ('garage_audio','web','profiles'):
        paths.extend(path for path in sorted((SOURCE/folder).rglob('*')) if path.is_file()
            and '__pycache__' not in path.parts and path.suffix!='.pyc'
            and path.name not in ('audio_container.py','audio_observer.py'))
    paths.append(SOURCE/'tests/spotify_protocol_ma_fixture.py')
    entries=[]
    with tarfile.open(P/'source.tar','w') as archive:
        for path in paths:
            assert not path.is_symlink()
            data=path.read_bytes();name=str(path.relative_to(SOURCE))
            entries.append({'path':name,'sha256':hashlib.sha256(data).hexdigest(),'bytes':len(data)})
            info=tarfile.TarInfo(name);info.mode=0o444;info.size=len(data);archive.addfile(info,io.BytesIO(data))
    digest=hashlib.sha256((P/'source.tar').read_bytes()).hexdigest()
    save('inventory.json',{'source_archive_sha256':digest,'entries':entries,'release_frozen':False,
                          'worker_exact_cached_image':WORKER_IMAGE,'no_active_audio_source_mount':True})
    return entries,digest


async def main():
    os.umask(0o077);P.mkdir(mode=0o700,parents=True,exist_ok=True)
    assert P.name.startswith('garage-spotify-protocol.') and NAME.startswith('gaudio-setup-')
    entries,digest=inventory()
    capacity=target_python('capacity','''import json,os
s=os.statvfs('/')
m={line.split(':')[0]:line.split(':')[1].strip() for line in open('/proc/meminfo')}
print(json.dumps({'free_bytes':s.f_bavail*s.f_frsize,'memory_available_kib':int(m['MemAvailable'].split()[0])}))
''')
    save('capacity.json',capacity)
    assert capacity['free_bytes']>2*1024**3+192*1024**2+2*(P/'source.tar').stat().st_size
    assert capacity['memory_available_kib']>1024*1024
    for label,image in [('app-image',APP_IMAGE),('worker-image',WORKER_IMAGE),('ma-image',MA_IMAGE)]:
        docker(label,['image','inspect',image,'--format','{{.Id}} {{.Architecture}}'])
    ssh('prepare',['sh','-c','umask 077; test ! -e '+shlex.quote(REMOTE)+'; mkdir -p '+shlex.quote(REMOTE+'/source')])
    run('copy',['scp','-q',str(P/'source.tar'),HOST+':'+REMOTE+'/source.tar'])
    target_python('extract',f'''import hashlib,json,tarfile
from pathlib import Path
p=Path({REMOTE!r});assert hashlib.sha256((p/'source.tar').read_bytes()).hexdigest()=={digest!r}
with tarfile.open(p/'source.tar') as archive:
 for member in archive:
  assert member.isfile() and not member.name.startswith('/') and '..' not in Path(member.name).parts
 archive.extractall(p/'source',filter='data')
print(json.dumps({{'source_verified':True}}))
''')
    network=NAME+'-internal';docker('network',['network','create','--internal','--label','garage.checkpoint='+NAME,network])
    volumes={key:NAME+'-'+key for key in ('ma-state','auth','app-state','public','events','evidence')}
    for key,value in volumes.items(): docker('volume-'+key,['volume','create','--label','garage.checkpoint='+NAME,value])
    mount=[]
    for key in volumes: mount+=['--mount',f'type=volume,source={volumes[key]},target=/{key}']
    docker('permissions',['run','--rm','--pull=never','--network=none','--read-only','--user=0:0',
        '--cap-drop=ALL','--cap-add=CHOWN','--cap-add=FOWNER','--cap-add=DAC_OVERRIDE',
        '--entrypoint=python',*mount,APP_IMAGE,'-c',
        'import os; from pathlib import Path; '+
        '[(os.chown(p,u,g),os.chmod(p,m)) for p,u,g,m in [("/ma-state",1000,1000,0o700),("/auth",1000,1000,0o700),("/app-state",1000,1000,0o700),("/public",1002,1003,0o710),("/events",1002,1001,0o710),("/evidence",1002,1003,0o700)]]; '+
        '[Path(p+"/fixture-only").touch(mode=0o600) for p in ("/ma-state","/auth")]; '+
        '[os.chown(p+"/fixture-only",1000,1000) for p in ("/ma-state","/auth")]; print("private isolated volumes initialized")'])
    common=['--pull=never','--init','--read-only','--cap-drop=ALL','--security-opt=no-new-privileges',
            '--label','garage.checkpoint='+NAME,'--pids-limit=64','--log-opt=max-size=1m','--log-opt=max-file=1']
    docker('start-ma',['run','-d','--name',MA,*common,'--network',network,'--network-alias','music-assistant',
        '--user=1000:1000','--memory=768m','--tmpfs=/tmp:rw,nosuid,uid=1000,gid=1000,size=64m',
        '--mount',f'type=volume,source={volumes["ma-state"]},target=/test-state',
        '--mount',f'type=volume,source={volumes["auth"]},target=/test-auth',
        '--mount',f'type=bind,source={REMOTE}/source/tests,target=/fixtures,readonly',
        '-e','GARAGE_SETUP_PROTOCOL_FIXTURE=terms-abort-only','--entrypoint=/app/venv/bin/python',
        MA_IMAGE,'/fixtures/spotify_protocol_ma_fixture.py']);started.append(MA)
    docker('wait-private-refs',['exec',MA,'/app/venv/bin/python','-c',
        'import time\nfrom pathlib import Path\ndeadline=time.monotonic()+45\n'+
        'while not Path("/test-auth/ready").exists():\n if time.monotonic()>deadline: raise RuntimeError("fixture readiness deadline")\n time.sleep(.1)\n'+
        'print("private reference readiness; values not read or returned")'])
    docker('start-file',['run','-d','--name',WORKER,*common,'--network=none','--user=1002:1003',
        '--group-add=1001','--memory=128m','--tmpfs=/tmp:rw,nosuid,size=8m',
        '--mount',f'type=volume,source={volumes["public"]},target=/run/garage-audio/public',
        '--mount',f'type=volume,source={volumes["events"]},target=/run/garage-audio/events',
        '--mount',f'type=volume,source={volumes["evidence"]},target=/evidence',WORKER_IMAGE]);started.append(WORKER)
    env={'GARAGE_APP_BIND':'0.0.0.0','GARAGE_APP_STATE':'/state','GARAGE_APP_TOKEN_FILE':'/auth/app-token',
         'GARAGE_APP_URL':'http://127.0.0.1:8098','GARAGE_APP_AUTHORITIES':'127.0.0.1:8098,localhost:8098',
         'GARAGE_DEPLOYMENT_MODE':'docker','GARAGE_AUDIO_CONTROL_SOCKET':'/run/garage-audio/public/control.sock',
         'MA_TOKEN_FILE':'/auth/ma-token','MA_URL':'http://music-assistant:8095','PYTHONDONTWRITEBYTECODE':'1'}
    flags=[]
    for key,value in env.items(): flags+=['-e',key+'='+value]
    binds=[]
    for folder in ('garage_audio','web','profiles'):
        binds+=['--mount',f'type=bind,source={REMOTE}/source/{folder},target=/app/{folder},readonly']
    docker('start-app',['run','-d','--name',APP,*common,'--network',network,'--user=1000:1000',
        '--group-add=1003','--memory=256m','--tmpfs=/tmp:rw,nosuid,size=8m',
        '--mount',f'type=volume,source={volumes["app-state"]},target=/state',
        '--mount',f'type=volume,source={volumes["auth"]},target=/auth,readonly',
        '--mount',f'type=volume,source={volumes["public"]},target=/run/garage-audio/public,readonly',
        *binds,*flags,APP_IMAGE,'python','-m','garage_audio.webserver']);started.append(APP)
    docker('wait-app',['exec',APP,'python','-c',
        'import time,urllib.request\ndeadline=time.monotonic()+15\n'+
        'while True:\n try:\n  urllib.request.urlopen("http://127.0.0.1:8098/",timeout=1).close();break\n except Exception:\n  if time.monotonic()>deadline: raise\n  time.sleep(.1)\n'+
        'print("matching app HTTP ready")'])
    client_env={key:os.environ[key] for key in ('HOME','PATH','SSH_AUTH_SOCK') if key in os.environ}
    client_env.update(GARAGE_HOST=HOST,GARAGE_MCP_CONTAINER=APP)
    params=StdioServerParameters(command='sh',args=[str(SOURCE/'scripts/dashboard-mcp')],env=client_env)
    with (P/'mcp.stderr').open('w') as error_log:
        async with stdio_client(params,errlog=error_log) as (read,write):
            async with ClientSession(read,write) as client:
                init=await client.initialize();tools=await client.list_tools();resources=await client.list_resources()
                save('mcp-inventory.json',{'initialize':init.model_dump(mode='json'),'tools':tools.model_dump(mode='json'),
                                         'resources':resources.model_dump(mode='json')})
                async def call(op,args=None):
                    args=args or {}
                    assert op in ('setup.spotify.status','setup.spotify.begin','setup.spotify.flow.get',
                                  'setup.spotify.flow.submit','setup.spotify.flow.abort','setup.status','audio.status')
                    if op=='setup.spotify.flow.submit': assert set(args)=={'id','step'} and args['step']=='backend'
                    result=await client.call_tool('garage_operation',{'operation':op,'args':args})
                    return json.loads(result.content[0].text)
                audio=await call('audio.status');save('audio-before.json',audio)
                assert audio['ok'] and audio['result']['state']=='off' and audio['result']['device_open'] is False
                status=await call('setup.spotify.status');save('spotify-before.json',status)
                assert status['ok'] and status['result']['configured'] is False
                begun=await call('setup.spotify.begin');save('begin.json',begun)
                assert begun['ok'] and begun['result']['step']=='backend'
                ident=begun['result']['id']
                selected=await call('setup.spotify.flow.submit',{'id':ident,'step':'backend'});save('terms.json',selected)
                assert selected['ok'] and selected['result']['step']=='soloist_terms'
                assert selected['result']['fields'][0]['value'] is False and selected['result']['sound_authority'] is False
                observed=await call('setup.spotify.flow.get',{'id':ident});save('mcp-terms-readback.json',observed)
                direct=json.loads(ssh('http-terms-readback',['docker','exec','-i',APP,'python','-'],
                    'import json\nfrom garage_audio.mcp_server import Adapter\nprint(json.dumps(Adapter().call("setup.spotify.flow.get",'+repr({'id':ident})+')))\n'))
                assert direct==observed;save('http-terms-readback.json',direct)
                aborted=await call('setup.spotify.flow.abort',{'id':ident});save('abort.json',aborted)
                assert aborted['ok'] and aborted['result']['type']=='abort'
                after=await call('setup.spotify.status');save('spotify-after.json',after)
                assert after['ok'] and after['result']['configured'] is False and after['result']['active_flow'] is None
                missing=json.loads(ssh('missing-credential',['docker','exec','-i',APP,'python','-'],
                    'import json,os\nos.environ.pop("MA_TOKEN_FILE",None)\nfrom garage_audio.application import Application\nprint(json.dumps(Application("/state/missing-credential").execute("setup.spotify.status",{})))\n'))
                assert not missing['ok'] and missing['error']['code']=='ma_credential_required';save('missing-credential.json',missing)
                setup=await call('setup.status');audio_after=await call('audio.status')
                save('setup-after.json',setup);save('audio-after.json',audio_after)
                assert setup['ok'] and setup['result']['agent']['inference_enabled'] is False
                assert audio_after['result']['state']=='off' and not audio_after['result']['owned_children']
    docker('no-soloist-artifacts',['exec',MA,'/app/venv/bin/python','-c',
        'from pathlib import Path; assert not any("soloist" in p.name.lower() for p in Path("/test-state/cache").rglob("*")); print("no downloaded Soloist cache artifacts")'])
    actual=target_python('source-capacity-after',f'''import hashlib,json,os
from pathlib import Path
root=Path({REMOTE+'/source'!r});entries={entries!r}
assert all(hashlib.sha256((root/e['path']).read_bytes()).hexdigest()==e['sha256'] for e in entries)
s=os.statvfs('/');print(json.dumps({{'source_unchanged':True,'free_bytes':s.f_bavail*s.f_frsize}}))
''')
    assert actual['free_bytes']>2*1024**3
    save('result.json',{'mode':'live','target':'isolated garage SSH MA2.10.5 + actual FILE worker',
        'source_archive_sha256':digest,'app_image':APP_IMAGE,'worker_image':WORKER_IMAGE,'ma_image':MA_IMAGE,
        'terms_readback_mcp_http_equal':True,'abort_without_provider_created':True,'fixture_ma_auth_only':True,
        'spotify_auth_terms_acceptance_key_download_music_sound_inference':False,'missing_credential_code':'ma_credential_required',
        'no_host_ports_or_devices':True,'internal_network':network,'remote_root':REMOTE,'source_capacity_after':actual,
        'limitations':['No Spotify authentication/key/terms acceptance','No configured running receiver','No physical device or sound',
                       'No rendered active-form geometry','Not production setup/release']})
    print('PASS actual MA setup→Soloist backend choice→human terms readback→abort, real SSH MCP/shared HTTP, FILE OFF and missing credential; no Spotify auth/key/consent/download/receiver/music/sound/inference')


if __name__=='__main__':
    try: asyncio.run(main())
    finally:
        for name in reversed(started):
            docker('logs-'+name,['logs',name],check=False)
            docker('stop-'+name,['stop','--time','10',name],check=False)
        if started: print('Only owned isolated services stopped; containers, private state/auth references, volumes and receipts retained.')
