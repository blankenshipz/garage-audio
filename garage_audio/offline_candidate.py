"""Typed cut-only proposal → saved candidate. Never apply or infer alignment."""
import copy
import time
import uuid
from .profile import validate
from .commissioning_proof import digest, obj, ID, TEXT, fail

SCHEMAS={'candidates.save':(['baseline_id','proposal_session_id','name'],[])}
OPERATION_SCHEMAS={'candidates.save':obj({'baseline_id':ID,'proposal_session_id':ID,'name':TEXT})}

def provenance(app, baseline_id, session_id):
    baseline=app.read('profiles',baseline_id)
    session=app.read('sessions',session_id)
    view=app.session_view(session)
    if not view.get('proposal_available') or session.get('state')=='cancelled':
        fail('candidate_source_invalid','Analyze and propose current fitting imports first')
    proposal=session['proposal']
    if proposal.get('kind')!='offline-cut-only-peq-proposal' or proposal.get('reserved_headroom_db')!=6:
        fail('candidate_source_invalid','Unsupported proposal/headroom semantics')
    return baseline,session

def execute(args,app):
    baseline,session=provenance(app,args['baseline_id'],args['proposal_session_id'])
    if baseline.get('candidate_provenance'):
        fail('candidate_source_invalid','Choose a fixed baseline, not another derived candidate')
    profile=copy.deepcopy(baseline['profile']); profile['name']=args['name']
    if profile['peq_bypass']: fail('candidate_source_invalid','Baseline PEQ bypass must be off to preserve proposal semantics')
    for channel,data in session['proposal']['channels'].items():
        if channel not in profile['channels'] or data.get('validation_passed') is not True:
            fail('candidate_source_invalid','Validated supported-channel proposal required')
        filters=data['peq']
        if any(eq['gain']>0 or eq['gain'] < -6 for eq in filters): fail('candidate_source_invalid','Cut-only bounded filters required')
        profile['channels'][channel]['peq'] += copy.deepcopy(filters)
    validate(profile)  # Includes total existing+new ten-filter cap; no silently dropped filters.
    # The existing compiler already reserves -6dB headroom. Do not subtract it twice.
    record={'id':uuid.uuid4().hex,'created_at':time.time(),'profile':profile,'verified_acoustically':False,
            'candidate_provenance':{'baseline_id':baseline['id'],'proposal_session_id':session['id'],
                                    'baseline_sha256':digest(baseline),'proposal_session_sha256':digest(session),
                                    'source':session['proposal']['source'],'reserved_headroom_db':6,
                                    'headroom_policy':'existing compiler -6dB, not additional channel attenuation',
                                    'applied':False}}
    app.save('profiles',record)
    return view(record,app)

def view(record,app):
    result=copy.deepcopy(record); p=record.get('candidate_provenance')
    if p:
        try:
            b,s=provenance(app,p['baseline_id'],p['proposal_session_id'])
            stale=digest(b)!=p['baseline_sha256'] or digest(s)!=p['proposal_session_sha256']
        except Exception: stale=True
        result.update(candidate_stale=stale,applied=False,verified_acoustically=False)
    return result
