"""Ops MCP room correction: REW import, target comparison, cut-biased PEQ, alignment, YAML preview, apply.

Synthetic measurements only (generated responses written in REW's text export format).
"""
import importlib.util
import json
import unittest
from pathlib import Path

from garage_audio.ops_mcp import backups, room, safety, tools
from garage_audio.ops_mcp.errors import OpsError

_spec = importlib.util.spec_from_file_location('ops_mcp_fakes', Path(__file__).parent / 'fixtures/ops_mcp/fakes.py')
fakes = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fakes)

# Two room-mode peaks and a null in the bass region, plus a treble wiggle outside the default range.
MODES = fakes.synthetic_response(peaks=[(45.0, 9.0, 0.12), (110.0, 6.0, 0.15), (4000.0, 6.0, 0.3)],
                                 dips=[(75.0, 12.0, 0.08)])


def imported(h, points=MODES, channel='left', **extra):
    return tools.call(h.ctx, 'room_import_measurement', {'text': fakes.rew_export(points), 'channel': channel,
                                                         **extra})


class Import(unittest.TestCase):
    def test_import_and_list(self):
        h = fakes.Harness(self)
        result = imported(h, label='seat 1', distance_m=3.2)
        self.assertEqual(result['points'], len(MODES))
        self.assertAlmostEqual(result['freq_range_hz'][0], 15.0, places=2)
        self.assertEqual(len(result['sha256']), 64)
        listed = tools.call(h.ctx, 'room_list_measurements', {})['measurements']
        self.assertEqual([m['id'] for m in listed], [result['measurement_id']])
        self.assertEqual(listed[0]['label'], 'seat 1')
        self.assertEqual(listed[0]['distance_m'], 3.2)

    def test_bad_exports_rejected(self):
        h = fakes.Harness(self)
        for text in ('hello world\n', '* only a header\n', '100 abc 0\n200 def 0\n'):
            with self.subTest(text=text), self.assertRaises(OpsError) as caught:
                tools.call(h.ctx, 'room_import_measurement', {'text': text, 'channel': 'left'})
            self.assertEqual(caught.exception.code, 'measurement_invalid')
        with self.assertRaises(OpsError) as caught:
            tools.call(h.ctx, 'room_import_measurement', {'text': fakes.rew_export(MODES), 'channel': 'centre'})
        self.assertEqual(caught.exception.code, 'invalid_argument')
        self.assertEqual(tools.call(h.ctx, 'room_list_measurements', {})['measurements'], [])


class Compare(unittest.TestCase):
    def test_peaks_positive_dips_negative(self):
        h = fakes.Harness(self)
        mid = imported(h)['measurement_id']
        result = tools.call(h.ctx, 'room_compare_to_target', {'measurement_id': mid})
        self.assertEqual(result['range_hz'], [25.0, 300.0])
        curve = {round(p['freq_hz']): p['deviation_db'] for p in result['curve']}
        near = lambda f: curve[min(curve, key=lambda x: abs(x - f))]  # noqa: E731
        self.assertGreater(near(45), 4)
        self.assertLess(near(75), -4)
        self.assertLess(abs(near(200)), 1.5)
        self.assertEqual(len(result['largest_deviations']), 5)

    def test_out_of_range_measurement(self):
        h = fakes.Harness(self)
        mid = imported(h, fakes.synthetic_response(f_lo=1000.0))['measurement_id']
        with self.assertRaises(OpsError) as caught:
            tools.call(h.ctx, 'room_compare_to_target', {'measurement_id': mid})
        self.assertEqual(caught.exception.code, 'measurement_range')


class ProposeMath(unittest.TestCase):
    KW = dict(target={}, smoothing=1 / 6, tolerance=1.0, min_gain=-15.0, q_min=0.3, q_max=10.0, fs=48000)

    def test_cut_only_default(self):
        result = room.propose(MODES, f_lo=25.0, f_hi=300.0, max_filters=6, max_boost=0.0, **self.KW)
        self.assertTrue(result['filters'])
        self.assertLessEqual(len(result['filters']), 6)
        for item in result['filters']:
            self.assertLess(item['gain'], 0)
            self.assertGreaterEqual(item['gain'], -15.0)
            self.assertTrue(25.0 <= item['freq'] <= 300.0)
            self.assertTrue(0.3 <= item['q'] <= 10.0)
        self.assertLess(result['rms_deviation_after_db'], result['rms_deviation_before_db'])
        self.assertLess(result['max_peak_after_db'], result['max_peak_before_db'])
        # The 4 kHz bump is outside the correction range and must not be touched.
        self.assertFalse(any(item['freq'] > 300 for item in result['filters']))
        # The null at 75 Hz is never boosted in cut-only mode.
        self.assertFalse(any(item['gain'] > 0 for item in result['filters']))

    def test_filter_count_bound(self):
        for count in (1, 2, 3):
            result = room.propose(MODES, f_lo=25.0, f_hi=300.0, max_filters=count, max_boost=0.0, **self.KW)
            self.assertLessEqual(len(result['filters']), count)

    def test_boost_is_capped_and_half_the_dip(self):
        dip_only = fakes.synthetic_response(dips=[(80.0, 10.0, 0.3)])
        result = room.propose(dip_only, f_lo=25.0, f_hi=300.0, max_filters=4, max_boost=2.0, **self.KW)
        boosts = [item['gain'] for item in result['filters'] if item['gain'] > 0]
        self.assertTrue(boosts)
        self.assertTrue(all(b <= 2.0 for b in boosts))
        none = room.propose(dip_only, f_lo=25.0, f_hi=300.0, max_filters=4, max_boost=0.0, **self.KW)
        self.assertFalse(any(item['gain'] > 0 for item in none['filters']))

    def test_deep_peak_limited_by_min_gain(self):
        spike = fakes.synthetic_response(peaks=[(50.0, 30.0, 0.2)])
        result = room.propose(spike, f_lo=25.0, f_hi=300.0, max_filters=3, max_boost=0.0, **self.KW)
        self.assertTrue(all(item['gain'] >= -15.0 for item in result['filters']))

    def test_flat_response_needs_nothing(self):
        flat = fakes.synthetic_response()
        result = room.propose(flat, f_lo=25.0, f_hi=300.0, max_filters=6, max_boost=3.0, **self.KW)
        self.assertEqual(result['filters'], [])

    def test_grid_endpoints_exact(self):
        grid = room.log_grid(25.0, 300.0)
        self.assertEqual((grid[0], grid[-1]), (25.0, 300.0))
        self.assertTrue(all(a < b for a, b in zip(grid, grid[1:])))

    def test_peaking_response_matches_gain_at_centre(self):
        self.assertAlmostEqual(room.peaking_db(100.0, 100.0, 2.0, -6.0, 48000), -6.0, places=3)
        self.assertAlmostEqual(room.peaking_db(10000.0, 100.0, 2.0, -6.0, 48000), 0.0, places=1)


class ProposeTool(unittest.TestCase):
    def test_propose_peq_tool_and_caps(self):
        h = fakes.Harness(self)
        mid = imported(h)['measurement_id']
        result = tools.call(h.ctx, 'room_propose_peq', {'measurement_id': mid})
        self.assertEqual(result['channel'], 'left')
        self.assertEqual(result['settings']['max_boost'], 0.0)
        self.assertTrue(all(item['gain'] <= 0 for item in result['filters']))
        self.assertLessEqual(len(result['filters']), 6)
        for args, code in (({'max_boost_db': 1}, 'safety_limit'), ({'max_filters': 7}, 'safety_limit'),
                           ({'max_filters': 11}, 'invalid_argument'), ({'max_boost_db': 7}, 'invalid_argument'),
                           ({'freq_min_hz': 10}, 'invalid_argument')):
            with self.subTest(args=args), self.assertRaises(OpsError) as caught:
                tools.call(h.ctx, 'room_propose_peq', {'measurement_id': mid, **args})
            self.assertEqual(caught.exception.code, code)
        # Nothing touched CamillaDSP.
        self.assertEqual(h.camilla.events, [])

    def test_operator_allowed_boost(self):
        h = fakes.Harness(self, {'room_correction': {'max_boost_db': 2.0}})
        mid = imported(h, fakes.synthetic_response(dips=[(80.0, 10.0, 0.3)]))['measurement_id']
        result = tools.call(h.ctx, 'room_propose_peq', {'measurement_id': mid, 'max_boost_db': 1.5})
        self.assertTrue(any(item['gain'] > 0 for item in result['filters']))
        self.assertTrue(all(item['gain'] <= 1.5 for item in result['filters']))

    def test_alignment(self):
        result = room.alignment(distances_m={'left': 3.0, 'right': 3.0, 'sub': 1.5})
        self.assertEqual(result['delay_method'], 'distances')
        self.assertEqual(result['delay_ms']['left'], 0.0)
        self.assertAlmostEqual(result['delay_ms']['sub'], 1.5 / safety.SPEED_OF_SOUND_M_S * 1000, places=3)
        preferred = room.alignment(distances_m={'left': 3.0, 'sub': 1.5},
                                   impulse_peaks_ms={'left': 10.0, 'right': 10.2, 'sub': 6.0},
                                   levels_db={'left': 80.0, 'right': 79.0, 'sub': 85.0})
        self.assertEqual(preferred['delay_method'], 'impulse_peaks')
        self.assertEqual(preferred['delay_ms'], {'left': 0.2, 'right': 0.0, 'sub': 4.2})
        self.assertEqual(preferred['gain_db'], {'left': -1.0, 'right': 0.0, 'sub': -6.0})
        self.assertTrue(all(v >= 0 for v in preferred['delay_ms'].values()))
        self.assertTrue(all(v <= 0 for v in preferred['gain_db'].values()))

    def test_alignment_tool_limits(self):
        h = fakes.Harness(self)
        ok = tools.call(h.ctx, 'room_propose_alignment', {'impulse_peaks_ms': {'left': 9.0, 'sub': 5.0}})
        self.assertEqual(ok['delay_ms'], {'left': 0.0, 'sub': 4.0})
        for args in ({'distances_m': {'left': 12.0, 'sub': 1.0}},       # 32 ms > 20 ms default delay cap
                     {'levels_db': {'left': 40.0, 'sub': 90.0}}):       # -50 dB trim < -40 dB default floor
            with self.subTest(args=args), self.assertRaises(OpsError) as caught:
                tools.call(h.ctx, 'room_propose_alignment', args)
            self.assertEqual(caught.exception.code, 'safety_limit')
        with self.assertRaises(OpsError) as caught:
            tools.call(h.ctx, 'room_propose_alignment', {})
        self.assertEqual(caught.exception.code, 'invalid_argument')
        with self.assertRaises(OpsError):
            tools.call(h.ctx, 'room_propose_alignment', {'distances_m': {'left': 3.0}})   # needs two channels


class PreviewAndApply(unittest.TestCase):
    def proposal(self, h, channel='left', **args):
        mid = imported(h, channel=channel)['measurement_id']
        return tools.call(h.ctx, 'room_propose_peq', {'measurement_id': mid, **args})

    def test_yaml_preview(self):
        h = fakes.Harness(self)
        prop = self.proposal(h)
        preview = tools.call(h.ctx, 'room_preview_yaml', {'proposal_id': prop['proposal_id']})
        text = preview['yaml']
        self.assertIn('filters:', text)
        self.assertIn('ops_rc_left_0:', text)
        self.assertIn('type: Biquad', text)
        self.assertIn('type: Peaking', text)
        self.assertIn('channels:\n      - 0\n', text)
        self.assertEqual(preview['unmapped_channels'], [])
        self.assertEqual(room.to_yaml({'a': {'b': [1, 'x y'], 'c': True, 'd': None, 'e': {}}}),
                         'a:\n  b:\n    - 1\n    - "x y"\n  c: true\n  d: null\n  e: {}')

    @unittest.skipUnless(importlib.util.find_spec('yaml'), 'PyYAML not installed in this interpreter')
    def test_yaml_preview_parses_back(self):
        import yaml
        h = fakes.Harness(self)
        prop = self.proposal(h)
        text = tools.call(h.ctx, 'room_preview_yaml', {'proposal_id': prop['proposal_id']})['yaml']
        parsed = yaml.safe_load(text)
        self.assertEqual(parsed, room._fragment(h.ctx, room.proposals(h.ctx).get(prop['proposal_id'])))

    def test_preview_reports_unmapped_channel(self):
        h = fakes.Harness(self, {'camilladsp': {'channels': {'left': 0, 'right': 1}}})
        prop = self.proposal(h, channel='sub')
        preview = tools.call(h.ctx, 'room_preview_yaml', {'proposal_id': prop['proposal_id']})
        self.assertEqual(preview['unmapped_channels'], ['sub'])

    def test_apply_replaces_earlier_correction(self):
        h = fakes.Harness(self, {'allow_write': True})
        first = self.proposal(h)
        applied = tools.call(h.ctx, 'room_apply_correction', {'proposal_id': first['proposal_id']})
        names = sorted(n for n in h.camilla.config['filters'] if n.startswith('ops_rc_left_'))
        self.assertEqual(len(names), len(first['filters']))
        self.assertTrue(applied['readback_verified'])
        step = [s for s in h.camilla.config['pipeline'] if 'ops_rc_left_0' in (s.get('names') or [])]
        self.assertEqual(len(step), 1)
        self.assertEqual(step[0]['channels'], [0])
        self.assertEqual(h.camilla.config['filters']['peq_left_1'], fakes.base_config()['filters']['peq_left_1'])
        second = self.proposal(h, max_filters=1)
        tools.call(h.ctx, 'room_apply_correction', {'proposal_id': second['proposal_id']})
        names = sorted(n for n in h.camilla.config['filters'] if n.startswith('ops_rc_left_'))
        self.assertEqual(names, ['ops_rc_left_0'])
        pipeline_names = [n for s in h.camilla.config['pipeline'] for n in (s.get('names') or [])]
        self.assertEqual([n for n in pipeline_names if n.startswith('ops_rc_left_')], ['ops_rc_left_0'])
        # Rolling back the second apply restores the first correction exactly.
        backup_id = tools.call(h.ctx, 'dsp_list_backups', {})['backups'][0]['id']
        tools.call(h.ctx, 'dsp_restore_backup', {'backup_id': backup_id})
        self.assertEqual(len([n for n in h.camilla.config['filters'] if n.startswith('ops_rc_left_')]),
                         len(first['filters']))

    def test_apply_alignment(self):
        h = fakes.Harness(self, {'allow_write': True})
        prop = tools.call(h.ctx, 'room_propose_alignment', {'impulse_peaks_ms': {'left': 9.0, 'right': 9.0, 'sub': 5.0},
                                                            'levels_db': {'left': 80.0, 'right': 80.0, 'sub': 83.0}})
        tools.call(h.ctx, 'room_apply_correction', {'proposal_id': prop['proposal_id']})
        filters = h.camilla.config['filters']
        self.assertEqual(filters['ops_left_delay']['parameters']['delay'], 0.0)
        self.assertEqual(filters['ops_sub_delay']['parameters']['delay'], 4.0)
        self.assertEqual(filters['ops_sub_gain']['parameters']['gain'], -3.0)

    def test_apply_is_write_gated(self):
        h = fakes.Harness(self)
        prop = self.proposal(h)
        with self.assertRaises(OpsError) as caught:
            tools.call(h.ctx, 'room_apply_correction', {'proposal_id': prop['proposal_id']})
        self.assertEqual(caught.exception.code, 'write_disabled')
        self.assertEqual(h.camilla.events, [])

    def test_tampered_proposal_is_revalidated(self):
        h = fakes.Harness(self, {'allow_write': True})
        prop = self.proposal(h)
        path = h.state_dir / room.PROPOSAL_DIR / f"{prop['proposal_id']}.json"
        record = json.loads(path.read_text())
        record['filters'][0]['gain'] = 4.0
        record['settings']['max_boost'] = 6.0
        backups.atomic_write_json(path, record)
        with self.assertRaises(OpsError) as caught:
            tools.call(h.ctx, 'room_apply_correction', {'proposal_id': prop['proposal_id']})
        self.assertEqual(caught.exception.code, 'safety_limit')
        record['filters'][0]['gain'] = -3.0
        record['filters'][0]['freq'] = 10.0
        backups.atomic_write_json(path, record)
        with self.assertRaises(OpsError) as caught:
            tools.call(h.ctx, 'room_apply_correction', {'proposal_id': prop['proposal_id']})
        self.assertEqual(caught.exception.code, 'safety_limit')
        self.assertNotIn('set_config', h.camilla.events)
        self.assertFalse((h.state_dir / 'dsp-backups').exists())

    def test_not_found_proposal(self):
        h = fakes.Harness(self, {'allow_write': True})
        with self.assertRaises(OpsError) as caught:
            tools.call(h.ctx, 'room_apply_correction', {'proposal_id': '20261122T100000.000000Z-0123abcd'})
        self.assertEqual(caught.exception.code, 'not_found')


if __name__ == '__main__':
    unittest.main()
