"""Ops MCP room profile, room modes, measurement plan, guided session state machine, averaging, and the
measurement resources/prompt. Synthetic only (fakes and a simulated room).
"""
import importlib.util
import json
import math
import os
import stat
import unittest
from pathlib import Path

from garage_audio.ops_mcp import content, measure_session, room_model, tools
from garage_audio.ops_mcp.errors import OpsError

FIXTURES = Path(__file__).parent / 'fixtures/ops_mcp'


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


fakes = _load('ops_mcp_fakes', FIXTURES / 'fakes.py')
try:
    import numpy  # noqa: F401
    sim = _load('ops_mcp_room_sim', FIXTURES / 'room_sim.py')
except ImportError:
    sim = None

PROFILE = {'dimensions_m': {'length': 5.0, 'width': 3.7, 'height': 2.6},
           'speakers': {'left': {'x': 0.9, 'y': 0.6, 'z': 1.0}, 'right': {'x': 2.8, 'y': 0.6, 'z': 1.0},
                        'sub': {'x': 0.4, 'y': 0.4, 'z': 0.2}},
           'listening_positions': {'main': {'x': 1.85, 'y': 3.1, 'ear_height': 1.1}},
           'seating': {'width': 1.8, 'depth': 0.9}}


def call(h, tool, **args):
    return tools.call(h.ctx, tool, args)


class RoomProfile(unittest.TestCase):
    def test_partial_updates_units_and_remove(self):
        h = fakes.Harness(self)
        first = call(h, 'room_profile_get')
        self.assertEqual(first['profile'], {})
        self.assertIn('dimensions_m.length', first['missing'])
        call(h, 'room_profile_update', set={'dimensions_m': {'length': 16.4, 'width': 12.1}}, units='ft')
        call(h, 'room_profile_update', set={'dimensions_m': {'height': 260}}, units='cm')
        call(h, 'room_profile_update', set={'notes': 'garage door on the back wall', 'obstacles': ['workbench']})
        profile = call(h, 'room_profile_get')['profile']
        self.assertAlmostEqual(profile['dimensions_m']['length'], 16.4 * 0.3048, places=3)
        self.assertAlmostEqual(profile['dimensions_m']['width'], 12.1 * 0.3048, places=3)
        self.assertAlmostEqual(profile['dimensions_m']['height'], 2.6, places=3)
        self.assertEqual(profile['obstacles'], ['workbench'])
        call(h, 'room_profile_update', remove=['notes'])
        self.assertNotIn('notes', call(h, 'room_profile_get')['profile'])
        path = Path(h.config.state_dir) / room_model.PROFILE_FILE
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    def test_positions_must_be_inside_the_room(self):
        h = fakes.Harness(self)
        call(h, 'room_profile_update', set={'dimensions_m': {'length': 5, 'width': 4, 'height': 2.5}})
        with self.assertRaises(OpsError):
            call(h, 'room_profile_update', set={'speakers': {'left': {'x': 7, 'y': 1, 'z': 1}}})
        with self.assertRaises(OpsError):  # schema: unknown fields are rejected
            call(h, 'room_profile_update', set={'windows': 3})
        self.assertNotIn('speakers', call(h, 'room_profile_get')['profile'])


class RoomModes(unittest.TestCase):
    def test_axial_modes(self):
        modes = room_model.axial_modes({'length': 5.0, 'width': 3.7, 'height': 2.6}, 100)
        expected = sorted([(n * 343.0 / 10.0) for n in (1, 2)] + [343.0 / 7.4, 2 * 343.0 / 7.4] + [343.0 / 5.2])
        got = [m['freq_hz'] for m in modes]
        self.assertEqual(len(got), len(expected))
        for g, e in zip(got, expected):
            self.assertAlmostEqual(g, e, delta=0.5)

    def test_advice_for_bad_seat_and_corner_sub(self):
        h = fakes.Harness(self)
        profile = json.loads(json.dumps(PROFILE))
        profile['listening_positions']['main']['y'] = 2.5   # exactly half the length
        profile['listening_positions']['main']['ear_height'] = 1.3  # half the height
        call(h, 'room_profile_update', set=profile)
        out = call(h, 'room_modes')
        text = ' '.join(out['advice'])
        self.assertIn('middle of the room length', text)
        self.assertIn('half the ceiling height', text)
        self.assertIn('corner', text)
        length1 = [m for m in out['seat_coupling'] if m['axis'] == 'length' and m['order'] == 1][0]
        self.assertLess(length1['at_seat'], 0.1)
        self.assertIn('null', length1['effect'])
        self.assertIn('schroeder_hz_estimate', out)

    def test_what_if_dimensions_and_missing(self):
        h = fakes.Harness(self)
        with self.assertRaises(OpsError) as caught:
            call(h, 'room_modes')
        self.assertEqual(caught.exception.code, 'room_incomplete')
        out = call(h, 'room_modes', dimensions_m={'length': 20, 'width': 13, 'height': 8}, units='ft')
        self.assertAlmostEqual(out['axial_modes'][0]['freq_hz'], 343.0 / (2 * 20 * 0.3048), delta=0.2)
        self.assertEqual(call(h, 'room_profile_get')['profile'], {})  # what-if does not store


class Plan(unittest.TestCase):
    def test_plan_scaled_described_and_bounded(self):
        h = fakes.Harness(self)
        call(h, 'room_profile_update', set=PROFILE)
        for count in (4, 6, 8):
            with self.subTest(count=count):
                out = call(h, 'measure_plan', positions=count, radius_m=0.3)
                positions = out['positions']
                self.assertEqual(len(positions), count + 1)
                self.assertEqual(positions[0]['role'], 'main')
                ids = [p['id'] for p in positions]
                self.assertEqual(len(set(ids)), len(ids))
                for p in positions[1:]:
                    r = math.hypot(p['offset_m']['right'], p['offset_m']['forward'])
                    self.assertAlmostEqual(r, 0.3, places=2)
                    self.assertEqual(p['height_m'], 1.1)
                    self.assertRegex(p['description'], r'\d+ cm')
                    self.assertRegex(p['description_imperial'], r'\d+ (in|ft)')
                    self.assertIn('seat', p['description'])
        out = call(h, 'measure_plan', positions=4, radius_m=0.3)
        self.assertIn('about 30 cm to the left of the centre of the seat', out['positions'][1]['description'])
        self.assertIn('about 1 ft to the left of the center of the seat', out['positions'][1]['description_imperial'])
        self.assertIn('straight up', out['mic_orientation'])
        self.assertIn('horizontally', call(h, 'measure_plan', mic_orientation='0')['mic_orientation'])
        with self.assertRaises(OpsError):
            call(h, 'measure_plan', positions=9)
        with self.assertRaises(OpsError):
            call(h, 'measure_plan', positions=3)

    def test_radius_limited_by_seating_and_walls_warned(self):
        h = fakes.Harness(self)
        profile = json.loads(json.dumps(PROFILE))
        profile['seating'] = {'width': 0.6}
        profile['listening_positions']['main']['x'] = 0.45  # the left position ends 15 cm from the wall
        call(h, 'room_profile_update', set=profile)
        out = call(h, 'measure_plan', positions=4, radius_m=1.0)
        self.assertEqual(out['radius_m'], 0.3)
        self.assertTrue(out['warnings'])


class Averaging(unittest.TestCase):
    def rec(self, db, phase=None, ident='x'):
        points = []
        for i in range(97):
            f = 20.0 * 2 ** (i / 12.0)
            if f > 20000:
                break
            p = {'frequency_hz': f, 'db': db}
            if phase is not None:
                p['phase_deg'] = phase
            points.append(p)
        return {'id': ident, 'points': points}

    def test_power_average(self):
        points, spread = measure_session.average_records([self.rec(0.0), self.rec(-10.0)], 'power')
        expected = 10 * math.log10((1 + 0.1) / 2)
        for p in points:
            self.assertAlmostEqual(p['db'], expected, places=2)
        self.assertAlmostEqual(spread['median_std_db'], 5.0, places=1)

    def test_vector_average_cancels_opposite_phase(self):
        points, _ = measure_session.average_records([self.rec(0.0, 0.0), self.rec(0.0, 180.0)], 'vector')
        self.assertTrue(all(p['db'] < -60 for p in points))
        same, _ = measure_session.average_records([self.rec(0.0, 10.0), self.rec(-6.0206, 10.0)], 'vector')
        self.assertTrue(all(abs(p['db'] - 20 * math.log10(0.75)) < 0.01 for p in same))
        self.assertTrue(all(abs(p['phase_deg'] - 10.0) < 0.01 for p in same))

    def test_vector_needs_phase_and_overlap(self):
        with self.assertRaises(OpsError):
            measure_session.average_records([self.rec(0.0), self.rec(0.0, 0.0)], 'vector')
        narrow = {'id': 'n', 'points': [{'frequency_hz': 15000.0, 'db': 0.0}, {'frequency_hz': 25000.0, 'db': 0.0}]}
        with self.assertRaises(OpsError):
            measure_session.average_records([self.rec(0.0), narrow], 'power')
        with self.assertRaises(OpsError):
            measure_session.average_records([self.rec(0.0)], 'power')

    def test_average_tool_stores_history(self):
        h = fakes.Harness(self)
        ids = []
        for level in (75.0, 69.0):
            text = fakes.rew_export(fakes.synthetic_response(level=level))
            ids.append(call(h, 'room_import_measurement', text=text, channel='left')['measurement_id'])
        out = call(h, 'measure_average', measurement_ids=ids)
        self.assertEqual(out['channel'], 'left')
        avg = h.ctx.history.get(out['measurement_id'])
        self.assertEqual(avg['source'], 'power_average')
        self.assertAlmostEqual(avg['points'][100]['db'], 10 * math.log10((10 ** 7.5 + 10 ** 6.9) / 2), places=2)
        right = call(h, 'room_import_measurement', text=fakes.rew_export(fakes.synthetic_response()),
                     channel='right')['measurement_id']
        with self.assertRaises(OpsError):  # mixed channels need an explicit label channel
            call(h, 'measure_average', measurement_ids=[ids[0], right])
        mixed = call(h, 'measure_average', measurement_ids=[ids[0], right], channel='sum')
        self.assertEqual(mixed['channel'], 'sum')


@unittest.skipIf(sim is None, 'numpy (measurement extra) not installed')
class Session(unittest.TestCase):
    def setUp(self):
        self.h = sim.harness(fakes, self)
        call(self.h, 'room_profile_update', set=PROFILE)

    def sweep(self, sid, channel, **extra):
        return call(self.h, 'measure_sweep', channel=channel, session_id=sid, user_confirmed=True,
                    sweep_seconds=1.5, **extra)

    def test_full_guided_flow_and_handoff(self):
        h = self.h
        started = call(h, 'measure_session_start', positions=4, radius_m=0.3, channels=['left', 'right'])
        sid = started['session_id']
        self.assertEqual(started['phase'], 'await_placement')
        self.assertIn('warning', started)  # no calibration
        self.assertIn('Wait for the user to confirm', started['next_step'])
        with self.assertRaises(OpsError) as caught:  # nothing plays before the placement is confirmed
            self.sweep(sid, 'left')
        self.assertEqual(caught.exception.code, 'session_state')
        with self.assertRaises(OpsError):
            call(h, 'measure_session_confirm_placement', session_id=sid, position_id='P1')
        self.assertEqual(h.room.played, [])
        gains = {}
        for index in range(5):
            pid = f'P{index}'
            # Each position hears the left speaker at a slightly different level.
            gains[pid] = -6.0 + 0.8 * index
            h.room.speakers[0].gain_db = gains[pid]
            h.room._irs.clear()
            view = call(h, 'measure_session_confirm_placement', session_id=sid, position_id=pid)
            self.assertEqual(view['phase'], 'await_noise_check')
            with self.assertRaises(OpsError):
                self.sweep(sid, 'left')
            noise = call(h, 'measure_noise_check', session_id=sid, seconds=1)
            self.assertEqual(noise['verdict'], 'ok')
            self.assertEqual(noise['session']['phase'], 'await_sweeps')
            if index == 0:
                self.assertIn('measure_level_check', noise['session']['next_step'])
                for channel in ('left', 'right'):
                    level = call(h, 'measure_level_check', channel=channel, session_id=sid, user_confirmed=True)
                    self.assertTrue(level['ok'])
            first = self.sweep(sid, 'left')
            self.assertEqual(first['session']['phase'], 'await_sweeps')
            second = self.sweep(sid, 'right')
            self.assertEqual(second['session']['phase'], 'position_review')
            self.assertIn('action=next', second['session']['next_step'])
            stored = h.ctx.history.get(first['measurement_id'])
            self.assertEqual((stored['position_id'], stored['session_id']), (pid, sid))
            if index == 1:  # redo one channel: the old measurement is superseded
                redo = call(h, 'measure_session_advance', session_id=sid, action='redo_channel', channel='right')
                self.assertEqual(redo['phase'], 'await_sweeps')
                self.sweep(sid, 'right')
            if index == 2:  # a fresh context on the same state dir resumes the same step
                resumed = fakes.Harness(self, {'state_dir': str(h.state_dir)})
                status = tools.call(resumed.ctx, 'measure_session_status', {})
                self.assertEqual((status['session_id'], status['phase'], status['current_position']),
                                 (sid, 'position_review', 'P2'))
            nxt = call(h, 'measure_session_advance', session_id=sid, action='next')
        self.assertEqual(nxt['phase'], 'ready_to_finish')
        session_file = Path(h.config.state_dir) / measure_session.SESSION_DIR / f'{sid}.json'
        self.assertEqual(stat.S_IMODE(session_file.stat().st_mode), 0o600)
        finished = call(h, 'measure_session_finish', session_id=sid)
        self.assertEqual(finished['status'], 'finished')
        averages = finished['result']['averages']
        self.assertEqual(averages['left']['positions'], 5)
        self.assertEqual(averages['left']['method'], 'power')
        # The power average at 1 kHz matches the analytic average of the five per-position levels.
        left = h.ctx.history.get(averages['left']['measurement_id'])
        base = sim.Speaker(3.0, 0.0, h.room.speakers[0].resonances).response_db(1000.0)
        expected = 10 * math.log10(sum(10 ** ((g + base) / 10) for g in gains.values()) / len(gains))
        at_1k = min(left['points'], key=lambda p: abs(math.log(p['frequency_hz'] / 1000.0)))
        self.assertAlmostEqual(at_1k['db'], expected, delta=0.3)
        # Hand-off to the existing EQ tools.
        comparison = call(h, 'room_compare_to_target', measurement_id=averages['left']['measurement_id'])
        self.assertGreater(comparison['largest_deviations'][0]['deviation_db'], 5.0)  # the 50 Hz peak
        peq = call(h, 'room_propose_peq', measurement_id=averages['left']['measurement_id'], channel='left')
        self.assertIn('proposal_id', peq)
        main_ids = list(finished['result']['main_position_measurement_ids'].values())
        alignment = call(h, 'room_propose_alignment', measurement_ids=main_ids)
        self.assertAlmostEqual(alignment['delay_ms']['left'], 0.6, delta=0.1)
        with self.assertRaises(OpsError):
            call(h, 'measure_session_advance', session_id=sid, action='next')

    def test_noisy_room_blocks_until_accepted_and_skip_rules(self):
        h = self.h
        h.ctx.measure_capture = sim.default_room(noise_dbfs=-30.0, loop=(3, 1))
        sid = call(h, 'measure_session_start', positions=4, channels=['left'])['session_id']
        with self.assertRaises(OpsError):  # the main position cannot be skipped
            call(h, 'measure_session_advance', session_id=sid, action='skip_position')
        call(h, 'measure_session_confirm_placement', session_id=sid, position_id='P0')
        noisy = call(h, 'measure_noise_check', session_id=sid, seconds=1)
        self.assertEqual(noisy['verdict'], 'too_noisy')
        self.assertEqual(noisy['session']['phase'], 'await_noise_check')
        accepted = call(h, 'measure_noise_check', session_id=sid, seconds=1, accept_noisy=True)
        self.assertEqual(accepted['session']['phase'], 'await_sweeps')
        self.sweep(sid, 'left')
        call(h, 'measure_session_advance', session_id=sid, action='next', accept_quality=True)
        skipped = call(h, 'measure_session_advance', session_id=sid, action='skip_position')
        self.assertEqual(skipped['current_position'], 'P2')
        with self.assertRaises(OpsError) as caught:
            call(h, 'measure_session_finish', session_id=sid)
        self.assertEqual(caught.exception.code, 'session_state')
        partial = call(h, 'measure_session_finish', session_id=sid, allow_partial=True)
        self.assertTrue(partial['result']['partial'])
        self.assertEqual(partial['result']['averages']['left']['method'], 'single')

    def test_quality_redo_must_be_handled(self):
        h = self.h
        h.ctx.measure_capture = sim.default_room(loop=(3, 1), disconnected=True)
        sid = call(h, 'measure_session_start', positions=4, channels=['left'])['session_id']
        call(h, 'measure_session_confirm_placement', session_id=sid, position_id='P0')
        h.ctx.measure_capture = sim.default_room(loop=(3, 1))  # noise check ok, then the mic "falls out"
        call(h, 'measure_noise_check', session_id=sid, seconds=1)
        h.ctx.measure_capture = sim.default_room(loop=(3, 1), disconnected=True)
        result = self.sweep(sid, 'left')
        self.assertEqual(result['quality']['status'], 'redo')
        self.assertIn('Redo', result['session']['next_step'])
        with self.assertRaises(OpsError) as caught:
            call(h, 'measure_session_advance', session_id=sid, action='next')
        self.assertEqual(caught.exception.code, 'session_quality')
        again = call(h, 'measure_session_advance', session_id=sid, action='redo_position')
        self.assertEqual(again['phase'], 'await_placement')

    def test_one_active_session_and_cancel(self):
        h = self.h
        sid = call(h, 'measure_session_start', positions=4)['session_id']
        with self.assertRaises(OpsError) as caught:
            call(h, 'measure_session_start', positions=4)
        self.assertEqual(caught.exception.code, 'session_active')
        replaced = call(h, 'measure_session_start', positions=5, replace=True)
        self.assertNotEqual(replaced['session_id'], sid)
        self.assertEqual(call(h, 'measure_session_status', session_id=sid)['status'], 'cancelled')
        cancelled = call(h, 'measure_session_cancel', session_id=replaced['session_id'])
        self.assertEqual(cancelled['status'], 'cancelled')
        self.assertIsNone(cancelled['aborted_running_measurement'])
        with self.assertRaises(OpsError):
            call(h, 'measure_session_confirm_placement', session_id=replaced['session_id'], position_id='P0')

    def test_session_uses_calibration_and_orientation(self):
        h = self.h
        text = '"Sens Factor =-1dB, SERNO: 0000000"\n' + '\n'.join(f'{f} 0.0' for f in (20, 100, 1000, 10000, 20000))
        cal = call(h, 'measure_import_calibration', text=text, name='mic 0deg')
        started = call(h, 'measure_session_start', positions=4, channels=['left'], calibration_id=cal['calibration_id'])
        self.assertIn('horizontally', started['plan']['mic_orientation'])
        self.assertNotIn('warning', started)
        sid = started['session_id']
        call(h, 'measure_session_confirm_placement', session_id=sid, position_id='P0')
        call(h, 'measure_noise_check', session_id=sid, seconds=1)
        result = self.sweep(sid, 'left')
        self.assertTrue(result['calibration_applied'])

    def test_bad_ids_and_unknown_channels(self):
        h = self.h
        for bad in ('../x', 'nope'):
            with self.subTest(bad=bad), self.assertRaises(OpsError):
                call(h, 'measure_session_status', session_id=bad)
        sid = call(h, 'measure_session_start', positions=4, channels=['left'])['session_id']
        call(h, 'measure_session_confirm_placement', session_id=sid, position_id='P0')
        call(h, 'measure_noise_check', session_id=sid, seconds=1)
        with self.assertRaises(OpsError) as caught:
            self.sweep(sid, 'right')
        self.assertEqual(caught.exception.code, 'invalid_argument')


class ResourcesAndPrompt(unittest.TestCase):
    def test_measurement_resources(self):
        h = fakes.Harness(self)
        self.assertEqual(content.read_resource(h.ctx, 'garage-audio://room/profile')['profile'], {})
        self.assertIsNone(content.read_resource(h.ctx, 'garage-audio://measure/session')['session'])
        call(h, 'room_profile_update', set=PROFILE)
        plan = content.read_resource(h.ctx, 'garage-audio://measure/plan')
        self.assertIsNone(plan['session_id'])
        self.assertGreaterEqual(len(plan['plan']['positions']), 5)
        sid = call(h, 'measure_session_start', positions=4)['session_id']
        self.assertEqual(content.read_resource(h.ctx, 'garage-audio://measure/plan')['session_id'], sid)
        self.assertEqual(content.read_resource(h.ctx, 'garage-audio://measure/session')['phase'], 'await_placement')

    def test_guided_prompt_requires_confirmation(self):
        text = content.get_prompt('guided-room-measurement', {'channels': 'left,right', 'units': 'imperial'})['text']
        self.assertIn('left,right', text)
        self.assertIn('imperial', text)
        self.assertIn('user_confirmed=true only then', text)
        self.assertIn('measure_abort', text)
        self.assertIn('Never assume the mic is placed', text)
        with self.assertRaises(OpsError):
            content.get_prompt('guided-room-measurement', {'volume': 'max'})


if __name__ == '__main__':
    unittest.main()
