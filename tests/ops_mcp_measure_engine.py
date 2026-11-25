"""Ops MCP measurement engine: synthetic recovery through a simulated room, calibration, quality checks,
exports, the REW backend (fake) and behaviour without numpy. Synthetic only: no audio hardware.

Stated tolerances (check ``synthetic-recovery``):
  * arrival time / delay: +-0.1 ms (loopback absolute and acoustic-reference relative)
  * magnitude: +-1.0 dB against the analytic speaker+room response over 30 Hz - 16 kHz
  * room-mode peaks: frequency within 3 %, level within 1.0 dB of the analytic peak
"""
import base64
import importlib.util
import json
import math
import os
import stat
import struct
import subprocess
import sys
import threading
import time
import unittest
import wave
from pathlib import Path

from garage_audio.ops_mcp import measure, room_model, tools
from garage_audio.ops_mcp.errors import ConfigError, OpsError

ROOT = Path(__file__).parents[1]
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
except ImportError:  # the measurement extra is optional; NoNumpy below still runs
    sim = None

DELAY_TOL_MS = 0.1
MAG_TOL_DB = 1.0
MODE_FREQ_TOL = 0.03
MODE_LEVEL_TOL_DB = 1.0
ROOM_DIMS = {'length': 5.0, 'width': 3.7, 'height': 2.6}


def sweep(h, channel, **args):
    return tools.call(h.ctx, 'measure_sweep', {'channel': channel, 'user_confirmed': True, **args})


def points(h, measurement_id):
    rec = h.ctx.history.get(measurement_id)
    return [p['frequency_hz'] for p in rec['points']], [p['db'] for p in rec['points']], rec


@unittest.skipIf(sim is None, 'numpy (measurement extra) not installed')
class SyntheticRecovery(unittest.TestCase):
    def test_loopback_timing_and_magnitude(self):
        h = sim.harness(fakes, self)
        for channel, out, delay in (('left', 0, 3.0), ('right', 1, 3.6)):
            with self.subTest(channel=channel):
                result = sweep(h, channel)
                timing = result['timing']
                self.assertEqual(timing['timing_source'], 'loopback')
                self.assertEqual(timing['timing_confidence'], 'high')
                self.assertAlmostEqual(timing['arrival_ms'], delay, delta=DELAY_TOL_MS)
                self.assertEqual(result['quality']['status'], 'ok', result['quality'])
                freqs, dbs, rec = points(h, result['measurement_id'])
                speaker = h.room.speakers[out]
                worst = max(abs(d - speaker.response_db(f)) for f, d in zip(freqs, dbs) if 30.0 <= f <= 16000.0)
                self.assertLess(worst, MAG_TOL_DB)
                self.assertTrue(all('phase_deg' in p for p in rec['points']))
                self.assertIsNotNone(rec['ops_delays_ms'])

    def test_room_modes_recovered(self):
        h = sim.harness(fakes, self)
        result = sweep(h, 'right')
        freqs, dbs, _rec = points(h, result['measurement_id'])
        speaker = h.room.speakers[1]
        fine = [20.0 * 2 ** (i / 480.0) for i in range(480 * 3)]
        analytic = [speaker.response_db(f) for f in fine]
        modes = [m for m in room_model.axial_modes(ROOM_DIMS, 100.0) if m['axis'] in ('length', 'width')]
        self.assertEqual(len(modes), 4)
        for mode in modes:
            with self.subTest(mode=mode):
                f_meas, d_meas = sim.peak_near(freqs, dbs, mode['freq_hz'])
                f_true, d_true = sim.peak_near(fine, analytic, mode['freq_hz'])
                self.assertLess(abs(f_meas / mode['freq_hz'] - 1.0), MODE_FREQ_TOL)
                self.assertLess(abs(f_meas / f_true - 1.0), MODE_FREQ_TOL)
                self.assertLess(abs(d_meas - d_true), MODE_LEVEL_TOL_DB)

    def test_acoustic_reference_timing_is_low_confidence(self):
        h = sim.harness(fakes, self, measurement={'loopback': None, 'capture_channels': 1})
        left = sweep(h, 'left')
        self.assertEqual(left['timing']['timing_source'], 'acoustic_reference')
        self.assertEqual(left['timing']['timing_confidence'], 'low')
        self.assertEqual(left['timing']['arrival_ms'], 0.0)
        right = sweep(h, 'right')
        self.assertEqual(right['timing']['timing_source'], 'acoustic_reference')
        self.assertEqual(right['timing']['timing_confidence'], 'low')
        self.assertEqual(right['timing']['reference_channel'], 'left')
        self.assertAlmostEqual(right['timing']['arrival_ms'], 0.6, delta=DELAY_TOL_MS)
        freqs, dbs, _rec = points(h, right['measurement_id'])
        worst = max(abs(d - h.room.speakers[1].response_db(f)) for f, d in zip(freqs, dbs) if 30.0 <= f <= 16000.0)
        self.assertLess(worst, MAG_TOL_DB)
        # The reference chirp never exceeds the sweep level.
        self.assertLessEqual(max(h.room.played[-1]['peak_dbfs'].values()), right['settings']['level_dbfs'] + 0.01)

    def test_alignment_from_measurements_removes_active_dsp_delay(self):
        h = sim.harness(fakes, self)
        # 1.0 ms of ops delay is active on the left channel while measuring: the simulated arrival includes it.
        h.camilla.config['filters']['ops_left_delay'] = {'type': 'Delay', 'parameters': {'delay': 1.0, 'unit': 'ms'}}
        h.room.speakers[0].delay_ms = 4.0
        ids = [sweep(h, ch)['measurement_id'] for ch in ('left', 'right')]
        proposal = tools.call(h.ctx, 'room_propose_alignment', {'measurement_ids': ids})
        self.assertEqual(proposal['delay_method'], 'measured_loopback')
        self.assertEqual(proposal['timing']['timing_confidence'], 'high')
        self.assertAlmostEqual(proposal['delay_ms']['left'], 0.6, delta=DELAY_TOL_MS)
        self.assertAlmostEqual(proposal['delay_ms']['right'], 0.0, delta=DELAY_TOL_MS)
        with self.assertRaises(OpsError):
            tools.call(h.ctx, 'room_propose_alignment', {'measurement_ids': ids, 'distances_m': {'left': 2.0}})

    def test_acoustic_alignment_removes_active_dsp_delay(self):
        h = sim.harness(fakes, self, measurement={'loopback': None, 'capture_channels': 1})
        # 2.0 ms of ops delay is active on the right channel (the reference is the left speaker).
        h.camilla.config['filters']['ops_right_delay'] = {'type': 'Delay', 'parameters': {'delay': 2.0, 'unit': 'ms'}}
        h.room.speakers[1].delay_ms = 3.6 + 2.0
        ids = [sweep(h, ch, sweep_seconds=2)['measurement_id'] for ch in ('left', 'right')]
        proposal = tools.call(h.ctx, 'room_propose_alignment', {'measurement_ids': ids})
        self.assertEqual(proposal['delay_method'], 'measured_acoustic_reference')
        self.assertEqual(proposal['timing']['timing_confidence'], 'low')
        self.assertIn('LOW', proposal['note'])
        self.assertAlmostEqual(proposal['delay_ms']['left'], 0.6, delta=DELAY_TOL_MS)
        self.assertAlmostEqual(proposal['delay_ms']['right'], 0.0, delta=DELAY_TOL_MS)

    def test_mixed_timing_sources_are_refused(self):
        h = sim.harness(fakes, self)
        loop_id = sweep(h, 'left')['measurement_id']
        h2 = sim.harness(fakes, self, measurement={'loopback': None, 'capture_channels': 1})
        h2.ctx.history = h.ctx.history
        acoustic_id = sweep(h2, 'right')['measurement_id']
        with self.assertRaises(OpsError) as caught:
            tools.call(h.ctx, 'room_propose_alignment', {'measurement_ids': [loop_id, acoustic_id]})
        self.assertIn('mix', caught.exception.message)

    def test_calibration_flattens_mic_coloration(self):
        mic = [(5000.0, 1.0, 4.0)]
        h = sim.harness(fakes, self, room=sim.default_room(loop=(3, 1), mic_resonances=mic))
        mic_only = sim.Speaker(0.0, 0.0, mic)
        grid = [10.0 * 2 ** (i / 12.0) for i in range(12 * 11 + 1)]
        text = '"Sens Factor =-1.25dB, AGain =18dB, SERNO: 0000000"\n' + '\n'.join(
            f'{f:.3f}\t{mic_only.response_db(f):.4f}' for f in grid if f <= 20000.0)
        raw = sweep(h, 'left')  # before any calibration is installed
        cal = tools.call(h.ctx, 'measure_import_calibration', {'text': text, 'name': 'synthetic 90deg'})
        self.assertEqual(cal['orientation'], '90')
        self.assertEqual(cal['sensitivity_factor_db'], -1.25)
        self.assertEqual(cal['activated'], {'90': cal['calibration_id']})
        # No calibration_id: the active calibration for the default (90-degree) orientation is applied automatically.
        corrected = sweep(h, 'left')
        self.assertTrue(corrected['calibration_applied'])
        self.assertEqual(points(h, corrected['measurement_id'])[2]['calibration_id'], cal['calibration_id'])
        self.assertTrue(any('applied automatically' in note for note in corrected['notes']))
        speaker = h.room.speakers[0]
        for result, expect_ok in ((raw, False), (corrected, True)):
            freqs, dbs, rec = points(h, result['measurement_id'])
            worst = max(abs(d - speaker.response_db(f)) for f, d in zip(freqs, dbs) if 30.0 <= f <= 16000.0)
            self.assertEqual(worst < MAG_TOL_DB, expect_ok, worst)
        self.assertEqual(h.ctx.history.get(corrected['measurement_id'])['calibration_id'], cal['calibration_id'])


@unittest.skipIf(sim is None, 'numpy (measurement extra) not installed')
class Quality(unittest.TestCase):
    def test_clipping_is_detected(self):
        h = sim.harness(fakes, self, room=sim.default_room(loop=(3, 1), mic_gain_db=45.0))
        result = sweep(h, 'left')
        self.assertEqual(result['quality']['status'], 'redo')
        self.assertIn('clipping', [i['code'] for i in result['quality']['issues']])

    def test_disconnected_mic_is_flagged(self):
        h = sim.harness(fakes, self, room=sim.default_room(loop=(3, 1), disconnected=True))
        result = sweep(h, 'left')
        self.assertEqual(result['quality']['status'], 'redo')
        codes = {i['code'] for i in result['quality']['issues']}
        self.assertTrue(codes & {'no_clear_impulse', 'low_snr', 'no_signal'}, codes)

    def test_noisy_room_reduces_quality(self):
        h = sim.harness(fakes, self, room=sim.default_room(loop=(3, 1), noise_dbfs=-45.0))
        result = sweep(h, 'left')
        self.assertIn(result['quality']['status'], ('redo', 'warn'))

    def test_noise_check_verdicts(self):
        cases = ((sim.default_room(noise_dbfs=-85.0), 'ok'), (sim.default_room(noise_dbfs=-30.0), 'too_noisy'),
                 (sim.default_room(disconnected=True), 'suspiciously_silent'))
        for room, verdict in cases:
            with self.subTest(verdict=verdict):
                h = sim.harness(fakes, self, room=room)
                result = tools.call(h.ctx, 'measure_noise_check', {'seconds': 1})
                self.assertEqual(result['verdict'], verdict)
                self.assertEqual(room.played, [])  # the noise check plays nothing

    def test_inverse_filter_is_finite_for_narrow_sweeps(self):
        # A two-octave sweep (the narrowest allowed) and a sub-capped reference chirp leave almost no inner
        # normalisation band; the inverse filter must still be finite (it used to be NaN: median of nothing).
        import numpy as np
        from garage_audio.ops_mcp import measure_math as mm
        for f1, f2, seconds in ((60.0, 240.0, 1.0), (31.25, 250.0, 0.5), (20.0, 20000.0, 1.0)):
            with self.subTest(band=(f1, f2)):
                inv = mm.inverse_filter(mm.log_sweep(f1, f2, seconds, 48000, -30.0), f1, f2, seconds, 48000)
                self.assertTrue(np.isfinite(inv).all())
        h = sim.harness(fakes, self, room=sim.default_room(loop=(3, 1)))
        result = tools.call(h.ctx, 'measure_sweep', {'channel': 'left', 'user_confirmed': True, 'sweep_seconds': 1.5,
                                                     'freq_min_hz': 100.0, 'freq_max_hz': 400.0})
        self.assertTrue(math.isfinite(result['timing']['arrival_ms']))


@unittest.skipIf(sim is None, 'numpy (measurement extra) not installed')
class Persistence(unittest.TestCase):
    def test_exports_are_private_and_importable(self):
        h = sim.harness(fakes, self)
        result = sweep(h, 'left', label='seat L')
        detail = tools.call(h.ctx, 'measure_get', {'measurement_id': result['measurement_id']})
        directory = Path(detail['artifacts']['directory'])
        self.assertEqual(sorted(p.name for p in directory.iterdir()),
                         ['impulse.wav', 'measurement.json', 'response-rew.txt', 'response.frd'])
        self.assertEqual(stat.S_IMODE(directory.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(directory.parent.stat().st_mode), 0o700)
        for path in directory.iterdir():
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600, path.name)
        meta = json.loads((directory / 'measurement.json').read_text())
        for key in ('channel', 'settings', 'timing', 'quality', 'stack', 'measured_at', 'calibration', 'position_id'):
            self.assertIn(key, meta)
        self.assertEqual(meta['timing']['timing_source'], 'loopback')
        with wave.open(str(directory / 'impulse.wav'), 'rb') as wav:
            self.assertEqual((wav.getnchannels(), wav.getsampwidth(), wav.getframerate()), (1, 4, 48000))
            self.assertGreater(wav.getnframes(), 1000)
        rew_text = (directory / 'response-rew.txt').read_text()
        imported = tools.call(h.ctx, 'room_import_measurement', {'text': rew_text, 'channel': 'left'})
        self.assertGreater(imported['points'], 100)
        frd = (directory / 'response.frd').read_text().split('\n')[0].split()
        self.assertEqual(len(frd), 3)
        [float(v) for v in frd]
        self.assertFalse(list(Path(h.config.state_dir, 'measure-work').iterdir()))  # work files removed

    def test_bounded_retention(self):
        h = sim.harness(fakes, self, measurement={'retention': 2})
        for _ in range(3):
            sweep(h, 'left', sweep_seconds=1.5)
        root = Path(h.config.state_dir) / measure.ARTIFACT_DIR
        self.assertEqual(len([p for p in root.iterdir() if p.is_dir()]), 2)

    def test_store_failure_leaves_no_partial_directory(self):
        h = sim.harness(fakes, self)
        original = measure.export_text
        measure.export_text = lambda *a, **k: (_ for _ in ()).throw(OSError('disk full'))
        self.addCleanup(setattr, measure, 'export_text', original)
        with self.assertLogs('garage_audio.ops_mcp', 'ERROR'), self.assertRaises(OpsError):
            sweep(h, 'left', sweep_seconds=1.5)
        root = Path(h.config.state_dir) / measure.ARTIFACT_DIR
        self.assertEqual([p.name for p in root.iterdir()], [])


class Calibration(unittest.TestCase):
    def umik(self, header='"Sens Factor =-0.7dB, AGain =18dB, SERNO: 0000000"'):
        rows = [f'{10.0 * 2 ** (i / 6.0):.3f}\t{0.5 * math.sin(i / 5.0):.3f}' for i in range(61)]
        return header + '\n' + '\n'.join(rows) + '\n'

    def test_umik_style(self):
        cal = measure.parse_calibration(self.umik(), 'umik 90deg')
        self.assertEqual(cal['sensitivity_factor_db'], -0.7)
        self.assertEqual(cal['again_db'], 18.0)
        self.assertEqual(cal['orientation'], '90')
        self.assertEqual(len(cal['points']), 61)

    def test_generic_and_orientation(self):
        generic = '\n'.join(f'{f} {d}' for f, d in ((20, 1.0), (100, 0.5), (1000, 0.0), (10000, -1.0), (20000, -2.0)))
        self.assertEqual(measure.parse_calibration(generic, 'cal')['orientation'], 'unknown')
        self.assertEqual(measure.parse_calibration(generic, 'cal 0 deg')['orientation'], '0')
        self.assertEqual(measure.parse_calibration(generic, 'cal', '90')['orientation'], '90')
        self.assertIsNone(measure.parse_calibration(generic, 'cal')['sensitivity_factor_db'])
        alt = 'Sensitivity -36.5 dBFS\n' + generic
        self.assertEqual(measure.parse_calibration(alt, 'cal')['sensitivity_factor_db'], -36.5)
        short = '"Sens Factor =-.7dB, SERNO: 0000000"\n' + generic
        self.assertEqual(measure.parse_calibration(short, 'cal')['sensitivity_factor_db'], -0.7)

    def test_invalid(self):
        for text in ('', 'not a file', '100 0\n200 0\n', '20 0\n1000 50\n20000 0\n'):
            with self.subTest(text=text), self.assertRaises(OpsError) as caught:
                measure.parse_calibration(text, 'bad')
            self.assertEqual(caught.exception.code, 'calibration_invalid')

    def test_offsets_interpolate_and_hold_edges(self):
        pts = [[100.0, 0.0], [1000.0, 3.0]]
        self.assertEqual(measure.calibration_offsets(pts, [50.0, 100.0, 2000.0]), [0.0, 0.0, 3.0])
        self.assertAlmostEqual(measure.calibration_offsets(pts, [math.sqrt(1e5)])[0], 1.5)

    def test_import_and_list(self):
        h = fakes.Harness(self)
        out = tools.call(h.ctx, 'measure_import_calibration', {'text': self.umik(), 'name': 'mic', 'orientation': '0'})
        self.assertIn('horizontally', out['mic_orientation_advice'])
        listed = tools.call(h.ctx, 'measure_list_calibrations', {})['calibrations']
        self.assertEqual([c['calibration_id'] for c in listed], [out['calibration_id']])


class FakeRew:
    """Fake REW REST API. In blocking mode the measure command returns only when the measurement is
    done (after ``measure_seconds``) or cancelled; measurements are numbered from 1."""

    def __init__(self, measure_seconds=0.05, rate=48000, as_list=False, blocking=False, existing=0,
                 measure_command='Measure'):
        self.calls, self.measurements = [], [str(i + 1) for i in range(existing)]
        self.measure_seconds, self.rate, self.as_list = measure_seconds, rate, as_list
        self.blocking, self.measure_command = blocking, measure_command
        self.cancelled = threading.Event()
        self.lock = threading.Lock()

    @staticmethod
    def floats(values):
        return base64.b64encode(struct.pack(f'>{len(values)}f', *values)).decode()

    def __call__(self, method, url, body, timeout):
        path = url.split('4735', 1)[1]
        with self.lock:
            self.calls.append((method, path, body))
        if path == '/application/blocking':
            if method == 'GET':
                return 200, self.blocking
            self.blocking = body
            return 200, None
        if path == '/measurements':
            with self.lock:
                items = list(self.measurements)
            return 200, ([{'title': 't'} for _ in items] if self.as_list else {m: {'title': 't'} for m in items})
        if path == '/measure/command' and body == {'command': 'Cancel'}:
            self.cancelled.set()
            return 200, None
        if path == '/measure/command' and body == {'command': self.measure_command}:
            assert self.blocking is True, 'the measure command must run in blocking mode'
            if self.cancelled.wait(min(self.measure_seconds, timeout)):
                return 200, None  # cancelled: nothing stored
            if self.measure_seconds > timeout:
                raise OSError('timed out')
            with self.lock:
                self.measurements.append(str(len(self.measurements) + 1))
            return 200, None
        if path == '/measure/command':
            return 400, None
        if path.endswith('/frequency-response'):
            mags = [80.0 + 3.0 * math.exp(-0.5 * (math.log2(20.0 * 2 ** (i / 48.0) / 60.0) / 0.2) ** 2)
                    for i in range(48 * 10)]
            return 200, {'startFreq': 20.0, 'ppo': 48, 'magnitude': self.floats(mags),
                         'phase': self.floats([0.0] * len(mags))}
        if path.endswith('/impulse-response'):
            ir = [0.0] * 4800
            ir[240] = 1.0
            return 200, {'sampleRate': self.rate, 'startTime': -0.001, 'data': self.floats(ir)}
        return 200, None


@unittest.skipIf(sim is None, 'numpy (measurement extra) not installed')
class RewBackend(unittest.TestCase):
    def make(self, rew, **rew_config):
        h = sim.harness(fakes, self, measurement={'backend': 'rew',
                                                  'rew': {'url': 'http://127.0.0.1:4735', **rew_config}})
        h.ctx.measure_capture = None
        h.ctx.rew_transport = rew
        return h

    def test_sweep_via_rew(self):
        rew = FakeRew(existing=2)
        h = self.make(rew)
        result = sweep(h, 'left')
        self.assertEqual(result['settings']['backend'], 'rew')
        self.assertEqual(result['settings']['rew_measurement_id'], '3')  # 1-based: two existed before
        self.assertIn(('GET', '/measurements/3/frequency-response', None), rew.calls)
        # Blocking mode read first, switched on for the measure command, then restored to off.
        blocking = [(m, b) for m, p, b in rew.calls if p == '/application/blocking']
        self.assertEqual(blocking, [('GET', None), ('POST', True), ('POST', False)])
        self.assertIs(rew.blocking, False)
        self.assertTrue(any('Pro licence' in n for n in result['notes']))
        self.assertEqual(result['timing']['timing_source'], 'rew')
        self.assertAlmostEqual(result['timing']['arrival_ms'], 4.0, places=3)
        self.assertEqual(result['quality']['status'], 'warn')
        level = [b for m, p, b in rew.calls if p == '/measure/level'][0]
        self.assertLessEqual(level['value'], h.config.measure_policy.start_level_dbfs)
        self.assertTrue(any('never the DAC' in n for n in result['notes']))
        freqs, dbs, _rec = points(h, result['measurement_id'])
        self.assertAlmostEqual(sim.peak_near(freqs, dbs, 60.0)[1], 83.0, delta=0.1)
        for tool in ('measure_noise_check', 'measure_level_check'):
            with self.subTest(tool=tool), self.assertRaises(OpsError) as caught:
                tools.call(h.ctx, tool, {'channel': 'left', 'user_confirmed': True} if 'level' in tool else {})
            self.assertEqual(caught.exception.code, 'not_supported')

    def test_rew_abort_cancels(self):
        rew = FakeRew(measure_seconds=60)  # the blocking measure call would hang for a minute
        h = self.make(rew)
        out = {}

        def run():
            try:
                sweep(h, 'left')
            except OpsError as error:
                out['code'] = error.code
        worker = threading.Thread(target=run)
        worker.start()
        deadline = time.monotonic() + 5
        while measure.runtime(h.ctx).active is None and time.monotonic() < deadline:
            time.sleep(0.01)
        while not any(b == {'command': 'Measure'} for _m, _p, b in rew.calls) and time.monotonic() < deadline:
            time.sleep(0.01)
        started = time.monotonic()
        tools.call(h.ctx, 'measure_abort', {})
        worker.join(5)
        self.assertFalse(worker.is_alive())
        self.assertLess(time.monotonic() - started, 3.0)  # did not wait for the blocking call to time out
        self.assertEqual(out.get('code'), 'measurement_aborted')
        self.assertIn(('POST', '/measure/command', {'command': 'Cancel'}), rew.calls)
        self.assertIs(rew.blocking, False)  # restored even on abort

    def test_rew_deadline_cancels_blocking_measure(self):
        from garage_audio.ops_mcp import measure_rew
        rew = FakeRew(measure_seconds=60, blocking=True)
        backend = measure_rew.RewBackend(measure_rew.RewClient('http://127.0.0.1:4735', 30, rew))
        started = time.monotonic()
        result = backend.sweep(f1=20, f2=20000, seconds=1, rate=48000, level_dbfs=-40, title='t',
                               should_stop=lambda: False, deadline=time.monotonic() + 0.3)
        self.assertEqual(result, {'status': 'timeout'})
        self.assertLess(time.monotonic() - started, 3.0)
        self.assertIn(('POST', '/measure/command', {'command': 'Cancel'}), rew.calls)
        self.assertIs(rew.blocking, True)  # it was on before: left on
        self.assertEqual(rew.measurements, [])

    def test_list_shaped_measurements_are_one_based(self):
        rew = FakeRew(as_list=True, existing=1)
        h = self.make(rew)
        result = sweep(h, 'left')
        self.assertEqual(result['settings']['rew_measurement_id'], '2')
        self.assertIn(('GET', '/measurements/2/impulse-response', None), rew.calls)

    def test_protocol_table_is_configurable_and_validated(self):
        rew = FakeRew(measure_command='SweepMeasure')
        h = self.make(rew, protocol={'command.measure': 'SweepMeasure'})
        self.assertEqual(sweep(h, 'left')['settings']['rew_measurement_id'], '1')
        self.assertIn(('POST', '/measure/command', {'command': 'SweepMeasure'}), rew.calls)
        for bad in ({'path.nope': '/x'}, {'path.measurements': '/a/../b'}, {'path.frequency_response': '/m/fr'},
                    {'path.command': 'http://elsewhere/x'}, {'command.cancel': 'Cancel; rm'}, {'index_base': 2},
                    {'index_base': True}):
            with self.subTest(bad=bad), self.assertRaises(ConfigError):
                sim.harness(fakes, self, measurement={'backend': 'rew', 'rew': {'url': 'http://127.0.0.1:4735',
                                                                                'protocol': bad}})

    def test_unreachable(self):
        def down(*_args):
            raise OSError('refused')
        h = self.make(down)
        with self.assertRaises(OpsError) as caught:
            sweep(h, 'left')
        self.assertEqual(caught.exception.code, 'rew_unreachable')
        self.assertIsNone(measure.runtime(h.ctx).active)


class NoNumpy(unittest.TestCase):
    def test_server_core_works_and_measurement_reports_missing_extra(self):
        code = '''
import json, sys, tempfile
sys.modules["numpy"] = None
from garage_audio.ops_mcp import tools, content
from garage_audio.ops_mcp.config import DEFAULT, Config, _merge
from garage_audio.ops_mcp.context import Context
from garage_audio.ops_mcp.clients import Clients
from garage_audio.ops_mcp.errors import OpsError
tmp = tempfile.mkdtemp()
cfg = Config(_merge(DEFAULT, {"state_dir": tmp + "/state", "allow_write": True, "allow_measure": True,
                              "supervisor": {"status_root": tmp}}))
ctx = Context(cfg, Clients(system=None, ma=None, camilla=None))
out = {"tools": len(tools.describe(cfg)), "status": tools.call(ctx, "measure_status", {})["extra"]}
out["modes"] = len(tools.call(ctx, "room_modes", {"dimensions_m": {"length": 5, "width": 4, "height": 2.5}})["axial_modes"])
out["prompt"] = bool(content.get_prompt("guided-room-measurement")["text"])
errors = {}
for name, args in (("measure_sweep", {"channel": "left", "user_confirmed": True}),
                   ("measure_level_check", {"channel": "left", "user_confirmed": True}),
                   ("measure_noise_check", {})):
    try:
        tools.call(ctx, name, args)
    except OpsError as error:
        errors[name] = [error.code, error.message]
out["errors"] = errors
out["numpy_loaded"] = sys.modules.get("numpy") is not None
print(json.dumps(out))
'''
        env = {k: v for k, v in os.environ.items() if not k.startswith('GARAGE_OPS_MCP_')}
        result = subprocess.run([sys.executable, '-B', '-c', code], cwd=ROOT, env=env, capture_output=True, text=True,
                                timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        out = json.loads(result.stdout.strip().splitlines()[-1])
        self.assertEqual(out['tools'], len(tools.TOOLS))
        self.assertFalse(out['status']['installed'])
        self.assertGreater(out['modes'], 3)
        self.assertTrue(out['prompt'])
        self.assertFalse(out['numpy_loaded'])
        for name in ('measure_sweep', 'measure_level_check', 'measure_noise_check'):
            self.assertEqual(out['errors'][name][0], 'measurement_extra_missing', name)
            self.assertIn('not installed', out['errors'][name][1])


if __name__ == '__main__':
    unittest.main()
