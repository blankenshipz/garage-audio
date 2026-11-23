"""Ops MCP measurement safety: gate, confirmation, level cap, gentle start and stepwise level check, sub
frequency cap, max duration, one-at-a-time, abort (only our own children) and the watchdog, preflight
refusals and configuration that can only tighten. Synthetic only (fakes, simulated room, sleep processes).
"""
import importlib.util
import json
import math
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

from garage_audio.ops_mcp import measure, safety, tools
from garage_audio.ops_mcp.config import DEFAULT, Config, _merge
from garage_audio.ops_mcp.errors import ConfigError, OpsError

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

MEASURE_TOOLS = ('measure_noise_check', 'measure_level_check', 'measure_sweep')
CONFIRM = {'measure_level_check': {'channel': 'left', 'user_confirmed': True},
           'measure_sweep': {'channel': 'left', 'user_confirmed': True}, 'measure_noise_check': {}}


def config_with(measurement, **top):
    tmp = tempfile.mkdtemp(dir='/tmp/opencode' if os.path.isdir('/tmp/opencode') else None)
    return Config(_merge(DEFAULT, {'state_dir': tmp + '/state', 'supervisor': {'status_root': tmp},
                                   'measurement': measurement, **top}))


class Gate(unittest.TestCase):
    def test_measure_needs_allow_write_and_allow_measure(self):
        for write, meas in ((False, False), (True, False), (False, True)):
            h = fakes.Harness(self, {'allow_write': write, 'allow_measure': meas})
            for name in MEASURE_TOOLS:
                with self.subTest(write=write, measure=meas, tool=name), self.assertRaises(OpsError) as caught:
                    tools.call(h.ctx, name, CONFIRM[name])
                self.assertEqual(caught.exception.code, 'write_disabled')
                self.assertIn('allow_measure=true', caught.exception.message)
            listed = {t['name']: t for t in tools.describe(h.config)}
            self.assertFalse(listed['measure_sweep']['enabled'])
            self.assertTrue(listed['measure_abort']['enabled'])  # stopping is always allowed
            self.assertTrue(listed['measure_status']['enabled'])

    def test_access_levels_and_annotations(self):
        access = {t.name: t.access for t in tools.TOOLS}
        for name in MEASURE_TOOLS:
            self.assertEqual(access[name], tools.MEASURE)
        self.assertEqual(access['measure_abort'], tools.STOP)
        h = fakes.Harness(self, {'allow_write': True, 'allow_measure': True})
        listed = {t['name']: t for t in tools.describe(h.config)}
        self.assertTrue(listed['measure_sweep']['description'].startswith('[MEASURE] '))
        self.assertTrue(listed['measure_abort']['description'].startswith('[STOP] '))
        self.assertTrue(listed['measure_sweep']['enabled'])

    def test_measure_default_off(self):
        self.assertFalse(DEFAULT['allow_measure'])
        self.assertEqual(DEFAULT['measurement']['backend'], 'builtin')

    @unittest.skipIf(sim is None, 'numpy (measurement extra) not installed')
    def test_audible_steps_need_user_confirmation(self):
        h = sim.harness(fakes, self)
        for name in ('measure_level_check', 'measure_sweep'):
            for confirmed in (False, None):
                args = {'channel': 'left', 'user_confirmed': confirmed} if confirmed is not None else {'channel': 'left'}
                with self.subTest(tool=name, confirmed=confirmed), self.assertRaises(OpsError) as caught:
                    tools.call(h.ctx, name, args)
                expected = 'confirmation_required' if confirmed is not None else 'invalid_argument'  # schema: required
                self.assertEqual(caught.exception.code, expected)
        self.assertEqual(h.room.played, [])


@unittest.skipIf(sim is None, 'numpy (measurement extra) not installed')
class Levels(unittest.TestCase):
    def test_hard_cap_cannot_be_exceeded(self):
        h = sim.harness(fakes, self)
        for level in (-3.0, 0.0):
            with self.subTest(level=level), self.assertRaises(OpsError) as caught:
                tools.call(h.ctx, 'measure_sweep', {'channel': 'left', 'user_confirmed': True, 'level_dbfs': level})
            self.assertEqual(caught.exception.code, 'safety_limit')
        self.assertEqual(h.room.played, [])

    def test_sweep_limited_to_start_level_until_level_check(self):
        h = sim.harness(fakes, self)
        policy = h.config.measure_policy
        with self.assertRaises(OpsError) as caught:
            tools.call(h.ctx, 'measure_sweep', {'channel': 'left', 'user_confirmed': True,
                                                'level_dbfs': policy.start_level_dbfs + 1})
        self.assertEqual(caught.exception.code, 'safety_limit')
        result = tools.call(h.ctx, 'measure_sweep', {'channel': 'left', 'user_confirmed': True, 'sweep_seconds': 1.5})
        self.assertEqual(result['settings']['level_dbfs'], policy.start_level_dbfs)
        self.assertLessEqual(max(h.room.played[-1]['peak_dbfs'].values()), policy.start_level_dbfs + 0.01)

    def test_start_level_cannot_be_raised(self):
        h = sim.harness(fakes, self)
        with self.assertRaises(OpsError) as caught:
            tools.call(h.ctx, 'measure_level_check', {'channel': 'left', 'user_confirmed': True,
                                                      'start_level_dbfs': h.config.measure_policy.start_level_dbfs + 3})
        self.assertEqual(caught.exception.code, 'safety_limit')
        self.assertEqual(h.room.played, [])

    def assert_steps_safe(self, h, result):
        policy = h.config.measure_policy
        levels = [s['level_dbfs'] for s in result['steps']]
        self.assertEqual(levels[0], policy.start_level_dbfs)
        for before, after in zip(levels, levels[1:]):
            self.assertGreater(after, before)
            self.assertLessEqual(after - before, policy.level_step_max_db + 1e-9)
        self.assertLessEqual(max(levels), policy.sweep_level_max_dbfs)
        self.assertLessEqual(len(levels), measure.MAX_LEVEL_STEPS)
        for played in h.room.played:
            self.assertLessEqual(max(played['peak_dbfs'].values()), policy.sweep_level_max_dbfs + 0.01)
        return levels

    def test_quiet_room_stops_at_target_snr(self):
        h = sim.harness(fakes, self)
        result = tools.call(h.ctx, 'measure_level_check', {'channel': 'left', 'user_confirmed': True})
        self.assertEqual(result['stop_reason'], 'target_snr')
        self.assertTrue(result['ok'])
        levels = self.assert_steps_safe(h, result)
        self.assertEqual(result['chosen_level_dbfs'], levels[-1])

    def test_noisy_room_steps_up_gradually(self):
        h = sim.harness(fakes, self, room=sim.default_room(noise_dbfs=-52.0, loop=(3, 1)))
        result = tools.call(h.ctx, 'measure_level_check', {'channel': 'left', 'user_confirmed': True})
        levels = self.assert_steps_safe(h, result)
        self.assertGreater(len(levels), 2, result)
        self.assertIn(result['stop_reason'], ('target_snr', 'level_cap', 'max_steps'))
        # A later sweep may use the confirmed level but not more.
        allowed = result['chosen_level_dbfs']
        with self.assertRaises(OpsError):
            tools.call(h.ctx, 'measure_sweep', {'channel': 'left', 'user_confirmed': True, 'level_dbfs': allowed + 1})
        sweep = tools.call(h.ctx, 'measure_sweep', {'channel': 'left', 'user_confirmed': True, 'sweep_seconds': 1.5})
        self.assertEqual(sweep['settings']['level_dbfs'], allowed)

    def test_hot_mic_stops_for_headroom(self):
        # -40 dBFS tone, -6 dB speaker, +43 dB mic gain: about -3 dBFS at the mic, inside the -6 dBFS headroom limit.
        h = sim.harness(fakes, self, room=sim.default_room(mic_gain_db=43.0, loop=(3, 1)))
        result = tools.call(h.ctx, 'measure_level_check', {'channel': 'left', 'user_confirmed': True})
        self.assertEqual(result['stop_reason'], 'capture_headroom')
        self.assertTrue(result['ok'])
        clipping = sim.harness(fakes, self, room=sim.default_room(mic_gain_db=60.0, loop=(3, 1)))
        clipped = tools.call(clipping.ctx, 'measure_level_check', {'channel': 'left', 'user_confirmed': True})
        self.assertEqual(clipped['stop_reason'], 'capture_clipped')
        self.assertEqual(clipped['chosen_level_dbfs'], h.config.measure_policy.start_level_dbfs - 12.0)
        self.assertFalse(clipped['ok'])
        self.assert_steps_safe(h, result)
        self.assertLessEqual(result['chosen_level_dbfs'], h.config.measure_policy.start_level_dbfs)

    def test_tightened_cap_is_honoured(self):
        h = sim.harness(fakes, self, room=sim.default_room(noise_dbfs=-40.0, loop=(3, 1)),
                        measurement={'limits': {'sweep_level_max_dbfs': -34.0}})
        result = tools.call(h.ctx, 'measure_level_check', {'channel': 'left', 'user_confirmed': True})
        self.assertEqual(result['level_cap_dbfs'], -34.0)
        self.assertLessEqual(max(s['level_dbfs'] for s in result['steps']), -34.0)
        self.assertEqual(result['stop_reason'], 'level_cap')

    def test_generated_file_peak_is_checked(self):
        h = sim.harness(fakes, self)
        import numpy as np
        with self.assertRaises(OpsError) as caught:
            measure._peak_check(measure.math_module(), np.ones((10, 2)) * 0.9, h.config.measure_policy)
        self.assertEqual(caught.exception.code, 'safety_limit')


@unittest.skipIf(sim is None, 'numpy (measurement extra) not installed')
class SweepLimits(unittest.TestCase):
    def test_sub_upper_frequency_is_capped(self):
        h = sim.harness(fakes, self)
        result = tools.call(h.ctx, 'measure_sweep', {'channel': 'sub', 'user_confirmed': True, 'sweep_seconds': 2})
        cap = h.config.measure_policy.sub_sweep_max_hz
        self.assertEqual(result['settings']['freq_max_hz'], cap)
        self.assertTrue(any('sub sweep upper frequency limited' in n for n in result['notes']))
        # Spectral check of what was actually played on the sub output: nothing well above the cap.
        import numpy as np
        played = h.room.last_play[:, 2]
        spectrum = np.abs(np.fft.rfft(played))
        freqs = np.fft.rfftfreq(len(played), 1 / 48000)
        above = spectrum[freqs > cap * 1.5].max() / spectrum.max()
        self.assertLess(20 * np.log10(above), -40)

    def test_sweep_range_limits(self):
        h = sim.harness(fakes, self)
        for args in ({'freq_min_hz': 5}, {'freq_max_hz': 23000}, {'sweep_seconds': 25}):
            with self.subTest(args=args), self.assertRaises(OpsError) as caught:
                tools.call(h.ctx, 'measure_sweep', {'channel': 'left', 'user_confirmed': True, **args})
            self.assertEqual(caught.exception.code, 'safety_limit')
        with self.assertRaises(OpsError) as caught:
            tools.call(h.ctx, 'measure_sweep', {'channel': 'left', 'user_confirmed': True, 'freq_min_hz': 100,
                                                'freq_max_hz': 300})
        self.assertEqual(caught.exception.code, 'invalid_argument')
        self.assertEqual(h.room.played, [])

    def test_max_duration_refused_before_playing(self):
        h = sim.harness(fakes, self, measurement={'limits': {'max_duration_seconds': 8.0, 'sweep_seconds_max': 8.0}})
        with self.assertRaises(OpsError) as caught:
            tools.call(h.ctx, 'measure_sweep', {'channel': 'left', 'user_confirmed': True, 'sweep_seconds': 8})
        self.assertEqual(caught.exception.code, 'safety_limit')
        self.assertIn('max_duration_seconds', caught.exception.message)
        self.assertEqual(h.room.played, [])

    def test_level_check_respects_duration_budget(self):
        h = sim.harness(fakes, self, room=sim.default_room(noise_dbfs=-30.0, loop=(3, 1)),
                        measurement={'limits': {'max_duration_seconds': 4.0, 'sweep_seconds_max': 4.0}})
        result = tools.call(h.ctx, 'measure_level_check', {'channel': 'left', 'user_confirmed': True})
        self.assertEqual(result['stop_reason'], 'max_duration')
        self.assertLess(len(result['steps']), measure.MAX_LEVEL_STEPS)


@unittest.skipIf(sim is None, 'numpy (measurement extra) not installed')
class Preflight(unittest.TestCase):
    def refused(self, h, code):
        with self.assertRaises(OpsError) as caught:
            tools.call(h.ctx, 'measure_sweep', {'channel': 'left', 'user_confirmed': True})
        self.assertEqual(caught.exception.code, code, caught.exception.message)
        self.assertEqual(h.room.played, [])
        return caught.exception

    def test_owner_not_ready(self):
        h = sim.harness(fakes, self)
        h.recovery_state('recovering')
        self.refused(h, 'stack_not_ready')

    def test_camilla_unreachable_or_stopped(self):
        h = sim.harness(fakes, self)
        h.camilla.reachable = False
        self.refused(h, 'stack_not_ready')
        h2 = sim.harness(fakes, self)
        h2.camilla.get_state = lambda: 'Inactive'
        self.refused(h2, 'stack_not_ready')

    def test_muted_is_not_unmuted(self):
        h = sim.harness(fakes, self)
        h.camilla.mute = True
        self.refused(h, 'stack_muted')
        self.assertNotIn(('set_mute', False), h.camilla.events)

    def test_music_playing(self):
        h = sim.harness(fakes, self)
        h.ma.players[1]['state'] = 'playing'
        self.refused(h, 'stack_busy')

    def test_sink_probe(self):
        h = sim.harness(fakes, self, measurement={'sink_check_argv': [sys.executable, '-c', 'raise SystemExit(3)']})
        error = self.refused(h, 'sink_unavailable')
        self.assertEqual(error.details['exit_status'], 3)
        ok = sim.harness(fakes, self, measurement={'sink_check_argv': [sys.executable, '-c', 'pass']})
        tools.call(ok.ctx, 'measure_sweep', {'channel': 'left', 'user_confirmed': True, 'sweep_seconds': 1.5})

    def test_unconfigured_player(self):
        h = sim.harness(fakes, self)
        h.ctx.measure_capture = None
        self.refused(h, 'measurement_unconfigured')

    def test_phone_capture_is_a_stub(self):
        h = sim.harness(fakes, self, measurement={'capture': 'phone'})
        h.ctx.measure_capture = None
        self.refused(h, 'not_implemented')

    def test_music_assistant_configured_but_unreachable_fails_closed(self):
        h = sim.harness(fakes, self)
        h.ma.reachable = False
        error = self.refused(h, 'ma_unreachable')
        self.assertIn('cannot be confirmed that nothing else is playing', error.message)
        self.assertEqual(error.details['cause'], 'ma_unreachable')
        with self.assertRaises(OpsError) as caught:  # the level check (audible) is refused the same way
            tools.call(h.ctx, 'measure_level_check', {'channel': 'left', 'user_confirmed': True})
        self.assertEqual(caught.exception.code, 'ma_unreachable')
        self.assertEqual(h.room.played, [])

    def test_music_assistant_not_configured_proceeds_with_a_note(self):
        h = sim.harness(fakes, self)

        def not_configured(*_args, **_kwargs):
            raise OpsError('ma_not_configured', 'music_assistant.token_file is not configured')
        h.ma.call = not_configured
        result = tools.call(h.ctx, 'measure_sweep', {'channel': 'left', 'user_confirmed': True, 'sweep_seconds': 1.5})
        self.assertTrue(any('Music Assistant is not configured' in n for n in result['notes']))
        self.assertEqual(len(h.room.played), 1)


def confirm_level(h, channel, level, calibration_id=None):
    """Record a confirmed level for ``channel`` bound to the stack as it is now (as a level check would)."""
    _mm, stack, _notes = measure._preflight(h.ctx, channel)
    measure.runtime(h.ctx).levels[channel] = {
        'level_dbfs': level, 'at': h.ctx.clock(), 'reason': 'test', 'valid_for_seconds': 1800.0,
        'snapshot': measure.level_snapshot(h.ctx, channel, stack, calibration_id)}


def spectrum_above(play, column, freq, rate=48000):
    """Largest spectral magnitude of ``play[:, column]`` above ``freq``, in dB relative to its maximum."""
    import numpy as np
    spectrum = np.abs(np.fft.rfft(play[:, column]))
    freqs = np.fft.rfftfreq(len(play), 1 / rate)
    return 20 * np.log10(spectrum[freqs > freq].max() / spectrum.max())


@unittest.skipIf(sim is None, 'numpy (measurement extra) not installed')
class AcousticReference(unittest.TestCase):
    def harness(self, **measurement):
        return sim.harness(fakes, self, measurement={'loopback': None, 'capture_channels': 1,
                                                     'limits': {'sweep_level_max_dbfs': -12.0}, **measurement})

    def test_unconfirmed_reference_chirp_stays_at_start_level(self):
        # Right confirmed at -12 dBFS, left (the timing reference) never level-checked: the chirp on
        # the left must not follow the right sweep up to -12; it stays at the gentle start level.
        h = self.harness()
        start = h.config.measure_policy.start_level_dbfs
        confirm_level(h, 'right', -12.0)
        result = tools.call(h.ctx, 'measure_sweep', {'channel': 'right', 'user_confirmed': True, 'sweep_seconds': 1.5})
        self.assertEqual(result['settings']['level_dbfs'], -12.0)
        peaks = h.room.played[-1]['peak_dbfs']
        self.assertLessEqual(peaks[0], start + 0.01)       # the reference chirp on the left output
        self.assertGreater(peaks[1], -12.5)                # the right sweep itself at its confirmed level

    def test_confirmed_reference_chirp_limited_to_its_own_level(self):
        h = self.harness()
        confirm_level(h, 'right', -12.0)
        confirm_level(h, 'left', -30.0)
        tools.call(h.ctx, 'measure_sweep', {'channel': 'right', 'user_confirmed': True, 'sweep_seconds': 1.5})
        peaks = h.room.played[-1]['peak_dbfs']
        self.assertLessEqual(peaks[0], -30.0 + 0.01)
        self.assertGreater(peaks[0], -31.0)

    def test_expired_reference_confirmation_falls_back_to_start_level(self):
        h = self.harness()
        confirm_level(h, 'left', -20.0)
        h.clock.advance(h.config.measure_policy.level_ttl_seconds + 1)
        confirm_level(h, 'right', -12.0)
        tools.call(h.ctx, 'measure_sweep', {'channel': 'right', 'user_confirmed': True, 'sweep_seconds': 1.5})
        self.assertLessEqual(h.room.played[-1]['peak_dbfs'][0], h.config.measure_policy.start_level_dbfs + 0.01)


@unittest.skipIf(sim is None, 'numpy (measurement extra) not installed')
class SubOnlyOutputs(unittest.TestCase):
    """The sub cap follows the outputs, not the channel name: sim map L/R/sub = 0/1/2, sum = [0, 1, 2]."""

    def test_sub_only_output_detection(self):
        def m(**cmap):
            return {'channel_map': cmap}
        dedicated = m(left=[0], right=[1], sub=[2], sum=[0, 1, 2])
        self.assertEqual(measure._sub_only_outputs(dedicated), {2})
        self.assertTrue(measure.sub_limited(dedicated, 'sum'))
        self.assertTrue(measure.sub_limited(dedicated, 'sub'))
        self.assertFalse(measure.sub_limited(dedicated, 'left'))
        shared = m(left=[0], right=[1], sub=[0, 1], sum=[0, 1])  # the sub fed from the main outputs
        self.assertEqual(measure._sub_only_outputs(shared), set())
        self.assertFalse(measure.sub_limited(shared, 'sum'))
        self.assertTrue(measure.sub_limited(shared, 'sub'))
        odd = m(left=[0], right=[1, 2], sub=[2, 3], sum=[0, 3])
        self.assertTrue(measure.sub_limited(odd, 'sum'))
        self.assertFalse(measure.sub_limited(odd, 'right'))

    def test_sum_sweep_including_the_sub_output_is_capped(self):
        h = sim.harness(fakes, self)
        cap = h.config.measure_policy.sub_sweep_max_hz
        result = tools.call(h.ctx, 'measure_sweep', {'channel': 'sum', 'user_confirmed': True, 'sweep_seconds': 2})
        self.assertEqual(result['settings']['freq_max_hz'], cap)
        self.assertTrue(any('sum sweep upper frequency limited' in n and 'sub-only output' in n
                            for n in result['notes']))
        for column in (0, 1, 2):
            with self.subTest(output=column):
                self.assertLess(spectrum_above(h.room.last_play, column, cap * 1.5), -40)

    def test_sum_without_sub_only_output_is_not_capped(self):
        h = sim.harness(fakes, self, measurement={'channel_map': {'left': [0], 'right': [1], 'sub': [0, 1],
                                                                  'sum': [0, 1]}})
        result = tools.call(h.ctx, 'measure_sweep', {'channel': 'sum', 'user_confirmed': True, 'sweep_seconds': 2})
        self.assertGreater(result['settings']['freq_max_hz'], h.config.measure_policy.sub_sweep_max_hz)

    def test_level_check_tone_on_sum_and_dedicated_sub_is_low(self):
        h = sim.harness(fakes, self)
        cap = h.config.measure_policy.sub_sweep_max_hz
        for channel in ('sum', 'sub'):
            with self.subTest(channel=channel):
                out = tools.call(h.ctx, 'measure_level_check', {'channel': channel, 'user_confirmed': True})
                self.assertLessEqual(out['tone_hz'], cap / 2)
                self.assertLess(spectrum_above(h.room.last_play, 2, cap * 1.5), -40)
        left = tools.call(h.ctx, 'measure_level_check', {'channel': 'left', 'user_confirmed': True})
        self.assertEqual(left['tone_hz'], 1000.0)

    def test_timing_reference_must_not_include_a_sub_only_output(self):
        for ref in ('sub', 'sum'):
            with self.subTest(ref=ref), self.assertRaises(ConfigError):
                sim.harness(fakes, self, measurement={'timing_reference': ref, 'loopback': None,
                                                      'capture_channels': 1})
        shared = sim.harness(fakes, self, measurement={'timing_reference': 'sum', 'loopback': None,
                                                       'capture_channels': 1,
                                                       'channel_map': {'left': [0], 'right': [1], 'sub': [0, 1],
                                                                       'sum': [0, 1]}})
        self.assertEqual(shared.config.measurement['timing_reference'], 'sum')

    def test_reference_chirp_on_a_sub_only_output_is_capped(self):
        # Defence in depth behind the configuration check: force such a reference and look at the chirp.
        h = sim.harness(fakes, self, measurement={'loopback': None, 'capture_channels': 1})
        h.config.measurement['timing_reference'] = 'sum'
        cap = h.config.measure_policy.sub_sweep_max_hz
        result = tools.call(h.ctx, 'measure_sweep', {'channel': 'right', 'user_confirmed': True, 'sweep_seconds': 1.5})
        self.assertLessEqual(result['timing']['reference_band_hz'][1], cap)
        self.assertTrue(math.isfinite(result['timing']['arrival_ms']))
        self.assertIsNotNone(h.room.last_play)
        self.assertLess(spectrum_above(h.room.last_play, 2, cap * 1.5), -40)  # output 2 carries only the chirp


@unittest.skipIf(sim is None, 'numpy (measurement extra) not installed')
class LevelExpiry(unittest.TestCase):
    def setUp(self):
        self.h = sim.harness(fakes, self, room=sim.default_room(noise_dbfs=-52.0, loop=(3, 1)))
        self.start = self.h.config.measure_policy.start_level_dbfs

    def level_check(self, **extra):
        out = tools.call(self.h.ctx, 'measure_level_check', {'channel': 'left', 'user_confirmed': True, **extra})
        self.assertGreater(out['chosen_level_dbfs'], self.start, out)  # the noisy room needs more than the start
        self.assertEqual(out['confirmation']['valid_for_seconds'], self.h.config.measure_policy.level_ttl_seconds)
        return out['chosen_level_dbfs']

    def sweep_level(self, **extra):
        out = tools.call(self.h.ctx, 'measure_sweep', {'channel': 'left', 'user_confirmed': True, 'sweep_seconds': 1.5,
                                                       **extra})
        return out['settings']['level_dbfs']

    def refused_at(self, level, expect, **extra):
        with self.assertRaises(OpsError) as caught:
            self.sweep_level(level_dbfs=level, **extra)
        self.assertEqual(caught.exception.code, 'safety_limit')
        self.assertIn(expect, caught.exception.message)

    def test_confirmation_expires(self):
        confirmed = self.level_check()
        self.h.clock.advance(self.h.config.measure_policy.level_ttl_seconds - 5)
        self.assertEqual(self.sweep_level(), confirmed)
        self.h.clock.advance(10)
        self.assertEqual(self.sweep_level(), self.start)
        self.refused_at(confirmed, 'expired')
        status = tools.call(self.h.ctx, 'measure_status', {})['confirmed_levels']['left']
        self.assertEqual(status['expires_in_seconds'], 0.0)

    def test_clock_step_back_invalidates(self):
        confirmed = self.level_check()
        self.h.clock.advance(-60)
        self.refused_at(confirmed, 'expired')

    def test_volume_change_invalidates(self):
        confirmed = self.level_check()
        self.h.camilla.volume += 6.0
        self.assertEqual(self.sweep_level(), self.start)
        self.refused_at(confirmed, 'main_volume_db')
        self.h.camilla.volume -= 6.0  # back to what was confirmed: valid again
        self.assertEqual(self.sweep_level(), confirmed)

    def test_dsp_routing_change_invalidates(self):
        confirmed = self.level_check()
        self.h.camilla.config['mixers']['to_out'] = {'channels': {'in': 2, 'out': 4}, 'mapping': []}
        self.refused_at(confirmed, 'dsp_mixers_sha256')

    def test_calibration_change_invalidates(self):
        confirmed = self.level_check()
        text = '"Sens Factor =-1dB, SERNO: 0000000"\n' + '\n'.join(f'{f} 0.0' for f in (20, 100, 1000, 10000, 20000))
        cal = tools.call(self.h.ctx, 'measure_import_calibration', {'text': text, 'name': 'mic'})['calibration_id']
        self.refused_at(confirmed, 'calibration_id', calibration_id=cal)
        again = self.level_check(calibration_id=cal)
        self.assertEqual(self.sweep_level(calibration_id=cal), again)
        played = len(self.h.room.played)
        with self.assertRaises(OpsError) as caught:  # a level check bound to an unknown calibration is refused
            tools.call(self.h.ctx, 'measure_level_check', {'channel': 'left', 'user_confirmed': True,
                                                           'calibration_id': cal[:-8] + ('0' * 8 if cal[-8:] != '0' * 8
                                                                                         else '1' * 8)})
        self.assertEqual(caught.exception.code, 'not_found')
        self.assertEqual(len(self.h.room.played), played)  # refused before anything was played

    def test_unreadable_volume_means_start_level(self):
        confirmed = self.level_check()

        def unreadable():
            raise OpsError('camilladsp_unreachable', 'no')
        self.h.camilla.get_volume = unreadable
        self.refused_at(confirmed, 'could not be read')

    def test_snapshot_fields_and_legacy_entries(self):
        h = self.h
        _mm, stack, _notes = measure._preflight(h.ctx, 'left')
        snap = measure.level_snapshot(h.ctx, 'left', stack, None)
        self.assertEqual(set(snap), {'main_volume_db', 'mute', 'outputs', 'dsp_mixers_sha256', 'capture_sha256',
                                     'calibration_id'})
        entry = {'level_dbfs': -20.0, 'at': h.ctx.clock(), 'snapshot': dict(snap)}
        self.assertEqual(measure._confirmed_level(h.ctx, 'left', {'levels': {'left': entry}}, snap), (-20.0, None))
        for key, value in (('outputs', [0, 2]), ('capture_sha256', 'x'), ('mute', None)):
            with self.subTest(changed=key):
                level, why = measure._confirmed_level(h.ctx, 'left', {'levels': {'left': entry}},
                                                      {**snap, key: value})
                self.assertIsNone(level)
                self.assertIn(key, why)
        level, why = measure._confirmed_level(h.ctx, 'left', {'levels': {'left': -20.0}}, snap)  # old session file
        self.assertIsNone(level)
        self.assertIn('predates', why)

    def test_session_level_is_bound_too(self):
        h = self.h
        sid = tools.call(h.ctx, 'measure_session_start', {'positions': 4, 'channels': ['left']})['session_id']
        tools.call(h.ctx, 'measure_session_confirm_placement', {'session_id': sid, 'position_id': 'P0'})
        tools.call(h.ctx, 'measure_noise_check', {'session_id': sid, 'seconds': 1, 'accept_noisy': True})
        out = tools.call(h.ctx, 'measure_level_check', {'channel': 'left', 'session_id': sid, 'user_confirmed': True})
        stored = tools.call(h.ctx, 'measure_session_status', {'session_id': sid})['levels']['left']
        self.assertEqual(stored['level_dbfs'], out['chosen_level_dbfs'])
        self.assertIn('snapshot', stored)
        measure.runtime(h.ctx).levels.clear()  # e.g. a restarted server: only the session file remains
        h.clock.advance(h.config.measure_policy.level_ttl_seconds + 1)
        with self.assertRaises(OpsError) as caught:
            tools.call(h.ctx, 'measure_sweep', {'channel': 'left', 'session_id': sid, 'user_confirmed': True,
                                                'sweep_seconds': 1.5, 'level_dbfs': out['chosen_level_dbfs']})
        self.assertIn('expired', caught.exception.message)


def sleeper_argv(pid_file):
    code = ('import os, sys, time\n'
            'open(sys.argv[1], "w").write(str(os.getpid()))\n'
            'time.sleep(60)\n')
    return [sys.executable, '-c', code, pid_file, '{file}']


def alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


@unittest.skipIf(sim is None, 'numpy (measurement extra) not installed')
class AbortAndWatchdog(unittest.TestCase):
    def process_harness(self, **measurement):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.player_pid = Path(tmp.name) / 'player.pid'
        self.recorder_pid = Path(tmp.name) / 'recorder.pid'
        h = sim.harness(fakes, self, measurement={'play_argv': sleeper_argv(str(self.player_pid)),
                                                  'record_argv': sleeper_argv(str(self.recorder_pid)), **measurement})
        h.ctx.measure_capture = None  # the real ProcessCapture
        return h

    def unrelated(self):
        proc = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])
        self.addCleanup(proc.wait)
        self.addCleanup(proc.kill)
        return proc

    def run_in_thread(self, h, args):
        out = {}

        def run():
            try:
                out['result'] = tools.call(h.ctx, 'measure_sweep', args)
            except OpsError as error:
                out['code'] = error.code
            out['ended'] = time.monotonic()
        worker = threading.Thread(target=run)
        worker.start()
        return worker, out

    def wait_for(self, path, timeout=10):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if path.exists() and path.read_text().strip():
                return int(path.read_text())
            time.sleep(0.02)
        self.fail(f'{path.name} never appeared')

    def test_abort_stops_only_our_children_promptly(self):
        h = self.process_harness()
        bystander = self.unrelated()
        worker, out = self.run_in_thread(h, {'channel': 'left', 'user_confirmed': True, 'sweep_seconds': 4})
        player = self.wait_for(self.player_pid)
        recorder = int(self.recorder_pid.read_text())
        with self.assertRaises(OpsError) as caught:  # one measurement at a time
            tools.call(h.ctx, 'measure_sweep', {'channel': 'right', 'user_confirmed': True})
        self.assertEqual(caught.exception.code, 'busy')
        started = time.monotonic()
        result = tools.call(h.ctx, 'measure_abort', {})
        worker.join(10)
        self.assertFalse(worker.is_alive())
        self.assertTrue(result['aborted'])
        self.assertEqual(result['scope'], 'this server')
        self.assertEqual(out.get('code'), 'measurement_aborted')
        self.assertLess(out['ended'] - started, 2.0)
        self.assertFalse(alive(player))
        self.assertFalse(alive(recorder))
        self.assertIsNone(bystander.poll())  # an unrelated process is untouched
        self.assertIsNone(tools.call(h.ctx, 'measure_status', {})['active'])
        self.assertFalse(list((Path(h.config.state_dir) / measure.WORK_DIR).iterdir()))

    def test_abort_from_another_server_process(self):
        h = self.process_harness()
        worker, out = self.run_in_thread(h, {'channel': 'left', 'user_confirmed': True, 'sweep_seconds': 4})
        player = self.wait_for(self.player_pid)
        other = sim.harness(fakes, self, state_dir=str(h.state_dir))  # a second server on the same state dir
        with self.assertRaises(OpsError) as caught:  # the cross-process lock
            tools.call(other.ctx, 'measure_noise_check', {})
        self.assertEqual(caught.exception.code, 'busy')
        result = tools.call(other.ctx, 'measure_abort', {})
        self.assertEqual(result['scope'], 'another ops server process')
        worker.join(10)
        self.assertEqual(out.get('code'), 'measurement_aborted')
        self.assertFalse(alive(player))

    def test_abort_without_measurement(self):
        h = fakes.Harness(self)
        self.assertFalse(tools.call(h.ctx, 'measure_abort', {})['aborted'])

    def test_watchdog_stops_overrunning_player(self):
        original = measure.WATCHDOG_SLACK_S
        measure.WATCHDOG_SLACK_S = 0.3
        self.addCleanup(setattr, measure, 'WATCHDOG_SLACK_S', original)
        h = self.process_harness()
        worker, out = self.run_in_thread(h, {'channel': 'left', 'user_confirmed': True, 'sweep_seconds': 1.5})
        player = self.wait_for(self.player_pid)
        worker.join(20)
        self.assertFalse(worker.is_alive())
        self.assertEqual(out.get('code'), 'measurement_timeout')
        self.assertFalse(alive(player))

    def test_failing_player_reports_and_cleans_up(self):
        h = sim.harness(fakes, self, measurement={'play_argv': [sys.executable, '-c', 'raise SystemExit(2)', '{file}'],
                                                  'record_argv': sleeper_argv('/dev/null')})
        h.ctx.measure_capture = None
        with self.assertRaises(OpsError) as caught:
            tools.call(h.ctx, 'measure_sweep', {'channel': 'left', 'user_confirmed': True, 'sweep_seconds': 1.5})
        self.assertEqual(caught.exception.code, 'playback_failed')
        self.assertIsNone(measure.runtime(h.ctx).active)


class MeasurementConfig(unittest.TestCase):
    def test_limits_can_only_tighten(self):
        for key, hard in safety.MEASURE_HARD.items():
            loose = hard + 1 if safety.MEASURE_DIRECTION[key] == 'max' else hard - 1
            with self.subTest(key=key), self.assertRaises(ConfigError):
                config_with({'limits': {key: loose}})
        cfg = config_with({'limits': {'sweep_level_max_dbfs': -24.0, 'sub_sweep_max_hz': 150.0}})
        self.assertEqual(cfg.measure_policy.sweep_level_max_dbfs, -24.0)
        self.assertEqual(cfg.measure_policy.sub_sweep_max_hz, 150.0)
        with self.assertRaises(ConfigError):
            config_with({'limits': {'not_a_limit': 1}})

    def test_unknown_keys_rejected(self):
        with self.assertRaises(ConfigError):
            config_with({'play_argz': ['pw-play', '{file}']})

    def test_player_argv_must_not_open_the_dac_or_a_shell(self):
        for argv in (['aplay', '-D', 'hw:0,0', '{file}'], ['aplay', '-D', 'plughw:CARD=X', '{file}'],
                     ['sh', '-c', 'pw-play {file}'], ['/usr/bin/env', 'pw-play', '{file}'],
                     ['sudo', 'pw-play', '{file}'], ['pw-play', '{file}', '{nope}'], 'pw-play {file}', []):
            with self.subTest(argv=argv), self.assertRaises(ConfigError):
                config_with({'play_argv': argv})
        cfg = config_with({'play_argv': ['pw-play', '--target', 'stack-sink', '{file}'],
                           'record_argv': ['pw-record', '--rate', '{rate}', '--channels', '{channels}', '{file}']})
        self.assertEqual(cfg.measurement['play_argv'][0], 'pw-play')

    def test_raw_alsa_names_and_bare_alsa_players_refused(self):
        for argv in (['aplay', '-D', 'default:CARD=DAC', '{file}'], ['aplay', '-Dplug:dac', '{file}'],
                     ['aplay', '--device=hw:1', '{file}'], ['aplay', '-D', 'plughw:1,0', '{file}'],
                     ['aplay', '-D', 'surround51:CARD=X', '{file}'], ['aplay', '-D', 'front:CARD=X', '{file}'],
                     ['aplay', '{file}'], ['/usr/bin/aplay', '-q', '{file}'], ['aplay', '-D', 'default', '{file}'],
                     ['aplay', '{file}', '-D'], ['speaker-test', '-t', 'sine', '{file}'],
                     ['pw-play', '--target', 'alsa_output.usb-dac', '{file}']):
            with self.subTest(argv=argv), self.assertRaises(ConfigError):
                config_with({'play_argv': argv})
        for argv in (['aplay', '-D', 'pipewire', '{file}'], ['aplay', '--device=pulse', '{file}'],
                     ['pw-play', '--target', 'stack-sink', '{file}']):
            with self.subTest(argv=argv):
                self.assertEqual(config_with({'play_argv': argv}).measurement['play_argv'][0], argv[0])

    def test_list_devices_argv_only_read_only_listers(self):
        for lists in ([['pw-play', '{file}']], [['aplay', '-l']], [['arecord', '-D', 'hw:0', 'x.wav']],
                      [['pw-record', 'x.wav']], [['pactl', 'set-sink-volume', '@DEFAULT_SINK@', '100%']],
                      [['wpctl', 'set-volume', '50', '1']], [['pw-cli', 'destroy', '42']], [['sh', '-c', 'ls']],
                      [['pw-dump', '--monitor']], [['arecord', '-l', 'extra']]):
            with self.subTest(lists=lists), self.assertRaises(ConfigError):
                config_with({'list_devices_argv': lists})
        ok = [['arecord', '-l'], ['arecord', '-L'], ['pw-record', '--list-targets'], ['pactl', 'list', 'sources']]
        self.assertEqual(config_with({'list_devices_argv': ok}).measurement['list_devices_argv'], ok)
        for lists in ([['pw-cli', 'ls', 'Node']], [['pw-dump', '-N']], [['wpctl', 'status']], [['pactl', 'info']]):
            with self.subTest(lists=lists):
                config_with({'list_devices_argv': lists})

    def test_level_ttl_can_only_tighten(self):
        self.assertEqual(config_with({}).measure_policy.level_ttl_seconds, 1800.0)
        self.assertEqual(config_with({'limits': {'level_ttl_seconds': 300}}).measure_policy.level_ttl_seconds, 300.0)
        for value in (3600, 5):
            with self.subTest(value=value), self.assertRaises(ConfigError):
                config_with({'limits': {'level_ttl_seconds': value}})

    def test_channel_layout_validated(self):
        for measurement in ({'channel_map': {'left': [5]}}, {'mic_channel': 2},
                            {'loopback': {'output_channel': 0, 'capture_channel': 0}},
                            {'backend': 'other'}, {'capture': 'bluetooth'}):
            with self.subTest(m=measurement), self.assertRaises(ConfigError):
                config_with(measurement)

    def test_rew_url_validated_and_default_off(self):
        self.assertIsNone(DEFAULT['measurement']['rew']['url'])
        for url in ('ftp://127.0.0.1:4735', 'http://user:pw@127.0.0.1:4735', 'http://127.0.0.1:4735/api?x=1'):
            with self.subTest(url=url), self.assertRaises(ConfigError):
                config_with({'backend': 'rew', 'rew': {'url': url}})
        with self.assertRaises(ConfigError):  # the rew backend needs a URL
            config_with({'backend': 'rew'})
        cfg = config_with({'backend': 'rew', 'rew': {'url': 'http://127.0.0.1:4735'}})
        self.assertEqual(cfg.measurement['backend'], 'rew')

    def test_public_projection_has_policy(self):
        cfg = config_with({})
        public = cfg.public()
        self.assertEqual(public['measurement']['limits']['sweep_level_max_dbfs'],
                         safety.MEASURE_DEFAULTS['sweep_level_max_dbfs'])
        json.dumps(public)


if __name__ == '__main__':
    unittest.main()
