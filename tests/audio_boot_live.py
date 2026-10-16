"""Real DHCP/Compose boot transaction and MA→PW→Camilla FILE; synthetic receiver/WAV."""
import array,json,os,sys,time
from pathlib import Path
from garage_audio.audio_network import Carrier,IMAGE,docker,atomic
from garage_audio.audio_boot import Boot

source=Path(__file__).resolve().parents[1];root=Path(sys.argv[1]);root.mkdir(mode=0o700)
# Public source fixtures must be readable by the actual non-root worker/MA identities.
# No credential/state files are copied or chmodded.
(source/'garage_audio').chmod(0o755)
for module in (source/'garage_audio').glob('*.py'):module.chmod(0o644)
(source/'tests/audio_ma_fixture.py').chmod(0o644)
MA='sha256:28023f8c0d96ca2496391f3218a6d70d7ef3ba84db846b3a3f3cd9ce7ef8aedc'
def journey(case):
 c=Carrier(root/case)
 try:
  c.start(source/'scripts/bootstrap-audio-network.py')
  c.ns('nft','add','element','inet','garage_audio','self_stream','{','18088','}')
  ipc=c.receipt/'ipc';ipc.mkdir(mode=0o711);evidence=c.receipt/'pcm';evidence.mkdir(mode=0o750);os.chown(evidence,1002,1003)
  for name,uid,gid,mode in [('pipewire',1001,1003,0o770),('events',1002,1001,0o710),('public',1002,1003,0o710)]:
   p=ipc/name;p.mkdir();os.chown(p,uid,gid);p.chmod(mode)
  generation='${GARAGE_AUDIO_NETWORK_GENERATION}'
  def service(image,entry,user,namespace=True):
   value={'image':image,'entrypoint':entry,'user':user,'read_only':True,'restart':'no','cap_drop':['ALL'],
    'security_opt':['no-new-privileges:true'],'pids_limit':96,'labels':{'garage.audio.generation':generation},'tmpfs':['/tmp:rw,size=64m']}
   if namespace:value['network_mode']='container:${GARAGE_AUDIO_CARRIER_ID}'
   else:value['network_mode']='none'
   return value
  volumes=[str(ipc)+':/run/garage-audio',str(evidence)+':/evidence']
  volumes+=[str(source/'garage_audio')+':/app/garage_audio:ro']
  worker=service(IMAGE,['python','-m','garage_audio.audio_container'],'1002:1003',False)
  worker['group_add']=['1001'];worker['volumes']=volumes+['${GARAGE_AUDIO_NETWORK_AUTHORITY_DIR}:/network:ro']
  worker['environment']={'GARAGE_AUDIO_NETWORK_LEASE':'/network/authority.json','GARAGE_AUDIO_NETWORK_GENERATION':generation,
   'PIPEWIRE_RUNTIME_DIR':'/run/garage-audio/pipewire','PIPEWIRE_REMOTE':'garage-pipewire'}
  transport=service(IMAGE,['sh','/app/audio/container-transport'],'1001:1001');transport['group_add']=['1003'];transport['volumes']=volumes
  transport['environment']={'MA_SLIMPROTO_HOST':'127.0.0.1'}
  ma=service(MA,['/app/venv/bin/python','/fixtures/audio_ma_fixture.py'],'1000:1000')
  ma['volumes']=[str(source/'tests/audio_ma_fixture.py')+':/fixtures/audio_ma_fixture.py:ro'];ma['environment']={'GARAGE_FIXTURE_IP':c.address}
  ma['tmpfs']=['/tmp:rw,uid=1000,gid=1000,size=64m','/test-state:rw,uid=1000,gid=1000,size=96m']
  observer=service(IMAGE,['sleep','infinity'],'1001:1001') # Receiver/identity seam synthetic, actual shared namespace.
  app=service(IMAGE,['python','-c',"import json,time;from pathlib import Path; p=Path('/network/app-ready.json'); t=time.monotonic()+25\nwhile not p.exists() and time.monotonic()<t: time.sleep(.05)\nassert p.exists(); Path('/tmp/app-ready').write_text('ready'); time.sleep(300)"],'1000:1000',False)
  del app['network_mode'];app['networks']=['management'];app['extra_hosts']=['music-assistant:${GARAGE_AUDIO_LAN_LEASE}'];app['environment']={'MA_URL':'http://music-assistant:8095'}
  app['volumes']=['${GARAGE_AUDIO_NETWORK_AUTHORITY_DIR}:/network:ro']
  init=service(IMAGE,['true'],'0:0',False)
  roles={'audio-device':worker,'audio-transport':transport,'music-assistant':ma,'audio-observer':observer,'dashboard':app,'audio-volume-init':init}
  compose=c.receipt/'compose.json';compose.write_text(json.dumps({'services':roles,'networks':{'management':{'external':True,'name':'${GARAGE_AUDIO_MANAGEMENT_NETWORK}'}}}))
  env=c.receipt/'frozen.env';env.write_text('')
  config={'project':'gaboottest'+c.generation[:10],'compose':[str(compose)],'environment':str(env),'images':{r:v['image'] for r,v in roles.items() if r!='audio-volume-init'}}
  boot=Boot(c,config);network=boot.create()
  assert all(not json.loads(docker('inspect',i))[0]['State']['Running'] for i in boot.roles.values())
  assert len(c.dependents)==6 and c.dependents[0]==boot.roles['audio-device']
  assert not (c.receipt/'authority/app-ready.json').exists()
  boot.start(network)
  assert set(c.dependent_processes)==set(boot.roles.values())-{boot.roles['audio-volume-init']}
  def status():return json.loads(docker('exec','--user','1000:1003',boot.roles['audio-device'],'python','-m','garage_audio.audio_container','--status'))
  for _ in range(60):
   try:
    if status()['network_admitted']:break
   except Exception:pass
   time.sleep(.1)
  assert status()['state']=='off' and not list(evidence.glob('*.f32'))
  def api(path,payload=None):
   code="import json,urllib.request; p="+repr(payload)+";r=urllib.request.urlopen(urllib.request.Request('http://127.0.0.1:18088' + "+repr(path)+",data=json.dumps(p).encode() if p else None,headers={'Content-Type':'application/json'}),timeout=4);print(r.read().decode())"
   return json.loads(c.ns('python3','-c',code))
  for _ in range(100):
   try:
    if api('/fixture/state')['available']:break
   except Exception:pass
   time.sleep(.2)
  else:raise AssertionError('actual MA player not registered')
  alias=docker('exec',boot.roles['dashboard'],'python','-c',"import socket,urllib.request,time;from pathlib import Path; assert Path('/tmp/app-ready').exists();assert urllib.request.urlopen('http://music-assistant:8095/info',timeout=3).status==200;print(socket.gethostbyname('music-assistant'))").strip()
  assert alias==c.address
  seq=0
  def event(kind,ticket=None):
   nonlocal seq
   seq+=1;value={'epoch':c.generation,'sequence':seq,'kind':kind,'state':'playing','active':True,'logged_in':True,'volume':100}
   if ticket:value['ticket']=ticket
   code="import json;from pathlib import Path;from garage_audio.audio_container import request;print(json.dumps(request(Path('/run/garage-audio/events/event.sock'),"+repr(value)+")))"
   return json.loads(docker('exec','--user','1001:1001',boot.roles['audio-transport'],'python','-c',code))
  event('baseline');event('heartbeat');assert status()['state']=='off'
  print('PASS actual Compose create/register-all-before-start private route gate snapshot OFF',flush=True)
  api('/fixture/command',{'op':'play'});api('/fixture/command',{'op':'volume','value':100})
  ticket=event('prepare')['pending_ticket'];event('delta',ticket)
  for _ in range(8):event('heartbeat');time.sleep(.3)
  assert status()['state']=='playing'
  pcm=list(evidence.glob('*.f32'));assert len(pcm)==1
  values=array.array('f',pcm[0].read_bytes());peaks=[max(map(abs,values[i::12])) for i in range(12)]
  assert all(peaks[i]==0 for i in range(12) if i not in (2,3)) and abs(peaks[2]-.15295)<.012
  print('PASS actual MA Squeezelite PipeWire Camilla FILE transactional boot',peaks,flush=True)
  if case=='dependent-loss':docker('stop','-t','0',boot.roles['audio-observer'])
  else:c.client.terminate();c.client.wait(timeout=3)
  for _ in range(100):
   if (c.receipt/'guard-revoked.json').exists():break
   time.sleep(.1)
  result=json.loads((c.receipt/'guard-revoked.json').read_text())
  assert result['software_children_closed'] is True and all(result['containers_stopped'].values())
  oldsize=pcm[0].stat().st_size;time.sleep(.3);assert pcm[0].stat().st_size==oldsize
  try:c.start_registered(boot.roles['audio-device'])
  except RuntimeError:pass
  else:raise AssertionError('revoked generation rejoined')
  print('PASS generation loss worker-first closure no stale rejoin',case,flush=True)
 finally:
  for identity in c.dependents:
   try:(c.receipt/(identity[:12]+'.log')).write_text(docker('logs',identity))
   except Exception:pass
  c.close()
for case in (sys.argv[2:] or ('dependent-loss','recovery')):journey(case)
print('COVERAGE real Compose DHCP MA PW Camilla FILE; synthetic receiver/WAV and gateway identity; no hardware/Spotify',flush=True)
