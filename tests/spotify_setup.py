"""Explicit unit MA client/socket mocks; no authenticated live MA/provider/sound proof."""
import json
import asyncio
import logging
import os
from pathlib import Path
import socket
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from garage_audio.application import Application
from garage_audio import spotify_setup, audio_public


def config(instance='spotify-garage',players=None,domain='spotify_connect'):
    return SimpleNamespace(instance_id=instance,domain=domain,enabled=True,setup_data={'backend':'soloist'},
        values={'connected_players':SimpleNamespace(value=players if players is not None else []),
                'volume_mode':SimpleNamespace(value='player_only'),
                'publish_name_template':SimpleNamespace(value='Original'),
                'soloist_api_key':SimpleNamespace(value='SYNTHETIC-SECRET-NEVER-ECHO')})


def step(name='backend',kind='form',hint=False):
    key={'backend':'backend','soloist_terms':'soloist_download_consent','soloist_api_key':'soloist_api_key'}
    entries=[{'key':key[name],'type':{'soloist_api_key':'secure_string','soloist_terms':'boolean','backend':'string'}[name],
              'value':'SYNTHETIC-SECRET-NEVER-ECHO','default_value':'SYNTHETIC-SECRET-NEVER-ECHO'}] if kind=='form' else []
    if hint: entries.append({'key':'soloist_api_key_hint','type':'label'})
    return {'type':kind,'step_id':name,'flow_id':'ma-flow-fixture','entries':entries,
            'result':{'instance_id':'spotify-garage'} if kind=='finish' else None,
            'description':'SYNTHETIC-SECRET-NEVER-ECHO','url':'https://unreviewed.invalid/secret',
            'errors':{}}


class ConfigFixture:
    def __init__(self,owner): self.owner=owner
    async def get_provider_configs(self,**kwargs):
        self.owner.calls.append(('get_provider_configs',kwargs));return self.owner.configs
    async def get_provider_config(self,target):
        self.owner.calls.append(('get_provider_config',target));return next(conf for conf in self.owner.configs if conf.instance_id==target)
    async def get_provider_config_entries(self,domain,instance_id=None):
        self.owner.calls.append(('get_provider_config_entries',domain,instance_id));return self.owner.entries
    async def save_provider_config(self,domain,values,instance_id):
        self.owner.calls.append(('save_provider_config',domain,values,instance_id))
        current=self.owner.configs[0]
        for key,value in values.items():
            if key not in self.owner.drop_save: current.values[key].value=value
        return current


class MAFixture:
    def __init__(self):
        self.configs=[];self.calls=[];self.current=step();self.failure=None;self.drop_save=set()
        self.entries=[SimpleNamespace(key='volume_mode',type='string',hidden=False,
            options=[SimpleNamespace(value='spotify_authoritative',disabled=False)])]
        self.config=ConfigFixture(self)
        async def fetch(): self.calls.append(('players.fetch_state',))
        self.players=SimpleNamespace(fetch_state=fetch,players=[SimpleNamespace(player_id=spotify_setup.fixed_player(),available=True,playback_state='idle')])
    async def __aenter__(self): return self
    async def __aexit__(self,*args): pass
    async def send_command(self,command,**kwargs):
        self.calls.append((command,kwargs))
        if self.failure: raise self.failure
        if command=='config/flows/submit':
            if 'backend' in kwargs['values']: self.current=step('soloist_terms')
            elif 'soloist_download_consent' in kwargs['values']: self.current=step('soloist_api_key')
            else:
                self.current=step('finish','finish')
                if not any(conf.instance_id=='spotify-garage' for conf in self.configs): self.configs.append(config())
        return self.current


class SetupTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir='/private/tmp/opencode')
        self.root=Path(self.temp.name)
        self.ma=MAFixture()
        self.app=Application(self.root/'app',broker=lambda _: self.fail('No native service'))
        self.auth=self.root/'private-auth';self.auth.write_text('SYNTHETIC-MA-CREDENTIAL-000000');self.auth.chmod(0o600)
        self.key=self.root/'private-key';self.key.write_text('SYNTHETIC-SOLOIST-KEY-000000');self.key.chmod(0o600)
        self.env=patch.dict(os.environ,{'MA_TOKEN_FILE':str(self.auth),'GARAGE_SOLOIST_API_KEY_FILE':str(self.key),
                                     'GARAGE_DEPLOYMENT_MODE':'docker'},clear=True);self.env.start()
        self.client=patch.object(spotify_setup,'client',return_value=self.ma);self.client.start()
        self.worker=patch.object(audio_public,'request',return_value={'state':'off','device_open':False,
                        'device_mode':'file','physical_verified':False,'owned_children':[]});self.worker.start()
        self.network=patch.object(socket.socket,'connect',side_effect=AssertionError('No network in unit tests'));self.network.start()
    def tearDown(self):
        self.network.stop();self.worker.stop();self.client.stop();self.env.stop()
        self.app.agent_harness.close();self.temp.cleanup()
    def op(self,operation,**args): return self.app.execute(operation,args)
    def clean(self,result):
        text=json.dumps(result)
        for forbidden in ('SYNTHETIC-SECRET','SYNTHETIC-MA-CREDENTIAL','SYNTHETIC-SOLOIST-KEY',str(self.key),str(self.auth),
                          'ma-flow-fixture','unreviewed.invalid'):
            self.assertNotIn(forbidden,text)

    def test_complete_controlled_flow_and_fixed_player_config(self):
        flow=self.op('setup.spotify.begin')['result'];self.clean(flow)
        self.assertEqual(flow['fields'][0]['options'],['soloist'])
        self.assertEqual(self.op('setup.spotify.status')['result']['active_flow']['id'],flow['id'])
        self.assertEqual(self.op('setup.spotify.begin')['error']['code'],'setup_flow_active')
        for fields in ({'step':'backend'},{'step':'soloist_terms','accept_terms':True},
                       {'step':'soloist_api_key','credential_ref':'spotify-soloist'}):
            result=self.op('setup.spotify.flow.submit',id=flow['id'],**fields)
            self.assertTrue(result['ok'],result);self.clean(result);self.assertFalse(result['result']['sound_authority'])
        result=self.op('setup.spotify.configure',advertised_name='Garage Speakers')
        self.assertTrue(result['ok'],result);self.clean(result)
        saves=[call for call in self.ma.calls if call[0]=='save_provider_config']
        self.assertEqual(saves[0][2],{'connected_players':[spotify_setup.fixed_player()],
                                    'publish_name_template':'Garage Speakers','volume_mode':'spotify_authoritative'})
        self.assertEqual(saves[0][3],'spotify-garage')
        self.assertTrue(result['result']['player_bound'])
        for call in self.ma.calls:
            self.assertNotIn(call[0],('config/providers/remove','players/cmd/play','players/cmd/seek','players/cmd/volume_set'))
        self.assertEqual(self.op('setup.spotify.flow.get',id=flow['id'])['result']['type'],'finish')

    def test_strict_schema_blocks_commands_targets_secrets_and_sound_fields(self):
        for operation,args in [
            ('setup.spotify.begin',{'provider_domain':'other'}),('setup.spotify.reconfigure',{'instance_id':'other'}),
            ('setup.spotify.configure',{'advertised_name':'{player_name}'}),
            ('setup.spotify.configure',{'advertised_name':'Garage','player_id':'other'}),
            ('setup.spotify.configure',{'advertised_name':'Garage','volume_mode':'player_only'}),
            ('setup.spotify.configure',{'advertised_name':'Garage','url':'http://other'}),
            ('setup.spotify.flow.submit',{'id':'a'*32,'step':'backend','backend':'go_librespot'}),
            ('setup.spotify.flow.submit',{'id':'a'*32,'step':'soloist_api_key','api_key':'never accept'}),
            ('setup.spotify.flow.submit',{'id':'a'*32,'step':'soloist_api_key','credential_ref':'/private/path'}),
            ('setup.spotify.flow.submit',{'id':'a'*32,'step':'soloist_terms','accept_terms':True,'human_approval':True}),
            ('setup.spotify.flow.abort',{'id':'../other'})]:
            result=self.op(operation,**args)
            self.assertEqual(result['error']['code'],'invalid_arguments');self.clean(result)
        self.assertEqual(self.ma.calls,[])

    def test_missing_invalid_credentials_and_provider_errors_are_structured_redacted(self):
        with patch.dict(os.environ,{},clear=True):
            result=self.op('setup.spotify.status')
            self.assertEqual(result['error']['code'],'ma_credential_required');self.clean(result)
            setup=self.op('setup.status')['result']
            self.assertEqual(setup['spotify_setup']['error']['code'],'ma_credential_required')
        self.auth.chmod(0o644)
        self.assertEqual(self.op('setup.spotify.begin')['error']['code'],'credential_invalid')
        self.auth.chmod(0o600)
        self.ma.failure=RuntimeError('SYNTHETIC-SECRET-NEVER-ECHO')
        result=self.op('setup.spotify.begin');self.assertEqual(result['error']['code'],'ma_setup_unavailable');self.clean(result)

    def test_preserve_unrelated_providers_stored_key_and_reconfigure(self):
        unrelated=config('another',players=['another-player'])
        self.ma.configs=[unrelated,config(players=[spotify_setup.fixed_player()])]
        flow=self.op('setup.spotify.reconfigure')['result']
        self.assertEqual([call for call in self.ma.calls if call[0]=='config/providers/reconfigure'][0][1],{'instance_id':'spotify-garage'})
        self.assertTrue(self.op('setup.spotify.flow.submit',id=flow['id'],step='backend')['ok'])
        self.assertTrue(self.op('setup.spotify.flow.submit',id=flow['id'],step='soloist_terms',accept_terms=True)['ok'])
        self.ma.current=step('soloist_api_key',hint=True)
        result=self.op('setup.spotify.flow.submit',id=flow['id'],step='soloist_api_key',keep_stored_key=True)
        self.assertTrue(result['ok'],result)
        # Fixture returns fixed garage config on finish; no original credential is ever requested/read.
        submitted=[call for call in self.ma.calls if call[0]=='config/flows/submit'][-1]
        self.assertEqual(submitted[1]['values'],{'soloist_api_key':''});self.clean(result)
        self.assertEqual(unrelated.values['connected_players'].value,['another-player'])

    def test_abort_expiry_quiet_guard_and_unknown_flows(self):
        result=self.op('setup.spotify.flow.get',id='a'*32);self.assertEqual(result['error']['code'],'not_found')
        with patch.object(audio_public,'request',return_value={'state':'playing','device_open':True,'owned_children':[123]}):
            self.assertEqual(self.op('setup.spotify.begin')['error']['code'],'quiet_setup_required')
        self.assertFalse(any(call[0]=='config/providers/setup' for call in self.ma.calls))
        self.ma.players.players[0].playback_state='playing'
        self.assertEqual(self.op('setup.spotify.begin')['error']['code'],'quiet_setup_required')
        self.ma.players.players[0].playback_state='idle'
        flow=self.op('setup.spotify.begin')['result']
        self.assertTrue(self.op('setup.spotify.flow.abort',id=flow['id'])['ok'])
        self.assertEqual(self.op('setup.spotify.flow.get',id=flow['id'])['result']['type'],'abort')
        flow=self.op('setup.spotify.begin')['result']
        path=self.app.root/'spotify-flows'/(flow['id']+'.json')
        record=json.loads(path.read_text());record['expires_at']=0;path.write_text(json.dumps(record))
        self.assertEqual(self.op('setup.spotify.flow.get',id=flow['id'])['error']['code'],'setup_flow_expired')

    def test_unreviewed_external_or_secure_form_data_never_forwarded(self):
        self.ma.current=step('backend',kind='external')
        result=self.op('setup.spotify.begin');self.assertEqual(result['error']['code'],'ma_flow_unsupported');self.clean(result)
        self.ma.current=step();self.ma.current['entries'].append({'key':'shell','value':'SYNTHETIC-SECRET'})
        result=self.op('setup.spotify.begin');self.assertEqual(result['error']['code'],'ma_flow_unsupported');self.clean(result)

    def test_fixed_target_conflict_missing_key_and_redacted_scope_failures(self):
        self.ma.configs=[config(players=['unrelated-player'])]
        with patch.dict(os.environ,{'GARAGE_CONNECT_PROVIDER':'spotify-garage'}):
            result=self.op('setup.spotify.reconfigure')
            self.assertEqual(result['error']['code'],'ma_provider_target_conflict');self.clean(result)
        self.assertFalse(any(call[0]=='config/providers/reconfigure' for call in self.ma.calls))
        self.ma.configs=[]
        flow=self.op('setup.spotify.begin')['result']
        self.op('setup.spotify.flow.submit',id=flow['id'],step='backend')
        self.op('setup.spotify.flow.submit',id=flow['id'],step='soloist_terms',accept_terms=True)
        with patch.dict(os.environ,{'GARAGE_SOLOIST_API_KEY_FILE':''}):
            result=self.op('setup.spotify.flow.submit',id=flow['id'],step='soloist_api_key',credential_ref='spotify-soloist')
            self.assertEqual(result['error']['code'],'soloist_credential_required');self.clean(result)
        class ScopeError(Exception): error_code=22
        self.ma.failure=ScopeError('SYNTHETIC-SECRET-NEVER-ECHO')
        result=self.op('setup.spotify.flow.get',id=flow['id'])
        self.assertEqual(result['error']['code'],'ma_scope_required');self.clean(result)
        for path in self.app.root.glob('*/*.json'):
            self.assertNotIn('SYNTHETIC-SECRET',path.read_text())
            self.assertNotIn('SYNTHETIC-SOLOIST-KEY',path.read_text())

    def test_timeout_and_wire_logging_policy(self):
        async def stalled(*args,**kwargs): await asyncio.sleep(.1)
        with patch.object(self.ma,'send_command',side_effect=stalled),patch.object(spotify_setup,'TIMEOUT',.01):
            result=self.op('setup.spotify.begin')
            self.assertEqual(result['error']['code'],'ma_setup_timeout');self.clean(result)
        self.client.stop()
        try:
            fake=SimpleNamespace(MusicAssistantClient=lambda *args,**kwargs: self.ma)
            with patch.dict(sys.modules,{'music_assistant_client':fake}),patch('importlib.metadata.version',return_value='1.6.0'):
                for name in ('music_assistant_client','music_assistant_client.connection'):
                    logging.getLogger(name).disabled=False;logging.getLogger(name).setLevel(logging.DEBUG)
                self.assertIs(spotify_setup.client('synthetic-unit-value'),self.ma)
                self.assertTrue(logging.getLogger('music_assistant_client.connection').disabled)
                self.assertTrue(logging.getLogger('music_assistant_client').disabled)
        finally: self.client.start()

    def reviewed_config(self):
        self.ma.configs=[config(players=[spotify_setup.fixed_player()])]
        spotify_setup.save(self.app,'setup-config','spotify.json',{'instance_id':'spotify-garage','official_setup_completed':True})

    def test_required_policy_missing_hidden_disabled_or_wrong_backend_never_saves(self):
        self.reviewed_config()
        entry=self.ma.entries[0]
        cases=[[],[SimpleNamespace(key='volume_mode',type='string',hidden=True,options=entry.options)],
            [SimpleNamespace(key='volume_mode',type='string',hidden=False,options=[SimpleNamespace(value='spotify_authoritative',disabled=True)])],
            [SimpleNamespace(key='volume_mode',type='string',hidden=False,options=[SimpleNamespace(value='player_only',disabled=False)])]]
        for entries in cases:
            self.ma.entries=entries
            result=self.op('setup.spotify.configure',advertised_name='Garage')
            self.assertEqual(result['error']['code'],'required_volume_policy_unavailable');self.clean(result)
            status=self.op('setup.spotify.status')
            self.assertTrue(status['ok']);self.assertFalse(status['result']['volume_policy_available'])
        self.ma.entries=[entry];self.ma.configs[0].setup_data['backend']='go_librespot'
        self.assertEqual(self.op('setup.spotify.configure',advertised_name='Garage')['error']['code'],'required_volume_policy_unavailable')
        self.assertFalse(any(call[0]=='save_provider_config' for call in self.ma.calls))

    def test_required_policy_persisted_readback_mismatch_and_unqualified_status(self):
        self.reviewed_config();self.ma.drop_save={'volume_mode'}
        result=self.op('setup.spotify.configure',advertised_name='Garage')
        self.assertEqual(result['error']['code'],'required_volume_policy_mismatch');self.clean(result)
        self.ma.drop_save=set();result=self.op('setup.spotify.configure',advertised_name='Garage')
        self.assertTrue(result['ok']);self.assertTrue(result['result']['volume_policy_configured'])
        self.assertTrue(any(call[0]=='get_provider_config' for call in self.ma.calls))
        status=self.op('setup.spotify.status')['result']
        self.assertTrue(status['volume_policy_available']);self.assertTrue(status['volume_policy_configured'])
        self.assertFalse(status['runtime_qualified']);self.assertFalse(status['runtime_verified']);self.assertFalse(status['sound_authority'])

    def test_verified_missing_flow_retains_interruption_and_permits_fresh_begin(self):
        flow=self.op('setup.spotify.begin')['result']
        class Missing(Exception): error_code=0
        self.ma.failure=Missing("'Unknown (or finished) setup flow: ma-flow-fixture'")
        status=self.op('setup.spotify.status')
        self.assertTrue(status['ok']);self.assertIsNone(status['result']['active_flow'])
        self.assertTrue(status['result']['setup_restart_required'])
        interrupted=self.op('setup.spotify.flow.get',id=flow['id'])['result']
        self.assertEqual(interrupted['type'],'interrupted');self.assertEqual(interrupted['code'],'setup_restart_required');self.clean(interrupted)
        self.assertFalse(interrupted['sound_authority']);self.assertEqual(interrupted['fields'],[])
        self.assertEqual(self.op('setup.spotify.flow.submit',id=flow['id'],step='backend')['error']['code'],'setup_restart_required')
        self.ma.failure=None;fresh=self.op('setup.spotify.begin')['result']
        self.assertNotEqual(fresh['id'],flow['id']);self.assertFalse(self.op('setup.spotify.status')['result']['setup_restart_required'])
        self.assertTrue((self.app.root/'spotify-flows'/(flow['id']+'.json')).exists())

    def test_offline_auth_scope_and_unrelated_codezero_never_interrupt_active_flow(self):
        flow=self.op('setup.spotify.begin')['result']
        for code in (None,0,20,22):
            failure=RuntimeError('SYNTHETIC-SECRET-NEVER-ECHO');failure.error_code=code
            self.ma.failure=failure;result=self.op('setup.spotify.status')
            self.assertFalse(result['ok']);self.clean(result)
            self.assertEqual(spotify_setup.active_flow(self.app)['id'],flow['id'])
            record=json.loads((self.app.root/'spotify-flows'/(flow['id']+'.json')).read_text())
            self.assertEqual(record['state'],'form')

    def test_readiness_never_confuses_configured_policy_with_runtime_qualification(self):
        self.reviewed_config();self.ma.entries=[]
        result=self.op('setup.status')['result']['spotify_readiness']
        self.assertEqual(result['code'],'required_volume_policy_unavailable');self.assertFalse(result['ready'])
        self.ma.entries=[SimpleNamespace(key='volume_mode',type='string',hidden=False,
            options=[SimpleNamespace(value='spotify_authoritative',disabled=False)])]
        self.assertEqual(self.op('setup.status')['result']['spotify_readiness']['code'],'required_volume_policy_mismatch')
        self.ma.configs[0].values['volume_mode'].value='spotify_authoritative'
        result=self.op('setup.status')['result']['spotify_readiness']
        self.assertTrue(result['volume_policy_configured']);self.assertFalse(result['runtime_qualified'])
        self.assertEqual(result['code'],'runtime_qualification_required');self.assertFalse(result['ready'])

    def test_flow_disappearing_before_submit_or_abort_is_retained_interrupted(self):
        class Missing(Exception): error_code=0
        failure=Missing("'Unknown (or finished) setup flow: ma-flow-fixture'")
        flow=self.op('setup.spotify.begin')['result']
        original=self.ma.send_command
        async def disappear(command,**args):
            if command=='config/flows/submit': raise failure
            return await original(command,**args)
        with patch.object(self.ma,'send_command',side_effect=disappear):
            self.assertEqual(self.op('setup.spotify.flow.submit',id=flow['id'],step='backend')['error']['code'],'setup_restart_required')
        self.assertEqual(self.op('setup.spotify.flow.get',id=flow['id'])['result']['type'],'interrupted')
        fresh=self.op('setup.spotify.begin')['result']
        self.ma.failure=failure
        result=self.op('setup.spotify.flow.abort',id=fresh['id'])
        self.assertTrue(result['ok']);self.assertEqual(result['result']['type'],'interrupted');self.clean(result)


if __name__=='__main__': unittest.main(verbosity=2)
