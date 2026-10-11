"""One validated operation boundary shared by HTTP and MCP."""
import asyncio
import copy
import fcntl
import json
import os
from pathlib import Path
import re
import threading
import time
import uuid
from urllib.parse import urlsplit
from jsonschema import Draft202012Validator
from . import calibration, ipc, rew, measurement, measurement_route
from .profile import DEFAULT, number, validate
from .human_authority import HumanVerifier
from .measurement_jobs import Jobs, JobFailure
from .room_profiles import Store as RoomProfileStore, DomainFailure, SCHEMAS as ROOM_SCHEMAS, OPERATION_SCHEMAS as ROOM_OPERATION_SCHEMAS
from .agent_harness import Store as AgentStore, AgentFailure, SCHEMAS as AGENT_SCHEMAS, OPERATION_SCHEMAS as AGENT_OPERATION_SCHEMAS
from . import commissioning_proof
from . import offline_candidate

SCHEMAS = {
    'status':([],[]), 'capabilities':([],[]), 'sessions.list':([],[]),
    'sessions.create':(['name'],['metadata']), 'sessions.get':(['id'],[]),
    'measurements.import':(['session_id','filename','format','text'],['metadata']),
    'measurements.compare':(['session_id','before_id','after_id'],[]),
    'profiles.list':([],[]), 'profiles.save':(['profile'],[]),
    'profiles.get':(['id'],[]), 'profiles.preview':(['id'],[]), 'profiles.apply':(['id'],[]),
    'profiles.import_rew':(['id','channel','text'],[]),
    'profiles.edit':(['id'],['channel','gain','mute','delay_ms','invert','peq','crossover_hz','sub_trim','peq_bypass']),
    'dsp.mute':([],[]), 'dsp.volume':(['db'],[]), 'dsp.sub_trim':(['db'],[]),
    'dsp.unmute':([],[]), 'commissioning.start':([],[]), 'calibration.guide':([],[]),
    'playback.status':([],[]), **{f'playback.{c}':(['player_id'],[]) for c in ('queue','pause','stop','clear','play','next')},
    'playback.play_url':(['player_id','url'],[]),
    'calibration.plan':(['name'],['positions','channels','sample_rate','orientation','target']),
    **{f'calibration.{op}':(['id'],[]) for op in ('get','analyze','propose','cancel')},
    'jobs.request':(['session_id','position','channel'],['adapter']),
    'jobs.list':([],[]), **{f'jobs.{op}':(['id'],[]) for op in ('get','execute','cancel')},
}

def object_schema(properties, required=None):
    return {'type':'object','properties':properties,'required':list(properties) if required is None else required,'additionalProperties':False}

def numeric(lo,hi):
    return {'type':'number','minimum':lo,'maximum':hi}

PEQ_SCHEMA = {'type':'array','maxItems':10,'items':object_schema({'freq':numeric(20,20000),'q':numeric(.1,20),'gain':numeric(-20,0)})}
CHANNEL_SCHEMA = object_schema({'gain':numeric(-60,0),'mute':{'type':'boolean'},'delay_ms':numeric(0,50),'invert':{'type':'boolean'},'peq':PEQ_SCHEMA})
PROFILE_SCHEMA = object_schema({'name':{'type':'string','maxLength':80},'channels':object_schema({c:CHANNEL_SCHEMA for c in ('left','right','sub')}),'sub_trim':numeric(-30,0),'peq_bypass':{'type':'boolean'},'crossover_hz':numeric(40,160)})
METADATA_SCHEMA = object_schema({**{k:{'type':'string','maxLength':2000} for k in ('room','position','profile','timing_reference','notes')},'channel':{'enum':['left','right','sub','combined','unknown']},'orientation':{'enum':['0','90','unknown']},'sample_rate':{'type':'integer','enum':[44100,48000,88200,96000,176400,192000]}},[])
METADATA_SCHEMA['properties'].update({'source':{'enum':['measured','synthetic']},
    'calibration_sha256':{'type':'string','pattern':'^[a-f0-9]{64}$'},
    'calibration_applied':{'type':'boolean'}, 'microphone_serial':{'type':'string','minLength':1,'maxLength':120},
    'timing_quality':{'enum':['unknown','unverified','verified']}, 'clock_drift_ppm':numeric(-1000,1000)})
ARG_TYPES = {
    **{k:{'type':'string','pattern':'^[a-f0-9]{32}$'} for k in ('id','session_id','before_id','after_id')},
    'name':{'type':'string','minLength':1,'maxLength':120},'metadata':METADATA_SCHEMA,
    'filename':{'type':'string','minLength':1,'maxLength':120,'pattern':r'^[\w .-]+$'},
    'format':{'enum':['rew-response','mic-calibration']},'text':{'type':'string','maxLength':262144},
    'profile':PROFILE_SCHEMA,'channel':{'enum':['left','right','sub']},
    'player_id':{'type':'string','minLength':1,'maxLength':200},
    'url':{'type':'string','minLength':1,'maxLength':2048,'description':'HTTP(S) media URL for official MA play_media REPLACE; no application fetch. Locked until independent trusted attended authority exists.'},
    **CHANNEL_SCHEMA['properties'], 'crossover_hz':numeric(40,160),'sub_trim':numeric(-30,0),'peq_bypass':{'type':'boolean'},
    'positions':{'type':'array','minItems':1,'maxItems':8,'uniqueItems':True,'items':{'type':'string','minLength':1,'maxLength':80}},
    'channels':{'type':'array','minItems':1,'maxItems':3,'uniqueItems':True,'items':{'enum':['left','right','sub']}},
    'sample_rate':{'type':'integer','enum':[44100,48000,96000]}, 'orientation':{'enum':['0','90']},
    'target':{'enum':['server-umik-umc','laptop-rew']},
    'position':{'type':'string','minLength':1,'maxLength':80},'adapter':{'enum':['file-null','hardware']},
}
OPERATION_SCHEMAS = {op:object_schema({k:ARG_TYPES[k] for k in required+optional},required) for op,(required,optional) in SCHEMAS.items() if op not in ('dsp.volume','dsp.sub_trim')}
OPERATION_SCHEMAS.update({'dsp.volume':object_schema({'db':numeric(-90,-20)}),'dsp.sub_trim':object_schema({'db':numeric(-30,0)})})
SCHEMAS.update(ROOM_SCHEMAS)
OPERATION_SCHEMAS.update(ROOM_OPERATION_SCHEMAS)
SCHEMAS.update(AGENT_SCHEMAS)
OPERATION_SCHEMAS.update(AGENT_OPERATION_SCHEMAS)
SCHEMAS.update(commissioning_proof.SCHEMAS)
OPERATION_SCHEMAS.update(commissioning_proof.OPERATION_SCHEMAS)
SCHEMAS.update(offline_candidate.SCHEMAS)
OPERATION_SCHEMAS.update(offline_candidate.OPERATION_SCHEMAS)

MAX_RECORD_BYTES = 4 * 1024 * 1024
MAX_STORAGE_BYTES = 32 * 1024 * 1024
MAX_PROFILES = 128
MAX_SESSIONS = 128

class Requirement(Exception):
    def __init__(self, code, message, requirements):
        self.code, self.message, self.requirements = code, message, requirements

def private_token(path):
    p = Path(path)
    if p.is_symlink() or not p.is_file() or p.stat().st_mode & 0o077 or p.stat().st_uid != os.getuid():
        raise ValueError('credential reference must be a private regular file')
    token = p.read_text().strip()
    if not 24 <= len(token) <= 4096 or not token.isascii():
        raise ValueError('invalid credential length')
    return token

class Application:
    def __init__(self, root, broker=ipc.request, *, human_reference=None, job_options=None):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.broker = broker
        self.lock = threading.RLock()
        self.human_approval_available = False
        self.human_approval_unavailable_reason = 'No verified standalone native loopback listener; shared/container listener cannot accept human credentials'
        self.humans = HumanVerifier(self.root,human_reference or os.environ.get('GARAGE_HUMAN_AUTH_FILE'),os.environ.get('GARAGE_APP_TOKEN_FILE'))
        self.jobs = Jobs(self,self.humans,**(job_options or {}))
        self.room_profiles = RoomProfileStore(self.root)
        self.agent_harness = AgentStore(self.root)

    def path(self, collection, identifier):
        if not isinstance(identifier,str) or not re.fullmatch(r'[a-f0-9]{32}',identifier):
            raise ValueError('invalid generated ID')
        return self.root / collection / (identifier+'.json')

    def read(self, collection, identifier):
        p = self.path(collection,identifier)
        if not p.is_file() or p.is_symlink():
            raise Requirement('not_found','Record not found',[])
        if p.stat().st_size > MAX_RECORD_BYTES:
            raise Requirement('storage_limit','Record exceeds bounded retrieval limit',['Operator must archive oversized state; no content is silently truncated'])
        return json.loads(p.read_text())

    def session_view(self, record):
        """Revision-gated public projection; private originals are not rewritten."""
        if 'workflow_version' not in record: return record
        view = dict(record)
        has_analysis = 'analysis' in record or record.get('analysis_available',False)
        has_proposal = 'proposal' in record or record.get('proposal_available',False)
        analysis_revision = record.get('analysis',{}).get('analysis_revision',record.get('analysis_revision'))
        proposal_revision = record.get('proposal',{}).get('analysis_revision',record.get('proposal_analysis_revision'))
        stale = (record.get('derived_results_superseded',False) or
                 (record.get('state') in ('analyzed','proposed') and not has_analysis) or
                 (record.get('state') == 'proposed' and not has_proposal) or
                 (has_analysis and analysis_revision != measurement.ANALYSIS_REVISION) or
                 (has_proposal and (not has_analysis or proposal_revision != measurement.ANALYSIS_REVISION)))
        view['analysis_available'] = bool(has_analysis and not stale)
        view['proposal_available'] = bool(has_proposal and not stale)
        view['derived_results_superseded'] = bool(stale)
        view['recompute_required'] = bool(stale and record.get('state') != 'cancelled')
        if stale:
            view.pop('analysis',None); view.pop('proposal',None)
            view['analysis_revision'] = None
            view['proposal_analysis_revision'] = None
            if record.get('state') != 'cancelled':
                view['state'] = 'imported' if record.get('files') else 'planned'
            view['derived_results_warning'] = 'derived_results_superseded: previous analysis/proposal validation is not current; original imports retained'
            view['derived_results_requirements'] = ['Run calibration.analyze then calibration.propose to recompute under current validation; cancelled sessions remain terminal']
        return view

    def save(self, collection, record):
        p = self.path(collection,record['id'])
        session_changed = collection == 'sessions' and p.exists() and self.read(collection,record['id']) != record
        if collection == 'profiles' and not p.exists() and self.count('profiles') >= MAX_PROFILES:
            raise Requirement('storage_limit','Profile limit reached',['Operator archive required; profiles are never silently deleted'])
        payloads = [(p,json.dumps(record,allow_nan=False).encode('utf-8'))]
        if collection == 'sessions':
            manifest = {k:v for k,v in record.items() if k not in ('files','analysis','proposal')}
            manifest['projection_version'] = 1
            manifest['analysis_revision'] = record.get('analysis',{}).get('analysis_revision')
            manifest['proposal_analysis_revision'] = record.get('proposal',{}).get('analysis_revision')
            manifest['analysis_available'] = 'analysis' in record
            manifest['proposal_available'] = 'proposal' in record
            manifest['files'] = [{k:v for k,v in f.items() if k not in ('points','original_text','headers')} | {'point_count':len(f['points'])} for f in record['files']]
            manifest = self.session_view(manifest)
            payloads.append((self.path('session-manifests',record['id']),json.dumps(manifest,allow_nan=False).encode('utf-8')))
        elif collection == 'profiles':
            manifest = {k:v for k,v in record.items() if k != 'source'}
            payloads.append((self.path('profile-manifests',record['id']),json.dumps(manifest,allow_nan=False).encode('utf-8')))
        if any(len(data)>MAX_RECORD_BYTES for _,data in payloads):
            raise Requirement('storage_limit','Session or record size limit reached',['Maximum detailed record is 4 MiB; start another session or archive state; originals are never truncated'])
        current = sum(f.stat().st_size for directory in ('sessions','session-manifests','profiles','profile-manifests') for f in (self.root/directory).glob('*.json') if f.is_file())
        replaced = sum(target.stat().st_size for target,_ in payloads if target.exists())
        if current - replaced + sum(len(data) for _,data in payloads) > MAX_STORAGE_BYTES:
            raise Requirement('storage_limit','Aggregate storage limit reached',['Maximum application record storage is 32 MiB; operator archive required; originals are never silently deleted'])
        for target,data in payloads:
            self.write_atomic(target,data)
        if session_changed and hasattr(self,'jobs'):
            self.jobs.invalidate_session(record['id'])
        return self.session_view(record) if collection == 'sessions' else record

    def write_atomic(self, p, data):
        p.parent.mkdir(mode=0o700,exist_ok=True)
        temp = p.with_suffix('.tmp')
        fd = os.open(temp,os.O_CREAT|os.O_TRUNC|os.O_WRONLY,0o600)
        with os.fdopen(fd,'wb') as f:
            f.write(data); f.flush(); os.fsync(f.fileno())
        os.replace(temp,p)

    def count(self, collection):
        return sum(1 for p in (self.root/collection).glob('*.json') if p.is_file() and not p.is_symlink())

    def records(self, collection):
        directory = self.root / collection
        result = []
        paths = sorted(directory.glob('*.json'))
        if collection == 'sessions' and len(paths) > MAX_SESSIONS:
            raise Requirement('storage_limit','Session list exceeds bounded migration limit',['Operator archive required; maximum 128 sessions'])
        for p in paths:
            if collection in ('sessions','profiles'):
                manifest_collection = 'session-manifests' if collection == 'sessions' else 'profile-manifests'
                manifest = self.path(manifest_collection,p.stem)
                needs_refresh = not manifest.exists() or manifest.stat().st_mtime_ns < p.stat().st_mtime_ns
                if collection == 'sessions' and not needs_refresh:
                    needs_refresh = self.read(manifest_collection,p.stem).get('projection_version') != 1
                if needs_refresh:
                    # Bounded migration of existing records, only once. Status
                    # does not call this path or parse any measurement records.
                    self.save(collection,self.read(collection,p.stem))
                value = self.read(manifest_collection,p.stem)
                result.append(self.session_view(value) if collection == 'sessions' else value)
            else:
                record = self.read(collection,p.stem)
                result.append({k:v for k,v in record.items() if k != 'source'})
        return result

    def policy(self, payload):
        try:
            if payload['cmd'] not in ('status','mute'):
                # Explicitly mute first; no restore-unmute authority is forwarded.
                self.broker({'cmd':'mute'})
            return self.broker(payload)
        except (OSError,RuntimeError,ValueError):
            raise Requirement('dsp_unavailable','Policy broker unavailable or operation rejected',
                              ['Install and commission native DSP and bound UMC hardware; inspect private broker diagnostics'])

    def execute(self, operation, args):
        try:
            if operation not in SCHEMAS or not isinstance(args,dict):
                raise ValueError('unknown operation or invalid arguments')
            required, optional = SCHEMAS[operation]
            if set(required)-set(args) or set(args)-set(required+optional):
                raise ValueError('missing or unknown argument fields')
            if not Draft202012Validator(OPERATION_SCHEMAS[operation]).is_valid(args):
                raise ValueError('argument schema violation')
            with self.lock, (self.root/'application.lock').open('a') as lock:
                fcntl.flock(lock,fcntl.LOCK_EX)
                result = self._execute(operation,args)
                if operation in ('capabilities','status'):
                    result['agent'] = self.agent_harness.status()
                    result.update(measurement_route.readiness())
                    result['human_approval_available'] = self.human_approval_available
                    result['human_approval_unavailable_reason'] = None if self.human_approval_available else self.human_approval_unavailable_reason
            return {'ok':True,'result':result,'error':None}
        except (Requirement,JobFailure,DomainFailure,AgentFailure) as e:
            return {'ok':False,'result':None,'error':{'code':e.code,'message':e.message,'requirements':e.requirements}}
        except (ValueError,TypeError,KeyError,OverflowError):
            return {'ok':False,'result':None,'error':{'code':'invalid_arguments','message':'Arguments failed strict validation','requirements':['Use the documented operation schema and bounded supported inputs']}}
        except Exception:
            return {'ok':False,'result':None,'error':{'code':'service_unavailable','message':'Operation could not complete','requirements':['Inspect private service diagnostics; no successful hardware action is claimed']}}

    def _execute(self, op, a):
        if op in offline_candidate.SCHEMAS:
            return offline_candidate.execute(a,self)
        if op in commissioning_proof.SCHEMAS:
            return commissioning_proof.execute(op,a,self)
        if op in AGENT_SCHEMAS:
            return self.agent_harness.execute(op,a,self)
        if op in ROOM_SCHEMAS:
            return self.room_profiles.execute(op,a,self)
        if op == 'dsp.mute':
            try: self.jobs.invalidate_all('policy_mutation_or_mute')
            except (OSError,JobFailure): pass  # File/null journaling may never prevent native emergency mute.
            return self.policy({'cmd':'mute'})
        if op in ('profiles.apply','dsp.volume','dsp.sub_trim','dsp.mute'):
            self.jobs.invalidate_all('policy_mutation_or_mute')
        if op == 'jobs.request': return self.jobs.request(**a)
        if op == 'jobs.list': return self.jobs.list()
        if op in ('jobs.get','jobs.execute','jobs.cancel'): return getattr(self.jobs,op.split('.')[1])(a['id'])
        if op == 'calibration.plan':
            if self.count('sessions') >= MAX_SESSIONS:
                raise Requirement('storage_limit','Session limit reached',['Operator archive required'])
            return self.save('sessions',{'id':uuid.uuid4().hex,'name':a['name'],'metadata':{},
                'created_at':time.time(),'files':[], 'workflow_version':measurement.VERSION,
                'state':'planned','plan':measurement.plan(a),'sound_disabled':True})
        if op in ('calibration.get','calibration.analyze','calibration.propose','calibration.cancel'):
            s = self.read('sessions',a['id'])
            if s.get('workflow_version') != measurement.VERSION:
                raise Requirement('workflow_required','Use a versioned measurement plan',['Create calibration.plan; legacy sessions remain available'])
            if op == 'calibration.get': return self.session_view(s)
            if s['state'] == 'cancelled':
                raise Requirement('workflow_cancelled','Cancelled workflow is immutable',['Create another plan; original data retained'])
            if op == 'calibration.cancel': s['state'] = 'cancelled'
            if op == 'calibration.analyze':
                try: s['analysis'] = measurement.analyze(s)
                except ValueError:
                    raise Requirement('measurement_quality_required','Imports do not satisfy the plan/provenance requirements',
                        ['Import every named channel/position once with explicit source, matching sample rate/orientation, calibration hash/applied status and timing reference/quality; do not mix synthetic and measured data'])
                s.pop('proposal',None)
                s['state'] = 'analyzed'
            if op == 'calibration.propose':
                if s['state'] not in ('analyzed','proposed'):
                    raise Requirement('analysis_required','Analyze current imports before proposing',['Run calibration.analyze'])
                if s['analysis'].get('analysis_revision') != measurement.ANALYSIS_REVISION:
                    raise Requirement('analysis_required','Fresh position-preserving analysis required',['Run calibration.analyze again; previous fit-only electrical proposals are superseded'])
                if not s['analysis']['proposal_eligibility']['eligible']:
                    raise Requirement('clock_quality_required','Known uncompensated clock drift exceeds conservative proposal limit',
                        ['Acquire new qualified measurements with absolute known drift at most 100 ppm; compensation is not implemented; unknown remains explicitly provisional magnitude-only'])
                s['proposal'] = measurement.propose(s['analysis'],s['plan']['sample_rate'])
                s['state'] = 'proposed'
            s['sound_disabled'] = True
            return self.save('sessions',s)
        if op == 'capabilities':
            return {'operations':{k:{'required':v[0],'optional':v[1],'input_schema':OPERATION_SCHEMAS[k]} for k,v in SCHEMAS.items()},'attended_authority':False,'calibration':'versioned offline plans, magnitude analysis and cut-only proposals; no capture or acoustic verification','measurement':{'version':1,'sound_disabled':True,'capture_available':False,'prerequisites':measurement.PREREQUISITES},'playback':{'adapter':'official MA client','media_url_semantics':'Official MA play_media with QueueOption.REPLACE; this application never fetches supplied URLs','requirements':['Operator MA credential and allowlisted commissioned GARAGE_MA_PLAYER_ID','Independent trusted human attended authority, bounded duration and revocation (not implemented)'],'sound_actions_available':False}}
        if op == 'status':
            try:
                dsp = {'available':True,'readback':self.policy({'cmd':'status'})}
            except Requirement as e:
                dsp = {'available':False,'error':{'code':e.code,'message':e.message,'requirements':e.requirements},'mute':'unknown'}
            return {'dsp':dsp,'calibration':{'state':'unverified','sessions':self.count('sessions'),
                    'analysis_revision':measurement.ANALYSIS_REVISION,'derived_results_validity':'revision-gated read/list; no acoustic verification'},
                    'playback':{'configured':bool(os.environ.get('MA_TOKEN_FILE')),'verified':False},'attended_authority':False}
        if op == 'playback.play_url':
            u=urlsplit(a['url'])
            if u.scheme not in ('http','https') or not u.hostname or u.username or u.password or u.fragment or any(ord(c)<33 for c in a['url']):
                raise ValueError('invalid media URL')
        if op in ('dsp.unmute','commissioning.start','playback.play','playback.play_url','playback.next','playback.clear'):
            raise Requirement('attended_authority_required','Sound-producing action is locked',['Separate trusted human authorization, physical commissioning and bounded revocable authority are required; agent statements are not consent'])
        if op == 'calibration.guide':
            return {'steps':['Install and bind UMC/UMIK hardware','Human verifies wiring at low gains with separate attended authority','Select microphone serial/orientation calibration in REW','Measure named positions/channels; record sample rate and timing reference','Export SPL & Phase text; import originals here','Inspect measured curves separately from electrical profile preview','Use validated cut-only REW filters; before/after claims require new actual measurements'], 'sound_actions_available':False,'delays_inferred':False}
        if op == 'sessions.list': return self.records('sessions')
        if op == 'sessions.create':
            if self.count('sessions') >= MAX_SESSIONS:
                raise Requirement('storage_limit','Session limit reached',['Operator archive required; originals are never silently deleted'])
            if not isinstance(a['name'],str) or not 1 <= len(a['name']) <= 120: raise ValueError()
            return self.save('sessions',{'id':uuid.uuid4().hex,'name':a['name'],'metadata':calibration.metadata(a.get('metadata',{})), 'created_at':time.time(),'files':[]})
        if op == 'sessions.get': return self.session_view(self.read('sessions',a['id']))
        if op == 'measurements.import':
            s = self.read('sessions',a['session_id'])
            if s.get('state') == 'cancelled':
                raise Requirement('workflow_cancelled','Cancelled workflow is immutable',['Create another plan'])
            if len(s['files']) >= 32:
                raise Requirement('storage_limit','Session import limit reached',['Create another session; originals are never silently deleted'])
            if not isinstance(a['filename'],str) or not re.fullmatch(r'[\w .-]{1,120}',a['filename']) or a['filename'] in ('.','..'): raise ValueError()
            record = calibration.parse(a['text'],a['format'])
            record.update(id=uuid.uuid4().hex,filename=a['filename'],metadata=calibration.metadata(a.get('metadata',{})),imported_at=time.time(),original_text=a['text'])
            if record['metadata'].get('source') == 'synthetic': record['interpretation'] = 'synthetic response; not an acoustic measurement'
            elif record['metadata'].get('source') != 'measured' and a['format'] == 'rew-response': record['interpretation'] = 'imported response; source unverified'
            s['files'].append(record)
            if 'workflow_version' in s:
                s['state'] = 'imported'
                s.pop('analysis',None); s.pop('proposal',None)
            self.save('sessions',s)
            return record
        if op == 'measurements.compare':
            s = self.read('sessions',a['session_id'])
            files = {f['id']:f for f in s['files']}
            b, c = files[a['before_id']],files[a['after_id']]
            if b['format'] != 'rew-response' or c['format'] != 'rew-response': raise ValueError()
            source = 'synthetic' if any(f['metadata'].get('source') == 'synthetic' for f in (b,c)) else 'measured' if all(f['metadata'].get('source') == 'measured' for f in (b,c)) else 'unverified-source'
            return {'kind':source+'-comparison','before':b,'after':c,'comparable_metadata':b['metadata']==c['metadata'],'warning':'No normalization, inferred delay or acoustic correction; verify matching positions, channels and measurement setup'}
        if op == 'profiles.list': return [offline_candidate.view(p,self) for p in self.records('profiles')]
        if op == 'profiles.save':
            return self.save('profiles',{'id':uuid.uuid4().hex,'profile':validate(a['profile']),'created_at':time.time(),'verified_acoustically':False})
        if op.startswith('profiles.'):
            p = self.read('profiles',a['id'])
            if op == 'profiles.get': return offline_candidate.view(p,self)
            if op == 'profiles.edit':
                profile=copy.deepcopy(p['profile'])
                fields={'gain','mute','delay_ms','invert','peq'} & set(a)
                if fields and 'channel' not in a or not fields and 'channel' in a or not (set(a)-{'id','channel'}): raise ValueError()
                for key in fields: profile['channels'][a['channel']][key]=a[key]
                for key in ('crossover_hz','sub_trim','peq_bypass'):
                    if key in a: profile[key]=a[key]
                return self.save('profiles',{'id':uuid.uuid4().hex,'profile':validate(profile),'created_at':time.time(),'verified_acoustically':False,'source':{'profile_id':p['id'],'kind':'explicit-offline-edit'},'delay_inferred':False})
            if op == 'profiles.preview': return calibration.preview(p['profile'])
            if op == 'profiles.apply': return self.policy({'cmd':'profile','profile':p['profile']})
            if op == 'profiles.import_rew':
                if a['channel'] not in ('left','right','sub') or not isinstance(a['text'],str) or len(a['text'].encode())>calibration.MAX_TEXT: raise ValueError()
                profile = copy.deepcopy(p['profile']); profile['channels'][a['channel']]['peq']=rew.parse(a['text'])
                return self.save('profiles',{'id':uuid.uuid4().hex,'profile':validate(profile),'created_at':time.time(),'verified_acoustically':False,'source':{'profile_id':p['id'],'rew_text':a['text']}})
        if op.startswith('dsp.'):
            cmd = {'dsp.mute':'mute','dsp.volume':'volume','dsp.sub_trim':'sub-trim'}[op]
            if 'db' in a: number(a['db'],-90 if cmd=='volume' else -30,-20 if cmd=='volume' else 0)
            return self.policy({'cmd':cmd,**a})
        if op.startswith('playback.'):
            return asyncio.run(self.music(op,a))
        raise ValueError()

    async def music(self, op, a):
        credential = os.environ.get('MA_TOKEN_FILE')
        if not credential:
            raise Requirement('ma_credential_required','Music Assistant credential is not configured',['Complete MA onboarding and provide operator-owned private MA_TOKEN_FILE'])
        try:
            token = private_token(credential)
            from music_assistant_client import MusicAssistantClient
            # Server-owned configuration, never accepted from a tool/HTTP caller.
            endpoint = os.environ.get('MA_URL','http://127.0.0.1:8095')
            if endpoint not in ('http://127.0.0.1:8095','http://music-assistant:8095'):
                raise ValueError('unsupported operator MA endpoint')
            async with MusicAssistantClient(endpoint,None,token=token) as ma:
                await ma.players.fetch_state(); await ma.player_queues.fetch_state()
                if op == 'playback.status':
                    return [{'id':p.player_id,'name':p.name,'available':p.available} for p in ma.players.players]
                player = a['player_id']
                target = os.environ.get('GARAGE_MA_PLAYER_ID')
                if not target:
                    raise Requirement('ma_target_required','Commissioned garage target is not configured',['Operator must configure GARAGE_MA_PLAYER_ID; arbitrary MA targets are forbidden'])
                if not isinstance(player,str) or player != target or player not in {p.player_id for p in ma.players.players}: raise ValueError()
                if op == 'playback.queue':
                    items = await ma.player_queues.get_queue_items(player)
                    return [{'id':i.queue_item_id,'name':i.name} for i in items]
                await getattr(ma.player_queues,op.split('.')[1])(player)
                return {'submitted':True,'player_id':player,'operation':op,'audible_playback_claimed':False}
        except Requirement:
            raise
        except Exception:
            raise Requirement('ma_unavailable','Music Assistant operation could not be verified',['Check private credential and MA onboarding/connectivity; no simulated playback state'])
