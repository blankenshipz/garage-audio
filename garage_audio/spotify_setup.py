"""Bounded official Spotify Connect setup; no generic MA configuration or sound authority."""
import asyncio
import json
import logging
import math
import os
import re
import stat
import time
import uuid
from . import audio_public, private_records

DOMAIN = 'spotify_connect'
TIMEOUT = 15
REQUIRED_VOLUME_MODE = 'spotify_authoritative'
SCHEMAS = {op:([],[]) for op in ('setup.spotify.status','setup.spotify.begin','setup.spotify.reconfigure')}
SCHEMAS.update({f'setup.spotify.flow.{op}':(['id'],[]) for op in ('get','abort')})
SCHEMAS['setup.spotify.flow.submit'] = (['id','step'],['accept_terms','credential_ref','keep_stored_key'])
SCHEMAS['setup.spotify.configure'] = (['advertised_name'],[])
ID = {'type':'string','pattern':'^[a-f0-9]{32}$'}
OPERATION_SCHEMAS = {op:{'type':'object','additionalProperties':False,'properties':{},'required':[]}
                     for op in SCHEMAS}
for op in ('get','abort'):
    OPERATION_SCHEMAS[f'setup.spotify.flow.{op}'].update(properties={'id':ID},required=['id'])
OPERATION_SCHEMAS['setup.spotify.configure'].update(properties={'advertised_name':{
    'type':'string','minLength':1,'maxLength':60,'pattern':r'^[A-Za-z0-9][A-Za-z0-9 ._-]*$'}},required=['advertised_name'])
OPERATION_SCHEMAS['setup.spotify.flow.submit'] = {'oneOf':[
    {'type':'object','additionalProperties':False,'required':['id','step',*extra],
     'properties':{'id':ID,'step':{'const':step},**extra}}
    for step,extra in [('backend',{}),('soloist_terms',{'accept_terms':{'type':'boolean'}}),
        ('soloist_api_key',{'credential_ref':{'const':'spotify-soloist'}}),
        ('soloist_api_key',{'keep_stored_key':{'const':True}})]]}


class SetupFailure(Exception):
    def __init__(self,code,message,requirements=None):
        self.code,self.message,self.requirements = code,message,requirements or []


def credential(env,minimum):
    path=os.environ.get(env)
    if not path:
        raise SetupFailure('ma_credential_required' if env=='MA_TOKEN_FILE' else 'soloist_credential_required',
            'Private server credential reference is missing',
            ['Operator provisions the existing private server reference; no dashboard token or credential value is requested'])
    try:
        fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
        with os.fdopen(fd,'rb') as stream:
            info=os.fstat(stream.fileno())
            if (not stat.S_ISREG(info.st_mode) or info.st_uid!=os.getuid() or info.st_nlink!=1
                    or stat.S_IMODE(info.st_mode)&0o077): raise ValueError()
            data=stream.read(4097)
        value=data.decode('ascii').strip()
        if len(data)>4096 or not minimum<=len(value)<=4096: raise ValueError()
        return value
    except (OSError,ValueError,UnicodeError):
        raise SetupFailure('credential_invalid','Private server credential reference is invalid') from None


def client(token):
    from importlib.metadata import version
    if version('music-assistant-client')!='1.6.0':
        raise SetupFailure('ma_client_unavailable','Pinned Music Assistant client is required')
    from music_assistant_client import MusicAssistantClient
    # The pinned client's DEBUG wire logs include authentication and secure forms.
    # This code-owned safeguard cannot be enabled by ambient application log levels.
    for name in ('music_assistant_client','music_assistant_client.connection'):
        logging.getLogger(name).disabled=True
    endpoint=os.environ.get('MA_URL','http://music-assistant:8095')
    if endpoint not in ('http://music-assistant:8095','http://127.0.0.1:8095'):
        raise SetupFailure('ma_endpoint_invalid','Server Music Assistant endpoint is not allowlisted')
    return MusicAssistantClient(endpoint,None,token=token)


def fixed_player():
    value=os.environ.get('GARAGE_AUDIO_PLAYER','02:00:00:00:21:01')
    if not re.fullmatch(r'[A-Za-z0-9:_.-]{1,100}',value):
        raise SetupFailure('ma_target_required','Server garage player configuration is invalid')
    return value


def load(application,collection,name):
    with private_records.directory(application.root,collection) as fd:
        try: return private_records.read(fd,name,16384)
        except FileNotFoundError: return None


def save(application,collection,name,value):
    with private_records.directory(application.root,collection) as fd:
        private_records.write(fd,name,json.dumps(value,allow_nan=False).encode())


def selected_players(config):
    entry=config.values.get('connected_players')
    value=entry.value if entry is not None else []
    if value is None: value=[]
    if not isinstance(value,list) or any(not isinstance(item,str) for item in value):
        raise SetupFailure('ma_provider_invalid','Provider player binding is invalid')
    return value


async def provider(ma,application):
    binding=load(application,'setup-config','spotify.json') or {}
    target=os.environ.get('GARAGE_CONNECT_PROVIDER') or binding.get('instance_id')
    configs=await ma.config.get_provider_configs(provider_domain=DOMAIN,include_values=True)
    if len(configs)>32: raise SetupFailure('storage_limit','Provider inventory exceeds bounded setup limit')
    configs=[conf for conf in configs if conf.domain==DOMAIN]
    if target:
        matches=[conf for conf in configs if conf.instance_id==target]
    else:
        matches=[conf for conf in configs if selected_players(conf)==[fixed_player()]]
    if len(matches)>1: raise SetupFailure('ma_provider_ambiguous','Garage provider binding is ambiguous')
    if not matches:
        if target: raise SetupFailure('ma_provider_missing','Server-bound garage provider is missing')
        return None
    config=matches[0]
    if selected_players(config) not in ([],[fixed_player()]):
        raise SetupFailure('ma_provider_target_conflict','Provider serves other players; it will not be modified')
    return config


def configured_backend(config):
    if config is None: return None
    setup_data=getattr(config,'setup_data',None) or {}
    value=setup_data.get('backend') if isinstance(setup_data,dict) else None
    if value is None:
        entry=config.values.get('backend');value=entry.value if entry else None
    return value if value in ('soloist','go_librespot') else None


def configured_volume_mode(config):
    entry=config.values.get('volume_mode') if config is not None else None
    value=entry.value if entry else None
    return value if value in ('player_only','sync_spotify',REQUIRED_VOLUME_MODE) else None


async def require_volume_policy(ma,config):
    if configured_backend(config)!='soloist':
        raise SetupFailure('required_volume_policy_unavailable','Required volume policy is available only for reviewed Soloist setup')
    entries=await ma.config.get_provider_config_entries(DOMAIN,instance_id=config.instance_id)
    if len(entries)>64: raise SetupFailure('required_volume_policy_unavailable','Provider configuration exceeds reviewed bounds')
    matches=[entry for entry in entries if entry.key=='volume_mode']
    if len(matches)==1:
        entry=matches[0]
        kind=getattr(entry.type,'value',entry.type)
        options=entry.options or ()
        if (kind=='string' and entry.hidden is False and len(options)<=8
                and any(option.value==REQUIRED_VOLUME_MODE and not option.disabled for option in options)):
            return
    raise SetupFailure('required_volume_policy_unavailable','Music Assistant does not advertise the required Soloist volume policy',
        ['Use the reviewed pinned MA volume-policy candidate; no player-only fallback or generic caller-selected mode is allowed'])


def summary(config):
    return {'provider':DOMAIN,'configured':config is not None,
        'player_id':fixed_player(),'player_bound':config is not None and selected_players(config)==[fixed_player()],
        'enabled':bool(config.enabled) if config else False,'runtime_verified':False,
        'setup_separate_from_daily_playback':True,'daily_playback_authority':'spotify',
        'sound_authority':False,'secure_values_returned':False,
        'backend':configured_backend(config),'required_volume_mode':REQUIRED_VOLUME_MODE,
        'configured_volume_mode':configured_volume_mode(config),
        'volume_policy_configured':configured_backend(config)=='soloist' and configured_volume_mode(config)==REQUIRED_VOLUME_MODE,
        'runtime_qualified':False}


def flow_records(application):
    with private_records.directory(application.root,'spotify-flows') as fd:
        names=[name for name in os.listdir(fd) if re.fullmatch(r'[a-f0-9]{32}\.json',name)]
    if len(names)>32: raise SetupFailure('storage_limit','Provider flow count exceeds bound')
    return [load(application,'spotify-flows',name) for name in names]


def active_record(application):
    active=[record for record in flow_records(application)
            if record['state'] not in ('finish','abort','interrupted') and record['expires_at']>time.time()]
    return max(active,key=lambda record:record['created_at']) if active else None


def active_flow(application):
    record=active_record(application)
    return record['view'] if record else None


def verified_flow_missing(error,record):
    # MA2.10.5 _get_flow raises this exact KeyError; pinned WS maps it to code0.
    # Never classify generic offline/auth/scope failures or unrelated code0 errors.
    message='Unknown (or finished) setup flow: '+record['ma_flow_id']
    return getattr(error,'error_code',None)==0 and str(error) in (message,repr(message))


def interrupt_flow(application,record):
    record['state']='interrupted'
    record['view']={'id':record['id'],'type':'interrupted','step':'interrupted',
        'expires_at':record['expires_at'],'fields':[],'requires_human_action':False,
        'sound_authority':False,'secure_values_returned':False,'restart_required':True,
        'code':'setup_restart_required','reason':'ma_flow_missing',
        'notice':'Music Assistant no longer owns this setup step, for example after restart. Start a new official setup; earlier terms or key steps are not reused. Stored everyday receiver credentials are unchanged.'}
    save(application,'spotify-flows',record['id']+'.json',record)
    return record['view']


async def get_owned_step(ma,application,record):
    try: return await ma.send_command('config/flows/get',flow_id=record['ma_flow_id'])
    except Exception as error:
        if not verified_flow_missing(error,record): raise
        interrupt_flow(application,record)
        return None


async def reconcile_active(ma,application):
    record=active_record(application)
    if record:
        step=await get_owned_step(ma,application,record)
        if step is not None:
            project_step(record,step)
            save(application,'spotify-flows',record['id']+'.json',record)


async def quiet(ma,required=False):
    if audio_public.deployment_mode()!='docker':
        raise SetupFailure('docker_audio_required','Spotify setup requires the Docker public worker')
    status=audio_public.request('audio.status')
    if status['state']!='off' or status['device_open'] is not False or status.get('owned_children'):
        raise SetupFailure('quiet_setup_required','Stop the garage output before changing provider setup')
    await ma.players.fetch_state()
    players=[player for player in ma.players.players if player.player_id==fixed_player()]
    if required and (not players or players[0].available is not True):
        raise SetupFailure('ma_player_unavailable','Fixed garage player is not available')
    if players and players[0].playback_state!='idle':
        raise SetupFailure('quiet_setup_required','Stop Spotify playback before changing provider setup; source is not idle')


def project_step(record,step):
    if not isinstance(step,dict): raise SetupFailure('ma_flow_invalid','Provider setup response invalid')
    kind=step.get('type')
    if kind not in ('form','progress','finish','abort'):
        raise SetupFailure('ma_flow_unsupported','Provider requires an unsupported handoff; no external URL or secure payload forwarded')
    raw_id=step.get('flow_id')
    if not isinstance(raw_id,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}',raw_id):
        raise SetupFailure('ma_flow_invalid','Provider setup identifier invalid')
    if record.get('ma_flow_id') not in (None,raw_id):
        raise SetupFailure('ma_flow_invalid','Provider setup changed unexpectedly')
    record['ma_flow_id']=raw_id
    name=step.get('step_id') if kind=='form' else kind
    entries=step.get('entries') or []
    if not isinstance(entries,list) or len(entries)>2:
        raise SetupFailure('ma_flow_unsupported','Provider form exceeds reviewed field bounds')
    if kind=='form' and name not in ('backend','soloist_terms','soloist_api_key'):
        raise SetupFailure('ma_flow_unsupported','Provider form is not reviewed')
    keys={entry.get('key') for entry in entries if isinstance(entry,dict)}
    allowed={'backend':{'backend'},'soloist_terms':{'soloist_download_consent'},
             'soloist_api_key':{'soloist_api_key','soloist_api_key_hint'}}
    types={'backend':'string','soloist_download_consent':'boolean',
           'soloist_api_key':'secure_string','soloist_api_key_hint':'label'}
    required={'backend':'backend','soloist_terms':'soloist_download_consent','soloist_api_key':'soloist_api_key'}
    if kind=='form' and (not keys or not keys<=allowed[name] or required[name] not in keys
            or len(keys)!=len(entries) or any(entry.get('type')!=types[entry['key']] for entry in entries)):
        raise SetupFailure('ma_flow_unsupported','Provider fields are not reviewed')
    record['stored_key_available']='soloist_api_key_hint' in keys
    fields=[]
    if name=='backend': fields=[{'name':'backend','type':'select','options':['soloist'],'value':'soloist'}]
    if name=='soloist_terms': fields=[{'name':'accept_terms','type':'boolean','value':False,'required':True}]
    if name=='soloist_api_key': fields=[{'name':'credential_ref','type':'server_reference','value':'spotify-soloist'},
        {'name':'keep_stored_key','type':'boolean','available':record['stored_key_available']}]
    expires=step.get('expires_at')
    if type(expires) in (int,float) and math.isfinite(expires): record['expires_at']=min(record['expires_at'],expires)
    view={'id':record['id'],'type':kind,'step':name,'expires_at':record['expires_at'],'fields':fields,
          'requires_human_action':name in ('soloist_terms','soloist_api_key'),
          'human_handoff':'explicit_soloist_terms_and_private_personal_key' if name in ('soloist_terms','soloist_api_key') else None,
          'sound_authority':False,'secure_values_returned':False,
          'validation_failed':bool(step.get('errors'))}
    if name=='soloist_terms':
        view['notice']='Spotify Premium is required for a personal Soloist API key. MA warns that using Soloist through Music Assistant may violate Spotify terms and is at your own risk. Explicit consent allows MA to download, run, retain and automatically update Soloist; it is not sound approval.'
        view['links']={'documentation':'https://developer.spotify.com/documentation/soloist',
                       'policy':'https://developer.spotify.com/policy'}
    if name=='soloist_api_key':
        view['notice']='Human-owned one-time setup: create a personal Soloist key using Spotify Premium, then provision the private server reference. Never send a key in chat, MCP arguments or LAN form fields. Keeping an existing key does not reveal it. This is separate from daily Spotify playback.'
        view['links']={'key_page':'https://developer.spotify.com/dashboard/soloist'}
    record.update(view=view,state=kind)
    return view


async def finish_binding(ma,application,record,step):
    if step.get('type')!='finish': return
    if not all(record.get(key) is True for key in ('backend_selected','terms_accepted','credential_handoff')):
        raise SetupFailure('ma_flow_unverified','Official Soloist setup did not complete the reviewed steps')
    target=(step.get('result') or {}).get('instance_id')
    if not isinstance(target,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}',target):
        raise SetupFailure('ma_flow_invalid','Finished provider identifier invalid')
    expected=record.get('instance_id')
    if expected is not None and target!=expected:
        raise SetupFailure('ma_flow_invalid','Finished provider does not match server binding')
    config=await ma.config.get_provider_config(target)
    if config.domain!=DOMAIN or selected_players(config) not in ([],[fixed_player()]):
        raise SetupFailure('ma_provider_target_conflict','Finished provider is not a garage-only configuration')
    save(application,'setup-config','spotify.json',{'instance_id':target,'official_setup_completed':True})


async def run(operation,args,application,token):
    async with client(token) as ma:
        if operation=='setup.spotify.status':
            config=await provider(ma,application)
            await reconcile_active(ma,application)
            binding=load(application,'setup-config','spotify.json') or {}
            reviewed=config is not None and binding.get('instance_id')==config.instance_id and binding.get('official_setup_completed') is True
            records=flow_records(application)
            latest=max(records,key=lambda record:record['created_at']) if records else None
            active=active_flow(application)
            last=latest['view'] if latest and latest['state'] in ('finish','abort','interrupted') else None
            available=False;policy_error=None
            if config is not None:
                try:
                    await require_volume_policy(ma,config)
                    available=True
                except SetupFailure as error:
                    if error.code!='required_volume_policy_unavailable': raise
                    policy_error={'code':error.code,'message':error.message}
            return {**summary(config),'official_setup_completed':reviewed,'active_flow':active,
                'last_flow':last,'setup_restart_required':bool(not active and last and last['type']=='interrupted'),
                'volume_policy_available':available,'volume_policy_error':policy_error,
                'volume_policy_configured':available and configured_volume_mode(config)==REQUIRED_VOLUME_MODE}
        if operation in ('setup.spotify.begin','setup.spotify.reconfigure','setup.spotify.configure'):
            config=await provider(ma,application)
            await quiet(ma,required=operation=='setup.spotify.configure')
            if operation=='setup.spotify.configure':
                if config is None: raise SetupFailure('ma_provider_setup_required','Complete provider setup first')
                binding=load(application,'setup-config','spotify.json') or {}
                if binding.get('instance_id')!=config.instance_id or binding.get('official_setup_completed') is not True:
                    raise SetupFailure('ma_official_setup_required','Complete reviewed Soloist reconfigure before changing the garage provider')
                await require_volume_policy(ma,config)
                # Partial public save preserves setup_data, secure fields, queues and other providers.
                await ma.config.save_provider_config(DOMAIN,
                    {'connected_players':[fixed_player()],'publish_name_template':args['advertised_name'],
                     'volume_mode':REQUIRED_VOLUME_MODE},
                    instance_id=config.instance_id)
                saved=await ma.config.get_provider_config(config.instance_id)
                if configured_backend(saved)!='soloist' or configured_volume_mode(saved)!=REQUIRED_VOLUME_MODE:
                    raise SetupFailure('required_volume_policy_mismatch','Persisted Soloist volume policy did not match the required policy')
                if saved.domain!=DOMAIN or saved.instance_id!=config.instance_id or selected_players(saved)!=[fixed_player()]:
                    raise SetupFailure('ma_configuration_unverified','Provider binding readback did not match')
                name=saved.values.get('publish_name_template')
                if name is None or name.value!=args['advertised_name']:
                    raise SetupFailure('ma_configuration_unverified','Provider advertised-name readback did not match')
                save(application,'setup-config','spotify.json',{'instance_id':saved.instance_id,'official_setup_completed':True})
                return {**summary(saved),'advertised_name':args['advertised_name'],'submitted':True}
            if operation=='setup.spotify.begin' and config is not None:
                raise SetupFailure('ma_provider_already_configured','Garage provider exists; use reconfigure without deleting it')
            if operation=='setup.spotify.reconfigure' and config is None:
                raise SetupFailure('ma_provider_setup_required','No garage provider is configured')
            await reconcile_active(ma,application)
            if active_flow(application) is not None:
                raise SetupFailure('setup_flow_active','Read or abort the current app-owned setup flow before starting another')
            with private_records.directory(application.root,'spotify-flows') as fd:
                if len(os.listdir(fd))>=32: raise SetupFailure('storage_limit','Provider flow limit reached; retained records are not deleted')
            command='config/providers/setup' if config is None else 'config/providers/reconfigure'
            values={'provider_domain':DOMAIN} if config is None else {'instance_id':config.instance_id}
            step=await ma.send_command(command,**values)
            record={'id':uuid.uuid4().hex,'instance_id':config.instance_id if config else None,
                    'expires_at':time.time()+1800,'created_at':time.time()}
            view=project_step(record,step)
            await finish_binding(ma,application,record,step)
        else:
            record=load(application,'spotify-flows',args['id']+'.json')
            if record is None: raise SetupFailure('not_found','App-owned provider flow not found')
            if record['state'] in ('finish','abort','interrupted'):
                if operation=='setup.spotify.flow.get': return record['view']
                if record['state']=='interrupted':
                    raise SetupFailure('setup_restart_required','Start a new official setup; the retained step is interrupted')
                raise SetupFailure('setup_flow_terminal','Provider flow is already terminal; originals retained')
            if time.time()>=record['expires_at']:
                raise SetupFailure('setup_flow_expired','Setup flow expired; provider playback credentials remain intact')
            if operation=='setup.spotify.flow.abort':
                try: await ma.send_command('config/flows/abort',flow_id=record['ma_flow_id'])
                except Exception as error:
                    if not verified_flow_missing(error,record): raise
                    return interrupt_flow(application,record)
                record['state']='abort';record['view'].update(type='abort',step='abort',fields=[],requires_human_action=False)
                view=record['view']
            else:
                step=await get_owned_step(ma,application,record)
                if step is None:
                    if operation=='setup.spotify.flow.get': return record['view']
                    raise SetupFailure('setup_restart_required','Start a new official setup; Music Assistant no longer owns the retained step')
                view=project_step(record,step)
                if operation=='setup.spotify.flow.submit':
                    await quiet(ma)
                    if view['type']!='form' or args['step']!=view['step']:
                        raise SetupFailure('setup_step_changed','Reload current provider step before submitting')
                    if args['step']=='backend':
                        values={'backend':'soloist'};record['backend_selected']=True
                    elif args['step']=='soloist_terms':
                        values={'soloist_download_consent':args['accept_terms']};record['terms_accepted']=args['accept_terms']
                    elif args.get('keep_stored_key'):
                        if not record['stored_key_available']:
                            raise SetupFailure('soloist_credential_required','No stored provider key is available')
                        values={'soloist_api_key':''}
                    else: values={'soloist_api_key':credential('GARAGE_SOLOIST_API_KEY_FILE',16)}
                    if args['step']=='soloist_api_key': record['credential_handoff']=True
                    # Journal non-secret attempted step metadata before remote mutation;
                    # a later get can reconcile a response lost after server-side finish.
                    save(application,'spotify-flows',record['id']+'.json',record)
                    try: step=await ma.send_command('config/flows/submit',flow_id=record['ma_flow_id'],values=values)
                    except Exception as error:
                        if not verified_flow_missing(error,record): raise
                        interrupt_flow(application,record)
                        raise SetupFailure('setup_restart_required','Start a new official setup; Music Assistant lost the owned step before submission') from None
                    view=project_step(record,step)
                await finish_binding(ma,application,record,step)
        save(application,'spotify-flows',record['id']+'.json',record)
        return view


def execute(operation,args,application):
    token=credential('MA_TOKEN_FILE',24)
    try:
        return asyncio.run(asyncio.wait_for(run(operation,args,application,token),TIMEOUT))
    except (SetupFailure,audio_public.AudioFailure): raise
    except asyncio.TimeoutError:
        raise SetupFailure('ma_setup_timeout','Music Assistant setup deadline expired',
            ['Read the current app-owned flow before retrying; no rollback or successful remote mutation is inferred']) from None
    except Exception as error:
        code=getattr(error,'error_code',None)
        if code in (20,21,23):
            raise SetupFailure('ma_authentication_required','Music Assistant server credential is not accepted',
                ['Operator refreshes the existing private server reference; no token value is exposed']) from None
        if code==22:
            raise SetupFailure('ma_scope_required','Music Assistant setup scope is missing',
                ['Existing server credential requires provider configuration read/write and player read scopes']) from None
        raise SetupFailure('ma_setup_unavailable','Music Assistant setup could not be verified',
            ['Check private server credential scopes, pinned MA connectivity and human-owned Spotify setup; private error details are not returned']) from None
