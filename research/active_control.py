"""Experimental causal MIMO feed-forward FIR target matching (synthetic only).

No audio, device access, profile export, or production-package dependency.
Run: python -m research.active_control --output /private/path/results.json
"""
import argparse
import json
from dataclasses import dataclass, asdict
from pathlib import Path

import numpy as np


@dataclass(frozen=True)
class Design:
    sample_rate: int = 1000
    ir_samples: int = 384
    taps: int = 48
    delay: int = 12
    late_start: int = 80
    late_weight: float = 4.0
    ridge: float = 0.08
    speaker_l1_limit: float = 2.5
    total_l2_limit: float = 2.5
    iterations: int = 1200
    tolerance: float = 1e-9


def validate(h, target, d):
    if h.ndim != 3 or target.ndim != 3 or not np.isfinite(h).all():
        raise ValueError('finite position x speaker x time IRs required')
    p, s, n = h.shape
    if not (1 <= p <= 16 and 1 <= s <= 8 and n == d.ir_samples):
        raise ValueError('fixture dimensions outside isolated experiment bounds')
    if target.shape[0] != p or target.shape[2] != n + d.taps - 1:
        raise ValueError('target must cover the full linear convolution tail')
    if not np.isfinite(target).all() or not (1 <= target.shape[1] <= 4):
        raise ValueError('finite target required')
    if not (1 <= d.taps <= 128 and 0 <= d.delay < d.taps and
            d.delay < d.late_start < n and d.sample_rate > 0):
        raise ValueError('invalid causal support, delay, or late window')
    if not all(np.isfinite(v) and v > 0 for v in
               (d.ridge, d.late_weight, d.speaker_l1_limit,
                d.total_l2_limit, d.tolerance)) or not 1 <= d.iterations <= 10000:
        raise ValueError('positive finite constraints/regularization required')


def fixture(speakers=3, seed=1820, d=Design()):
    """Analytic modal + direct/reflection toy room, NOT an image-source garage.

    First four positions train; last two held out. Three speakers models
    L/R/sub (sub acoustical response low-passed); six means hypothetical full
    range sources, not hardware. Independent L/R program inputs, no LFE input.
    """
    if speakers not in (3, 6):
        raise ValueError('only labeled 2.1 and expanded synthetic fixtures')
    rng = np.random.default_rng(seed)
    p, n = 6, d.ir_samples
    h = np.zeros((p, speakers, n))
    t = np.arange(n) / d.sample_rate
    modes = [43, 67, 97, 137]
    source = rng.uniform(-1, 1, (speakers, len(modes)))
    receiver = rng.uniform(-1, 1, (p, len(modes)))
    for pos in range(p):
        for s in range(speakers):
            arrival = 3 + (pos + s) % 4
            h[pos, s, arrival] = 0.7 + 0.03 * np.cos(pos + s)
            h[pos, s, arrival + 17 + s] = 0.16 * np.cos(pos - s)
            for m, f in enumerate(modes):
                shifted = np.maximum(t - arrival / d.sample_rate, 0)
                h[pos, s] += (0.12 * source[s, m] * receiver[pos, m] *
                              np.exp(-shifted / (0.10 + 0.035 * m)) *
                              np.sin(2 * np.pi * f * shifted))
            if speakers == 3 and s == 2:
                # Causal FIR low pass; intentionally poor authority at high f.
                kernel = np.exp(-np.arange(16) / 3.0)
                kernel /= kernel.sum()
                h[pos, s] = np.convolve(h[pos, s], kernel)[:n]
    target = np.zeros((p, 2, n + d.taps - 1))
    for pos in range(p):
        for inp in range(2):
            target[pos, inp, d.delay + 3 + (pos + inp) % 4] = 0.7
    return h, target


def convolution_matrix(h, taps):
    p, s, n = h.shape
    a = np.zeros((p, n + taps - 1, s * taps))
    for speaker in range(s):
        for tap in range(taps):
            a[:, tap:tap + n, speaker * taps + tap] = h[:, speaker]
    return a


def l1_project(x, limit):
    if np.abs(x).sum() <= limit:
        return x.copy()
    values = np.sort(np.abs(x).ravel())[::-1]
    cumulative = np.cumsum(values)
    idx = np.flatnonzero(values > (cumulative - limit) / np.arange(1, len(values) + 1))[-1]
    threshold = (cumulative[idx] - limit) / (idx + 1)
    return np.sign(x) * np.maximum(np.abs(x) - threshold, 0)


def project(x, speakers, inputs, d):
    # Per-speaker bound applies to SUM across simultaneous program inputs.
    g = x.reshape(speakers, d.taps, inputs).copy()
    for s in range(speakers):
        g[s] = l1_project(g[s], d.speaker_l1_limit)
    norm = np.linalg.norm(g)
    if norm > d.total_l2_limit:
        g *= d.total_l2_limit / norm
    return g.reshape(x.shape)


def optimize(h, target, d=Design(), independent=False):
    """Projected gradient of weighted target error + ridge filter energy.

    Full LINEAR convolution, no FFT inverse wraparound/truncation. Nonzero
    coefficients only at samples 0..taps-1. Independent comparator has diagonal
    L->L/R->R routing and zero sub/aux support. Same targets and constraints.
    Alternating feasible L1/L2 shrinkage is not asserted to be exact Euclidean
    intersection projection; monotone backtracking retains objective decreases.
    """
    validate(h, target, d)
    p, speakers, _ = h.shape
    inputs = target.shape[1]
    a = convolution_matrix(h, d.taps)
    weights = np.ones(a.shape[1])
    weights[d.late_start:] += d.late_weight
    aw = (a * np.sqrt(weights)[None, :, None]).reshape(-1, speakers * d.taps)
    b = (target.transpose(0, 2, 1) * np.sqrt(weights)[None, :, None]).reshape(-1, inputs)
    gram = aw.T @ aw / p + d.ridge * np.eye(speakers * d.taps)
    rhs = aw.T @ b / p
    step = 1 / np.linalg.eigvalsh(gram)[-1]
    x = np.zeros((speakers * d.taps, inputs))
    mask = np.ones_like(x)
    if independent:
        mask[:] = 0
        for inp in range(min(inputs, speakers)):
            mask[inp * d.taps:(inp + 1) * d.taps, inp] = 1
    def objective(v):
        return float(np.sum(v * (gram @ v)) - 2 * np.sum(v * rhs) + np.sum(b * b) / p)
    history = [objective(x)]
    movement = float('inf')
    for _ in range(d.iterations):
        gradient = gram @ x - rhs
        trial_step = step
        for _backtrack in range(30):
            candidate = project((x - trial_step * gradient) * mask, speakers, inputs, d)
            value = objective(candidate)
            if value <= history[-1] + 1e-12:
                break
            trial_step *= 0.5
        else:
            break
        history.append(value)
        movement = np.linalg.norm(candidate - x)
        x = candidate
        if movement < d.tolerance:
            break
    g = x.reshape(speakers, d.taps, inputs).transpose(0, 2, 1)
    return g, {'objective_initial': history[0], 'objective_final': history[-1],
               'iterations': len(history) - 1, 'monotone': bool(np.all(np.diff(history) <= 1e-10)),
               'converged': bool(movement < d.tolerance),
               'last_step_norm': float(movement)}


def render(h, g):
    p, s, n = h.shape
    result = np.zeros((p, g.shape[1], n + g.shape[2] - 1))
    for pos in range(p):
        for inp in range(g.shape[1]):
            for speaker in range(s):
                result[pos, inp] += np.convolve(h[pos, speaker], g[speaker, inp])
    return result


def metrics(h, g, target, d):
    y = render(h, g)
    freq = np.fft.rfftfreq(1024, 1 / d.sample_rate)
    band = (freq >= 25) & (freq <= 180)
    yf = np.abs(np.fft.rfft(y, n=1024, axis=-1))[..., band]
    tf = np.abs(np.fft.rfft(target, n=1024, axis=-1))[..., band]
    error = 20 * np.log10(np.maximum(yf, 1e-9) / np.maximum(tf, 1e-9))
    energy = np.sum(y * y, axis=-1)
    late = np.sum(y[..., d.late_start:] ** 2, axis=-1)
    target_energy = np.sum(target * target, axis=-1)
    l1 = np.sum(np.abs(g), axis=(1, 2))
    return {'spectral_rmse_db': float(np.sqrt(np.mean(error ** 2))),
            'worst_position_spectral_rmse_db': float(np.max(np.sqrt(np.mean(error ** 2, axis=(1, 2))))),
            'target_nmse': float(np.sum((y - target) ** 2) / np.sum(target ** 2)),
            'late_energy_relative_target_db': float(10 * np.log10(max(np.mean(late / target_energy), 1e-15))),
            'late_energy_fraction': float(np.mean(late / np.maximum(energy, 1e-15))),
            'filter_energy': float(np.sum(g * g)),
            'speaker_l1_max': float(l1.max()),
            'conservative_required_input_backoff_db': float(max(0, 20 * np.log10(max(l1.max(), 1e-15)))),
            'target_delay_ms': 1000 * d.delay / d.sample_rate,
            'filter_support_ms': 1000 * d.taps / d.sample_rate}


def mismatch(h, seed=33):
    """Unseen +/-1ms delay, +/-10% gain and IR noise; not clock-drift proof."""
    rng = np.random.default_rng(seed)
    result = h.copy()
    for p in range(h.shape[0]):
        for s in range(h.shape[1]):
            shift = int(rng.integers(-1, 2))
            row = h[p, s]
            if shift == 1:
                row = np.r_[0, row[:-1]]
            elif shift == -1:
                row = np.r_[row[1:], 0]
            result[p, s] = row * rng.uniform(0.9, 1.1) + rng.normal(0, 0.0005, row.shape)
    return result


def experiment(speakers=3, d=Design()):
    h, target = fixture(speakers, d=d)
    baseline = np.zeros((speakers, 2, d.taps))
    baseline[0, 0, d.delay] = baseline[1, 1, d.delay] = 1
    independent, ir = optimize(h[:4], target[:4], d, independent=True)
    joint, jr = optimize(h[:4], target[:4], d)
    result = {'label': 'SYNTHETIC ONLY: ' + ('2.1 L/R/sub' if speakers == 3 else 'hypothetical six full-range sources'),
              'design': asdict(d), 'seed': 1820, 'train_positions': [0, 1, 2, 3],
              'held_out_positions': [4, 5], 'solver': {'independent': ir, 'joint': jr}, 'methods': {}}
    for name, g in [('baseline', baseline), ('independent', independent), ('joint', joint)]:
        result['methods'][name] = {
            'train': metrics(h[:4], g, target[:4], d),
            'held_out': metrics(h[4:], g, target[4:], d),
            'mismatch_held_out': metrics(mismatch(h[4:]), g, target[4:], d)}
    regressions = []
    for split in ('train', 'held_out', 'mismatch_held_out'):
        for metric in ('spectral_rmse_db', 'late_energy_relative_target_db', 'target_nmse'):
            for comparator in ('baseline', 'independent'):
                j = result['methods']['joint'][split][metric]
                c = result['methods'][comparator][split][metric]
                if j > c:
                    regressions.append({'split': split, 'metric': metric, 'versus': comparator,
                                        'joint': j, 'comparator': c})
    result['regressions'] = regressions
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    report = {'experimental': True, 'production_export': False,
              'coverage': 'synthetic numerical model only; no live audio',
              'experiments': [experiment(3), experiment(6)]}
    args.output.write_text(json.dumps(report, indent=2, allow_nan=False) + '\n')
    print('Wrote synthetic metrics only (no filter/profile export):', args.output)


if __name__ == '__main__':
    main()
