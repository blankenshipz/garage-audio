# Ops MCP room measurement and guided mic placement

The operator MCP server ([ops MCP](ops-mcp.md)) can measure the room itself, the way REW
does, and walk a person through placing the microphone. The results feed the existing
[room-correction workflow](ops-mcp-room-correction.md): compare to a target, propose PEQ,
propose mains/sub alignment, preview, apply, verify.

Nothing here is verified against real hardware yet. The engine is tested end to end against
a **simulated room** (known resonances, delays and noise; see "Verification" below). The first
real run needs a person in the room.

## Normal journey

1. **Setup (once, operator):** connect a measurement mic, install the measurement extra,
   fill in the `measurement` block of the config (player into the stack sink, recorder from
   the mic), set `allow_write=true` and `allow_measure=true`, validate with
   `--check-config`.
2. **Describe the room (agent + person):** `room_profile_update` with dimensions, speaker
   and sub positions, the main seat and ear height. Partial updates are fine; units m, cm,
   ft or in. `room_modes` explains the expected bass problems and placement advice.
3. **Plan:** `measure_session_start` creates a plan: the main listening position plus 4–8
   nearby positions on a circle (default 30 cm / 1 ft) at ear height, each described in
   plain words in metric and imperial ("about 30 cm to the left of the centre of the
   seat, same height").
4. **Per position:** the agent tells the person where to put the mic and **waits for their
   confirmation** → `measure_session_confirm_placement` → `measure_noise_check` (silent) →
   first position only: `measure_level_check` per channel (audible, needs
   `user_confirmed=true`) → `measure_sweep` per channel (audible, needs
   `user_confirmed=true`) → quality check → `measure_session_advance` (`next`,
   `redo_channel`, `redo_position`, `skip_position`).
5. **Result:** `measure_session_finish` averages every channel across the positions
   (power average by default, vector average optional) and stores the averages in the
   measurement history. Hand them to `room_compare_to_target` / `room_propose_peq`; hand
   the main-position sweeps to `room_propose_alignment` (`measurement_ids`).
6. **Exit and reconnect:** sessions are files under `state_dir`. If the agent disconnects,
   `measure_session_status` (or the `garage-audio://measure/session` resource) shows the
   exact step to resume at. `measure_abort` stops a running measurement at any time.

The MCP prompt **`guided-room-measurement`** (arguments `channels`, `units`) tells an agent
exactly this flow, including "never sweep without the person's confirmation".

## Recommended microphone

* **USB calibrated measurement mic** (for example a miniDSP UMIK-1 or UMIK-2): simplest. It
  comes with a per-serial calibration file (download both the 0° and the 90° file). Use a
  **USB extension cable** (a few metres) so the mic can sit at the seat while the computer
  stays put, and a mic stand: never hand-held.
* **XLR measurement mic** (omni, phantom powered) on an input of the audio interface. Use its
  calibration file if it has one. With an interface you can also wire a **loopback** (below)
  for exact timing.
* Phone microphones are **not** supported (see "Phone microphone (design only)").

Mic orientation: with a **90°** calibration file point the mic **straight up** at the ceiling;
with a **0°** file point it **horizontally at the speakers** (between them for the mains).
The plan tells the person which one, based on the calibration file's name or the
`mic_orientation` argument.

## How measurement interacts with the running stack

The signal path is exactly the music path:

```
measurement WAV -> play_argv (e.g. pw-play) -> stack sink (PipeWire) -> CamillaDSP -> DAC
                                                            (volume ceiling, limits, crossover)
```

* The ops server **never opens the DAC**. The DAC stays owned by the supervisor-managed
  stack. The configuration refuses `play_argv` that names a raw ALSA or PipeWire ALSA
  device (`hw:`, `plughw:`, `plug:`, `default:CARD=`, `sysdefault`, `front:`, `surround…`,
  `iec958`, `dmix`, `dsnoop`, `alsa_output.*`, `/dev/snd/…`), including the colon-less ALSA
  forms (case-insensitive): a `-D`/`--device` value that starts with `hw`, `plughw`, `plug`,
  `front`, `surround`, `iec958`, `dmix`, `dsnoop` or `sysdefault` is refused, and so is any
  other argument or option value that is exactly such a name, optionally followed by `:` or
  `,` and parameters (`hw`, `hw,0`, `surround51`). It also refuses an argv that runs an ALSA player (`aplay`,
  `speaker-test`) without an explicit device (`-D`/`--device`, and not `default`, which may
  be the DAC), or runs through a shell, `env` or `sudo`.
* Every signal passes CamillaDSP, so its main volume, the volume ceiling, the limits and the
  crossovers apply. The measured response is what you hear, including the current DSP.
* Before anything audible plays, the engine **refuses (never auto-fixes)** when:
  * the owner does not report `ready` (`stack_not_ready`);
  * CamillaDSP is unreachable or not Running/Paused (`stack_not_ready`). In the hardened
    deployment with a namespace-isolated CamillaDSP websocket this means measurement is
    unavailable, by design (see [ops MCP known limits](ops-mcp.md#known-limits-and-open-risks));
  * CamillaDSP is muted (`stack_muted`). Measurement never unmutes;
  * Music Assistant is playing on an allowed player (`stack_busy`). Pause it first;
  * Music Assistant is configured (`music_assistant.token_file` set) but not reachable
    (`ma_unreachable`): nobody can confirm that nothing else is playing, so the engine fails
    closed. Only when Music Assistant is not configured at all does it proceed, with a note
    that other playback was not checked;
  * the optional `sink_check_argv` probe fails (`sink_unavailable`);
  * the player/recorder is not configured (`measurement_unconfigured`).
* The DSP main volume and the `ops_<channel>_delay` delays active at that moment are stored
  with each measurement. Alignment subtracts those delays, so proposals stay absolute.
* Only one measurement runs at a time across all server processes sharing `state_dir`
  (`busy`). An audible measurement also holds the server's exclusive DSP-write lock.

### Channels

`measurement.channel_map` maps `left`, `right`, `sub` and `sum` to output channels of the WAV
sent to the sink. The generated multichannel WAV carries the signal only in those channels.

The stack's sink is usually stereo and **the sub is fed by CamillaDSP's crossover from L+R**.
Then `sub` must map to `[0, 1]` (the default), and a `left` sweep includes the part of the
sub that the crossover sends from the left input. If your sink exposes a dedicated sub input,
map `sub` to that channel instead.

The sub frequency cap (`sub_sweep_max_hz`) follows the **outputs**, not the channel name. A
*sub-only output* is an output in `channel_map.sub` that is in neither `left` nor `right`.
The cap applies to every sweep, level-check tone and timing chirp on `sub` **and on any
channel whose outputs include a sub-only output**. For example, with `sub: [2]` and
`sum: [0, 1, 2]`, a `sum` sweep is capped. With the default map (sub fed from `[0, 1]`) there
is no sub-only output, so only `sub` itself is capped. A `timing_reference` that includes a
sub-only output is refused by the configuration.

## Configuration

Install the optional extra into the server's virtualenv (numpy, pinned like
`research/requirements.txt`):

```
<venv>/bin/pip install '<path to garage-audio checkout>[measurement]'   # numpy==2.2.6
```

Without it the server starts and every other tool works; `measure_noise_check`,
`measure_level_check` and `measure_sweep` return `measurement_extra_missing`
("the measurement extra is not installed …") and `measure_status` shows `extra.installed=false`.

| Key | Default | Meaning |
|---|---|---|
| `allow_measure` | `false` | Together with `allow_write`: noise check, level check and sweeps. Env `GARAGE_OPS_MCP_ALLOW_MEASURE` |
| `measurement.backend` | `builtin` | `builtin` engine or `rew` (REW REST API, below) |
| `measurement.capture` | `process` | `process` (player/recorder argv) or `phone` (design stub; refused with `not_implemented`) |
| `measurement.sample_rate` | `48000` | Signal and capture rate |
| `measurement.play_argv` | none | Player argv, no shell; must target the stack sink. Placeholders `{file}`, `{rate}`, `{channels}`, `{seconds}` |
| `measurement.record_argv` | none | Recorder argv writing a WAV to `{file}` from the measurement mic |
| `measurement.sink_check_argv` | none | Optional read-only probe that exits 0 when the stack sink exists |
| `measurement.list_devices_argv` | `[["arecord","-l"],["pw-record","--list-targets"]]` | Listings for `measure_list_devices`. Only read-only listing programs are accepted: `arecord -l/-L`, `pw-record --list-targets`, `pw-cli ls/list-objects/info/dump`, `pw-dump` (no `--monitor`), `pactl list/info`, `wpctl status/inspect`; anything else (a player, a recorder, a setter) stops startup. The program must be given by bare name or as exactly `/usr/bin/<name>`, `/bin/<name>` or `/usr/local/bin/<name>`; any other path (e.g. `/opt/x/arecord`) is refused |
| `measurement.output_channels` | `2` | Channels of the played WAV (the sink's channel count) |
| `measurement.channel_map` | L `[0]`, R `[1]`, sub `[0,1]`, sum `[0,1]` | Output channels per measured channel |
| `measurement.capture_channels` / `.mic_channel` | `1` / `0` | Recorded channels and which one is the mic |
| `measurement.loopback` | `null` | `{"output_channel": N, "capture_channel": M}` for absolute timing |
| `measurement.timing_reference` | `left` | Reference speaker for acoustic timing without loopback (full range: not `sub`, no sub-only output) |
| `measurement.require_owner_ready` | `true` | Refuse unless the owner reports `ready` |
| `measurement.sweep_seconds`, `.sweep_min_hz`, `.sweep_max_hz` | `6`, `20`, `20000` | Default sweep |
| `measurement.target_snr_db` / `.min_snr_db` | `35` / `20` | Level-check target; below the minimum a sweep must be redone |
| `measurement.smoothing` | `1/48` | Stored response smoothing (`none`, `1/1` … `1/48`); `measure_get` can re-smooth |
| `measurement.retention` | `100` | Measurement file sets kept (oldest removed first) |
| `measurement.limits.*` | see below | Safety limits; can only be tightened |
| `measurement.rew.url` / `.timeout_seconds` | `null` / `60` | REW API base URL, e.g. `http://127.0.0.1:4735` (no credentials, path or query) |
| `measurement.rew.protocol` | `{}` | Per-key overrides of the REW endpoint/command table (below); validated |
| `measurement.plan.radius_m` / `.positions` | `0.3` / `6` | Default plan pattern (4–8 positions) |

Example with PipeWire tools (placeholders; find the names with `measure_list_devices`):

```json
"measurement": {
  "play_argv": ["pw-play", "--target", "<stack-sink-name>", "{file}"],
  "record_argv": ["pw-record", "--target", "<measurement-mic-name>", "--rate", "{rate}",
                  "--channels", "{channels}", "--format", "s24", "{file}"],
  "sink_check_argv": ["pw-cli", "info", "<stack-sink-name>"]
}
```

ALSA alternative for the recorder: `["arecord", "-D", "<mic-pcm>", "-f", "S24_3LE", "-r", "{rate}", "-c", "{channels}", "{file}"]`
(recording from a mic device is fine; only *playback* must go through the sink).

The server account needs access to the stack's PipeWire instance (for the player) and to the
mic (for the recorder), nothing more. See the commented lines in
`config/system/garage-audio-ops-mcp.service.example`. With `ProtectHome=yes`, a PipeWire
socket under `/run/user/…` is hidden from the service; point `PIPEWIRE_REMOTE` at a socket
path the unit can reach, or relax that one setting deliberately.

## Safety (code-enforced)

Hard limits live in `garage_audio/ops_mcp/safety.py` (`MEASURE_HARD`). The configuration
(`measurement.limits`) can only **tighten** them; a looser value stops startup.

| Limit | Default | Hard cap | Meaning |
|---|---|---|---|
| `sweep_level_max_dbfs` | −18 | −6 | Peak digital level of any measurement signal |
| `start_level_dbfs` | −40 | −30 | First level-check tone; also the sweep level before any level check |
| `level_step_max_db` | 3 | 6 | Largest increase per level-check step |
| `sub_sweep_max_hz` | 200 | 250 | Upper frequency of any sweep/tone/chirp on the sub or on a sub-only output |
| `sweep_seconds_max` | 10 | 20 | Longest single sweep |
| `max_duration_seconds` | 30 | 60 | Longest audible time of one action (sweep + pre/post roll, or all level-check steps) |
| `sweep_min_hz` / `sweep_max_hz` | 15 / 20000 | 10 / 22000 | Sweep range |
| `level_ttl_seconds` | 1800 | 1800 (min 10) | How long a confirmed level (level check) stays valid |

Further rules:

* **Gate:** `measure_noise_check`, `measure_level_check` and `measure_sweep` need
  `allow_write=true` **and** `allow_measure=true`. They are refused server-side before their
  arguments are parsed. `measure_abort` is always available.
* **Confirmation:** level checks and sweeps require `user_confirmed=true`. The agent must tell
  the person what will play and get their agreement first (`confirmation_required`).
* **Gentle start, stepwise level check:** a short tone starts at `start_level_dbfs` and rises
  by at most `level_step_max_db` per step (at most 8 steps). It stops at the target SNR, when
  the mic capture gets within 6 dB of full scale, on clipping (then it backs off 12 dB), at
  the level cap, or when the duration budget is used.
* **Sweeps never exceed the confirmed level:** without a valid level check, a sweep plays at
  `start_level_dbfs`; with one, at most at the level it chose. An explicit `level_dbfs`
  above that is refused (`safety_limit`) with the reason.
* **Confirmed levels expire and are bound to the stack:** a confirmation records when it
  was made and a snapshot of the DSP main volume, mute, the channel's outputs, a digest of the
  DSP mixer routing, the capture settings (recorder argv, mic channel, channel count) and the
  calibration id. It is valid for `level_ttl_seconds` (default and maximum 30 min) and only
  while that snapshot still matches. A change, an unreadable volume or DSP configuration, a
  clock step back or a session file from before this rule means: back to the start level
  until a new level check. `measure_status` shows each confirmation with its expiry.
* **Timing chirp level:** without loopback, the short reference chirp on the
  `timing_reference` speaker plays at most at the sweep level **and** at most at that
  speaker's own valid confirmed level. If the reference speaker has no valid confirmation,
  the chirp is held at `start_level_dbfs`, even when the swept speaker was confirmed louder.
* **Every generated file is checked** for its peak level before it is played.
* **Sub:** sweeps, tones and chirps on `sub` or on any sub-only output are limited to
  `sub_sweep_max_hz` (see "Channels").
* **Max duration and watchdog:** an action whose planned audible time exceeds
  `max_duration_seconds` is refused before anything plays; a watchdog stops the children if
  they overrun it by more than 5 s (`measurement_timeout`).
* **Abort:** `measure_abort` stops the player and recorder processes **this server started**
  (signalled through their process handles, so a reused pid is never hit), never any other
  process. From another server process sharing the same
  `state_dir`, it signals the running one through a file in `state_dir`. An aborted
  measurement returns `measurement_aborted`; its files are discarded.

These limits protect against bad parameters, not every hazard. Keep the amplifier gain
structure and hardware protection in place, and start with the DSP main volume low.

## Engine

* **Signal:** exponential (log) sine sweep with fades, plus pre-roll silence (noise estimate)
  and a tail (room decay).
* **Deconvolution:** Farina inverse filter (time-reversed sweep with an amplitude envelope),
  FFT convolution → impulse response.
* **Outputs:** impulse response (WAV), frequency response with fractional-octave power
  smoothing, phase and group delay, arrival time, noise floor and SNR per octave band,
  capture peak/RMS/clipped samples, a quality verdict (`ok` / `warn` / `redo` with reasons:
  clipping, no signal, low SNR, no clear impulse (mic disconnected/noise only), DC offset).
* **Timing:**
  * With `measurement.loopback` (an output of the interface wired back to an input), the
    arrival time is measured against the loopback channel: **absolute**,
    `timing_source=loopback`, `timing_confidence=high` (low if the loopback is weak).
  * Without loopback, each sweep is preceded by a short reference chirp on the
    `timing_reference` speaker: arrivals are **relative to that speaker**,
    `timing_source=acoustic_reference`, `timing_confidence=low`. The result records the
    chirp's level and band (`reference_level_dbfs`, `reference_band_hz`).
  * `room_propose_alignment` with `measurement_ids` uses these arrivals, removes the DSP delays
    that were active while measuring, and refuses mixing timing sources or references.
* **Calibration:** `measure_import_calibration` accepts UMIK/miniDSP files (`"Sens Factor
  =…dB, AGain=…"` header) and generic `freq dB` files, records 0°/90° from the name or an
  argument, and stores them (last 20). Pass `calibration_id` to sweeps or sessions; the
  correction is subtracted from the response. The sensitivity factor is recorded; responses
  are relative (dB re the digital transfer), not absolute SPL.
* **Files** (per measurement, under `state_dir/measurement-files/<id>/`, directory 0700,
  files 0600, written atomically): `measurement.json` (channel, position, session,
  calibration, settings, timing, quality, stack state, timestamp), `response.frd`,
  `response-rew.txt` (REW-importable text), `impulse.wav` (mono 32-bit PCM, peak-normalised). The response also
  enters the measurement history, so `room_compare_to_target` and `room_propose_peq` take its
  `measurement_id`. Bounded by `measurement.retention`.

## Guided session

Phases: `await_placement` → `await_noise_check` → `await_sweeps` → `position_review` →
(next position …) → `ready_to_finish` → `finished` (or `cancelled`).

* Sessions are JSON files in `state_dir/measure-sessions/`, written atomically (0600). One
  session is active at a time (`session_active`; `replace=true` cancels the old one). Up to 20
  are kept.
* The noise check refuses to advance a too-noisy position unless the person accepts it
  (`accept_noisy=true`); a silent mic input is reported as such.
* A sweep with quality `redo` blocks `next` until it is redone or explicitly accepted
  (`accept_quality=true`).
* The main position (P0) cannot be skipped. `measure_session_finish` needs every position
  measured unless `allow_partial=true`.
* While a measurement of the session is running in this server, `measure_session_advance`
  (`next`, `redo_channel`, `redo_position`, `skip_position`) is refused with `busy`: wait for
  it or call `measure_abort`. Every advance also bumps a session **epoch**. A measurement
  that started before the step changed (for example a second server process redid the
  position meanwhile) is kept in the history but **not attached** to the session
  (`session_note` says so), so the redone position still needs placement and a noise check.
* Known limitation (advance): the `busy` check sees only measurements running in **this**
  server process. A sibling process sharing `state_dir` is not detected there; its late
  result is still kept out of the session by the epoch check, but the advance is not refused.
* Known limitation (finish): `measure_session_finish` does not refuse while a measurement is
  running, and only the vector timing check runs before anything is saved. If saving a later
  channel's average fails for another reason (for example an unreadable record), averages
  already saved for earlier channels stay in the history while the session remains active.
* Averages: power average (default; robust, magnitude only) or vector average (keeps phase).
  The per-channel averages and the main-position sweeps are returned with the next steps.
* **What phase a vector average uses:** each sweep's impulse response is windowed around
  **its own** impulse peak, and that peak's (fractional) delay is removed before the phase is
  computed. So the vector average combines *peak-aligned* phase, not phase on a shared
  absolute clock. Loopback or acoustic timing is not used to align the phase. Arrival
  differences between positions are therefore removed, not averaged. If a position's
  strongest peak is a reflection rather than the direct sound, its phase is referenced to
  the wrong instant and skews the average.
* Vector averaging is refused (`timing_low_confidence`) when the inputs mix timing sources
  or any input has low or unknown timing confidence (acoustic-reference and REW timings are
  always low). `allow_low_confidence=true` (on `measure_average` and
  `measure_session_finish`) computes it anyway; the result is then flagged
  (`low_confidence`, `low_confidence_reasons`, `LOW-CONFIDENCE` in its notes). The finish
  step checks every channel before saving anything, so a refusal leaves no partial averages.
  Power averaging never needs timing.
* Resources: `garage-audio://room/profile`, `garage-audio://measure/plan`,
  `garage-audio://measure/session`.

## Room model

`room_profile_get` / `room_profile_update` keep `state_dir/room-profile.json`:
`dimensions_m`, `shape`, `speakers` (left/right/sub/sub2/center positions),
`listening_positions` (with `ear_height`), `seating` (width/depth), `obstacles`, `notes`.
Coordinates: x from the left wall, y from the front wall (the wall behind the speakers),
z up from the floor, all in metres after conversion.

`room_modes` lists axial modes up to a limit, the modes the seat is near a peak or null of,
an estimated Schroeder frequency and simple advice (do not sit at half or quarter room
length/width or at half the ceiling height; corner subs excite every mode; try sub positions
with a crawl). It also accepts what-if dimensions without storing them.

## REW backend (optional, default off)

`measurement.backend = "rew"` drives REW 5.40+ through its REST API
(`measurement.rew.url`, unset by default; normally `http://127.0.0.1:4735` on the same machine.
The REW API has no authentication, so only point it at a REW on a host and network you
control) for `measure_sweep`; results are stored the same
way (`timing_source=rew`). REW plays through the output device configured **inside REW**;
that must be the stack sink, never the DAC. The server cannot verify REW's routing and says
so in every result. Noise and level checks are not available through this backend
(`not_supported`). The sweep level sent to REW is still capped by the same policy (start
level unless a valid level check was made with the builtin backend).

**A REW Pro licence is required.** Starting a sweep and reading results back through the API
is a Pro feature; without it REW refuses the measure command and the tool returns
`rew_error`.

Behaviour:

* REW's **blocking mode** is read first, switched on for the measurement and switched back
  off afterwards if it was off. If it could not be read it is left on, and the result notes
  that.
* The blocking measure call runs in a **worker thread**. `measure_abort` or the action's
  deadline sends REW's cancel command and returns without waiting for REW to finish
  (`measurement_aborted` / `measurement_timeout`).
* Measurement **indices are 1-based** (REW's numbering); a list-shaped `/measurements` answer
  is numbered from 1 (configurable with `index_base`).
* Every result carries a note that REW's routing and the protocol table are unverified.

**Protocol table: unverified against a live REW.** Every endpoint path, command name and
payload field lives in one table (`PROTOCOL` in `garage_audio/ops_mcp/measure_rew.py`). It
follows the published API description as far as it could be read; it has **not** been
checked against a running REW, and the tests use a fake. The installed REW serves its own
API description on the API port, and that is authoritative. Override any key with
`measurement.rew.protocol` (validated: plain absolute paths, `{id}` where the default has
it, plain identifiers for names, `index_base` 0 or 1; unknown keys stop startup):

| Key | Default (unverified) | Used for |
|---|---|---|
| `path.version` | `/version` | Reachability/version check |
| `path.blocking` | `/application/blocking` | Read (GET) and set (POST) blocking mode |
| `path.level` | `/measure/level` | Sweep level (dBFS) |
| `path.sweep_config` | `/measure/sweep/configuration` | Sweep start/end frequency and length |
| `path.naming` | `/measure/naming` | Measurement title |
| `path.command` | `/measure/command` | Measure / cancel commands |
| `path.measurements` | `/measurements` | List of measurements (keyed by index) |
| `path.frequency_response` | `/measurements/{id}/frequency-response` | Response of the new measurement |
| `path.impulse_response` | `/measurements/{id}/impulse-response` | Impulse of the new measurement |
| `command.measure` | `Measure` | The sweep command (REW also has an SPL command) |
| `command.cancel` | `Cancel` | Abort / deadline |
| `field.command` | `command` | Command field in the command body |
| `field.sweep_start` / `.sweep_end` / `.sweep_length` | `startFreq` / `endFreq` / `length` | Sweep configuration fields |
| `index_base` | `1` | First measurement index of a list-shaped answer |

## Phone microphone (design only)

Not implemented. `measurement.capture = "phone"` is accepted by the configuration and every
measurement then fails with `not_implemented` (`PhoneCaptureBackend` in
`garage_audio/ops_mcp/measure_io.py` is the interface hook).

Design if it is ever built:

* The ops server serves a small page (same bearer token, same loopback/private-network
  rules) that records with the browser's `MediaRecorder` and uploads each take.
* The server starts playback through the normal sink, the page records, the upload is
  decoded and analysed like any capture.
* Caveats: phone mics are uncalibrated and roll off heavily below ~100 Hz and above ~10 kHz;
  browsers apply echo cancellation, noise suppression and automatic gain unless disabled
  (and some ignore that); compressed upload formats and resampling add error; there is no
  shared clock, so **no timing** (no alignment) and **magnitude only**. Useful at most for a
  rough look at bass modes; never as input for alignment.

## Tools

| Tool | Access | Purpose |
|---|---|---|
| `room_profile_get` | read | Room profile, missing fields, coordinate system |
| `room_profile_update` | local | Partial update (`set`, `remove`, `units`) |
| `room_modes` | read | Axial modes, seat coupling, placement advice |
| `measure_plan` | read | Positions around a listening position with plain descriptions |
| `measure_status` | read | Extra installed?, running measurement, confirmed levels, effective limits |
| `measure_list_devices` | read | Capture-device listings (configured read-only commands) |
| `measure_import_calibration` | local | Import a mic calibration file |
| `measure_list_calibrations` | read | Stored calibrations |
| `measure_noise_check` | measure | Record background noise (plays nothing) |
| `measure_level_check` | measure | Stepwise tone level check (audible; `user_confirmed`) |
| `measure_sweep` | measure | Log-sweep measurement of one channel (audible; `user_confirmed`) |
| `measure_abort` | stop | Stop the running measurement (always enabled) |
| `measure_get` | read | Stored measurement, files, optional re-smoothing |
| `measure_average` | local | Power/vector average of stored measurements |
| `measure_session_start` / `_status` / `_confirm_placement` / `_advance` / `_finish` / `_cancel` | local/read | Guided session |

## Verification

* `tests/ops_mcp_measure_engine.py`: synthetic recovery through `tests/fixtures/ops_mcp/room_sim.py`
  (a simulated room with room-mode resonances, speaker delays, noise, start latency and
  optional clipping/disconnection). Stated tolerances: arrival/delay ±0.1 ms (loopback and
  acoustic reference), magnitude ±1 dB over 30 Hz–16 kHz against the analytic response, room
  modes within 3 % in frequency and 1 dB in level; calibration flattening; exports; narrow
  (two-octave) sweeps; REW backend against a fake (blocking mode restore, cancel on abort and
  deadline, 1-based indices, protocol overrides); behaviour without numpy.
* `tests/ops_mcp_measure_safety.py`: gate, confirmation, level cap, gentle start, stepwise
  level check, level expiry and snapshot binding, reference-chirp level, sub cap on sub-only
  outputs (sweep, tone, chirp), max duration, preflight refusals (including Music Assistant
  unreachable), configuration guardrails (raw ALSA names, bare ALSA players, listing
  allowlist), one-at-a-time, abort (real child processes, an unrelated process survives)
  and the watchdog.
* `tests/ops_mcp_measure_session.py`: room profile, modes, plan, session state machine
  (including advance refused mid-sweep and stale results discarded by epoch), averaging
  (vector timing refusal and the low-confidence flag) and the hand-off to the correction
  tools.

Not verified: a real microphone, interface, PipeWire sink, REW instance or room.
