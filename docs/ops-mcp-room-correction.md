# Ops MCP room-correction workflow

How an agent uses the [ops MCP server](ops-mcp.md) to turn REW measurements into
bounded CamillaDSP corrections for the two mains and the sub. Measuring is done by a
person with REW and a microphone. The server never plays a test signal, never runs
a sweep and never moves the microphone.

The dashboard's own [measurement workflow](measurement-workflow.md) and calibration
records are separate and unchanged. This workflow keeps its own history in the ops
server's `state_dir`. It reuses the repository's REW text parser
(`garage_audio/calibration.py`) so both accept the same export format.

## Before you start

* Map pipeline channels in the config: `camilladsp.channels` = `{"left": 0, "right": 1, "sub": 2}`
  (your indexes may differ). Proposals for an unmapped channel can be previewed but
  not applied.
* Reading and proposing need no write permission. Applying needs `allow_write=true`.
  The CamillaDSP websocket must be reachable from the ops server. In isolated
  deployments it is not (see [open risks](ops-mcp.md#known-limits-and-open-risks)).
  There you can still use the YAML preview with your normal, reviewed configuration
  process.
* Note the current state first: `dsp_get_filters` and `dsp_list_backups`.

## 1. Measure and export (person, in REW)

1. Measure each channel separately from the listening position, at a safe level.
   Use the same microphone position for every channel and for re-measurements.
2. Export each measurement with *File > Export > Export measurement as text*.
   The file has optional `*` header lines, then `frequency magnitude [phase]` rows.
3. If you want timing alignment from measurements, note each channel's impulse peak
   time (ms) from REW's impulse view, using the same timing reference for all
   channels. Otherwise measure each speaker's distance to the listening position.

## 2. Import

`room_import_measurement` with `channel` (`left` | `right` | `sub`) and `text` (the
whole export, up to 256 KiB). Optionally add `label`, `distance_m` and `notes`.

* Returns `measurement_id`, point count, frequency and level range, and a SHA-256 of
  the input.
* Microphone calibration is **not** applied by this import. Export the measurement
  with the calibration already applied in REW.
* `room_list_measurements` shows the history (newest first). Retention is bounded
  by `room_correction.history_retention`.

## 3. Compare to a target

`room_compare_to_target` with `measurement_id`, and optionally `freq_min_hz`,
`freq_max_hz`, `smoothing_octaves`, `tolerance_db` and `target`.

* The `target` object takes `tilt_db_per_octave` (−3 to +3) and `bass_shelf_db`
  (−12 to +12) at `bass_shelf_hz`. The default target is flat.
* The comparison is relative: the median offset is removed, so only the *shape*
  matters, not the absolute level.
* Positive deviation means louder than target. Those are cut candidates.
* Negative deviation means a dip. Dips are often room nulls (cancellation) that EQ
  cannot fix. Boosting them wastes amplifier headroom and can stress drivers.

## 4. Propose PEQ (cut-biased)

`room_propose_peq` with `measurement_id`, and optionally `max_filters`,
`max_boost_db` and the same range, smoothing and target options.

The algorithm is a greedy fit on a log-frequency grid:

1. Smooth the response (default 1/6 octave).
2. Find the largest peak above `tolerance_db` and place a Peaking cut on it. The
   width comes from the half-height bandwidth, and Q is clamped to the limits.
3. Keep a filter only if it lowers the RMS deviation. Stop at `max_filters` or
   when nothing above tolerance remains.

Boosts are only considered when `max_boost_db > 0`. Even then they are limited to
half the dip and must be worth twice a cut to be chosen.

Bounds, all of which can only be tightened:

* Range defaults to 25–300 Hz (`room_correction.freq_min_hz` / `freq_max_hz`): room
  modes, where EQ helps most.
* `room_correction.max_boost_db` defaults to **0**, which means cut-only.
* `room_correction.max_filters` defaults to 6 per channel.
* Every filter is checked against the speaker-protection limits (gain, Q,
  frequency) before the proposal is stored.

The result includes the filters plus RMS deviation, worst peak and worst dip before
and after, as predicted by the filter math. It is stored as a proposal
(`proposal_id`) and **does not change DSP**.

## 5. Propose mains and sub alignment

`room_propose_alignment` takes any of:

* `impulse_peaks_ms` per channel (preferred, measured with a shared timing
  reference). Every channel is delayed to match the latest arrival.
* `distances_m` per channel. Delay = (farthest − this distance) / 343 m/s.
  Use this when you have no timing reference.
* `levels_db` per channel: level trims that bring every channel **down** to the
  quietest one. Trims are never positive.

Results are checked against `delay_max_ms` and the channel-gain limits, then stored
as a proposal. Distance-based delay ignores the sub's own processing latency. Confirm
the crossover region by measurement (step 8). Polarity is not changed by this tool.

## 6. Preview

`room_preview_yaml` with `proposal_id` returns a CamillaDSP YAML fragment:

* the filters, named `ops_rc_<channel>_<n>` for PEQ, or `ops_<channel>_delay` and
  `ops_<channel>_gain` for alignment
* `Filter` pipeline steps on the mapped channel indexes
* any unmapped channels

Review it, or merge it by hand through your normal configuration process.

## 7. Apply (write-gated)

`room_apply_correction` with `proposal_id`:

1. Every filter is re-checked against the **current** limits and room bounds.
2. A PEQ proposal replaces earlier `ops_rc_<channel>_*` filters for that channel, so
   applying again does not stack corrections. An alignment proposal sets the
   `ops_<channel>_delay` and `ops_<channel>_gain` filters.
3. The change goes through the shared DSP write path: limit check, CamillaDSP
   validation, **automatic backup**, apply, readback. The result includes `backup_id`.

## 8. Verify and roll back

Re-measure each channel at the **same** microphone position and import the new
measurements. Then run `room_compare_to_target` on old and new and compare
`rms_deviation_db`. Listen at a modest level. If the result is worse, run
`dsp_restore_backup` with the `backup_id` from step 7. The restore is validated and
backed up too.

Iterate in small steps: one channel at a time, sub first (largest modal problems),
then the mains, then alignment. Predicted improvements are filter math on one
measurement position, not proof of what you hear.
