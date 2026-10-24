"""Synthetic control-plane integration, no Docker/ALSA/network/provider access."""
import importlib.util,json,os,tempfile,threading,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from garage_audio import audio_recovery as recovery
from garage_audio import audio_network as network

spec=importlib.util.spec_from_file_location('lifecycle',Path(__file__).parents[1]/'scripts/audio-lifecycle.py')
lifecycle=importlib.util.module_from_spec(spec);spec.loader.exec_module(lifecycle)

class Recovery(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
 def test_caps_backoff_and_exhaustion(self):
  stop=SimpleNamespace(is_set=lambda:False,wait=lambda delay:self.delays.append(delay) or False)
  self.delays=[];calls=[]
  result=recovery.Supervisor(self.root,stop,lambda:0).run(lambda:calls.append(1) or 'recoverable')
  self.assertEqual((result,len(calls),self.delays),('exhausted',4,[5,30,120]))
 def test_lifetime_cap_does_not_reset_after_window(self):
  now=iter([0,700,1400,2100]);stop=SimpleNamespace(is_set=lambda:False,wait=lambda _:False)
  self.assertEqual(recovery.Supervisor(self.root,stop,lambda:next(now)).run(lambda:'recoverable'),'exhausted')
 def test_stop_cancels_backoff_and_terminal_does_not_retry(self):
  stop=threading.Event();calls=[]
  def wait(_):stop.set();return True
  with patch.object(stop,'wait',side_effect=wait):
   self.assertEqual(recovery.Supervisor(self.root,stop).run(lambda:calls.append(1) or 'recoverable'),'stopped')
  self.assertEqual(len(calls),1)
  stop.clear()
  self.assertEqual(recovery.Supervisor(self.root,stop).run(lambda:'terminal'),'terminal')
 def test_lock_prevents_second_owner(self):
  path=self.root/'lock';fd=recovery.owner_lock(path)
  try:
   with self.assertRaises(BlockingIOError):recovery.owner_lock(path)
  finally:os.close(fd)
 def test_classifier_excludes_security_intentional_and_worker_exits(self):
  value={'Id':'id','Config':{'Labels':{'garage.audio.generation':'g','com.docker.compose.service':'audio-observer'}},'State':{'Running':False,'Status':'exited','ExitCode':1,'OOMKilled':False}}
  with self.assertRaises(recovery.WorkloadCrash):recovery.check_dependent(value,'id',{},'g')
  for exitcode in [0,137,143]:
   value['State']['ExitCode']=exitcode;recovery.check_dependent(value,'id',{},'g')
  value['State']['ExitCode']=1
  for role in ['music-assistant','audio-device','unknown']:
   value['Config']['Labels']['com.docker.compose.service']=role;recovery.check_dependent(value,'id',{},'g')
  with self.assertRaises(PermissionError):recovery.check_dependent(value,'wrong',{},'g')
 def test_wrapped_permission_and_expired_consent_are_terminal(self):
  (self.root/'guard-failure.json').write_text(json.dumps({'kind':'owned_workload_crash'}))
  for error in [PermissionError(),RuntimeError('consent_expired'),RuntimeError('recording_expired'),RuntimeError('receiver owner changed')]:
   self.assertFalse(recovery.recoverable(error,self.root))
  outer=RuntimeError('network generation permanently revoked');outer.__cause__=PermissionError()
  self.assertFalse(recovery.recoverable(outer,self.root))
  self.assertTrue(recovery.recoverable(RuntimeError('network generation permanently revoked'),self.root))
 def test_stale_or_active_worker_closure_never_allows_replacement(self):
  (self.root/'guard-failure.json').write_text(json.dumps({'time':20}))
  ack=self.root/'ack.json'
  carrier=SimpleNamespace(dependents=[],id=None,worker_ack=ack,receipt=self.root,generation='g')
  for value in [{'generation':'g','state':'off','owned_children':[],'observed':10},
                {'generation':'g','state':'playing','owned_children':['child'],'observed':30}]:
   ack.write_text(json.dumps(value))
   with self.assertRaises(PermissionError):recovery.verify_closed(carrier)
  ack.write_text(json.dumps({'generation':'g','state':'off','owned_children':[],'observed':30}))
  recovery.verify_closed(carrier)
 def test_real_guard_records_pre_teardown_crash_and_security_wins(self):
  expected={'pid':123,'birth':'b','inode':7}
  record={'receipt':str(self.root),'generation':'g','owner':expected,'dhcp':expected,'receiver':expected,
          'carrier':'carrier','carrier_process':expected,'inode':7,'address':'192.0.2.1',
          'dependent_processes':{'app':expected,'worker':expected}}
  registry=self.root/'registry.json';registry.write_text(json.dumps(record));registry.chmod(0o600)
  (self.root/'lease.json').write_text(json.dumps({'event':'bound','ip':'192.0.2.1','received':0,'seconds':1000}))
  def inspect(*args):
   identity=args[1];role='dashboard' if identity=='app' else 'audio-device'
   return json.dumps([{'Id':identity,'Config':{'Labels':{'garage.audio.generation':'g','com.docker.compose.service':role}},
    'State':{'Pid':123 if identity!='app' else 0,'Running':identity!='app','Status':'exited' if identity=='app' else 'running','ExitCode':1 if identity=='app' else 0}}])
  original_stat=network.os.stat
  def proc_stat(path,*args,**kwargs):
   return SimpleNamespace(st_ino=7) if str(path)=='/proc/123/ns/net' else original_stat(path,*args,**kwargs)
  with patch.object(network.os,'geteuid',return_value=0),patch.object(network,'birth',return_value='b'),patch.object(network,'docker',side_effect=inspect),patch.object(network.os,'stat',side_effect=proc_stat),patch.object(network.time,'monotonic',return_value=1),patch.object(network.subprocess,'check_output',return_value='[{"addr_info":[{"local":"192.0.2.1"}]}]'),patch.object(network,'revoke') as revoke:
   network.guard_main(registry)
   self.assertEqual(json.loads((self.root/'guard-failure.json').read_text())['kind'],'owned_workload_crash')
   self.assertEqual(revoke.call_count,1)
   # An independently invalid sibling must override an earlier eligible crash.
   def invalid(*args):
    value=json.loads(inspect(*args))
    if args[1]=='worker':value[0]['Id']='wrong'
    return json.dumps(value)
   with patch.object(network,'docker',side_effect=invalid):network.guard_main(registry)
   self.assertEqual(json.loads((self.root/'guard-failure.json').read_text())['kind'],'terminal')

class Lifecycle(unittest.TestCase):
 setUp=Recovery.setUp
 def fixture(self,error=None,cleanup_error=False,standby=False,unsafe=False,ready_receiver=False,ready=None):
  events=[];root=self.root;stop=threading.Event()
  (root/'audio-lifecycle-policy.py').write_text('# isolated synthetic policy\n')
  class Carrier:
   def __init__(self,receipt):
    receipt.mkdir();(receipt/'authority').mkdir();self.receipt=receipt;self.registry=receipt/'registry.json';self.generation='g';self.inode=1;self.address='192.0.2.1';self.dependents=[];self.id='carrier';self.worker_ack=None;self.guard=SimpleNamespace(poll=lambda:None);self.health=0
   def start(self,*_):events.append('carrier-start')
   def healthy(self):
    self.health+=1
    if self.health>(6 if ready_receiver else 1):
     (self.receipt/'guard-failure.json').write_text(json.dumps({'kind':'owned_workload_crash'}))
     if error:raise error
     stop.set()
   def start_registered(self,role,**_):events.append('start:'+role)
   def management(self,*_):pass
   def ns(self,*_):pass
   def write_registry(self):pass
   def close(self):
    events.append('closed')
    if cleanup_error:raise PermissionError('fixture cleanup incomplete')
  class Boot:
   def __init__(self,c,config):self.roles={k:k for k in ['audio-device','audio-volume-init','music-assistant','dashboard','audio-transport','audio-observer']}
   def create(self):return 'network'
  def docker(*args):
   events.append(tuple(args))
   if args[0]=='inspect':
    role=args[1]
    return json.dumps([{'Id':role,'Config':{'Cmd':['--mode','standby' if standby else 'hardware'],'Labels':{'garage.audio.generation':'g'}},'HostConfig':{'Privileged':False,'CapAdd':None,'Devices':[] if standby and not unsafe else [{},{}]},'Mounts':[],'State':{'ExitCode':0,'Pid':123,'Running':False}}])
   if args[:2]==('network','inspect'):return json.dumps([{'Options':{},'IPAM':{'Config':[{'Gateway':'192.0.2.2'}]}}])
   return ''
  def command(argv,**kwargs):
   events.append(tuple(argv))
   if argv[0]=='docker':events.append('policy')
   return SimpleNamespace(returncode=0)
  def status(*args,**kwargs):
   events.append('status')
   return json.dumps({'state':'off','owned_children':[],'device_open':False,'device_mode':'standby' if standby else 'hardware','scalar_readers_ready':False,'segment':0,'standby':standby,'qualification_attempt':'consumed' if standby else None,'qualification_terminal':standby})
  adverts=SimpleNamespace(close=lambda:events.append('advertisements-close'),ports=lambda:[])
  endpoint=SimpleNamespace(observe=lambda *_:(_ for _ in ()).throw(FileNotFoundError()))
  if ready_receiver:
   endpoint=SimpleNamespace(observe=lambda *_:{'pid':123,'birth':'b'},publish=lambda *_:events.append('receiver-publish'))
   patch.object(lifecycle,'pairing_instances',return_value=[]).start()
   ticks=iter(range(1000))
   patch.object(lifecycle.time,'monotonic',side_effect=lambda:next(ticks)).start()
  self.addCleanup(patch.stopall)
  for name,value in [('ROOT',root),('Carrier',Carrier),('Boot',Boot),('docker',docker),('run',lambda *_:''),('PairingAdvertisements',lambda *_:adverts),('ReceiverEndpoint',lambda *_:endpoint)]:patch.object(lifecycle,name,value).start()
  patch.object(lifecycle.subprocess,'run',side_effect=command).start();patch.object(lifecycle.subprocess,'check_output',side_effect=status).start()
  # Endpoint declaration must never write host /etc in synthetic coverage.
  original=lifecycle.atomic
  patch.object(lifecycle,'atomic',side_effect=lambda path,value,**kw:None if str(path).startswith('/etc/') else original(path,value,**kw)).start()
  patch.object(recovery,'docker',side_effect=docker).start()
  config={'hook':'fixture','receiver_binary_sha256':'0'*64}
  return lambda:lifecycle.generation(config,stop,root,ready),events
 def test_ready_callback_requires_stable_receiver_and_follows_preadmission(self):
  admitted=[]
  call,events=self.fixture(ready_receiver=True,ready=lambda generation:admitted.append((generation,list(events))))
  self.assertEqual(call(),'stopped')
  self.assertEqual(len(admitted),1)
  self.assertEqual(admitted[0][0],'g')
  before=admitted[0][1]
  self.assertIn('status',before);self.assertIn('start:audio-observer',before)
  self.assertIn('receiver-publish',before);self.assertNotIn('closed',before)
 def test_absent_receiver_never_reports_ready(self):
  call,_=self.fixture(ready=lambda _:self.fail('receiver not admitted'))
  self.assertEqual(call(),'stopped')
 def test_wired_recovery_closes_before_replacement(self):
  call,events=self.fixture(RuntimeError('network generation permanently revoked'))
  self.assertEqual(call(),'recoverable')
  self.assertLess(events.index('policy'),events.index('start:audio-transport'))
  self.assertLess(events.index('status'),events.index('start:audio-observer'))
  self.assertLess(events.index('advertisements-close'),events.index('closed'))
  self.assertEqual(events[-1],('inspect','carrier'))
  self.assertFalse(any('audio.record' in str(x) or "'operation': 'audio.play'" in str(x) for x in events))
 def test_wired_permission_failure_cleans_up_without_retry(self):
  call,events=self.fixture(PermissionError('fixture denial'))
  self.assertEqual(call(),'terminal');self.assertIn('closed',events)
 def test_wired_deliberate_stop_and_cleanup_failure(self):
  call,events=self.fixture()
  self.assertEqual(call(),'stopped');self.assertIn('closed',events)
 def test_cleanup_failure_cannot_report_recoverable(self):
  call,events=self.fixture(RuntimeError('network generation permanently revoked'),True)
  with self.assertRaises(PermissionError):call()
 def test_existing_consumed_standby_path_is_wired(self):
  call,events=self.fixture(standby=True)
  self.assertEqual(call(),'stopped');self.assertIn('start:audio-observer',events)
 def test_standby_with_sound_mapping_is_terminal_before_any_start(self):
  call,events=self.fixture(standby=True,unsafe=True)
  self.assertEqual(call(),'terminal');self.assertNotIn('start:audio-device',events)

if __name__=='__main__':unittest.main()
