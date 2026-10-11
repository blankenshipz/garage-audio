"""Versioned offline magnitude workflow. There is deliberately no audio executor."""
import bisect
import cmath
import math
import statistics

VERSION = 1
ANALYSIS_REVISION = 2
MAX_KNOWN_DRIFT_PPM = 100  # Conservative policy, not a validated synchronization claim.
PREREQUISITES = ['Calibrated server-attached UMIK serial/orientation verified',
                 'Bound UMC outputs/wiring independently commissioned',
                 'Independent attended human sound authorization (not implemented)',
                 'Capture/sweep executor not implemented; sound disabled']

def plan(args):
    positions = args.get('positions', ['seat'])
    channels = args.get('channels', ['left', 'right', 'sub'])
    rate = args.get('sample_rate', 48000)
    target = args.get('target', 'server-umik-umc')
    prerequisites = list(PREREQUISITES) if target == 'server-umik-umc' else [
        'Calibrated laptop REW microphone and orientation provenance',
        'Import named channel/position REW text with timing/sample-rate metadata',
        'Independent attended authorization required for any external measurement sound',
        'Application only imports text; no capture/sweep executor; sound disabled']
    # Specification only: conservative digital level is NOT calibrated acoustic SPL.
    return {'version': VERSION, 'target': target,
            'positions': positions, 'channels': channels, 'sample_rate': rate,
            'orientation': args.get('orientation', '90'),
            'sweep': {'kind': 'logarithmic-specification-only', 'start_hz': 20,
                      'end_hz': min(20000, rate * .4), 'level_dbfs': -40,
                      'duration_seconds': 5, 'pre_silence_seconds': 1,
                      'post_silence_seconds': 2, 'fade_seconds': .1,
                      'auto_stop_seconds': 8, 'repetitions': 1},
            'maximum_total_seconds': 8 * len(positions) * len(channels),
            'prerequisites': prerequisites, 'hardware_verified': False,
            'sound_disabled': True, 'capture_available': False}

def interpolate(points, frequency):
    frequencies = [p['frequency_hz'] for p in points]
    i = bisect.bisect_left(frequencies, frequency)
    if i == 0: return points[0]['db']
    if i == len(points): return points[-1]['db']
    a, b = points[i-1], points[i]
    t = math.log(frequency/a['frequency_hz']) / math.log(b['frequency_hz']/a['frequency_hz'])
    return a['db'] + t * (b['db']-a['db'])

def analyze(session):
    specification = session['plan']
    responses = [f for f in session['files'] if f['format'] == 'rew-response']
    if not responses: raise ValueError('no response imports')
    required = {'source','channel','position','sample_rate','orientation',
                'calibration_sha256','calibration_applied','timing_reference','timing_quality'}
    indexed = {}
    sources = set()
    for f in responses:
        m = f['metadata']
        if required - set(m): raise ValueError('missing provenance metadata')
        if (m['sample_rate'] != specification['sample_rate'] or
                m['orientation'] != specification['orientation'] or
                m['channel'] not in specification['channels'] or
                m['position'] not in specification['positions'] or
                not m['calibration_applied'] or not m['timing_reference'].strip()):
            raise ValueError('incompatible metadata or unapplied calibration')
        key = (m['channel'], m['position'])
        if key in indexed: raise ValueError('ambiguous duplicate channel/position; use another session')
        indexed[key] = f
        sources.add(m['source'])
    if len(sources) != 1: raise ValueError('measured and synthetic responses cannot be pooled')
    if set(indexed) != {(c,p) for c in specification['channels'] for p in specification['positions']}:
        raise ValueError('missing planned channel/position')
    curves = {}
    drift = {f['id']:f['metadata'].get('clock_drift_ppm') for f in responses}
    high_drift = [identifier for identifier,value in drift.items()
                  if value is not None and abs(value) > MAX_KNOWN_DRIFT_PPM]
    for channel in specification['channels']:
        files = [indexed[channel,p] for p in specification['positions']]
        # Calibration serial/hash must be stable across positions of a channel.
        if len({(f['metadata']['calibration_sha256'], f['metadata'].get('microphone_serial')) for f in files}) != 1:
            raise ValueError('incompatible microphone calibration provenance')
        lo = max(20, max(f['points'][0]['frequency_hz'] for f in files))
        hi = min(300 if channel == 'sub' else 1000, min(f['points'][-1]['frequency_hz'] for f in files))
        if hi / lo < 2: raise ValueError('insufficient common low-frequency bandwidth')
        grid = [lo * (hi/lo)**(i/120) for i in range(121)]
        series = [[interpolate(f['points'], hz) for hz in grid] for f in files]
        offsets = [statistics.median(values) for values in series]
        points = []
        for i,hz in enumerate(grid):
            values = [s[i]-offset for s,offset in zip(series,offsets)]
            points.append({'frequency_hz':hz, 'db':statistics.median(values),
                           'minimum_db':min(values), 'maximum_db':max(values),
                           'spread_db':max(values)-min(values)})
        curves[channel] = {'points':points, 'normalization_offsets_db':offsets,
                            'file_ids':[f['id'] for f in files], 'positions':specification['positions'],
                            'position_curves':[{'file_id':f['id'],'position':f['metadata']['position'],
                                'points':[{'frequency_hz':hz,'db':value-offset} for hz,value in zip(grid,values)],
                                'import_points':[{'frequency_hz':p['frequency_hz'],'db':p['db']-offset}
                                                 for p in f['points'] if lo <= p['frequency_hz'] <= hi] +
                                                [{'frequency_hz':hz,'db':interpolate(f['points'],hz)-offset} for hz in (lo,hi)]}
                                for f,values,offset in zip(files,series,offsets)]}
    quality_warnings = []
    if any(value is None for value in drift.values()):
        quality_warnings.append('Clock drift unknown for some imports; magnitude-only proposals are provisional and clock quality is not verified')
    if high_drift:
        quality_warnings.append('Known uncompensated clock drift exceeds conservative 100 ppm policy; proposal blocked; acquire new qualified measurements')
    if any(value is not None for value in drift.values()):
        quality_warnings.append('Known drift values are supplied provenance only; no compensation or independent timing verification is implemented')
    return {'version': VERSION, 'analysis_revision':ANALYSIS_REVISION, 'kind': 'synthetic-magnitude-analysis' if 'synthetic' in sources else 'measured-magnitude-analysis',
            'source': next(iter(sources)), 'channels':curves,
            'method':'log-frequency interpolation; per-position median normalization; spatial median/spread',
            'timing':{'supported':False, 'delay_inferred':False, 'phase_inferred':False,
                      'clock_drift_verified':False, 'crossover_inferred':False,
                      'clock_drift_ppm_by_file':drift,
                      'quality_by_file':{f['id']:f['metadata']['timing_quality'] for f in responses}},
            'proposal_eligibility':{'eligible':not high_drift,'maximum_known_absolute_drift_ppm':MAX_KNOWN_DRIFT_PPM,
                                    'high_drift_file_ids':high_drift,'compensation_supported':False},
            'warnings':['Magnitude-only imports cannot establish delay, phase, crossover or clock synchrony',
                        'Calibration application is supplied provenance, not independently verified',
                        'Normalized curves are not calibrated SPL; original response offsets retained'] + quality_warnings,
            'input_sha256':[f['sha256'] for f in responses], 'sound_disabled':True}

def peq_db(filters, frequency, rate):
    z = cmath.exp(-2j*math.pi*frequency/rate)
    h = 1
    for eq in filters:
        w = 2*math.pi*eq['freq']/rate
        a = 10**(eq['gain']/40)
        alpha = math.sin(w)/(2*eq['q'])
        c = math.cos(w)
        h *= ((1+alpha*a)-2*c*z+(1-alpha*a)*z*z)/((1+alpha/a)-2*c*z+(1-alpha/a)*z*z)
    return 20*math.log10(max(abs(h), 1e-15))

def rms(points, filters, rate):
    return math.sqrt(statistics.mean((p['db'] + peq_db(filters,p['frequency_hz'],rate))**2 for p in points))

def fit_metrics(curve, filters, rate):
    # Check both uniform log grid and every native imported frequency: resampling
    # alone must not hide adverse narrow features at an individual position.
    return {'aggregate_rms_db':rms(curve['points'],filters,rate),
            'positions':[{'file_id':p['file_id'],'position':p['position'],
                          'grid_rms_db':rms(p['points'],filters,rate),
                          'import_rms_db':rms(p['import_points'],filters,rate)} for p in curve['position_curves']]}

def fit_nonworsening(before, after):
    return (after['aggregate_rms_db'] < before['aggregate_rms_db']-1e-6 and
            all(a[key] <= b[key]+1e-9 for b,a in zip(before['positions'],after['positions'])
                for key in ('grid_rms_db','import_rms_db')))

def peak_q(points, index):
    peak = points[index]
    level = max(2,peak['minimum_db']/2)
    left = right = index
    while left > 0 and points[left-1]['minimum_db'] >= level: left -= 1
    while right < len(points)-1 and points[right+1]['minimum_db'] >= level: right += 1
    # Demand three or more supporting points; one-bin spikes are not useful
    # robust peaks. No guessing a narrow filter from insufficient resolution.
    if right-left < 2 or left == 0 or right == len(points)-1: return None
    width = points[right]['frequency_hz']-points[left]['frequency_hz']
    q = peak['frequency_hz']/width
    return max(.5,q) if q <= 10 else None

def propose(analysis, rate):
    if analysis.get('analysis_revision') != ANALYSIS_REVISION:
        raise ValueError('fresh position-preserving analysis required')
    if not analysis['proposal_eligibility']['eligible']:
        raise ValueError('known high uncompensated clock drift')
    channels = {}
    for channel,curve in analysis['channels'].items():
        points = curve['points']
        candidates = [(i,p) for i,p in enumerate(points[1:-1],1)
                      if p['db'] >= points[i-1]['db'] and p['db'] > points[i+1]['db']
                      and p['minimum_db'] > 2 and p['spread_db'] <= 6]
        filters = []
        baseline = fit_metrics(curve,[],rate)
        current_fit = baseline
        skipped = 0
        for index,peak in sorted(candidates,key=lambda item:-item[1]['minimum_db']):
            freq = peak['frequency_hz']
            if any(abs(math.log2(freq/e['freq'])) < .5 for e in filters): continue
            q = peak_q(points,index)
            if q is None:
                skipped += 1
                continue
            remaining = peak['minimum_db'] + peq_db(filters, freq, rate)
            if remaining < 2: continue
            accepted = False
            for scale in (1,.5,.25,.125):
                trial = filters + [{'freq':round(freq,3),'q':round(q,3),
                                    'gain':round(-min(6,remaining*.65)*scale,3)}]
                predicted_fit = fit_metrics(curve,trial,rate)
                if fit_nonworsening(current_fit,predicted_fit):
                    filters = trial
                    current_fit = predicted_fit
                    accepted = True
                    break
            if not accepted: skipped += 1
            if len(filters) == 4: break
        electrical = [{'frequency_hz':20*(1000**(i/480)),
                       'db':peq_db(filters,20*(1000**(i/480)),rate)-6} for i in range(481)]
        maximum = max(p['db'] for p in electrical)
        if maximum > -5.99 or any(e['gain'] > 0 or e['gain'] < -6 for e in filters):
            raise ValueError('electrical headroom validation failed')
        channels[channel] = {'peq':filters, 'electrical_points':electrical,
                             'maximum_electrical_db':maximum, 'validation_passed':True,
                             'target_fit':{'target_db':0,'before':baseline,'after':current_fit,
                                           'nonworsening_each_position':True,'aggregate_improved':bool(filters)},
                             'useful_cut_available':bool(filters),'skipped_candidates':skipped,
                             'selection_message':'Predicted target fit improved without worsening any imported position' if filters else 'No useful conservative cut available; leave unchanged',
                             'predicted_normalized_points':[{'frequency_hz':p['frequency_hz'],
                                 'db':p['db']+peq_db(filters,p['frequency_hz'],rate)} for p in points]}
    return {'version':VERSION, 'analysis_revision':ANALYSIS_REVISION, 'kind':'offline-cut-only-peq-proposal', 'source':analysis['source'],
            'channels':channels, 'sample_rate':rate, 'reserved_headroom_db':6,
            'max_filters_per_channel':4, 'maximum_cut_db':6, 'boost_allowed':False,
            'electrical_kind':'PEQ-only plus reserved headroom; no crossover, summing, master gain or room',
            'prediction_kind':'magnitude multiplication assumption only; not measured acoustic verification',
            'applied':False, 'verified_acoustically':False, 'sound_disabled':True,
            'warnings':['No delay/crossover/phase inferred; do not treat this as a commissioned profile',
                        'New attended before/after measurements required to verify acoustic improvement',
                        'Target-fit checks are magnitude predictions at supplied points only, not acoustic verification'] + analysis['warnings']}
