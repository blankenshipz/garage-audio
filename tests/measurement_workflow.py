"""Executable offline workflow checks. All response data here is SYNTHETIC."""
import hashlib
import json
import math
import tempfile
import unittest
from garage_audio.application import Application

def fixture(position='seat', channel='left'):
    text = '\n'.join(f'{20*50**(i/240):.8f} {70+8*math.exp(-(math.log2((20*50**(i/240))/100)/.25)**2):.8f}' for i in range(241))
    metadata = {'source':'synthetic','position':position,'channel':channel,'sample_rate':48000,
                'orientation':'90','calibration_sha256':'a'*64,'calibration_applied':True,
                'timing_reference':'unknown; synthetic magnitude fixture','timing_quality':'unknown',
                'microphone_serial':'synthetic-not-a-microphone'}
    return {'filename':position+'.txt','format':'rew-response','text':text,'metadata':metadata}

class Workflow(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        def forbidden(_): raise AssertionError('offline workflow must never call DSP')
        self.app = Application(self.tmp.name,forbidden)
    def tearDown(self): self.tmp.cleanup()
    def op(self, operation, **args): return self.app.execute(operation,args)
    def create(self, positions=None):
        return self.op('calibration.plan',name='synthetic only',channels=['left'],positions=positions or ['seat'])['result']['id']
    def imported(self, positions=None):
        positions = positions or ['seat']
        sid = self.create(positions)
        for position in positions:
            result = self.op('measurements.import',session_id=sid,**fixture(position))
            self.assertTrue(result['ok'],result)
        return sid
    def test_plan_bounds_and_authority_denial(self):
        sid = self.create()
        s = self.op('calibration.get',id=sid)['result']
        self.assertEqual(s['plan']['sweep']['level_dbfs'],-40)
        self.assertEqual(s['plan']['sweep']['auto_stop_seconds'],8)
        self.assertFalse(s['plan']['hardware_verified'])
        laptop = self.op('calibration.plan',name='laptop import',target='laptop-rew')['result']['plan']
        self.assertEqual(laptop['target'],'laptop-rew')
        self.assertIn('laptop REW',laptop['prerequisites'][0])
        for args in ({'attended':True},{'sample_rate':True},{'positions':['x']*9},{'channels':['left','left']},
                     {'orientation':'unknown'},{'level_dbfs':0},{'target':'arbitrary'}):
            self.assertFalse(self.op('calibration.plan',name='invalid',**args)['ok'])
        self.assertFalse(self.op('calibration.propose',id=sid)['ok'])
        self.assertFalse(self.op('calibration.analyze',id=sid)['ok'])
        self.assertFalse(self.op('calibration.get',id='../escape')['ok'])
        self.assertFalse(self.op('calibration.analyze',id=sid,consent='human')['ok'])
        self.assertFalse(self.op('dsp.unmute')['ok'])
    def test_multi_position_proposal_persistence_and_no_sound(self):
        sid = self.imported(['seat','back'])
        analyzed = self.op('calibration.analyze',id=sid)
        self.assertTrue(analyzed['ok'],analyzed)
        self.assertEqual(analyzed['result']['analysis']['source'],'synthetic')
        self.assertFalse(analyzed['result']['analysis']['timing']['supported'])
        proposed = self.op('calibration.propose',id=sid)
        self.assertTrue(proposed['ok'],proposed)
        s = proposed['result']; proposal = s['proposal']
        peq = proposal['channels']['left']['peq']
        self.assertTrue(peq)
        self.assertTrue(all(-6 <= f['gain'] < 0 for f in peq))
        self.assertLessEqual(len(peq),4)
        self.assertLessEqual(proposal['channels']['left']['maximum_electrical_db'],-5.99)
        self.assertFalse(proposal['applied']); self.assertFalse(proposal['verified_acoustically'])
        self.assertTrue(s['sound_disabled'])
        reopened = Application(self.tmp.name).execute('calibration.get',{'id':sid})['result']
        self.assertEqual(reopened,s)
        original = fixture()['text']
        self.assertEqual(s['files'][0]['original_text'],original)
        self.assertEqual(s['files'][0]['sha256'],hashlib.sha256(original.encode()).hexdigest())
        manifest = self.op('sessions.list')['result'][0]
        self.assertNotIn('analysis',manifest); self.assertNotIn('proposal',manifest)
        self.assertNotIn('original_text',manifest['files'][0])
        self.assertTrue(manifest['proposal_available'])
    def test_metadata_gating(self):
        for change in ({'source':'measured'}, {'sample_rate':44100},{'orientation':'0'},
                       {'calibration_applied':False},{'calibration_sha256':'b'*64},{'microphone_serial':'other'}):
            sid = self.create(['seat','back'])
            self.op('measurements.import',session_id=sid,**fixture())
            second = fixture('back'); second['metadata'].update(change)
            self.assertTrue(self.op('measurements.import',session_id=sid,**second)['ok'])
            self.assertEqual(self.op('calibration.analyze',id=sid)['error']['code'],'measurement_quality_required')
        sid = self.create()
        missing = fixture(); missing['metadata'].pop('timing_quality')
        self.op('measurements.import',session_id=sid,**missing)
        self.assertFalse(self.op('calibration.analyze',id=sid)['ok'])
    def test_cancel_and_import_invalidation(self):
        sid = self.imported()
        self.op('calibration.analyze',id=sid); self.op('calibration.propose',id=sid)
        s = self.op('measurements.import',session_id=sid,**fixture())
        self.assertTrue(s['ok'])
        self.assertFalse(self.op('calibration.propose',id=sid)['ok'])
        self.assertFalse(self.op('calibration.analyze',id=sid)['ok'])  # duplicate position is ambiguous
        self.assertEqual(self.op('calibration.cancel',id=sid)['result']['state'],'cancelled')
        self.assertFalse(self.op('measurements.import',session_id=sid,**fixture())['ok'])
        self.assertFalse(self.op('calibration.analyze',id=sid)['ok'])
        self.assertTrue(self.op('calibration.get',id=sid)['ok'])
    def test_no_boost_for_nulls_and_nonrobust_peaks(self):
        for signed_peak in (-8,8):
            sid = self.create(['seat','back'])
            for position in ('seat','back'):
                data = fixture(position)
                # One position has a peak, the other a null: not a shared robust cut.
                amplitude = signed_peak if position == 'seat' else -8
                data['text'] = '\n'.join(f'{20*50**(i/240):.8f} {70+amplitude*math.exp(-(math.log2((20*50**(i/240))/100)/.25)**2):.8f}' for i in range(241))
                self.assertTrue(self.op('measurements.import',session_id=sid,**data)['ok'])
            self.assertTrue(self.op('calibration.analyze',id=sid)['ok'])
            proposed = self.op('calibration.propose',id=sid)
            self.assertTrue(proposed['ok'])
            self.assertEqual(proposed['result']['proposal']['channels']['left']['peq'],[])
    def test_review_narrow_spike_is_not_broadened_into_a_cut(self):
        sid = self.create()
        data = fixture()
        grid = [20*50**(i/120) for i in range(121)]
        spike = min(range(121),key=lambda i:abs(grid[i]-98.804))
        data['text'] = '\n'.join(f'{hz:.12f} {79 if i == spike else 70}' for i,hz in enumerate(grid))
        self.assertTrue(self.op('measurements.import',session_id=sid,**data)['ok'])
        self.assertTrue(self.op('calibration.analyze',id=sid)['ok'])
        channel = self.op('calibration.propose',id=sid)['result']['proposal']['channels']['left']
        self.assertEqual(channel['peq'],[])
        self.assertFalse(channel['useful_cut_available'])
        self.assertIn('No useful',channel['selection_message'])
        self.assertEqual(channel['target_fit']['before'],channel['target_fit']['after'])
    def test_wide_peak_improves_each_position_and_native_points(self):
        sid = self.imported(['seat','back'])
        analyzed = self.op('calibration.analyze',id=sid)['result']['analysis']
        self.assertEqual(len(analyzed['channels']['left']['position_curves']),2)
        channel = self.op('calibration.propose',id=sid)['result']['proposal']['channels']['left']
        self.assertTrue(channel['peq'])
        fit = channel['target_fit']
        self.assertLess(fit['after']['aggregate_rms_db'],fit['before']['aggregate_rms_db'])
        for before,after in zip(fit['before']['positions'],fit['after']['positions']):
            self.assertLess(after['grid_rms_db'],before['grid_rms_db'])
            self.assertLess(after['import_rms_db'],before['import_rms_db'])
    def test_conflicting_position_cannot_be_sacrificed_for_aggregate(self):
        sid = self.create(['seat','back'])
        for position in ('seat','back'):
            data = fixture(position)
            data['text'] = '\n'.join(f'{20*50**(i/240):.12f} {70+8*math.exp(-(math.log2((20*50**(i/240))/100)/.35)**2)-(80*math.exp(-(math.log2((20*50**(i/240))/65)/.20)**2) if position == "back" else 0):.12f}' for i in range(241))
            self.assertTrue(self.op('measurements.import',session_id=sid,**data)['ok'])
        self.assertTrue(self.op('calibration.analyze',id=sid)['ok'])
        channel = self.op('calibration.propose',id=sid)['result']['proposal']['channels']['left']
        self.assertEqual(channel['peq'],[])
        self.assertTrue(channel['target_fit']['nonworsening_each_position'])
    def test_known_drift_gates_proposal_unknown_is_provisional(self):
        for drift,eligible in ((1000,False),(-101,False),(100,True),(25,True),(None,True)):
            sid = self.create()
            data = fixture()
            # Test supplied measured metadata without claiming a hardware capture.
            data['metadata']['source'] = 'measured'
            if drift is not None: data['metadata']['clock_drift_ppm'] = drift
            self.assertTrue(self.op('measurements.import',session_id=sid,**data)['ok'])
            analyzed = self.op('calibration.analyze',id=sid)
            self.assertTrue(analyzed['ok'])
            analysis = analyzed['result']['analysis']
            self.assertEqual(analysis['proposal_eligibility']['eligible'],eligible)
            self.assertEqual(list(analysis['timing']['clock_drift_ppm_by_file'].values()),[drift])
            self.assertFalse(analysis['timing']['clock_drift_verified'])
            proposed = self.op('calibration.propose',id=sid)
            self.assertEqual(proposed['ok'],eligible)
            if not eligible: self.assertEqual(proposed['error']['code'],'clock_quality_required')
            if drift is None: self.assertTrue(any('Clock drift unknown' in w for w in analysis['warnings']))
    def test_pre_repair_persisted_analysis_requires_reanalysis(self):
        sid = self.imported()
        self.op('calibration.analyze',id=sid)
        s = self.app.read('sessions',sid)
        s['analysis'].pop('analysis_revision')
        self.app.save('sessions',s)
        self.assertEqual(self.op('calibration.propose',id=sid)['error']['code'],'analysis_required')
    def test_stale_derived_results_hidden_on_all_reads_and_recomputable(self):
        sid = self.imported()
        self.op('calibration.analyze',id=sid); self.op('calibration.propose',id=sid)
        stored = self.app.read('sessions',sid)
        stored['analysis'].pop('analysis_revision')
        stored['proposal'].pop('analysis_revision')
        self.app.save('sessions',stored)
        # Reproduce an old lightweight manifest advertising validation as current.
        manifest = self.app.read('session-manifests',sid)
        for key in ('projection_version','analysis_revision','proposal_analysis_revision',
                    'derived_results_superseded','derived_results_warning','derived_results_requirements','recompute_required'):
            manifest.pop(key,None)
        manifest.update(state='proposed',analysis_available=True,proposal_available=True)
        self.app.write_atomic(self.app.path('session-manifests',sid),json.dumps(manifest).encode())
        for result in (self.op('calibration.get',id=sid), self.op('sessions.get',id=sid),
                       {'ok':True,'result':self.op('sessions.list')['result'][0]}):
            self.assertTrue(result['ok'])
            view = result['result']
            self.assertNotIn('analysis',view); self.assertNotIn('proposal',view)
            self.assertFalse(view['analysis_available']); self.assertFalse(view['proposal_available'])
            self.assertEqual(view['state'],'imported')
            self.assertTrue(view['derived_results_superseded']); self.assertTrue(view['recompute_required'])
            self.assertIn('calibration.analyze',view['derived_results_requirements'][0])
        # A second lightweight list preserves the warning without re-migration.
        path = self.app.path('session-manifests',sid); timestamp = path.stat().st_mtime_ns
        self.assertTrue(self.op('sessions.list')['result'][0]['derived_results_superseded'])
        self.assertEqual(path.stat().st_mtime_ns,timestamp)
        self.assertEqual(self.app.read('sessions',sid),stored)  # private original remains intact
        self.app.broker = lambda payload: {'muted':True}  # isolated status fixture, no real DSP
        status = self.op('status')['result']['calibration']
        self.assertEqual(status['state'],'unverified')
        self.assertEqual(status['analysis_revision'],2)
        self.assertNotIn('proposal_available',status)
        analyzed = self.op('calibration.analyze',id=sid)
        self.assertTrue(analyzed['ok'])
        self.assertFalse(analyzed['result']['derived_results_superseded'])
        proposed = self.op('calibration.propose',id=sid)
        self.assertTrue(proposed['ok'])
        self.assertTrue(proposed['result']['proposal_available'])
        self.assertEqual(proposed['result']['proposal']['analysis_revision'],2)
        self.assertEqual(proposed['result']['files'],stored['files'])
        current = self.op('sessions.list')['result'][0]
        self.assertTrue(current['analysis_available']); self.assertTrue(current['proposal_available'])
        self.assertFalse(current['derived_results_superseded'])
    def test_stale_cancelled_projection_stays_terminal(self):
        sid = self.imported()
        self.op('calibration.analyze',id=sid); self.op('calibration.propose',id=sid)
        stored = self.app.read('sessions',sid)
        stored['state'] = 'cancelled'; stored['proposal'].pop('analysis_revision')
        self.app.save('sessions',stored)
        for operation in ('calibration.get','sessions.get'):
            view = self.op(operation,id=sid)['result']
            self.assertEqual(view['state'],'cancelled')
            self.assertTrue(view['derived_results_superseded'])
            self.assertFalse(view['recompute_required']); self.assertNotIn('proposal',view)
        self.assertFalse(self.op('calibration.analyze',id=sid)['ok'])

if __name__ == '__main__': unittest.main()
