"""Frozen offline REW comparison. No capture, apply, sound or authority."""
import hashlib
import json
import math
import time
import uuid
from .room_profiles import DomainFailure
from .measurement import interpolate

def obj(properties):
    return dict(type='object', properties=properties, required=list(properties), additionalProperties=False)

ID = dict(type='string', pattern='^[a-f0-9]{32}$')
TEXT = dict(type='string', minLength=1, maxLength=120)
def num(lo, hi): return dict(type='number', minimum=lo, maximum=hi)
def arr(item, maximum=8): return dict(type='array', items=item, minItems=1, maxItems=maximum, uniqueItems=True)
REF = obj({'session_id': ID, 'file_id': ID})
CHAIN = obj({'microphone_serial': TEXT, 'calibration_sha256':dict(type='string',pattern='^[a-f0-9]{64}$'),
             'sample_rate':dict(type='integer',enum=[44100,48000,88200,96000,176400,192000]),
             'orientation':dict(enum=['0','90']), 'setup_id':TEXT, 'hardware_gain_db':num(-120,120),
             'transport_normalization':dict(enum=['off']), 'timing_reference':TEXT})
QC = obj({'provenance':dict(enum=['operator-export-attestation','unknown']),
          'clipped':dict(enum=[True,False,'unknown']), 'noise_margin_db':num(-100,150),
          'level_chain_known':dict(type='boolean'), 'chain':CHAIN})
PAIR = obj({'position':TEXT,'channel':dict(enum=['left','right','sub','combined']),
            'baseline':REF,'guided':REF,'baseline_qc':QC,'guided_qc':QC})
SPEC = obj({'listening_profile_id':ID, 'baseline_profile_id':ID,'guided_profile_id':ID,
            'proposal_session_id':ID, 'fit_sources':arr(REF,24),
            'baseline_session_id':ID,'guided_session_id':ID,
            'channels':arr(dict(enum=['left','right','sub','combined']),4),
            'grid_hz':arr(num(20,20000),256), 'target_db':num(-150,150),
            'reference_offset_db':num(-150,150), 'chain':CHAIN,
            'minimum_improvement_db':num(0,20),'maximum_worsening_db':num(0,20),
            'maximum_level_change_db':num(0,20),'minimum_noise_margin_db':num(0,80),
            'capture_budget':dict(type='integer',minimum=1,maximum=128),
            'rollback_profile_id':ID})
SCHEMAS = {'commissioning_proof.freeze':(['spec'],[]),
           'commissioning_proof.analyze':(['id','pairs'],[]), 'commissioning_proof.get':(['id'],[]),
           'commissioning_proof.list':(['listening_profile_id'],[])}
OPERATION_SCHEMAS = {'commissioning_proof.freeze':obj({'spec':SPEC}),
                     'commissioning_proof.analyze':obj({'id':ID,'pairs':arr(PAIR,32)}),
                     'commissioning_proof.get':obj({'id':ID}),
                     'commissioning_proof.list':obj({'listening_profile_id':ID})}

def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()

def fail(code, message): raise DomainFailure(code, message)

def source(app, ref):
    session = app.read('sessions',ref['session_id'])
    files = [f for f in session['files'] if f['id'] == ref['file_id']]
    if len(files) != 1: fail('source_invalid','Missing unique imported source')
    f = files[0]
    if f['format'] != 'rew-response' or f['metadata'].get('source') != 'measured':
        fail('source_invalid','Explicit measured REW source required; fixtures are not capture proof')
    if session.get('state') == 'cancelled': fail('source_invalid','Cancelled source')
    return session, f

def references(app, spec):
    store = app.room_profiles
    plan = store._read('profiles',spec['listening_profile_id'])
    room = store._read('rooms',plan['room_id'])
    store._validate_plan(plan,room)
    if plan['archived'] or room['archived'] or not room.get('annotations_confirmed'):
        fail('source_invalid','Active confirmed room and listening plan required')
    result = {'listening_plan':plan,'room':room}
    for key in ('baseline_profile_id','guided_profile_id','rollback_profile_id'):
        result[key] = app.read('profiles',spec[key])
    from .offline_candidate import view as candidate_view
    if candidate_view(result['guided_profile_id'],app).get('candidate_stale'):
        fail('source_invalid','Guided candidate provenance is stale')
    proposal = app.read('sessions',spec['proposal_session_id'])
    if not app.session_view(proposal).get('proposal_available'):
        fail('source_invalid','Current fit-only proposal required')
    result['proposal'] = proposal
    result['fit_sources'] = [source(app,r)[1] for r in spec['fit_sources']]
    return result

def execute(op, args, app):
    if op.endswith('.list'):
        result=[]
        for record in app.records('commissioning-proofs'):
            if record['spec']['listening_profile_id']!=args['listening_profile_id']: continue
            v=view(record,app)
            result.append({k:v[k] for k in ('id','frozen_at','stale','report_available','protocol_sha256')} |
                          {'recommendation':v['report']['recommendation'] if v['report'] else None})
        return sorted(result,key=lambda r:r['frozen_at'],reverse=True)
    if op.endswith('.freeze'):
        spec = args['spec']
        refs = references(app,spec)
        fit = {p['position_id'] for p in refs['listening_plan']['fit_positions']}
        holds = refs['listening_plan']['verify_positions']
        if not holds or fit & set(holds): fail('source_invalid','Independent held-out positions required')
        positions={p['id']:p for p in refs['room']['annotations']}
        if any(positions[p].get('orientation')!=spec['chain']['orientation'] or 'mic_height_m' not in positions[p] for p in fit|set(holds)):
            fail('source_chain_mismatch','Map must freeze explicit microphone height and matching orientation')
        actual_fit = refs['fit_sources']
        if len({f['id'] for f in actual_fit}) != len(actual_fit): fail('source_invalid','Duplicate fit source')
        for f in actual_fit:
            m=f['metadata']
            if m.get('calibration_applied') is not True or any(m.get(k)!=spec['chain'][k] for k in ('microphone_serial','calibration_sha256','sample_rate','orientation','timing_reference')):
                fail('source_chain_mismatch','Fit provenance differs from frozen chain')
        if any(f['metadata'].get('position') not in fit for f in actual_fit):
            fail('source_invalid','Fit sources must belong only to fit positions')
        proposal = refs['proposal']
        if set(proposal['plan']['positions']) != fit or {f['id'] for f in proposal['files']} != {f['id'] for f in actual_fit}:
            fail('source_invalid','Proposal must use exactly frozen fit sources, not held-out inputs')
        grid = spec['grid_hz']
        if len(grid)<2 or grid != sorted(grid): fail('invalid_arguments','Increasing fixed grid required')
        if spec['baseline_session_id'] == spec['guided_session_id']:
            fail('source_invalid','Distinct condition sessions required')
        conditions = {}
        for key in ('baseline_session_id','guided_session_id'):
            s = app.read('sessions',spec[key])
            if s['files'] or s.get('workflow_version'): fail('protocol_order','Create empty legacy condition sessions before freeze; import only afterward')
            conditions[key] = {k:v for k,v in s.items() if k != 'files'}
        required = len(actual_fit)+2*len(holds)*len(spec['channels'])
        if required > spec['capture_budget']: fail('budget_exceeded','Frozen acquisition budget insufficient')
        if app.count('commissioning-proofs') >= 128: fail('storage_limit','Proof limit reached')
        record = {'id':uuid.uuid4().hex,'version':1,'frozen_at':time.time(), 'spec':spec,
                  'references':refs,'reference_sha256':digest(refs),'conditions':conditions,
                  'positions':holds,'required_capture_count':required,'report':None}
        record['protocol_sha256'] = digest(record)
        app.save('commissioning-proofs',record)
        return view(record,app)
    record = app.read('commissioning-proofs',args['id'])
    if op.endswith('.get'): return view(record,app)
    if stale(record,app): fail('proof_stale','Frozen references or imported inputs changed; retain receipt and freeze a new protocol')
    if record['report'] is not None:
        if digest(args['pairs']) != record['pairs_sha256']: fail('proof_immutable','Finalized receipt is immutable')
        return view(record,app)
    spec = record['spec']; pairs = args['pairs']
    expected = {(p,c) for p in record['positions'] for c in spec['channels']}
    if {(p['position'],p['channel']) for p in pairs} != expected or len(pairs)!=len(expected):
        fail('pairs_incomplete','Exactly one complete pair per frozen position/channel required')
    seen = {f['id'] for f in record['references']['fit_sources']}
    if any(len(app.read('sessions',spec[k])['files']) != len(expected) for k in ('baseline_session_id','guided_session_id')):
        fail('budget_exceeded','Condition sessions must contain exactly planned sources, without extra captures')
    used = []; metrics = []; issues = []
    for pair in pairs:
        curves = []
        for arm in ('baseline','guided'):
            ref = pair[arm]; qc = pair[arm+'_qc']
            if ref['session_id'] != spec[arm+'_session_id']: fail('source_invalid','Wrong condition session')
            s,f = source(app,ref); m = f['metadata']
            if f['id'] in seen: fail('source_invalid','Reused fit or pair source')
            seen.add(f['id']); used.append(ref)
            if f['imported_at'] <= record['frozen_at']: fail('protocol_order','Holdout import predates freeze')
            if m.get('position') != pair['position'] or m.get('channel') != pair['channel']:
                fail('source_invalid','Source position/channel mismatch')
            if qc['chain'] != spec['chain']: fail('source_chain_mismatch','Hardware/setup/gain chain differs from frozen protocol')
            for key in ('microphone_serial','calibration_sha256','sample_rate','orientation','timing_reference'):
                if m.get(key) != spec['chain'][key]: fail('source_chain_mismatch','Imported calibration/rate/mic/orientation/timing mismatch')
            if m.get('calibration_applied') is not True: fail('source_invalid','Applied calibration required')
            if m.get('timing_quality') not in ('unknown','unverified','verified'): fail('source_invalid','Timing provenance required')
            if qc['provenance']=='unknown' or not qc['level_chain_known'] or qc['clipped'] is not False or qc['noise_margin_db']<spec['minimum_noise_margin_db']:
                issues.append(pair['position']+'/'+pair['channel']+'/'+arm+': insufficient QC')
            points=f['points']; grid=spec['grid_hz']
            if points[0]['frequency_hz']>grid[0] or points[-1]['frequency_hz']<grid[-1]: fail('band_missing','Full frozen band coverage required; no endpoint extrapolation')
            curves.append([interpolate(points,hz)+spec['reference_offset_db'] for hz in grid])
        b,g=curves; target=spec['target_db']
        rms=lambda xs:math.sqrt(sum(x*x for x in xs)/len(xs))
        br=rms([x-target for x in b]); gr=rms([x-target for x in g])
        change=sum(y-x for x,y in zip(b,g))/len(b)
        metrics.append({'position':pair['position'],'channel':pair['channel'],
                        'baseline_rms_db':br,'guided_rms_db':gr,'improvement_db':br-gr,
                        'baseline_peak_error_db':max(abs(x-target) for x in b),
                        'guided_peak_error_db':max(abs(x-target) for x in g),
                        'mean_level_change_db':change,'attenuation_db':-change,
                        'baseline_variation_db':max(b)-min(b),'guided_variation_db':max(g)-min(g),
                        'maximum_bin_worsening_db':max(abs(y-target)-abs(x-target) for x,y in zip(b,g)),
                        'baseline_db':b,'guided_db':g})
    complete_system = 'combined' in spec['channels']
    primary = [m for m in metrics if m['channel']=='combined'] if complete_system else metrics
    improvement=sum(m['improvement_db'] for m in primary)/len(primary)
    # Uniform attenuation never earns an unqualified keep: absolute level guard is separate.
    worse=any(m['guided_rms_db']-m['baseline_rms_db']>spec['maximum_worsening_db'] or
              m['maximum_bin_worsening_db']>spec['maximum_worsening_db'] or
              abs(m['mean_level_change_db'])>spec['maximum_level_change_db'] for m in metrics)
    recommendation='inconclusive' if issues or not complete_system else ('reject' if worse or improvement<spec['minimum_improvement_db'] else 'keep')
    record.update(pairs=pairs,pairs_sha256=digest(pairs),input_sha256={ref['session_id']:digest(app.read('sessions',ref['session_id'])) for ref in used},
                  report={'kind':'imported-measured-comparison','provenance':'self-reported imports and QC, not independent capture proof',
                          'recommendation':recommendation,'aggregate_improvement_db':improvement,
                          'combined_system_imports_complete':complete_system,'qc_issues':issues,
                          'metrics':metrics,'threshold_units':'dB; prospectively selected engineering criteria, not JND/confidence intervals',
                          'rollback_reference':spec['rollback_profile_id'],'rollback_performed':False,
                          'applied':False,'verified_acoustically':False,'sound_disabled':True})
    record['receipt_sha256']=digest(record)
    app.save('commissioning-proofs',record)
    return view(record,app)

def stale(record,app):
    try:
        if digest(references(app,record['spec'])) != record['reference_sha256']: return True
        for key,snapshot in record['conditions'].items():
            s=app.read('sessions',record['spec'][key])
            if {k:v for k,v in s.items() if k!='files'} != snapshot: return True
        return any(digest(app.read('sessions',sid)) != sha for sid,sha in record.get('input_sha256',{}).items())
    except Exception:
        # Unreadable/deleted references are never grounds to expose an old pass.
        return True

def view(record,app):
    invalid=stale(record,app)
    return {'id':record['id'],'protocol_sha256':record['protocol_sha256'],
            'spec':record['spec'],'positions':record['positions'],'frozen_at':record['frozen_at'],
            'required_capture_count':record['required_capture_count'],
            'stale':invalid,'report_available':record['report'] is not None and not invalid,
            'report':record['report'] if not invalid else None,
            'receipt_sha256':record.get('receipt_sha256'),'applied':False,
            'verified_acoustically':False,'sound_disabled':True,
            'warning':'No physical capture, human attendance, applied correction or acoustic improvement independently verified'}
