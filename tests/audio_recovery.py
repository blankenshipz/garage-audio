"""Synthetic control-plane integration, no Docker/ALSA/network/provider access."""
import importlib.util,json,os,subprocess,tempfile,threading,unittest
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
  def inspect(*args,**kwargs):
   # Intentional change: the guard now issues ONE batched `docker inspect` per pass.
   def one(identity):
    role='dashboard' if identity=='app' else 'audio-device'
    return {'Id':identity,'Config':{'Labels':{'garage.audio.generation':'g','com.docker.compose.service':role}},
     'State':{'Pid':123 if identity!='app' else 0,'Running':identity!='app','Status':'exited' if identity=='app' else 'running','ExitCode':1 if identity=='app' else 0}}
   return json.dumps([one(identity) for identity in args[1:]])
  original_stat=network.os.stat
  def proc_stat(path,*args,**kwargs):
   return SimpleNamespace(st_ino=7) if str(path)=='/proc/123/ns/net' else original_stat(path,*args,**kwargs)
  with patch.object(network.os,'geteuid',return_value=0),patch.object(network,'birth',return_value='b'),patch.object(network,'docker',side_effect=inspect),patch.object(network.os,'stat',side_effect=proc_stat),patch.object(network.time,'monotonic',return_value=1),patch.object(network.subprocess,'check_output',return_value='[{"addr_info":[{"local":"192.0.2.1"}]}]'),patch.object(network,'revoke') as revoke:
   network.guard_main(registry)
   self.assertEqual(json.loads((self.root/'guard-failure.json').read_text())['kind'],'owned_workload_crash')
   self.assertEqual(revoke.call_count,1)
   # An independently invalid sibling must override an earlier eligible crash.
   def invalid(*args,**kwargs):
    value=json.loads(inspect(*args))
    for item in value:
     if item['Id']=='worker':item['Config']['Labels']['garage.audio.generation']='other'
    return json.dumps(value)
   with patch.object(network,'docker',side_effect=invalid):network.guard_main(registry)
   self.assertEqual(json.loads((self.root/'guard-failure.json').read_text())['kind'],'terminal')

class Lifecycle(unittest.TestCase):
 setUp=Recovery.setUp
 def fixture(self,error=None,cleanup_error=False,standby=False,unsafe=False,ready_receiver=False,ready=None,pairing=True,start_error=None):
  events=[];root=self.root;stop=threading.Event()
  (root/'audio-lifecycle-policy.py').write_text('# isolated synthetic policy\n')
  class Carrier:
   def __init__(self,receipt):
    receipt.mkdir();(receipt/'authority').mkdir();self.receipt=receipt;self.registry=receipt/'registry.json';self.generation='g';self.inode=1;self.address='192.0.2.1';self.dependents=[];self.id='carrier';self.worker_ack=None;self.guard=SimpleNamespace(poll=lambda:None);self.health=0
   def await_guard_verdict(self,*_):return True
   def start(self,*_):
    events.append('carrier-start')
    if start_error:raise start_error
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
   patch.object(lifecycle,'pairing_instances',return_value=[{'port':12345}] if pairing else []).start()
   patch.object(lifecycle,'verify_pairing_instances',return_value=[]).start()
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
 def test_admitted_receiver_without_pairing_never_reports_ready(self):
  call,_=self.fixture(ready_receiver=True,pairing=False,ready=lambda _:self.fail('pairing not admitted'))
  self.assertEqual(call(),'stopped')
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

class PreadmissionRestart(unittest.TestCase):
 setUp=Recovery.setUp
 fixture=Lifecycle.fixture
 def test_owner_restart_macvlan_race_is_preadmission_not_terminal(self):
  call,events=self.fixture(start_error=subprocess.CalledProcessError(2,['nsenter','ip','link','set','ga-lan','up']))
  self.assertEqual(call(),'preadmission');self.assertIn('closed',events)
  self.assertFalse(any(str(x).startswith('start:') for x in events))
 def policy_failure(self,safe_class):
  call,events=self.fixture()
  seen=[];original=lifecycle.classify_failure
  def classify(error,receipt,carrier):
   seen.append(error);carrier.dependents.append('music-assistant')  # dependents registered: only transient evidence may retry
   return original(error,receipt,carrier)
  def run(argv,**kwargs):
   if argv[0]=='docker':
    kwargs['stdout'].write(json.dumps({'policy_admission':False,'safe_error_class':safe_class}).encode())
    return SimpleNamespace(returncode=1)
   return SimpleNamespace(returncode=0)
  ticks=iter([0,10]+[1000]*50)
  patch.object(lifecycle,'classify_failure',side_effect=classify).start()
  patch.object(lifecycle.subprocess,'run',side_effect=run).start()
  patch.object(lifecycle.time,'monotonic',side_effect=lambda:next(ticks)).start()
  return call(),seen,events
 def test_policy_transient_class_raises_transient_error_and_is_retried(self):
  result,seen,events=self.policy_failure('ClientConnectorDNSError')
  self.assertEqual(len(seen),1);self.assertIsInstance(seen[0],recovery.TransientNetworkError)
  self.assertEqual(result,'preadmission');self.assertFalse(any(str(x).startswith('start:audio-transport') for x in events))
 def test_policy_non_transient_class_stays_terminal(self):
  result,seen,events=self.policy_failure('RuntimeError')
  self.assertEqual(len(seen),1);self.assertNotIsInstance(seen[0],recovery.TransientNetworkError)
  self.assertEqual(result,'terminal')
 def test_preadmission_ownership_failures_stay_terminal(self):
  carrier=SimpleNamespace(dependents=[],worker_ack=None)
  self.assertTrue(recovery.preadmission(RuntimeError('bounded DHCP admission timed out'),carrier))
  wrapped=RuntimeError('x');wrapped.__cause__=PermissionError()
  for error in [PermissionError(),wrapped,ValueError(),FileNotFoundError()]:
   self.assertFalse(recovery.preadmission(error,carrier))
  for late in [SimpleNamespace(dependents=['ma'],worker_ack=None),SimpleNamespace(dependents=[],worker_ack='ack')]:
   self.assertFalse(recovery.preadmission(RuntimeError('x'),late))
 def test_preadmission_retries_are_bounded(self):
  stop=SimpleNamespace(is_set=lambda:False,wait=lambda _:False);calls=[]
  supervisor=recovery.Supervisor(self.root,stop,lambda:0)
  self.assertEqual(supervisor.run(lambda:calls.append(1) or 'preadmission'),'exhausted')
  self.assertEqual((len(calls),supervisor.last),(4,'preadmission'))

class TransientNetwork(unittest.TestCase):
 setUp=Recovery.setUp
 carrier=SimpleNamespace(dependents=[],worker_ack=None)
 def test_transient_dns_and_connection_errors_are_preadmission(self):
  import socket
  for error in [socket.gaierror(socket.EAI_AGAIN,'Temporary failure in name resolution'),ConnectionRefusedError(),ConnectionResetError(),
    RuntimeError('Temporary failure in name resolution'),RuntimeError('Connection refused by music assistant')]:
   self.assertTrue(recovery.preadmission(error,self.carrier),repr(error))
  wrapped=RuntimeError('MA unavailable');wrapped.__cause__=socket.gaierror(socket.EAI_AGAIN,'x')
  self.assertTrue(recovery.preadmission(wrapped,self.carrier))
 def test_unknown_and_permanent_errors_stay_terminal(self):
  import socket
  denied=ValueError('x');denied.__cause__=PermissionError()
  denied_net=RuntimeError('Temporary failure in name resolution');denied_net.__context__=PermissionError()
  ident=ValueError('identity');ident.__cause__=socket.gaierror(socket.EAI_AGAIN,'x')
  for error in [OSError('boom'),TimeoutError(),BrokenPipeError(),socket.gaierror(socket.EAI_FAMILY,'x'),
    PermissionError(),KeyError('x'),denied,denied_net,ident,ValueError('unrelated'),FileNotFoundError('x')]:
   self.assertFalse(recovery.preadmission(error,self.carrier),repr(error))
  self.assertFalse(recovery.preadmission(ConnectionRefusedError(),SimpleNamespace(dependents=['ma'],worker_ack=None)))
 def test_wrapped_and_stringified_shapes(self):
  import socket,urllib.error,asyncio
  gai=socket.gaierror(socket.EAI_AGAIN,'Temporary failure in name resolution')
  nonam=socket.gaierror(socket.EAI_NONAME,'Name or service not known')
  shapes=[nonam,
   urllib.error.URLError(gai),urllib.error.URLError('Temporary failure in name resolution'),
   OSError(-3,'Temporary failure in name resolution'),OSError(111,'Connect call failed'),
   OSError('Connection reset by peer'),
   subprocess.CalledProcessError(1,['docker','exec'],output=b'',stderr=b'curl: Could not resolve; Temporary failure in name resolution')]
  try:
   import aiohttp
   key=aiohttp.client_reqrep.ConnectionKey('music-assistant',8095,False,None,None,None,None)
   shapes+=[aiohttp.ClientConnectorDNSError(key,gai),aiohttp.ClientConnectorError(key,ConnectionRefusedError(111,'x'))]
  except ImportError:pass
  wrapper=RuntimeError('MA unavailable');wrapper.__cause__=gai
  shapes.append(wrapper)
  for error in shapes:
   self.assertTrue(recovery.transient_network(error),repr(error));self.assertTrue(recovery.preadmission(error,self.carrier),repr(error))
  # A bare timeout or unrelated OSError text is NOT transient evidence.
  for error in [asyncio.TimeoutError(),OSError('disk full'),urllib.error.URLError('unknown url type')]:
   self.assertFalse(recovery.transient_network(error),repr(error))
 def test_nonrecoverable_guard_failure_blocks_transient_retry(self):
  import socket
  late=SimpleNamespace(dependents=['ma'],worker_ack=None,await_guard_verdict=lambda:True)
  gai=socket.gaierror(socket.EAI_AGAIN,'x')
  receipt=self.root/'guard-receipt';receipt.mkdir()
  self.assertEqual(recovery.classify_failure(gai,receipt,late),'preadmission')  # no guard failure recorded
  (receipt/'guard-failure.json').write_text(json.dumps({'kind':'dependent_identity_mismatch'}))
  for error in [gai,ConnectionResetError(),recovery.TransientNetworkError('x')]:
   self.assertEqual(recovery.classify_failure(error,receipt,late),'terminal',repr(error))
  (receipt/'guard-failure.json').write_text('not json')
  self.assertEqual(recovery.classify_failure(gai,receipt,late),'preadmission')
 def test_dependents_registered_transient_network_is_recoverable_but_unknown_terminal(self):
  import socket
  late=SimpleNamespace(dependents=['ma'],worker_ack='ack',await_guard_verdict=lambda:True)
  gai=socket.gaierror(socket.EAI_AGAIN,'x')
  self.assertEqual(recovery.classify_failure(gai,self.root,late),'preadmission')
  self.assertEqual(recovery.classify_failure(recovery.TransientNetworkError('x'),self.root,late),'preadmission')
  for error in [RuntimeError('supported Soloist policy admission failed before transport'),OSError('x'),ValueError('x'),PermissionError('Temporary failure in name resolution')]:
   self.assertEqual(recovery.classify_failure(error,self.root,late),'terminal',repr(error))
 def test_policy_stdout_class_names(self):
  for name,expected in [('ClientConnectorDNSError',True),('ClientConnectorError',True),('ConnectionRefusedError',True),
    ('RuntimeError',False),('PermissionError',False),('KeyError',False)]:
   path=self.root/('p-'+name);path.write_text(json.dumps({'safe_error_class':name,'policy_admission':False}))
   self.assertIs(recovery.policy_failure_transient(path),expected,name)
  bad=self.root/'bad';bad.write_text('not json')
  self.assertFalse(recovery.policy_failure_transient(bad));self.assertFalse(recovery.policy_failure_transient(self.root/'missing'))
 def test_dns_failure_retries_three_times_then_exhausts_and_does_not_restart(self):
  import socket
  carrier=SimpleNamespace(dependents=[],worker_ack=None,await_guard_verdict=lambda:True)
  error=socket.gaierror(socket.EAI_AGAIN,'Temporary failure in name resolution')
  stop=SimpleNamespace(is_set=lambda:False,wait=lambda _:False);calls=[]
  def generation():
   calls.append(1);return recovery.classify_failure(error,self.root,carrier)
  supervisor=recovery.Supervisor(self.root,stop,lambda:0)
  self.assertEqual(supervisor.run(generation),'exhausted')
  self.assertEqual((len(calls),supervisor.retries,supervisor.last),(4,3,'preadmission'))
  unknown=SimpleNamespace(is_set=lambda:False,wait=lambda _:False);calls.clear()
  self.assertEqual(recovery.Supervisor(self.root,unknown,lambda:0).run(lambda:calls.append(1) or recovery.classify_failure(OSError('x'),self.root,carrier)),'terminal')
  self.assertEqual(len(calls),1)

class CarrierMacRelease(unittest.TestCase):
 def carrier(self,holders,link_up_failures):
  c=object.__new__(network.Carrier);c.name='gaudio-net-x';c.generation='g';c.id=None
  c.receipt=Path(tempfile.mkdtemp());self.addCleanup(lambda:__import__('shutil').rmtree(c.receipt))
  events=[];polls=iter(holders);failures=[link_up_failures]
  def run(*args):
   events.append(args)
   if args[-2:]==('ga-lan','up') and failures[0]:
    failures[0]-=1;raise subprocess.CalledProcessError(2,args)
   return ''
  def docker(*args):return json.dumps([{'State':{'Pid':42}}]) if args[0]=='inspect' else 'id'
  hook=c.receipt/'hook';hook.write_text('');hook.chmod(0o700)
  patches=[patch.object(network,'run',side_effect=run),patch.object(network,'docker',side_effect=docker),
   patch.object(network.os,'stat',side_effect=lambda path,*a,_stat=os.stat,**k:SimpleNamespace(st_ino=7) if str(path).startswith('/proc/') else _stat(path,*a,**k)),patch.object(network.time,'sleep'),
   patch.object(network.subprocess,'run',side_effect=lambda *a,**k:events.append('nft')),
   patch.object(network.subprocess,'Popen',side_effect=StopIteration('reached DHCP')),
   patch.object(network.Carrier,'mac_holders',side_effect=lambda:next(polls))]
  for item in patches:item.start();self.addCleanup(item.stop)
  return c,hook,events
 def test_waits_for_prior_namespace_then_retries_link_up(self):
  c,hook,events=self.carrier([['old-sandbox'],['old-sandbox'],[],[],[]],2)
  with patch.object(network.time,'monotonic',return_value=0),self.assertRaises(StopIteration):c.start(hook)
  add=[i for i,x in enumerate(events) if x[:3]==('ip','link','add')];up=[i for i,x in enumerate(events) if x[-2:]==('ga-lan','up')]
  self.assertEqual((len(add),len(up)),(1,3));self.assertLess(events.index('nft'),up[0])
 def test_foreign_holder_fails_after_bounded_wait_without_macvlan(self):
  c,hook,events=self.carrier([['host']]*3,0)
  clock=iter([0,0,5,network.MAC_RELEASE_WAIT+1])
  with patch.object(network.time,'monotonic',side_effect=lambda:next(clock)):
   with self.assertRaisesRegex(RuntimeError,'still owned after bounded wait: host'):c.start(hook)
  self.assertFalse(any(x[:3]==('ip','link','add') for x in events))
 def test_link_up_failure_with_visible_holder_is_not_retried(self):
  c,hook,events=self.carrier([[],['other']],1)
  with patch.object(network.time,'monotonic',return_value=0),self.assertRaises(subprocess.CalledProcessError):c.start(hook)

if __name__=='__main__':unittest.main()
