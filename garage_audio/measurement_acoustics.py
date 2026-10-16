"""Latency-tolerant microphone magnitude QC. No absolute SPL or clock claims."""
import math
from .measurement_signal import fft
from .measurement import interpolate


def analyze(reference, capture, rate, band, calibration, guard=lambda: None):
    if rate != 48000 or not reference or len(capture) > rate*4:
        raise ValueError('bounded 48k microphone/reference required')
    if len(capture) < len(reference): raise ValueError('capture truncated')
    n = 1
    while n < len(reference)+len(capture): n *= 2
    X = fft(reference+[0.]*(n-len(reference)), guard=guard)
    Y = fft(capture+[0.]*(n-len(capture)), guard=guard)
    correlation = fft([y*x.conjugate() for x,y in zip(X,Y)], inverse=True, guard=guard)
    # Search positive acquisition/output latency, not zero-lag waveform likeness.
    maximum_lag = min(rate//2, len(capture)-len(reference))
    lag = max(range(maximum_lag+1), key=lambda i: abs(correlation[i]))
    aligned = capture[lag:lag+len(reference)]
    energy = sum(v*v for v in reference)
    response_energy = sum(v*v for v in aligned)
    coherence = abs(correlation[lag])/max(math.sqrt(energy*response_energy), 1e-20)
    pre = max(1, int(.15*rate))
    noise = math.sqrt(sum(v*v for v in capture[:pre])/pre)
    rms = math.sqrt(response_energy/len(reference))
    snr = 20*math.log10(max(rms, 1e-15)/max(noise, 1e-12))
    peak = max(abs(v) for v in capture)
    checks = {'clipping_free': peak < .98, 'snr_at_least_20_db': snr >= 20,
              'response_present': rms >= 1e-7, 'latency_not_at_search_boundary': lag < maximum_lag,
              'reference_correlated': coherence >= .1}
    points = []
    power = max(abs(x)**2 for x in X)
    lo, hi = band
    for i in range(121):
        frequency = lo*(hi/lo)**(i/120)
        index = max(1, round(frequency*n/rate))
        transfer = Y[index]*X[index].conjugate()/(abs(X[index])**2+power*1e-6)
        points.append({'frequency_hz': frequency,
                       'db': 20*math.log10(max(abs(transfer), 1e-15))-interpolate(calibration, frequency)})
    return {'source': 'measured', 'supported_magnitude': all(checks.values()), 'checks': checks,
            'latency_samples_estimate': lag, 'reference_correlation': coherence,
            'snr_db': snr, 'peak_dbfs': 20*math.log10(max(peak, 1e-15)), 'points': points,
            'calibration_applied': True, 'clock_drift_ppm': None, 'timing_verified': False,
            'warnings': ['Relative calibrated magnitude only; not absolute SPL',
                         'Latency is an estimate, not calibrated acoustic delay; independent USB clocks unverified',
                         'No phase, delay, crossover or acoustic improvement claim']}
