# Offline measurement workflow v1

## Quiet preparation journey (source update, not yet deployment evidence)

Audio components are intentionally not installed. They are **not a blocker** to
room planning, recorded REW imports, recommendations or imported comparisons.
Connect now leads with “Prepare your listening room”; external MCP configuration
is optional secondary “Use my own agent” setup. Check leads with a private room,
optional photo, editable P/V labels and whole-room/seat/area/custom listening goal.
Hardware/readiness details stay collapsed for later sound work. Existing no-sound
boundaries and disabled SDK/provider inference are unchanged.

One evidence-aware deterministic guide uses actual room/profile/session/proof
state and unique qualified fitting slots, not file count or navigation. It never
promotes held-out imports into fitting evidence or invents assistant messages.
Photos are private local assets, not provider input. Recent activity shows actual
server acknowledgements, not fictitious agent narration. Selected opaque room,
goal and fitting-session IDs can resume in sessionStorage after validated readback;
no token/photo bytes are stored there, and Lock clears pointers/private views.

Measure offers a fitting-only map-labelled export checklist and explicit source,
mic/calibration/orientation/rate/timing fields with retained drafts on import
errors. Empty plans are useful saved preparation. Improve explains predictions
and valid unchanged outcomes, then `candidates.save` copies the selected fixed
baseline and appends validated proposal cuts. It preserves baseline delay,
polarity, crossover and gain; the compiler already reserves -6 dB headroom, so
the candidate does not add another -6 dB channel attenuation. No apply occurs.
Candidate list/get readback marks stale provenance; stale candidates cannot freeze.

Verify is a typed comparison form rather than expert JSON: choose baseline,
candidate and rollback reference; review held-out labels, matching chain and
engineering rules; review the fixed protocol; explicitly freeze; then import
baseline/guided held-out exports and declare calibration/QC. No quality approval
is prechecked. Unknown QC honestly yields inconclusive. Saved export QC can be
reviewed again after reload without re-importing; unfinalized QC is local memory
only and resets to unknown on reload. Finalized report retains its declarations.
Saved protocols/results are profile-scoped and resume with fresh stale guards.
Results say imported evidence supports/does not support the candidate, or that
more reliable evidence is needed. Nothing is applied, and no independent capture,
human attendance or acoustic improvement is claimed. Fixed-level changes are
shown separately. Branch-only reports remain partial; combined-system response
is imported directly. A stale report cannot masquerade as a current recommendation.

Desktop and 390px browser-viewports require actual isolated browser journey proof;
neither is physical-phone or real-room evidence. This update adds no public demo
that relabels synthetic data measured. Test originals explicitly identify recorded
numerical fixtures, even when exercising claimed-measured metadata. The production
user's rooms/photos/imports are not test inputs. Hardware/capture/LLM enablement is
a separate future scope, not a prerequisite for enjoying the quiet workflow.

## Frozen imported-measured commissioning comparison (additive v1)

This quiet backend gate is available through `garage_operation` and authenticated
HTTP operations, not yet a new dashboard Verify screen or deployed release. It
does not capture, apply, approve sound, or set `verified_acoustically`. Numerical
fixtures testing claimed-measured metadata remain synthetic test evidence.

1. Confirm a room map with explicit mic height/orientation, select its listening
   profile with disjoint fit/verify positions. Fit an existing calibration plan
   using **only** its exact fit positions; analyze/propose under current revision.
   Save separate fixed baseline/guided offline profile candidates and a rollback
   reference. Their snapshots are declarations, not evidence they were applied.
2. Create two **empty legacy** `sessions.create` condition sessions. Call
   `commissioning_proof.freeze` with `spec` before any condition imports. The
   protocol snapshots room/map/listening plan, proposal, exact fit sources,
   baseline/guided/rollback profiles, chain, capture allowance and criteria.
   Existing held-out imports cannot be retroactively labeled prospective by this
   API. Re-exported historical data are still self-reported provenance: import
   ordering cannot prove the operator had never seen the original acoustics.
3. Import REW text through `measurements.import` into each condition session,
   one source per frozen position/channel. Match explicit measured provenance,
   applied calibration hash, microphone serial, orientation, rate and timing
   reference. Actual acquisition still needs separate attended authorization;
   `source:measured` is not independently authenticated physical capture.
4. `commissioning_proof.analyze` takes `{id,pairs}` with exact complete baseline/
   guided slots and separate export/operator QC attestations. Unknown/clipped/
   low-noise-margin/unknown-level QC returns an **inconclusive** report. Synthetic,
   unverified, duplicate, mismatched chains, incomplete bands/pairs or extra
   condition captures are rejected. No gain adjustment or recapture is triggered.
5. `commissioning_proof.get` revalidates every snapshot and complete source
   session digest, including on fresh-process reads. Changed map, plan, profile,
   proposal, fit or held-out source hides the report (`stale:true`). Receipts are
   immutable; identical finalize retries return the same digest. Retain old
   receipts and freeze a new protocol rather than editing one to recover a pass.

The strictly increasing bounded grid defines the fixed common band. A single
`reference_offset_db` and flat `target_db` are frozen before test imports, applied
unchanged to both conditions and every position; **no per-position/per-after
median normalization**. Report per-position RMS/peak target error, response
variation, maximum bin worsening and mean level change/attenuation. Keep requires
minimum aggregate improvement, no excessive bin/RMS worsening, no excessive
absolute level change, sufficient QC, and measured **combined-system** imports.
Uniform attenuation cannot bypass the separate level guard. Thresholds are
explicit engineering dB criteria, not audibility or measurement-derived intervals.
Combined response is imported directly; branch magnitudes are never summed.
Branch-only results are partial/inconclusive, not 2.1 acceptance. This magnitude
gate does not infer sub delay, polarity, crossover alignment or phase corrections.

`keep`/`reject`/`inconclusive` are report recommendations only. `applied:false`,
`verified_acoustically:false`, `sound_disabled:true` remain invariant; rollback is
only a profile reference, not an action. This is not independent real-room proof.
Supported mic/interface, trusted attended authority, commissioned native routing,
bounded actual capture/QC, candidate application and independently held-out actual
measurements/rollback readback remain separate physical gates.

This implements reproducible deterministic **magnitude-only** processing, not
garage acoustic success, automated hardware capture, Dirac or ART equivalence.
`sound_disabled` is always true. There is no hardware sweep/capture executor or
proposal apply operation. [File/null measurement jobs](measurement-jobs.md) now
provide a separate authenticated human-fixture approval and actual offline child
execution journey, never hardware/sound authority. Human-sounding caller strings
cannot unlock any sound action. Existing broker safety/authentication is unchanged.

Create a plan, import REW response text for each named position/channel, analyze,
propose, then read back. See [`API_CONTRACT.md`](../API_CONTRACT.md) for exact HTTP/MCP operations.
Server UMIK plus verified UMC is the first hardware prerequisite; laptop REW
imports remain supported. Digital -40 dBFS is a quiet **specification default**,
not a promised safe acoustic SPL. The bounded sweep specification has a five
second sweep, one second pre-silence, two second tail and eight second auto-stop.
No signal is generated or sent. A future executor must implement independently
trusted attended approval, device calibration, clipping checks, capture quality,
clock correction and hard fail-close before use.

## Data and limits

The session embeds versioned state: planned → imported → analyzed → proposed.
New imports invalidate derived results; cancellation is terminal but retains
readback/originals. Sessions/imports/storage retain existing bounded limits and
process-independent lock. Lightweight list manifests omit analysis/proposal
curves and original text. Original text, headers and SHA256 remain in detailed
records. Explicit synthetic metadata persists through import, analysis and
proposal; measured and synthetic sources cannot be pooled. Legacy imports with
no source are unverified-source responses, not silently asserted measurements.

Every planned channel/position requires matching sample rate/orientation and
explicit calibration hash/applied status, timing reference/quality. Calibration
application is caller-supplied provenance, not independently validated. Multiple
imports for a channel/position are ambiguous and rejected; start a new session.
Calibration hash and microphone serial must agree across positions per channel.
Unknown timing/drift is supported only for magnitude processing. Even claimed
verified timing cannot unlock phase/delay/crossover inference in this version.
Response text cannot validate separate UMIK/output clocks or their drift.

Known absolute `clock_drift_ppm` above **100 ppm** blocks proposal creation
(`clock_quality_required`) while retaining analysis and originals. This is a
conservative eligibility policy, not an acoustically validated limit: at the
five-second planned duration 100 ppm corresponds to 0.5 ms accumulated mismatch.
No compensation is implemented or accepted as validated provenance. Values at
or below the threshold remain supplied, unverified provenance. Missing drift
remains explicitly provisional magnitude-only processing with a persistent
warning; even `timing_quality:verified` cannot bypass known-high-drift gating or
grant clock synchrony/phase/delay claims. Analysis records per-file drift and
timing quality, eligibility and offending file IDs. Synthetic data carrying
known high drift is gated too, for deterministic reproducibility.

## Algorithm and electrical checks

Use common measured bandwidth (20–1000 Hz for mains, 20–300 Hz for sub), requiring
at least an octave, on 121 log-spaced points. Interpolate linearly in dB against
log-frequency; subtract each position's median response level, retaining offsets.
Aggregate using spatial median and retain min/max/spread. This robust descriptive
aggregate is not an energy sum or calibrated SPL. No phase alignment is assumed.

Select local positive peaks shared by all positions (>2 dB minimum), excluding
spatial spread >6 dB. At most four separated (>half octave) peaking cuts per
channel, scaled to 65% of remaining common peak and bounded to -6 dB each; never
boost a null. Width is estimated from the contiguous common peak support above
half-height (minimum 2 dB), requiring at least three supporting log-grid samples.
Q = center-frequency/support-bandwidth, bounded to 0.5–10; unresolved spikes or
peaks requiring higher Q are skipped rather than broadened. Cascaded existing
cuts reduce the next candidate's available cut. Trial gain is reduced in four
bounded steps (1, 1/2, 1/4, 1/8 of initial cut) until aggregate target RMS improves
and **every position** has non-worsening predicted target RMS on both the log
grid and all original imported frequencies within the common supported analysis
band (plus interpolated band endpoints). Target is the fixed normalized 0 dB
curve; there is no post-filter renormalization hiding loss. Pre/post RMS metrics,
per-position original/grid curves and provenance remain available. These are
sampled predicted magnitude checks, not full-band or measured acoustic guarantees.
If no candidate passes, store zero cuts with `useful_cut_available:false` and
an honest “No useful conservative cut available; leave unchanged” message.
RBJ digital peaking transfer functions are evaluated at the declared sample
rate, on 481 log-spaced 20–20000 Hz points. Reserved headroom is 6 dB and the
maximum electrical response must remain below -5.99 dB. This is PEQ-only:
no crossover, master gain, sum-of-channels, loudspeaker, room or microphone.
The resulting normalized magnitude multiplication plot is a **prediction**,
not a measured after-response. New attended measurements are needed for actual
acoustic verification. No automatic profile creation/application is provided.

## Executable checks

`PYTHONPATH=. python tests/measurement_workflow.py` covers strict schema/authority,
multi-position analysis, calibration mismatch/missing metadata, synthetic
provenance, bounded filters/headroom, immutable cancellation, import invalidation,
original preservation and fresh-process disk readback.
Additional regression cases reproduce the reviewer narrow-spike failure, a
useful broad peak, conflicting positions, and known-high/acceptable/unknown drift.
Analysis revision 2 preserves position curves and gates proposal creation;
older persisted analyses must be reanalyzed before a new proposal is generated.
Every public session read/list and mutation response applies the same freshness
projection. Old analysis/proposal payloads and their validation claims are hidden,
availability flags become false, and effective state becomes imported/planned;
cancelled stays terminal. `derived_results_superseded` and a persistent warning /
recompute prerequisite direct the agent/UI to analyze then propose again.
Original imports and private stored history remain intact. Old lightweight
manifests refresh once, under existing bounded storage/retrieval limits and a
128-session maximum; current lists do not reparse detailed curves. Status remains
unverified and count-only, with a current analysis-revision/read-validity label,
never a claim that stored old proposals are validated. Regression tests cover
both full read routes, old manifest list refresh, terminal cancellation and
successful fresh recomputation from the same retained originals.
`PYTHONPATH=. python tests/measurement_mcp.py` uses the real official SDK client,
stdio server, authenticated local HTTP and disk for create→import→analyze→propose
→readback→cancel. These fixtures are synthetic; no hardware acceptance is implied.
