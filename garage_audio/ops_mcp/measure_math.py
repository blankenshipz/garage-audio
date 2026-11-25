"""Measurement signal processing. Needs the optional ``measurement`` extra (numpy).

Import this module only through ``measure.math_module()``, which turns a missing numpy
into a clear ``measurement_extra_missing`` error; everything else in the ops server is
stdlib-only and keeps working without the extra.

Method (Farina): an exponential (log) sine sweep is played, the recording is convolved
with the time-reversed sweep weighted by a 6 dB/octave envelope (the inverse filter).
The linear impulse response appears at a fixed index after the recording position of
the sweep; harmonic distortion products land *before* it and are excluded by the peak
search window. The frequency response is the FFT of the windowed impulse response,
divided by the response of the method itself (sweep deconvolved with its own inverse)
so band-edge ripple from fades does not appear as a speaker/room feature.
"""
import io
import math
import struct
import wave

import numpy as np

FADE_IN_S = 0.05
FADE_OUT_S = 0.01
PRE_WINDOW_MS = 50.0  # keeps the band-limited pre-ringing at 20-30 Hz (10 ms cost ~0.5 dB there)
GRID_PER_OCTAVE = 48
OCTAVE_CENTRES = (16.0, 31.5, 63.0, 125.0, 250.0, 500.0, 1000.0, 2000.0, 4000.0, 8000.0, 16000.0)
MAX_LATENCY_S = 1.0   # search window for an impulse peak after the expected position
REF_SEARCH_S = 0.2    # reference-chirp peak search half-width around the main arrival (~68 m)


def _nfft(n):
    return 1 << int(math.ceil(math.log2(max(2, n))))


def dbfs(value, floor=1e-12):
    return float(20.0 * math.log10(max(float(value), floor)))


# ---- signals --------------------------------------------------------------------------------
def log_sweep(f1, f2, seconds, rate, level_dbfs):
    """Exponential sine sweep f1..f2 with short raised-cosine fades; peak = level_dbfs."""
    n = int(round(seconds * rate))
    t = np.arange(n) / rate
    rate_const = seconds / math.log(f2 / f1)
    x = np.sin(2.0 * math.pi * f1 * rate_const * (np.exp(t / rate_const) - 1.0))
    fi = max(2, int(min(FADE_IN_S, seconds / 10.0) * rate))
    fo = max(2, int(min(FADE_OUT_S, seconds / 20.0) * rate))
    x[:fi] *= 0.5 - 0.5 * np.cos(np.pi * np.arange(fi) / fi)
    x[n - fo:] *= 0.5 + 0.5 * np.cos(np.pi * np.arange(fo) / fo)
    return x * 10.0 ** (level_dbfs / 20.0)


def inverse_filter(sweep, f1, f2, seconds, rate):
    """Farina inverse: time-reversed sweep with a decaying envelope, normalised to unity gain in band."""
    n = len(sweep)
    t = np.arange(n) / rate
    rate_const = seconds / math.log(f2 / f1)
    inv = sweep[::-1] * np.exp(-t / rate_const)
    nfft = _nfft(2 * n)
    h = np.abs(np.fft.rfft(sweep, nfft) * np.fft.rfft(inv, nfft))
    freqs = np.fft.rfftfreq(nfft, 1.0 / rate)
    band = (freqs >= 2.0 * f1) & (freqs <= f2 / 2.0)
    if np.count_nonzero(band) < 4:  # a two-octave sweep leaves (almost) no inner band: use the whole sweep band
        band = (freqs >= f1) & (freqs <= f2)
    if not band.any():
        raise ValueError(f'sweep band {f1:g}-{f2:g} Hz is too narrow to normalise the inverse filter')
    return inv / float(np.median(h[band]))


def tone(freq, seconds, rate, level_dbfs):
    n = int(round(seconds * rate))
    x = np.sin(2.0 * math.pi * freq * np.arange(n) / rate)
    fade = max(2, int(0.02 * rate))
    ramp = 0.5 - 0.5 * np.cos(np.pi * np.arange(fade) / fade)
    x[:fade] *= ramp
    x[n - fade:] *= ramp[::-1]
    return x * 10.0 ** (level_dbfs / 20.0)


def program(length, out_channels, placements):
    """A (length, out_channels) float array; ``placements`` = [(start, signal, [channels])]."""
    frames = np.zeros((length, out_channels))
    for start, signal, channels in placements:
        for ch in channels:
            frames[start:start + len(signal), ch] += signal
    return frames


# ---- WAV I/O (stdlib wave for writing; tolerant RIFF reader for recorder output) -------------
def wav_bytes(frames, rate, sampwidth=3):
    """Integer PCM WAV bytes (16, 24 or 32 bit) from float frames in [-1, 1)."""
    frames = np.atleast_2d(np.asarray(frames, dtype=np.float64))
    if frames.shape[0] == 1 and frames.shape[1] > 8:
        frames = frames.T
    full = float(2 ** (8 * sampwidth - 1))
    ints = np.round(np.clip(frames, -1.0, (full - 1) / full) * full).astype('<i4')
    if sampwidth == 2:
        data = ints.astype('<i2').tobytes()
    elif sampwidth == 3:
        data = ints.reshape(-1, 1).view(np.uint8).reshape(-1, 4)[:, :3].tobytes()
    elif sampwidth == 4:
        data = ints.tobytes()
    else:
        raise ValueError('sample width must be 2, 3 or 4 bytes')
    buffer = io.BytesIO()
    with wave.open(buffer, 'wb') as handle:
        handle.setnchannels(frames.shape[1])
        handle.setsampwidth(sampwidth)
        handle.setframerate(int(rate))
        handle.writeframes(data)
    return buffer.getvalue()


class WavError(ValueError):
    pass


def read_wav(data):
    """(rate, frames float64 (n, channels)) from WAV bytes.

    Accepts PCM 16/24/32-bit and IEEE float 32/64 (also WAVE_FORMAT_EXTENSIBLE). A data
    chunk whose size was never finalised (recorder killed) is read to the end of the file.
    """
    if len(data) < 12 or data[:4] != b'RIFF' or data[8:12] != b'WAVE':
        raise WavError('not a RIFF/WAVE file')
    pos, fmt, payload = 12, None, None
    while pos + 8 <= len(data):
        cid, size = data[pos:pos + 4], struct.unpack('<I', data[pos + 4:pos + 8])[0]
        body = pos + 8
        if cid == b'fmt ':
            fmt = data[body:body + size]
        elif cid == b'data':
            end = body + size if 0 < size <= len(data) - body else len(data)
            payload = data[body:end]
            break
        pos = body + size + (size & 1)
    if fmt is None or len(fmt) < 16 or payload is None:
        raise WavError('WAV file has no fmt/data chunk')
    tag, channels, rate, _byte_rate, block, bits = struct.unpack('<HHIIHH', fmt[:16])
    if tag == 0xFFFE and len(fmt) >= 26:
        tag = struct.unpack('<H', fmt[24:26])[0]
    if channels < 1 or channels > 32 or block != channels * (bits // 8) or rate < 1000:
        raise WavError('unsupported WAV layout')
    usable = len(payload) - len(payload) % block
    raw = payload[:usable]
    if tag == 1 and bits == 16:
        values = np.frombuffer(raw, '<i2').astype(np.float64) / 32768.0
    elif tag == 1 and bits == 24:
        b = np.frombuffer(raw, np.uint8).reshape(-1, 3).astype(np.int32)
        ints = b[:, 0] | (b[:, 1] << 8) | (b[:, 2] << 16)
        ints = np.where(ints >= 1 << 23, ints - (1 << 24), ints)
        values = ints.astype(np.float64) / float(1 << 23)
    elif tag == 1 and bits == 32:
        values = np.frombuffer(raw, '<i4').astype(np.float64) / float(1 << 31)
    elif tag == 3 and bits == 32:
        values = np.frombuffer(raw, '<f4').astype(np.float64)
    elif tag == 3 and bits == 64:
        values = np.frombuffer(raw, '<f8').astype(np.float64)
    else:
        raise WavError(f'unsupported WAV sample format (tag {tag}, {bits} bit); record s16, s24, s32 or f32')
    if not np.all(np.isfinite(values)):
        raise WavError('WAV contains non-finite samples')
    return int(rate), values.reshape(-1, channels)


# ---- analysis -------------------------------------------------------------------------------
def deconvolve(recording, inverse):
    n = len(recording) + len(inverse) - 1
    nfft = _nfft(n)
    return np.fft.irfft(np.fft.rfft(recording, nfft) * np.fft.rfft(inverse, nfft), nfft)[:n]


def find_peak(ir, lo, hi):
    """(integer index, fractional index) of the largest |ir| in [lo, hi) with parabolic refinement."""
    lo, hi = max(0, int(lo)), min(len(ir), int(hi))
    if hi - lo < 3:
        raise ValueError('peak search window is empty')
    mag = np.abs(ir[lo:hi])
    i = lo + int(np.argmax(mag))
    frac = 0.0
    if 0 < i < len(ir) - 1:
        a, b, c = abs(ir[i - 1]), abs(ir[i]), abs(ir[i + 1])
        den = a - 2.0 * b + c
        if den != 0:
            frac = float(np.clip(0.5 * (a - c) / den, -0.5, 0.5))
    return i, i + frac


def windowed_spectrum(ir, peak_index, frac, rate, window_ms, nfft_min=1 << 16):
    """FFT of the impulse response around its peak, with the peak moved to t=0 (delay removed)."""
    pre = max(4, int(PRE_WINDOW_MS * rate / 1000.0))
    post = max(16, int(window_ms * rate / 1000.0))
    start, stop = max(0, peak_index - pre), min(len(ir), peak_index + post)
    seg = np.array(ir[start:stop], dtype=np.float64)
    left = peak_index - start
    if left > 1:
        rise = left // 2
        seg[:rise] *= 0.5 - 0.5 * np.cos(np.pi * np.arange(rise) / rise)
    right = len(seg) - left
    fall = max(2, right // 4)
    seg[len(seg) - fall:] *= 0.5 + 0.5 * np.cos(np.pi * np.arange(fall) / fall)
    nfft = max(nfft_min, _nfft(2 * len(seg)))
    buf = np.zeros(nfft)
    buf[:right] = seg[left:]
    if left:
        buf[nfft - left:] = seg[:left]
    spectrum = np.fft.rfft(buf)
    freqs = np.fft.rfftfreq(nfft, 1.0 / rate)
    spectrum *= np.exp(2j * np.pi * freqs * frac / rate)  # remove the sub-sample part of the delay
    return freqs, spectrum


def log_grid(f_lo, f_hi, per_octave=GRID_PER_OCTAVE):
    count = max(2, int(math.ceil(math.log2(f_hi / f_lo) * per_octave)) + 1)
    grid = f_lo * 2.0 ** (np.arange(count) * math.log2(f_hi / f_lo) / (count - 1))
    grid[0], grid[-1] = f_lo, f_hi
    return grid


def smooth_power(freqs, spectrum, grid, fraction):
    """Fractional-octave power smoothing onto ``grid`` (fraction 0 = interpolation only). Returns dB."""
    power = np.abs(spectrum) ** 2
    interp = np.interp(grid, freqs, power)
    if fraction <= 0:
        return 10.0 * np.log10(np.maximum(interp, 1e-30))
    cs = np.concatenate([[0.0], np.cumsum(power)])
    lo = np.searchsorted(freqs, grid * 2.0 ** (-fraction / 2.0), 'left')
    hi = np.searchsorted(freqs, grid * 2.0 ** (fraction / 2.0), 'right')
    count = hi - lo
    mean = np.where(count >= 2, (cs[hi] - cs[lo]) / np.maximum(count, 1), interp)
    return 10.0 * np.log10(np.maximum(mean, 1e-30))


def phase_and_group_delay(freqs, spectrum, grid):
    """Unwrapped phase (rad) interpolated onto ``grid`` and group delay (ms) relative to the arrival."""
    sel = (freqs >= grid[0] / 1.05) & (freqs <= grid[-1] * 1.05)
    f = freqs[sel]
    unwrapped = np.unwrap(np.angle(spectrum[sel]))
    phase = np.interp(grid, f, unwrapped)
    group_ms = -np.gradient(phase, 2.0 * np.pi * grid) * 1000.0
    return phase, group_ms


def band_power(x, rate, f_lo, f_hi):
    """Mean power of ``x`` restricted to [f_lo, f_hi] (FFT brick-wall)."""
    if len(x) < 16:
        return 0.0
    spectrum = np.fft.rfft(x * np.hanning(len(x)))
    freqs = np.fft.rfftfreq(len(x), 1.0 / rate)
    sel = (freqs >= f_lo) & (freqs <= f_hi)
    # Hann window power gain 3/8; one-sided spectrum -> x2 (Parseval).
    return float(2.0 * np.sum(np.abs(spectrum[sel]) ** 2) / (len(x) ** 2 * 0.375))


def capture_stats(x):
    peak = float(np.max(np.abs(x))) if len(x) else 0.0
    rms = float(np.sqrt(np.mean(x ** 2))) if len(x) else 0.0
    return {'peak_dbfs': round(dbfs(peak), 2), 'rms_dbfs': round(dbfs(rms), 2),
            'dc_offset': round(float(np.mean(x)) if len(x) else 0.0, 6)}


def clipped_count(x, threshold_dbfs):
    return int(np.count_nonzero(np.abs(x) >= 10.0 ** (threshold_dbfs / 20.0)))


def noise_report(x, rate, f_lo=20.0, f_hi=20000.0):
    """Broadband and octave-band noise levels (dBFS RMS) of a noise-only recording."""
    f_hi = min(f_hi, 0.49 * rate)
    octaves = {}
    for centre in OCTAVE_CENTRES:
        lo, hi = centre / math.sqrt(2), centre * math.sqrt(2)
        if lo >= f_lo / 1.5 and hi <= f_hi * 1.5:
            octaves[str(centre)] = round(10.0 * math.log10(max(band_power(x, rate, lo, hi), 1e-24)), 1)
    return {'noise_floor_dbfs': round(10.0 * math.log10(max(band_power(x, rate, f_lo, f_hi), 1e-24)), 2),
            'octave_dbfs': octaves, **capture_stats(x)}


def tone_level(mic, rate, freq, noise_slice):
    """Tone level, noise in the same band and SNR from a level-check recording."""
    lo, hi = freq * 2.0 ** (-1 / 6), freq * 2.0 ** (1 / 6)
    noise = mic[noise_slice]
    noise_p = max(band_power(noise, rate, lo, hi), 1e-24)
    block = int(0.1 * rate)
    body = mic[noise_slice.stop:]
    powers = [band_power(body[i:i + block], rate, lo, hi) for i in range(0, len(body) - block + 1, block)]
    signal_p = max(float(np.percentile(powers, 75)) if powers else 0.0, 1e-24)
    return {'tone_dbfs': round(10.0 * math.log10(signal_p), 2),
            'noise_in_band_dbfs': round(10.0 * math.log10(noise_p), 2),
            'snr_db': round(10.0 * math.log10(signal_p / noise_p), 2)}


def sweep_snr(mic, rate, f1, f2, seconds, sweep_start, noise_slice):
    """Per-octave SNR: band power while the sweep is in that octave vs background noise in the band."""
    rate_const = seconds / math.log(f2 / f1)
    noise = mic[noise_slice]
    out = {}
    for centre in OCTAVE_CENTRES:
        lo, hi = max(centre / math.sqrt(2), f1), min(centre * math.sqrt(2), f2)
        if hi / lo < 2 ** 0.5:
            continue
        t0, t1 = rate_const * math.log(lo / f1), rate_const * math.log(hi / f1)
        a = int(sweep_start + t0 * rate)
        b = int(sweep_start + t1 * rate) + int(0.05 * rate)
        seg = mic[max(0, a):max(0, min(len(mic), b))]
        if len(seg) < 64:
            continue
        s = band_power(seg, rate, lo, hi)
        n = max(band_power(noise, rate, lo, hi), 1e-24)
        out[str(centre)] = round(10.0 * math.log10(max(s, 1e-24) / n), 1)
    return out


def analyze_sweep(recording, *, rate, mic_channel, layout, sweep, inverse, f1, f2, seconds, window_ms,
                  smoothing, noise_slice, loop_channel=None, ref=None, identity=None, clip_dbfs=-0.5):
    """Impulse response, response on a log grid, arrival times and quality metrics for one sweep.

    ``layout``: {'main_start': samples}; ``ref``: optional {'start', 'sweep', 'inverse'} timing chirp
    on the reference speaker; ``identity``: (freqs, spectrum) of the method response (cached).
    Arrival indices are measured from the start of the recording and include player/recorder
    start latency; only differences (loopback or reference chirp) are meaningful timings.
    """
    mic = recording[:, mic_channel]
    n_sweep = len(sweep)
    main_start = layout['main_start']
    ir = deconvolve(mic, inverse)
    lo = n_sweep - 1 + main_start
    hi = lo + int(MAX_LATENCY_S * rate)
    peak_i, peak_f = find_peak(ir, lo, hi)
    t_main = peak_f - (n_sweep - 1) - main_start
    result = {'raw_arrival_ms': round(t_main / rate * 1000.0, 3)}
    short_noise = False
    if noise_slice is None:
        # The generated file starts (at the mic) at recording index ~t_main and begins with
        # LEAD silence, so everything before t_main is background noise.
        skip = int(0.02 * rate)
        end = int(t_main)
        short_noise = end - skip < int(0.1 * rate)
        noise_slice = slice(skip, max(skip + 256, end))
    timing = None
    if loop_channel is not None:
        loop = recording[:, loop_channel]
        ir_loop = deconvolve(loop, inverse)
        _i, loop_f = find_peak(ir_loop, lo, hi)
        t_loop = loop_f - (n_sweep - 1) - main_start
        loop_peak = float(np.max(np.abs(ir_loop[lo:hi])))
        loop_floor = float(np.median(np.abs(ir_loop[lo:hi]))) or 1e-24
        timing = {'timing_source': 'loopback', 'timing_confidence': 'high' if loop_peak > 30 * loop_floor else 'low',
                  'arrival_ms': round((t_main - t_loop) / rate * 1000.0, 3),
                  'loopback_clipped_samples': clipped_count(loop, clip_dbfs)}
    elif ref is not None:
        n_ref = len(ref['sweep'])
        ir_ref = deconvolve(mic, ref['inverse'])
        # Same start latency as the main sweep: search +-REF_SEARCH_S around the main arrival.
        expected = ref['start'] + n_ref - 1 + t_main
        span = int(REF_SEARCH_S * rate)
        _i, ref_f = find_peak(ir_ref, expected - span, expected + span)
        t_ref = ref_f - (n_ref - 1) - ref['start']
        timing = {'timing_source': 'acoustic_reference', 'timing_confidence': 'low',
                  'arrival_ms': round((t_main - t_ref) / rate * 1000.0, 3)}
    freqs, spectrum = windowed_spectrum(ir, peak_i, peak_f - peak_i, rate, window_ms)
    if identity is None:
        identity = method_response(sweep, inverse, rate, window_ms)
    id_freqs, id_spectrum = identity
    if len(id_freqs) != len(freqs):
        id_spectrum = np.interp(freqs, id_freqs, id_spectrum.real) + 1j * np.interp(freqs, id_freqs, id_spectrum.imag)
    floor = 1e-6 * float(np.max(np.abs(id_spectrum)))
    safe = np.where(np.abs(id_spectrum) < floor, floor, id_spectrum)
    spectrum = spectrum / safe
    grid = log_grid(f1, f2)
    mag_db = smooth_power(freqs, spectrum, grid, smoothing)
    phase, group_ms = phase_and_group_delay(freqs, spectrum, grid)
    snr = sweep_snr(mic, rate, f1, f2, seconds, main_start + t_main, noise_slice)
    ir_window = ir[max(0, peak_i - int(0.05 * rate)):peak_i + int(1.0 * rate)]
    tail = ir[peak_i + int(0.6 * rate):peak_i + int(0.9 * rate)]
    tail_rms = float(np.sqrt(np.mean(tail ** 2))) if len(tail) else 0.0
    result['ir_peak_to_tail_db'] = round(dbfs(abs(ir[peak_i])) - dbfs(tail_rms, 1e-15), 1)
    result.update({
        'grid': grid, 'magnitude_db': mag_db, 'phase_rad': phase, 'group_delay_ms': group_ms,
        'impulse': ir_window, 'impulse_peak_offset': peak_i - max(0, peak_i - int(0.05 * rate)),
        'timing': timing, 'snr_by_octave_db': snr,
        'capture': capture_stats(mic), 'clipped_samples': clipped_count(mic, clip_dbfs),
        'noise': {**noise_report(mic[noise_slice], rate, f1, f2), 'short_estimate': short_noise},
    })
    return result


def method_response(sweep, inverse, rate, window_ms):
    """Spectrum of the deconvolution method itself (sweep through an ideal wire)."""
    ir = deconvolve(sweep, inverse)
    n = len(sweep)
    peak_i, peak_f = find_peak(ir, n - 1 - 8, n - 1 + 8)
    return windowed_spectrum(ir, peak_i, peak_f - peak_i, rate, window_ms)
