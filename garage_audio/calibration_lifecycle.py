"""Qualified acquisition -> magnitude proposal -> next-Play transaction.

No caller consent, hardware privilege escalation, or inferred acoustic verification.
"""
import copy
import hashlib
import json
import time
import uuid
from . import audio_public, calibration, measurement, measurement_artifacts as artifacts
from .measurement_jobs import JobFailure, canonical, digest
from . import measurement_verification
from . import runtime_readback

SCHEMAS = {'calibration.capture.import': (['job_id'], []),
           **{f'calibration.correction.{op}': (['id'], []) for op in ('prepare','get','apply','rollback','verify')}}
OPERATION_SCHEMAS = {op: {'type':'object','additionalProperties':False,'required':fields[0],
    'properties': {key:{'type':'string','pattern':'^[a-f0-9]{32}$'} for key in fields[0]}}
    for op,fields in SCHEMAS.items()}
SCHEMAS['calibration.correction.verify'] = (['id'],['after_id'])
OPERATION_SCHEMAS['calibration.correction.verify']['properties']['after_id'] = {'type':'string','pattern':'^[a-f0-9]{32}$'}


def fail(code, message): raise JobFailure(code,message)


def import_capture(app, identifier):
    job = app.jobs._read(identifier); manifest = job['manifest']; evidence = job.get('evidence',{})
    if (job['state'] != 'completed' or job['adapter'] != 'hardware' or not job.get('owned_child_exit_verified')
            or job.get('child_exit_status') != 0 or not job.get('descendant_reaping',{}).get('owned_group_children_reaped')
            or evidence.get('source') != 'measured' or evidence.get('supported_magnitude') is not True
            or evidence.get('calibration_applied') is not True or not evidence.get('checks')
            or not all(value is True for value in evidence['checks'].values())):
        fail('qualified_capture_required','Completed measured hardware acquisition with passing magnitude QC/reaping required; synthetic fixtures cannot be promoted')
    directory = app.jobs.directory(identifier)
    if (evidence.get('approved_manifest_sha256') != digest(manifest) or digest(manifest) != job['manifest_sha256']
            or hashlib.sha256(artifacts.read(directory,'manifest.json',262144)).hexdigest() != job['manifest_file_sha256']):
        fail('capture_changed','Approved capture manifest changed')
    retained = json.loads(artifacts.read(directory,'evidence.json',262144))
    # Monitor adds receipt hashes; scientific evidence must still match originals.
    if any(evidence.get(key) != value for key,value in retained.items()): fail('capture_changed','Retained capture evidence differs')
    if hashlib.sha256(artifacts.read(directory,'microphone.raw',2*1024*1024)).hexdigest() != evidence['capture_sha256']:
        fail('capture_changed','Retained microphone bytes changed')
    rendered = evidence.get('correction_render',{})
    if (manifest.get('version') != 4 or rendered.get('binding') != manifest['correction_render']['binding']
            or rendered.get('graph_sha256') != manifest['correction_render']['graph_sha256']
            or rendered.get('native_exit_status') != 0 or rendered.get('unused_outputs_zero') is not True
            or hashlib.sha256(artifacts.read(directory,'output.raw',4*1024*1024)).hexdigest() != rendered.get('emitted_sha256')):
        fail('capture_changed','Exact correction render/emitted waveform evidence required')
    session = app.read('sessions',job['session_id']); plan = session['plan']
    for record in session['files']:
        if record.get('capture_provenance',{}).get('job_id') == identifier: return record
    mic = manifest['hardware_admission']['policy']['microphone']
    if (session['state'] == 'cancelled' or manifest['channel'] not in plan['channels'] or manifest['position'] not in plan['positions']
            or manifest['signal']['sample_rate'] != plan['sample_rate'] or mic['orientation'] != plan['orientation']):
        fail('capture_plan_changed','Capture no longer matches active plan')
    if len(session['files']) >= 32: fail('storage_limit','Session response limit reached; originals retained')
    text = '\n'.join(f"{point['frequency_hz']:.12g} {point['db']:.12g}" for point in evidence['points'])+'\n'
    record = calibration.parse(text,'rew-response')
    record.update(id=uuid.uuid4().hex,filename='capture-'+identifier+'.txt',original_text=text,imported_at=time.time(),
        metadata={'source':'measured','position':manifest['position'],'channel':manifest['channel'],
            'sample_rate':plan['sample_rate'],'orientation':mic['orientation'],'microphone_serial':mic['serial'],
            'calibration_sha256':evidence['calibration_sha256'],'calibration_applied':True,
            'timing_reference':'acquired excitation; estimated latency; independent USB clocks',
            'timing_quality':'unverified'},
        capture_provenance={'job_id':identifier,'manifest_sha256':job['manifest_sha256'],
            'capture_sha256':evidence['capture_sha256'],'raw_artifact':'microphone.raw',
            'latency_samples_estimate':evidence['latency_samples_estimate'],'clock_drift_verified':False,
            'hardware_sha256':evidence['approved_hardware_sha256'],'correction_render':rendered})
    session['files'].append(record); session['state'] = 'imported'
    session.pop('analysis',None); session.pop('proposal',None)
    app.save('sessions',session)
    return record


def inputs(session):
    return digest({key:session.get(key) for key in ('plan','files','analysis','proposal')})


def execute(operation, args, app):
    if operation == 'calibration.capture.import': return import_capture(app,args['job_id'])
    session = app.read('sessions',args['id']); transaction = session.get('correction_transaction')
    if operation == 'calibration.correction.get':
        return {'transaction':transaction,'inputs_current':bool(transaction and transaction['inputs_sha256']==inputs(session)),
                'runtime':runtime_readback.configuration(),
                'paired_magnitude_verification_available':True,
                'acoustic_improvement_verified':bool(transaction and transaction.get('verification',{}).get('acoustic_improvement_verified')),
                'remaining_requirement':'Matching qualified before/after acquisitions bound to exact baseline/correction revisions'}
    if session.get('state') == 'cancelled' and operation != 'calibration.correction.rollback':
        fail('workflow_cancelled','Cancelled workflow cannot change correction; owned rollback remains available')
    if operation == 'calibration.correction.prepare':
        view = app.session_view(session); proposal = view.get('proposal')
        if not proposal or proposal.get('analysis_revision') != measurement.ANALYSIS_REVISION:
            fail('analysis_required','Current analyzed correction proposal required')
        if transaction and transaction['state'] in ('apply_requested','accepted_for_next_play','rollback_requested'):
            fail('transaction_active','Reconcile or rollback the current correction before replacing its journal')
        runtime = audio_public.request('audio.config.get'); candidate = copy.deepcopy(runtime['configuration'])
        if any(candidate.get('correction',{}).values()):
            fail('uncorrected_baseline_required','Existing correction must be explicitly rolled back before fitting an unbound new baseline')
        candidate['correction'] = {channel:copy.deepcopy(proposal['channels'].get(channel,{}).get('peq',[])) for channel in ('left','right','sub')}
        if not any(candidate['correction'].values()): fail('no_useful_cut','No useful conservative cut; leave configuration unchanged')
        if runtime['binding']['kind'] != 'file':
            fail('hardware_authority_required','Hardware correction requires independently commissioned binding and exact-proposal authority; no FILE fallback')
        exact = audio_public.request('audio.config.propose',{'configuration':candidate})
        transaction = {'state':'prepared','inputs_sha256':inputs(session),'source':proposal['source'],
            'proposal_id':exact['proposal_id'],'base_revision':runtime['revision'],'configuration':candidate,
            'previous_configuration':runtime['configuration'],'acoustic_improvement_verified':False}
        session['correction_transaction'] = transaction; app.save('sessions',session)
        return transaction
    if not transaction or (operation != 'calibration.correction.rollback' and transaction['inputs_sha256'] != inputs(session)):
        fail('correction_stale','Correction inputs changed; old transaction is not current')
    runtime = audio_public.request('audio.config.get')
    if operation == 'calibration.correction.verify':
        if not args.get('after_id'): fail('paired_capture_required','Select a separate matching post-correction acquisition session')
        after = app.read('sessions',args['after_id'])
        runtime = {**runtime,'generation':audio_public.request('audio.status')['generation']}
        if after['id'] == session['id']: fail('paired_capture_required','Independent before/after sessions required')
        for captured in (session,after):
            for record in captured['files']:
                if record['format']=='rew-response' and record['metadata'].get('source')=='measured':
                    job_id = record.get('capture_provenance',{}).get('job_id')
                    if not job_id or import_capture(app,job_id)['id'] != record['id']:
                        fail('qualified_capture_required','Measured verification requires retained qualified raw acquisition, not self-reported imports')
        try: report = measurement_verification.compare(session,after,transaction,runtime)
        except ValueError as error: fail('paired_capture_required',str(error))
        transaction['verification'] = report; app.save('sessions',session)
        return report
    if operation == 'calibration.correction.apply':
        if transaction['state'] != 'prepared' or runtime['revision'] != transaction['base_revision']:
            fail('correction_stale','Apply requires current prepared proposal/base revision; reconcile uncertain prior attempts')
        exact = {'proposal_id':transaction['proposal_id']}
        audio_public.request('audio.config.validate',exact)
        transaction['state'] = 'apply_requested'; app.save('sessions',session)  # Write-ahead; never blindly replay after a crash.
        result = audio_public.request('audio.config.apply',exact)
        transaction.update(state='accepted_for_next_play',accepted_revision=result['revision'])
    elif operation == 'calibration.correction.rollback':
        # Also recover an interrupted post-commit journal write, but never stomp a
        # different revision/configuration accepted by another operator.
        expected_revision = transaction.get('accepted_revision',transaction['base_revision']+1)
        if runtime['revision'] != expected_revision or runtime['configuration'] != transaction['configuration']:
            fail('correction_stale','Runtime differs from this correction; reconcile before rollback')
        exact = audio_public.request('audio.config.propose',{'configuration':transaction['previous_configuration']})
        arguments = {'proposal_id':exact['proposal_id']}; audio_public.request('audio.config.validate',arguments)
        transaction['state'] = 'rollback_requested'; app.save('sessions',session)
        result = audio_public.request('audio.config.rollback',arguments)
        transaction.update(state='rolled_back',rollback_revision=result['revision'])
    else: raise ValueError('unknown correction lifecycle operation')
    app.save('sessions',session)
    return {'transaction':transaction,'runtime':result,'acoustic_improvement_verified':False}
