"""Host-only owner of the external-normal lifecycle; never an HTTP/MCP action.

Policy must be the frozen audio-lifecycle-policy.py beside this entrypoint. This preserves the
deployed external-normal-r20 sequencing, with bounded classified recovery added.
"""
import json,pathlib,subprocess,time,signal,os,threading
from garage_audio.audio_boot import Boot,load_config
from garage_audio.audio_network import Carrier,atomic,docker,run
from garage_audio.audio_receiver import ReceiverEndpoint,ReceiverOwnershipError,PairingAdvertisements,pairing_instances,verify_pairing_instances
from garage_audio.audio_recovery import Supervisor,owner_lock,recoverable,verify_closed

ROOT=pathlib.Path(__file__).parent

def generation(config,stop,root,ready=None):
 receipt=root/('generation-'+str(time.time_ns()));c=Carrier(receipt)
 host_route=None;advertisements=None;result='stopped';diagnostic_stop=threading.Event();diagnostic_thread=None
 try:
  c.start(config['hook']);b=Boot(c,config);network=b.create()
  worker=json.loads(docker('inspect',b.roles['audio-device']))[0]
  host=worker['HostConfig'];mounts=worker['Mounts']
  standby=worker['Config']['Cmd']==['--mode','standby']
  if standby:
   # Existing external-r18 consumed-ledger standby validation, not scalar testing.
   if host['Devices'] or host.get('DeviceCgroupRules') or host['Privileged'] or host.get('CapAdd'):
    raise PermissionError('standby forbids device mappings and added capabilities')
   if any(m['Destination'] in ('/dev/snd','/host-snd','/host-asound','/device-policy.json','/device-lock') or m['Source'].startswith('/dev/snd') for m in mounts):
    raise PermissionError('standby forbids DAC/device-policy mounts')
  else:
   if worker['Config']['Cmd']!=['--mode','hardware'] or host['Privileged'] or host.get('CapAdd'):
    raise PermissionError('explicit unprivileged hardware binding required')
   if len(host['Devices'])!=2 or any(m['Destination'].startswith('/qualification') for m in mounts):
    raise PermissionError('exact two UMC nodes and no qualification mounts required')
  def diagnostic():
   # Metadata only, not microphone/diagnostic recording. Bound thread to generation.
   while not diagnostic_stop.is_set():
    path=receipt/'authority/revoked.json'
    if path.exists():
     try:atomic(receipt/'first-revocation.json',json.loads(path.read_text()))
     except (OSError,ValueError):pass
     return
    if c.guard.poll() is not None:
     atomic(receipt/'first-guard-exit.json',{'exit':c.guard.returncode,'time':time.monotonic()});return
    diagnostic_stop.wait(.01)
  diagnostic_thread=threading.Thread(target=diagnostic,daemon=True);diagnostic_thread.start()
  docker('start','-a',b.roles['audio-volume-init'])
  if json.loads(docker('inspect',b.roles['audio-volume-init']))[0]['State']['ExitCode']!=0:raise RuntimeError('volume init failed')
  for role in ('audio-device','music-assistant','dashboard'):
   if stop.is_set():return 'stopped'
   c.start_registered(b.roles[role],shared_namespace=role=='music-assistant')
   if role=='dashboard':
    c.management(network,b.roles[role]);pid=json.loads(docker('inspect',b.roles[role]))[0]['State']['Pid']
    run('nsenter','-t',str(pid),'-n','ip','route','add',c.address+'/32','dev','eth0')
    net=json.loads(docker('network','inspect',network))[0]
    bridge=net['Options'].get('com.docker.network.bridge.name','br-'+network[:12]);gateway=net['IPAM']['Config'][0]['Gateway']
    c.ns('nft','add','element','inet','garage_audio','management','{',gateway,'}')
    run('ip','route','add',c.address+'/32','dev',bridge,'src',gateway);host_route=(c.address,bridge)
    atomic(c.receipt/'authority/app-ready.json',{'generation':c.generation,'route_ready':True},mode=0o444)
  # Preserve admission BEFORE transport start: no receiver under old sync policy.
  deadline=time.monotonic()+45;policy_ok=False
  while time.monotonic()<deadline and not stop.is_set():
   c.healthy();attempt=receipt/('policy-'+str(time.time_ns()))
   with (attempt.with_suffix('.stdout')).open('wb') as out,(attempt.with_suffix('.stderr')).open('wb') as err,(ROOT/'audio-lifecycle-policy.py').open('rb') as code:
    p=subprocess.run(['docker','exec','-i','--user','1000:1000',b.roles['dashboard'],'python','-'],stdin=code,stdout=out,stderr=err,timeout=15)
   attempt.with_suffix('.exit').write_text(str(p.returncode))
   if p.returncode==0:policy_ok=True;break
   stop.wait(2)
  if stop.is_set():return 'stopped'
  if not policy_ok:raise RuntimeError('supported Soloist policy admission failed before transport')
  code="from garage_audio.audio_container import request;from pathlib import Path;import json;print(json.dumps(request(Path('/run/garage-audio/public/control.sock'),{'operation':'audio.status'})))"
  value=json.loads(subprocess.check_output(['docker','exec','--user','1000:1000',b.roles['dashboard'],'python','-c',code],text=True,timeout=5))
  if value['state']!='off' or value['owned_children'] or value['device_open'] or value['scalar_readers_ready']:
   raise PermissionError('fresh OFF worker with no device/children/readers required')
  if standby:
   if value['device_mode']!='standby' or not value.get('standby') or not value.get('qualification_attempt') or not value.get('qualification_terminal'):
    raise PermissionError('consumed-ledger standby not ready')
  elif value['device_mode']!='hardware' or value.get('qualification_attempt') or value['segment']!=0:
   raise PermissionError('fresh OFF ordinary hardware worker required')
  atomic(receipt/'hardware-preadmission.json',value)
  advertisements=PairingAdvertisements(b.roles['music-assistant'],c.address)
  for role in ('audio-transport','audio-observer'):
   if stop.is_set():return 'stopped'
   c.start_registered(b.roles[role],shared_namespace=True)
  atomic(receipt/'boot-ready.json',{'generation':c.generation,'roles':b.roles,'sound_authority':False})
  atomic(pathlib.Path('/etc/garage-audio/ma-enrollment/network-endpoint.json'),{'generation':c.generation,'registry':str(c.registry),'endpoint':'http://'+c.address+':8095'})
  subprocess.run(['systemctl','start','garage-ma-enrollment-admission.service'],check=True)
  receiver=ReceiverEndpoint(config['receiver_binary_sha256']);observed=None;deadline=time.monotonic()+60;pairing=[];pairing_check=0;candidate=None;stable_since=None;pairing_baseline=[]
  while not stop.is_set():
   c.healthy();pid=json.loads(docker('inspect',b.roles['music-assistant']))[0]['State']['Pid']
   try:current=receiver.observe(pid,c.inode)
   except ReceiverOwnershipError as error:
    candidate=None;stable_since=None
    if error.reason!='owner_absent' or observed is not None or time.monotonic()>deadline:raise
   except PermissionError:
    raise  # Never retry a permission denial/ambiguous or changed receiver owner.
   except (OSError,ValueError):
    candidate=None;stable_since=None
    if observed is not None or time.monotonic()>deadline:raise RuntimeError('receiver ownership unavailable')
   else:
    if observed is None:
     if current!=candidate:
      candidate=current;stable_since=time.monotonic()
      atomic(receipt/'receiver-preadmission.json',{'observation':current,'stable_since':stable_since,'admitted':False,'sound_authority':False})
     if time.monotonic()-stable_since<3:
      stop.wait(.2);continue
    if observed is not None and current!=observed:raise RuntimeError('receiver owner changed')
    first_admission=observed is None
    receiver.publish(receipt/'authority',current);observed=current;c.receiver_process={'pid':current['pid'],'birth':current['birth']};c.write_registry()
    atomic(receipt/'receiver-listener.json',current)
    if time.monotonic()-pairing_check>.2:
     if pairing_baseline:verify_pairing_instances(current,pid,c.inode,c.address,pairing_baseline)
     instances=pairing_instances(current,pid,c.inode,c.address,advertisements.ports())
     ports=sorted({r['port'] for r in instances})
     if instances and pairing_baseline and instances!=pairing_baseline:raise RuntimeError('advertised pairing socket instance changed')
     if pairing and not ports:
      c.ns('nft','delete','element','inet','garage_audio','pairing','{',','.join(map(str,pairing)),'}');pairing=[]
     if not pairing and ports:
      c.ns('nft','add','element','inet','garage_audio','pairing','{',','.join(map(str,ports)),'}');pairing=ports;pairing_baseline=instances
     atomic(receipt/'pairing-listeners.json',{'generation':c.generation,'ports':ports,'advertisement':'_spotify-connect._tcp.local.','actual_verified_socket_ports':sorted({r['port'] for r in pairing_baseline}),'listener_instances':pairing_baseline,'indeterminate_ingress_closed':not bool(ports)})
     pairing_check=time.monotonic()
    if first_admission and ready:ready(c.generation)
   stop.wait(.2)
 except Exception as error:
  result='recoverable' if recoverable(error,receipt) else 'terminal'
  atomic(receipt/'driver-failure.json',{'class':type(error).__name__,'errno':getattr(error,'errno',None),'time':time.monotonic(),'recovery':result,'guard_exit':c.guard.poll() if c.guard else None})
 finally:
  # Any cleanup failure propagates, preventing Supervisor from making a new generation.
  diagnostic_stop.set()
  if diagnostic_thread:diagnostic_thread.join()
  try:
   if advertisements:advertisements.close()
  finally:
   try:subprocess.run(['systemctl','stop','garage-ma-enrollment-admission.service','garage-ma-enrollment.service'],check=True,timeout=20)
   finally:
    try:c.close()
    finally:
     if host_route:run('ip','route','del',host_route[0]+'/32','dev',host_route[1])
  if result=='recoverable':verify_closed(c)
 return 'stopped' if stop.is_set() else result

def main():
 if os.geteuid()!=0:raise PermissionError('trusted host administrator only')
 stop=threading.Event()
 signal.signal(signal.SIGTERM,lambda *_:stop.set());signal.signal(signal.SIGINT,lambda *_:stop.set())
 lock=owner_lock()
 try:
  root=pathlib.Path('/var/lib/garage-audio-network');root.mkdir(mode=0o700,exist_ok=True)
  # Revalidate all frozen inputs and image IDs for EVERY generation.
  supervisor=Supervisor(root,stop)
  result=supervisor.run(lambda:generation(load_config(),stop,root,supervisor.ready))
  if result!='stopped':raise SystemExit(1)
 finally:os.close(lock)

if __name__=='__main__':main()
