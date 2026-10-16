"""Matched acquired before/after magnitude checks; fixtures never acoustic proof."""
import math
import statistics
from .measurement import interpolate


def compare(before, after, transaction, runtime):
    if transaction['state'] != 'accepted_for_next_play' or runtime['revision'] != transaction['accepted_revision'] or runtime['configuration'] != transaction['configuration']:
        raise ValueError('exact correction is no longer accepted')
    if before['plan'] != after['plan']: raise ValueError('paired acquisition plans differ')
    def slots(session):
        values = {}
        for record in session['files']:
            if record['format'] != 'rew-response': continue
            key = (record['metadata'].get('channel'),record['metadata'].get('position'))
            if key in values: raise ValueError('ambiguous paired response slot')
            values[key] = record
        expected = {(c,p) for c in before['plan']['channels'] for p in before['plan']['positions']}
        if set(values) != expected: raise ValueError('complete exact paired slots required')
        return values
    originals, repeated = slots(before), slots(after); results = []; sources = set()
    for slot,b in originals.items():
        a = repeated[slot]; bm,am = b['metadata'],a['metadata']; sources.update((bm['source'],am['source']))
        for key in ('source','sample_rate','orientation','microphone_serial','calibration_sha256','calibration_applied'):
            if bm.get(key) != am.get(key) or bm.get(key) in (None,False,''): raise ValueError('paired microphone/calibration/format mismatch')
        bp,ap = b.get('capture_provenance',{}),a.get('capture_provenance',{})
        br,ar = bp.get('correction_render',{}),ap.get('correction_render',{})
        bb,ab = br.get('binding',{}),ar.get('binding',{})
        if (bb.get('configuration') != transaction['previous_configuration'] or bb.get('revision') != transaction['base_revision']
                or ab.get('configuration') != transaction['configuration'] or ab.get('revision') != transaction['accepted_revision']
                or bb.get('binding') != ab.get('binding') or bb.get('generation') != ab.get('generation')
                or br.get('native_exit_status') != 0 or ar.get('native_exit_status') != 0
                or br.get('unused_outputs_zero') is not True or ar.get('unused_outputs_zero') is not True
                or not br.get('emitted_sha256') or not ar.get('emitted_sha256')):
            raise ValueError('paired response is not bound to baseline and exact accepted rendered correction')
        if (ab.get('generation') != runtime.get('generation') or bb.get('compiler_revision') != ab.get('compiler_revision')
                or not br.get('input_sha256') or br.get('input_sha256') != ar.get('input_sha256')
                or br.get('binary_sha256') != ar.get('binary_sha256') or not br.get('binary_sha256')
                or not bp.get('hardware_sha256') or bp.get('hardware_sha256') != ap.get('hardware_sha256')):
            raise ValueError('paired generation, excitation, renderer or hardware/level binding changed')
        low = max(20,b['points'][0]['frequency_hz'],a['points'][0]['frequency_hz'])
        high = min(300 if slot[0]=='sub' else 1000,b['points'][-1]['frequency_hz'],a['points'][-1]['frequency_hz'])
        if high/low < 2: raise ValueError('insufficient paired comparison band')
        frequencies = [low*(high/low)**(i/120) for i in range(121)]
        series = [[interpolate(record['points'],hz) for hz in frequencies] for record in (b,a)]
        offsets = [statistics.median(values) for values in series]
        rms = [math.sqrt(statistics.mean((value-offset)**2 for value in values)) for values,offset in zip(series,offsets)]
        results.append({'channel':slot[0],'position':slot[1],'before_file_id':b['id'],'after_file_id':a['id'],
            'before_rms_db':rms[0],'after_rms_db':rms[1],'improvement_db':rms[0]-rms[1],
            'relative_level_change_db':offsets[1]-offsets[0],'nonworsening':rms[1] <= rms[0]+.1})
    if len(sources) != 1 or not sources <= {'measured','synthetic'}: raise ValueError('paired source types must match')
    source = next(iter(sources))
    improved = all(row['nonworsening'] for row in results) and statistics.mean(row['improvement_db'] for row in results) >= .25
    return {'kind':source+'-paired-magnitude-verification','source':source,'slots':results,
        'minimum_mean_improvement_db':.25,'per_slot_nonworsening_tolerance_db':.1,'improved':improved,
        'acoustic_improvement_verified':source=='measured' and improved,
        'verification_scope':'relative magnitude flatness at acquired positions only; no phase/timing/absolute SPL',
        'next_action':'Keep exact correction and retain captures' if improved else 'Rollback or review; no improvement verified',
        'warnings':['Mic positions and stationary room conditions remain operator-reported',
                    'Per-condition median normalization measures shape, not absolute level or calibrated SPL',
                    'Synthetic comparisons never establish acoustic improvement']}
