"""Simulated room for the ops MCP measurement tests (needs numpy; synthetic, no audio hardware).

``SimulatedRoom`` is injected as ``ctx.measure_capture``: it reads the WAV the engine generated,
"plays" every output channel through a known speaker+room impulse response (delay, gain and
peaking resonances built in the frequency domain, so the analytic response is exact), sums
them at the microphone, adds background noise, an unknown start latency and optional
clipping, and writes the recording the engine then analyses. A loopback capture channel
receives the loopback output channel with the same start latency but no acoustic delay.
"""
import math
import time
from pathlib import Path

import numpy as np

from garage_audio.ops_mcp import measure_math as mm

NFFT = 1 << 17


class Speaker:
    def __init__(self, delay_ms=3.0, gain_db=-6.0, resonances=(), lowpass_hz=None):
        self.delay_ms, self.gain_db = delay_ms, gain_db
        self.resonances = list(resonances)  # (f0, q, gain_db)
        self.lowpass_hz = lowpass_hz

    def transfer(self, freqs, rate):
        """Complex response without the delay: gain * peaking biquads (* optional 4th-order lowpass)."""
        z = np.exp(1j * 2.0 * np.pi * np.asarray(freqs, dtype=float) / rate)
        h = np.full(z.shape, 10.0 ** (self.gain_db / 20.0), dtype=complex)
        for f0, q, gain in self.resonances:
            big_a = 10.0 ** (gain / 40.0)
            w = 2.0 * math.pi * f0 / rate
            alpha = math.sin(w) / (2.0 * q)
            b = (1 + alpha * big_a, -2 * math.cos(w), 1 - alpha * big_a)
            a = (1 + alpha / big_a, -2 * math.cos(w), 1 - alpha / big_a)
            h *= (b[0] + b[1] / z + b[2] / z ** 2) / (a[0] + a[1] / z + a[2] / z ** 2)
        if self.lowpass_hz:
            s = 1j * np.asarray(freqs, dtype=float) / self.lowpass_hz
            h *= 1.0 / (1.0 + 1.41421356 * s + s * s) ** 2
        return h

    def response_db(self, freq, rate=48000):
        return float(20.0 * math.log10(abs(self.transfer([freq], rate)[0])))

    def impulse(self, rate):
        freqs = np.fft.rfftfreq(NFFT, 1.0 / rate)
        h = self.transfer(freqs, rate) * np.exp(-2j * np.pi * freqs * self.delay_ms / 1000.0)
        ir = np.fft.irfft(h, NFFT)
        return ir[:rate]  # 1 s is ample for the resonances used in the tests


class SimulatedRoom:
    """``speakers``: {output_channel_index: Speaker}. Records what was played for the safety tests."""

    name = 'simulated'
    provides_timing = True

    def __init__(self, speakers, *, rate=48000, noise_dbfs=-85.0, latency_ms=7.3, mic_channel=0,
                 loop=None, mic_gain_db=0.0, mic_resonances=(), dc_offset=0.0, disconnected=False, seed=1234):
        self.speakers, self.rate = speakers, rate
        self.noise_dbfs, self.latency_ms = noise_dbfs, latency_ms
        self.mic_channel, self.loop = mic_channel, loop  # loop = (output_channel, capture_channel)
        self.mic_gain_db, self.dc_offset, self.disconnected = mic_gain_db, dc_offset, disconnected
        self.mic_resonances = list(mic_resonances)  # microphone coloration (what a calibration file corrects)
        self.rng = np.random.default_rng(seed)
        self.played = []    # [{'peak_dbfs': {channel: dB}, 'seconds': s, 'frames': n}]
        self.last_play = None
        self.recordings = 0
        self.stops = 0
        self._irs = {}

    def _ir(self, channel):
        if channel not in self._irs:
            sp = self.speakers[channel]
            with_mic = Speaker(sp.delay_ms, sp.gain_db, sp.resonances + self.mic_resonances, sp.lowpass_hz)
            self._irs[channel] = with_mic.impulse(self.rate)
        return self._irs[channel]

    def _noise(self, n, channels):
        rms = 10.0 ** (self.noise_dbfs / 20.0)
        return self.rng.normal(0.0, rms, (n, channels))

    def _write(self, path, frames):
        Path(path).write_bytes(mm.wav_bytes(frames, self.rate, 3))

    def play_and_record(self, *, play_file, record_file, rate, out_channels, capture_channels, play_seconds,
                        preroll, tail, should_stop, deadline):
        assert rate == self.rate
        got_rate, play = mm.read_wav(Path(play_file).read_bytes())
        assert got_rate == rate and play.shape[1] == out_channels
        peaks = {}
        for ch in range(out_channels):
            peak = float(np.max(np.abs(play[:, ch])))
            if peak > 0:
                peaks[ch] = 20.0 * math.log10(peak)
        self.played.append({'peak_dbfs': peaks, 'seconds': len(play) / rate, 'frames': len(play)})
        self.last_play = play
        if should_stop():
            return {'status': 'aborted'}
        start = int(round(preroll * rate)) + int(round(self.latency_ms / 1000.0 * rate))
        total = start + len(play) + int(round(tail * rate)) + rate
        rec = self._noise(total, capture_channels)
        mic = np.zeros(total)
        if not self.disconnected:
            for ch, _speaker in self.speakers.items():
                if ch < out_channels and np.any(play[:, ch]):
                    wet = _fft_convolve(play[:, ch], self._ir(ch))
                    end = min(total, start + len(wet))
                    mic[start:end] += wet[:end - start]
        mic = mic * 10.0 ** (self.mic_gain_db / 20.0) + self.dc_offset
        rec[:, self.mic_channel] += mic
        if self.loop is not None and self.loop[1] < capture_channels:
            out_ch, cap_ch = self.loop
            rec[start:start + len(play), cap_ch] += play[:, out_ch]
        rec = np.clip(rec, -1.0, 1.0)
        self._write(record_file, rec)
        self.recordings += 1
        return {'status': 'ok', 'recorder_exit': 0}

    def record(self, *, record_file, rate, capture_channels, seconds, should_stop, deadline):
        n = int(seconds * rate)
        rec = self._noise(n, capture_channels)
        if self.disconnected:
            rec[:, self.mic_channel] = 0.0
        self._write(record_file, np.clip(rec, -1.0, 1.0))
        self.recordings += 1
        return {'status': 'ok', 'recorder_exit': 0}

    def stop(self):
        self.stops += 1
        return 0


class BlockingCapture(SimulatedRoom):
    """Plays "forever" (until abort or the deadline), to exercise abort and the watchdog without processes."""

    def play_and_record(self, *, should_stop, deadline, **_kwargs):
        while True:
            if should_stop():
                return {'status': 'aborted'}
            if time.monotonic() >= deadline:
                return {'status': 'timeout'}
            time.sleep(0.01)


def _fft_convolve(x, h):
    n = len(x) + len(h) - 1
    nfft = 1 << (n - 1).bit_length()
    return np.fft.irfft(np.fft.rfft(x, nfft) * np.fft.rfft(h, nfft), nfft)[:n]


def measurement_config(**overrides):
    """Test measurement section: L/R/sub on outputs 0/1/2, loopback output 3 -> capture 1, mic on capture 0."""
    m = {'output_channels': 4, 'capture_channels': 2, 'mic_channel': 0,
         'channel_map': {'left': [0], 'right': [1], 'sub': [2], 'sum': [0, 1, 2]},
         'loopback': {'output_channel': 3, 'capture_channel': 1}, 'sweep_seconds': 4.0}
    m.update(overrides)
    return m


def harness(fakes, testcase, room=None, allow=True, measurement=None, **top):
    """A fakes.Harness wired for measurement: owner ready, MA idle, the simulated room as capture."""
    m = measurement_config(**(measurement or {}))
    h = fakes.Harness(testcase, {'allow_write': allow, 'allow_measure': allow, 'measurement': m, **top})
    h.recovery_state('ready')
    for player in h.ma.players:
        player['state'] = 'idle'
    loop = (m['loopback']['output_channel'], m['loopback']['capture_channel']) if m.get('loopback') else None
    h.room = room if room is not None else default_room(loop=loop)
    h.ctx.measure_capture = h.room
    return h


def peak_near(freqs, dbs, f0, span=0.12):
    """(frequency, dB) of the largest value within f0*(1 +- span)."""
    best = None
    for f, d in zip(freqs, dbs):
        if f0 * (1 - span) <= f <= f0 * (1 + span) and (best is None or d > best[1]):
            best = (f, d)
    return best


def default_room(noise_dbfs=-85.0, **kwargs):
    """Left/right mains on outputs 0/1 (left 3.0 ms, right 3.6 ms), sub on output 2 (5.0 ms, lowpassed).

    Resonances: room modes of a 5.0 x 3.7 m room (34.3, 46.4, 68.6, 92.7 Hz) as +8 dB / Q 8 peaks on
    both mains, plus a +10 dB / Q 5 peak at 50 Hz on the left speaker.
    """
    modes = [(343.0 / 10.0, 8.0, 8.0), (343.0 / 7.4, 8.0, 8.0), (343.0 / 5.0, 8.0, 8.0), (2 * 343.0 / 7.4, 8.0, 8.0)]
    speakers = {0: Speaker(3.0, -6.0, modes + [(50.0, 5.0, 10.0)]),
                1: Speaker(3.6, -7.0, modes),
                2: Speaker(5.0, -4.0, [(40.0, 4.0, 6.0)], lowpass_hz=120.0)}
    return SimulatedRoom(speakers, noise_dbfs=noise_dbfs, **kwargs)
