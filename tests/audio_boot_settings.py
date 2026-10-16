"""Isolated network-only retained settings update; no live provider/account operation."""
import json,os,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch
from garage_audio.audio_boot import Boot,ROLES

class Settings(unittest.TestCase):
 def test_only_network_fields_and_private_rollback(self):
  with tempfile.TemporaryDirectory() as directory:
   root=Path(directory);p=root/'settings.json';receipt=root/'receipt';receipt.mkdir()
   before={'providers':{'retained':{'domain':'spotify_connect','enabled':True,'values':{'volume_mode':'spotify_authoritative','retained_secret':'fixture-not-real'}}},'queues':{'retained':'fixture'},'core':{'streams':{'domain':'streams','values':{'other':'preserved','publish_ip':'old'}}}}
   p.write_text(json.dumps(before));p.chmod(0o600)
   boot=Boot(NS(receipt=receipt,address='192.0.2.4',generation='fixture'),{'ma_settings':str(p)})
   with patch('garage_audio.audio_boot.docker',return_value=''):boot.network_settings()
   after=json.loads(p.read_text())
   self.assertEqual(after['providers'],before['providers']);self.assertEqual(after['queues'],before['queues'])
   self.assertEqual(after['core']['streams']['values'],{'other':'preserved','publish_ip':'192.0.2.4','bind_ip':'0.0.0.0'})
   self.assertEqual(json.loads((receipt/'retained-settings-before.json').read_text()),before)
   self.assertEqual((receipt/'retained-settings-before.json').stat().st_mode&0o777,0o600)
 def test_running_data_owner_denies_update(self):
  with tempfile.TemporaryDirectory() as directory:
   p=Path(directory)/'settings.json';p.write_text('{}')
   boot=Boot(NS(),{'ma_settings':str(p)})
   with patch('garage_audio.audio_boot.docker',side_effect=['fixture',json.dumps([{'Mounts':[{'Source':str(p.parent)}]}])]):
    with self.assertRaises(PermissionError):boot.network_settings()
   self.assertEqual(p.read_text(),'{}')

class BootDeviceMapping(unittest.TestCase):
 def fixture(self,root,card=0,ignore_env=False,wrong_created=False,mode='hardware'):
  images={role:'sha256:'+'a'*64 for role in ROLES}
  registered=[];calls=[];plans=[]
  carrier=NS(receipt=root,id='carrier',generation='fresh',address='192.0.2.4',
             healthy=lambda:None,management_network=lambda:'network',
             register=lambda *args,**kwargs:registered.append((args,kwargs)))
  boot=Boot(carrier,{'images':images,'ma_settings':'retained-private-settings'})
  def compose(*args):
   calls.append(args)
   if args[0]=='config':
    env=dict(line.split('=',1) for line in (root/'generation.env').read_text().splitlines())
    pcm=env.get('GARAGE_AUDIO_PCM_NODE','/dev/snd/pcmC1D0p')
    control=env.get('GARAGE_AUDIO_CONTROL_NODE','/dev/snd/controlC1')
    if ignore_env:pcm='/dev/snd/pcmC1D0p';control='/dev/snd/controlC1'
    services={r:{'image':images[r],'restart':'no','labels':{'garage.audio.generation':'fresh'}} for r in ROLES}
    services['audio-device'].update(command=['--mode',mode],devices=[
     {'source':pcm,'target':pcm,'permissions':'rw'},
     {'source':control,'target':control,'permissions':'rw'}] if mode=='hardware' else [])
    plans.append(services)
    return json.dumps({'services':services})
   if args[0]=='ps':return 'id-'+args[-1]
   return ''
  def docker(*args):
   role=args[-1].removeprefix('id-')
   devices=plans[-1]['audio-device']['devices']
   mapped=[{'PathOnHost':d['source'],'PathInContainer':d['target'],'CgroupPermissions':d['permissions']} for d in devices]
   if wrong_created and mapped:mapped[0]['PathOnHost']='/dev/snd/pcmC1D0p'
   return json.dumps([{'Id':'id-'+role,'Mounts':[{'Destination':'/evidence','Source':str(root/'evidence')}],
                       'HostConfig':{'Devices':mapped,'Privileged':False}}])
  boot.compose=compose
  paths={'GARAGE_AUDIO_PCM_NODE':f'/dev/snd/pcmC{card}D0p',
         'GARAGE_AUDIO_CONTROL_NODE':f'/dev/snd/controlC{card}'}
  return boot,docker,paths,calls,registered,plans

 def test_hardware_re_resolves_card_before_create_and_preserves_exact_permissions(self):
  for card in (0,1,7):
   with self.subTest(card=card),tempfile.TemporaryDirectory() as directory:
    root=Path(directory);boot,docker,paths,calls,registered,plans=self.fixture(root,card)
    with patch('garage_audio.audio_boot.device_environment',return_value=paths) as preflight,\
         patch('garage_audio.audio_boot.docker',side_effect=docker),\
         patch.object(boot,'network_settings') as settings:
     self.assertEqual(boot.create(),'network')
    preflight.assert_called_once();settings.assert_called_once()
    self.assertEqual(len(registered),6)
    self.assertEqual([c[0] for c in calls[:3]],['config','config','create'])
    self.assertEqual(plans[-1]['audio-device']['devices'][0],{
     'source':paths['GARAGE_AUDIO_PCM_NODE'],'target':paths['GARAGE_AUDIO_PCM_NODE'],'permissions':'rw'})
    self.assertNotIn('start',[c[0] for c in calls])

 def test_identity_or_ownership_failure_precedes_create_and_retained_settings_write(self):
  for error in (ValueError('USB identity/capabilities changed; no remap or fallback'),
                PermissionError('exclusive numeric UID1002/GID1003 mode0600 required')):
   with self.subTest(error=type(error).__name__),tempfile.TemporaryDirectory() as directory:
    boot,docker,paths,calls,registered,plans=self.fixture(Path(directory))
    with patch('garage_audio.audio_boot.device_environment',side_effect=error),\
         patch.object(boot,'network_settings') as settings:
     with self.assertRaises(type(error)):boot.create()
    self.assertEqual([c[0] for c in calls],['config']);self.assertFalse(registered)
    settings.assert_not_called()

 def test_literal_stale_mapping_is_rejected_instead_of_trusting_numeric_index(self):
  with tempfile.TemporaryDirectory() as directory:
   boot,docker,paths,calls,registered,plans=self.fixture(Path(directory),ignore_env=True)
   with patch('garage_audio.audio_boot.device_environment',return_value=paths),\
        patch.object(boot,'network_settings') as settings:
    with self.assertRaisesRegex(PermissionError,'identity-resolved UMC mapping'):boot.create()
   self.assertNotIn('create',[c[0] for c in calls]);settings.assert_not_called()

 def test_created_container_must_match_resolved_devices_before_registration_or_start(self):
  with tempfile.TemporaryDirectory() as directory:
   boot,docker,paths,calls,registered,plans=self.fixture(Path(directory),wrong_created=True)
   with patch('garage_audio.audio_boot.device_environment',return_value=paths),\
        patch('garage_audio.audio_boot.docker',side_effect=docker),\
        patch.object(boot,'network_settings'):
    with self.assertRaisesRegex(PermissionError,'created worker differs'):boot.create()
   self.assertEqual(len(registered),1) # finite init only; hardware worker never admitted
   self.assertNotIn('start',[c[0] for c in calls])

 def test_file_mode_never_requires_or_receives_a_hardware_mapping(self):
  with tempfile.TemporaryDirectory() as directory:
   boot,docker,paths,calls,registered,plans=self.fixture(Path(directory),mode='file')
   with patch('garage_audio.audio_boot.device_environment') as preflight,\
        patch('garage_audio.audio_boot.docker',side_effect=docker),\
        patch.object(boot,'network_settings'):
    boot.create()
   preflight.assert_not_called();self.assertEqual(plans[-1]['audio-device']['devices'],[])

 def test_generated_mapping_env_overrides_frozen_stale_values(self):
  boot=Boot(NS(receipt=Path('/private-generation')),{
   'project':'garage-audio-next','environment':'/frozen-stale.env','compose':['/frozen-compose.yaml']})
  with patch('garage_audio.audio_boot.subprocess.check_output',return_value='{}') as command:
   boot.compose('config','--format','json')
  args=command.call_args.args[0]
  self.assertLess(args.index('/frozen-stale.env'),args.index('/private-generation/generation.env'))
  self.assertEqual(args[-3:],['config','--format','json'])

 def test_hardware_plan_cannot_add_privilege_or_broad_device_rules(self):
  for key,value in [('privileged',True),('cap_add',['SYS_ADMIN']),('device_cgroup_rules',['c 116:* rw'])]:
   with self.subTest(key=key),tempfile.TemporaryDirectory() as directory:
    boot,docker,paths,calls,registered,plans=self.fixture(Path(directory))
    original=boot.compose
    def widened(*args):
     result=original(*args)
     if args[0]=='config':
      plan=json.loads(result);plan['services']['audio-device'][key]=value
      return json.dumps(plan)
     return result
    boot.compose=widened
    with patch('garage_audio.audio_boot.device_environment',return_value=paths),\
         patch.object(boot,'network_settings') as settings:
     with self.assertRaisesRegex(PermissionError,'least privilege'):boot.create()
    self.assertNotIn('create',[c[0] for c in calls]);settings.assert_not_called()

if __name__=='__main__':unittest.main()
