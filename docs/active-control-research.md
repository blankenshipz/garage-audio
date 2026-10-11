# Experimental multi-channel room-control research

**Synthetic numerical research only.** No garage measurement, sound, production
profile, DSP integration, or claim of Dirac/ART equivalence. This is delayed
feed-forward FIR **response/late-energy reshaping**, not a microphone-feedback
absorber and not a demonstrated change to the room's intrinsic damping. It is
isolated from the dashboard, MCP, application dependencies, and profile storage.

## Reviewed primary sources (2026-10-07)

These are original papers, not marketing summaries. Accessible sections listed
below were actually read from extracted original PDFs; mathematical text
extraction can lose symbols. No paper code or proprietary implementation copied.
Downloaded PDFs/extractions are retained privately, not bundled in the project.

1. **S. J. Elliott and P. A. Nelson (1988), “Multiple point least squares
   equalisation in a room,” NASA-CR-181733 / ICASE-88-61.**
   [Official record](https://ntrs.nasa.gov/citations/19890004692),
   [original report PDF](https://ntrs.nasa.gov/api/citations/19890004692/downloads/19890004692.pdf).
   Reviewed official metadata/abstract and report section 3: multi-point squared
   error, delayed targets, conflicting front/rear requirements, and importance of
   position-dependent propagation delay. The reported car simulation is not our
   room. The report is dated October 1988; do not confuse it with later journal
   versions. Full PDF accessible; selective sections reviewed, not entire report.

2. **Ole Kirkeby, Philip A. Nelson, Hareo Hamada and Felipe Orduna-Bustamante
   (1998), “Fast Deconvolution of Multichannel Systems Using Regularization,”
   IEEE Transactions on Speech and Audio Processing 6(2), 189–194.**
   [University-hosted original PDF](https://resource.isvr.soton.ac.uk/staff/pubs/PubPDFs/Pub192.pdf).
   Reviewed title/abstract, sections II/III formulation, III-C effort penalty and
   IV FFT design, wraparound, modeling delay, and 4×4 example. The first page
   shares space with an unrelated previous article; its audio-coder claims are
   not this paper. University repository metadata returned HTTP 401; the original
   PDF was accessible. We use its plant/target/filter matrix and regularization
   rationale, **not its FFT/cyclic-shift algorithm**: our full linear-convolution
   finite-support solve avoids assuming FFT inversion automatically is causal.

3. **Aki Mäkivirta, Poju Antsalo, Matti Karjalainen and Vesa Välimäki (2001),
   “Low-Frequency Modal Equalization of Loudspeaker-Room Responses,” AES
   111th Convention, preprint 5480.**
   [Official university record](https://research.aalto.fi/en/publications/low-frequency-modal-equalization-of-loudspeaker-room-responses/),
   [Genelec-hosted original paper](https://assets.ctfassets.net/4zjnzn055a4v/1PmYuXIBmuySLxSGG1iViz/bd768fcbeb5a5b2a4655793186a1973d/Low-Frequency_Modal_Equalization_Of_Loudspeaker-Room_Responses.pdf).
   Reviewed title, abstract, concept/type I/type II equations and implementation
   discussion/conclusions: pole-zero decay replacement vs secondary radiators,
   low-frequency/listening-position scope, time-of-flight and secondary-source
   authority. The PDF footer uses the originally scheduled September convention
   dates; the official record lists Nov. 30–Dec. 3. Both identify **2001**. Do not
   label this PDF as the related, differently titled 2003 JAES article (not
   reviewed here). Full paper accessible; selected sections read. Its case studies
   are type I, not proof of our multi-secondary-source design. This prototype
   does not implement that paper's modal pole-zero identification/replacement.

4. **Ina Kodrasi, Stefan Goetze and Simon Doclo (2013), “Regularization for
   Partial Multichannel Equalization for Speech Dereverberation,” IEEE
   Transactions on Audio, Speech, and Language Processing 21(9), 1879–1890,
   DOI 10.1109/TASL.2013.2260743.**
   [Author-hosted original PDF](https://inakodrasi.github.io/publications_pdfs/Kodrasi_ITASLP_2013.pdf).
   Reviewed title/abstract/introduction and sections III/IV on RIR perturbations,
   regularization, partial equalization and L-curve tradeoffs. This is a
   **microphone-side speech dereverberation** problem; transferring the
   regularization/channel-shortening idea to speaker-side control is our design
   choice, not evidence its perceptual results apply to playback. Full PDF
   accessible; no PESQ results or L-curve optimization reproduced here.

## EQ, coordinated control, and what 2.1 permits

Independent per-speaker EQ cannot allocate cancellation across sources. Joint
filters can use distinct source-to-position transfer paths to trade response
error, late energy and effort. This depends on linear, time-invariant measured
paths, correct relative phase/timing, sufficient rank, and source bandwidth.
Shared nulls, near-collinearity, position movement, crossover limits and amplifier
headroom may make a desired target infeasible. Regularization limits inversion
of poorly observable directions; it does not repair missing acoustic authority.

Current **2.1** means two main speakers and one band-limited sub. It is useful
for bass/sub integration and limited local response shaping, not proof of
spatially broad active treatment. The toy 2.1 case has two independent program
inputs (L/R), no separate LFE; all three sources can serve either input in the
joint experiment. Stereo imaging and production bass management remain
unverified. The six-full-range-source case is hypothetical, not an equipment
recommendation or purchase request. Its RNG draw sequence differs from 2.1,
so it is **not a controlled same-room comparison of adding three speakers**.

## Implemented algorithm and explicit constraints

`research/active_control.py` builds a causal plant `H[p,s,n]`, target
`T[p,i,n]`, and FIR `G[s,i,k]`, where p=position, s=speaker and i=program input.
For each independent input, `Y[p,i] = sum_s H[p,s] * G[s,i]` with **full linear
convolution**, including its complete 431-sample tail. It minimizes:

```
(1/P) sum_{p,i,n} (1 + beta * 1[n >= late_start]) * (Y - T)^2
    + lambda * sum_{s,i,k} G^2
subject to G[k] = 0 outside k=0..47,
           sum_{i,k} abs(G[s,i,k]) <= 2.5 for every speaker,
           ||G||_F <= 2.5.
```

The target is a 0.7-amplitude direct impulse at each primary speaker's arrival
plus **12 ms modeling delay**. It is zero in the late window (>=80 ms), so the
weighted late error is explicitly late **output energy**, not only spectral
flattening. The sample rate is **1 kHz**, FIR support 48 ms, plant support 384 ms,
beta=4, lambda=0.08. These parameters are fixed experimental choices, not tuned
to measured garage responses. All filters are finite/causal by construction,
not post-truncated noncausal inverses. Broadband 48-kHz realization/resampling
and runtime block latency are not implemented or benchmarked.

Projected gradient uses an eigenvalue-based step, per-speaker L1-ball projection,
then global L2 shrinkage, and monotone backtracking. This sequential constraint
projection is feasible but is **not claimed to be the exact Euclidean projection
onto the intersection**, nor a certified optimal constrained solution. Iteration
limit, last step and convergence status are retained. Both joint examples reach
the 1200-iteration cap without the strict 1e-9 movement tolerance; do not call
them converged. The independent comparator restricts L->L and R->R, with
sub/auxiliary filters zero; it is a causal diagonal ridge-EQ comparator, **not**
a production parametric minimum-phase EQ implementation.

The cross-input L1 constraint bounds worst-case output for **simultaneous**
bounded inputs; unit inputs can still exceed unit output. Reported conservative
input backoff is `max(0, 20 log10(max_s L1_s))`. It is not permission to emit sound,
nor a calibrated SPL/excursion limit. No gain increase/headroom is silently
normalized away when calculating spectral metrics.

## Reproducible evaluation and observed tradeoffs

Seed 1820 creates direct arrivals, a reflection and four damped analytic modes
(43/67/97/137 Hz). First four positions train; last two are unseen. Sub is
causally low-passed only in the 2.1 model. This deliberately small toy is not a
geometrically validated room simulator. The baseline routes L/R with the **same
12 ms delay**; independent and joint use the same targets/constraints. Spectral
RMSE uses 25–180 Hz, target magnitude 0.7, without fitted gain. Target NMSE
includes phase/time. Late energy is normalized to target energy, with late
fraction also retained. The mismatch test adds unseen +/-1 ms per-path delay,
+/-10% gains and 0.0005-RMS IR noise at held-out positions (seed 33).

Observed numerical results (baseline / independent / joint):

| Layout/split | Spectral RMSE dB | Late energy / target dB |
|---|---|---|
| 2.1 train | 6.63 / 10.24 / 6.23 | -5.00 / -25.98 / -26.53 |
| 2.1 held-out | 5.07 / 9.63 / 6.05 | -8.68 / -28.58 / -31.74 |
| 2.1 mismatch held-out | 5.05 / 9.57 / 6.43 | -8.58 / -28.19 / -29.83 |
| six sources train | 6.49 / 9.14 / 3.64 | -5.69 / -27.37 / -30.44 |
| six sources held-out | 7.08 / 8.27 / 4.09 | -3.29 / -23.93 / -20.86 |
| six sources mismatch held-out | 7.02 / 8.32 / 6.05 | -3.58 / -23.80 / -10.46 |

Important failures are retained in the JSON `regressions` array: **2.1 joint
spectral RMSE is worse than baseline at held-out positions**, and six-source
joint late energy is worse than independent, dramatically so under mismatch.
The 2.1 joint bound saturates at L1=2.5 (7.96 dB conservative input backoff);
six-source joint requires 5.63 dB. Better training error does not demonstrate
broad spatial benefit. Late-energy reduction is an acoustic-model response
statistic, **not a fitted modal T60/EDT change**; the room poles themselves remain
unchanged. No listening-quality claim follows from these results.

## Running and acceptance

Use an isolated virtual environment, never add NumPy to base requirements:

```
python3 -m venv /private/tmp/opencode/active-control-venv
/private/tmp/opencode/active-control-venv/bin/pip install -r research/requirements.txt
/private/tmp/opencode/active-control-venv/bin/python -m unittest discover -s tests -p test_active_control.py -v
/private/tmp/opencode/active-control-venv/bin/python -m research.active_control --output /private/tmp/opencode/active-control-results.json
```

The CLI writes **metrics only**, never filters, device configs or profiles.
Tests check deterministic fixtures, full convolution tail, finite causal support,
effort limits, monotone training improvement, routing restriction, effective
late weighting, invalid-data rejection, and retained mismatch/regressions.
Numerical equivalence is tolerance-based across BLAS/platform implementations.

Before acoustic acceptance: attended human sound authorization; real calibrated
UMIK/verified output; repeated synchronized multi-position IRs; sample-rate,
UMIK independent-clock drift and orientation checks; reliable relative timing;
speaker bandwidth/SPL/excursion bounds; train/validation split untouched by
tuning; deliberate movement/temperature/crossover perturbations; baseline/EQ/
joint **measured** magnitude, impulse/late-energy and decay-slope comparisons;
listening/stereo-imaging evaluation; safe reversal. Preserve raw measurements.
Nothing here fulfills those hardware prerequisites. A later research extension
could use L-curve/held-out regularization selection, robust scenario training,
frequency-shaped source constraints and modal decay fitting; these are not
silently presented as implemented features.
