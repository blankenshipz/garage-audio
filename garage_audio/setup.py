"""App-owned setup readback/config; private references are server-owned, not client paths."""
import json
import os
import stat
from jsonschema import Draft202012Validator
from . import private_records, audio_public, spotify_setup, ma_enrollment

SCHEMAS = {'setup.status':([],[]), 'setup.config.get':([],[]),
           'setup.config.set':(['display_name'],[])}
OPERATION_SCHEMAS = {op:{'type':'object','additionalProperties':False,
    'required':required,'properties':{'display_name':{'type':'string','minLength':1,'maxLength':80}}
    if required else {}} for op,(required,_) in SCHEMAS.items()}


def reference_status(env, name):
    path = os.environ.get(env)
    if not path:
        return {'reference':name,'available':False,'code':'credential_missing'}
    try:
        fd = os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
        try:
            info = os.fstat(fd)
            valid = (stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid()
                     and not stat.S_IMODE(info.st_mode)&0o077 and info.st_nlink == 1
                     and 24 <= info.st_size <= 4097)
        finally:
            os.close(fd)
    except OSError:
        valid = False
    return {'reference':name,'available':valid,'code':None if valid else 'credential_invalid'}


def execute(operation,args,application):
    with private_records.directory(application.root,'setup-config') as fd:
        if operation == 'setup.config.set':
            private_records.write(fd,'config.json',json.dumps(args,allow_nan=False).encode())
        try:
            config = private_records.read(fd,'config.json',4096)
        except FileNotFoundError:
            config = {'display_name':'Garage audio'}
    if not Draft202012Validator(OPERATION_SCHEMAS['setup.config.set']).is_valid(config):
        raise ValueError('Private setup configuration invalid')
    if operation != 'setup.status': return config
    try:
        readback = application.policy({'cmd':'status'})
        docker = audio_public.deployment_mode() == 'docker'
        device = {'available':not docker or readback.get('device_mode')=='hardware',
                  'verified':False,'code':'device_verification_required'}
        audio = {'available':True,'readback':readback,'error':None}
        if docker and readback.get('device_mode') != 'hardware':
            device['code'] = 'physical_device_unavailable'
        # Broker availability alone never proves the physical identity or output mapping.
        if readback.get('device_verified') is True and (not docker or readback.get('physical_verified') is True):
            device = {'available':True,'verified':True,'code':None}
    except audio_public.AudioFailure as error:
        audio = {'available':False,'readback':None,'error':{'code':error.code,'message':error.message}}
        device = {'available':False,'verified':False,'code':'device_unavailable'}
    except Exception:
        audio = {'available':False,'readback':None,'error':{'code':'device_unavailable'}}
        device = {'available':False,'verified':False,'code':'device_unavailable'}
    ma = reference_status('MA_TOKEN_FILE','music-assistant')
    agent = reference_status('GARAGE_AGENT_TOKEN_FILE','application-agent')
    try:
        spotify = {'ok':True,'result':spotify_setup.execute('setup.spotify.status',{},application),'error':None}
    except spotify_setup.SetupFailure as error:
        spotify = {'ok':False,'result':None,'error':{'code':error.code,'message':error.message,'requirements':error.requirements}}
    source=spotify['result'] or {}
    code=(spotify['error']['code'] if not spotify['ok'] else
          'setup_restart_required' if source.get('setup_restart_required') else
          'ma_provider_setup_required' if not source.get('configured') else
          'required_volume_policy_unavailable' if not source.get('volume_policy_available') else
          'required_volume_policy_mismatch' if not source.get('volume_policy_configured') else
          'runtime_qualification_required')
    readiness={'provider_configured':bool(source.get('configured')),
               'volume_policy_configured':bool(source.get('volume_policy_configured')),
               'runtime_qualified':False,'ready':False,'code':code}
    return {'config':config,'device':device,'audio':audio,'spotify_setup':spotify,'spotify_readiness':readiness,
            'ma_enrollment':ma_enrollment.status(),
            'deployment_mode':audio_public.deployment_mode(),'credentials':{'music_assistant':ma,'agent':agent},
            'agent':application.agent_harness.status(),'backend':'music-assistant',
            'ordinary_playback_authority':'spotify','inference_enabled':False,
            'prerequisites':[{'code':entry['code'],'component':name} for name,entry in
                [('device',device),('music_assistant',ma)] if entry['code']],
            'sound_authority':False,'raw_dsp_exposed':False}
