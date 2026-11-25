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
3. **Mic calibration (once per microphone):** `measure_calibration_status` shows whether a
   calibration is active for 0° and 90°. If not: `measure_mic_detect` → the agent asks the
   person for the serial printed on the mic → `measure_calibration_fetch` (needs
   `allow_write`) downloads, checks, stores and activates it; if that fails, the person
   downloads the file in a browser and the agent imports it with
   `measure_import_calibration`. See "Microphone calibration" below.
4. **Plan:** `measure_session_start` creates a plan: the main listening position plus 4–8
   nearby positions on a circle (default 30 cm / 1 ft) at ear height, each described in
   plain words in metric and imperial ("about 30 cm to the left of the centre of the
   seat, same height"). The active calibration for the session's mic orientation (default
   90°, mic pointing up) is pinned automatically; without one the session still starts, with
   a warning.
5. **Per position:** the agent tells the person where to put the mic and **waits for their
   confirmation** → `measure_session_confirm_placement` → `measure_noise_check` (silent) →
   first position only: `measure_level_check` per channel (audible, needs
   `user_confirmed=true`) → `measure_sweep` per channel (audible, needs
   `user_confirmed=true`) → quality check → `measure_session_advance` (`next`,
   `redo_channel`, `redo_position`, `skip_position`).
6. **Result:** `measure_session_finish` averages every channel across the positions
   (power average by default, vector average optional) and stores the averages in the
   measurement history. Hand them to `room_compare_to_target` / `room_propose_peq`; hand
   the main-position sweeps to `room_propose_alignment` (`measurement_ids`).
7. **Exit and reconnect:** sessions are files under `state_dir`. If the agent disconnects,
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
The plan tells the person which one: the `mic_orientation` argument, else the orientation of
an explicitly given calibration, else `measurement.calibration.default_orientation` (`"90"`).
The matching active calibration is then applied automatically.

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
* **Calibration:** the correction is subtracted from the response. Which file is used:
  an explicit `calibration_id`, else the session's pinned calibration, else the active
  calibration for `measurement.calibration.default_orientation`. The id is recorded in the
  measurement metadata and in confirmed-level snapshots. The sensitivity factor is recorded;
  responses are relative (dB re the digital transfer), not absolute SPL. See "Microphone
  calibration".
* **Files** (per measurement, under `state_dir/measurement-files/<id>/`, directory 0700,
  files 0600, written atomically): `measurement.json` (channel, position, session,
  calibration, settings, timing, quality, stack state, timestamp), `response.frd`,
  `response-rew.txt` (REW-importable text), `impulse.wav` (mono 32-bit PCM, peak-normalised). The response also
  enters the measurement history, so `room_compare_to_target` and `room_propose_peq` take its
  `measurement_id`. Bounded by `measurement.retention`.

## Microphone calibration

A calibrated measurement mic ships with a **per-serial** correction file, usually two
variants: **0°** (mic pointing at the speakers) and **90°** (mic pointing straight up; miniDSP
generates this one automatically). The server can find the mic, download its file, check
it, store it and use it automatically.

### Tools

The user asked for tools to "detect the mic, download the calibration file, check its status
and import one". This repo keeps its `measure_` prefix, so they are named:

| Requested | Tool | Tier | What it does |
|---|---|---|---|
| detect mic | `measure_mic_detect` | read | Looks for UMIK-1 / UMIK-2 (and other USB capture devices) |
| download calibration | `measure_calibration_fetch` | **write** (`allow_write`; not `allow_measure`) | Downloads, validates, stores and activates the file for a serial |
| calibration status | `measure_calibration_status` | read | Active calibration per orientation, whether the session matches, providers, manual steps |
| import calibration | `measure_import_calibration` (extended) | local | Pasted contents, a file from the import directory, or re-activating a stored one |
| list calibrations | `measure_list_calibrations` | read | Stored calibrations with metadata and `active_for` |

`measure_calibration_fetch` is the only tool that reaches the internet. It is marked
`openWorldHint=true`.

### Agent flow

1. `measure_calibration_status`. If `ready_for_default_orientation` is true, you are done.
2. `measure_mic_detect` is read-only. It reads `/proc/asound/cards`,
   `/proc/asound/card*/{usbid,stream0}`,
   `/sys/bus/usb/devices/*/{idVendor,idProduct,product,manufacturer,serial}` and the
   configured `measure_list_devices` output. It runs no new commands and needs no extra
   privileges. Possible results:
   * `serial_candidate`: the USB serial string has the 7-digit form. It is reported as
     **"candidate, confirm with user"**: nobody has verified that the USB string equals the
     printed serial.
   * `needs_serial`: a mic was found, but there is no usable serial.
   * `no_microphone_found`: nothing was found.

   In every case `ask_user` holds a plain question for the person. The serial is printed on
   the mic body or its box as `XXX-XXXX`.
3. `measure_calibration_fetch` with `model` (`UMIK-1` / `UMIK-2`), `serial`, `orientation`
   (`0`, `90` or `both`, the default) and an optional `provider`:
   * Files are stored and **activated per orientation**. The orientation comes from the
     file name or header (`90`, `90deg`, `_90`). In a pair, the unmarked file is the 0°
     file.
   * A single unmarked file is stored but **not** activated (`needs_orientation`). Ask the
     person, then call `measure_import_calibration` with `calibration_id` and `orientation`.
   * Two files with the same marker are both stored and reported as
     `ambiguous_orientations`.
4. If the fetch fails (`not_found`, `calibration_unparseable`, `provider_disabled`,
   `rate_limited`, a network refusal), the error carries `manual_steps`:
   1. Open the product page in a browser.
   2. Enter the serial and download the file(s).
   3. Either paste the contents into `measure_import_calibration` (`text`, `name`,
      `orientation`, `serial`), or have the operator copy the file into
      `measurement.calibration.import_dir` and import it with `path`.

The `guided-room-measurement` prompt walks through these steps before the session starts.
`measure_session_start` without a matching active calibration **warns but still starts**: the
measurement is then uncorrected.

### Providers

* **`minidsp`** (UMIK-1, UMIK-2; on by default, `providers.minidsp.enabled` turns it off).
  The only host is `www.minidsp.com`. One fetch is:
  1. One GET of the product page
     (`https://www.minidsp.com/products/acoustic-measurement/umik-1` or `/umik-2`).
  2. Parse *that page's* calibration form: `<form method="POST" action="#umikcalfile">`, or
     `#umik2calfile` for UMIK-2.
  3. One form-urlencoded POST to the same page, with `serial1` (3 digits), `serial2`
     (4 digits) and every other field **exactly as served**. The hidden honeypot text field
     (its name rotates, e.g. `your_name_here`) stays empty, and so does any other text input or
     `<textarea>`. A `<select>` sends its selected (else first) option, and the hidden
     32-hex-named token keeps its served timestamp value.
  4. Follow at most 4 file links named after the serial, or a meta-refresh, iframe or script
     download location, on the allowlisted host. "Named after the serial" means one contiguous
     run `XXXXXXX` or `XXX-XXXX` inside a single path, never digits gathered across segments.

  An unknown serial gets miniDSP's own message ("Calibration data for UMIK 000-0000 not
  found. Please check your serial number and try again."), returned as `not_found`.
* **`url`** (off by default). A direct `https` download link for other microphones.
  Enabling it needs `providers.url.enabled=true` **and** its own `allowed_hosts` (DNS names
  only: no IPs, ports or wildcards). URLs with a query string are refused, and HTML pages are
  not followed.
* **`import`**: pasted contents or a file in `import_dir` through `measure_import_calibration`.
  This is not a network provider.

**Dayton Audio** (EMM-6, iMM-6, UMM-6) and **Cross-Spectrum** are not hard-coded: Dayton's
pages refused automated requests (HTTP 403) and Cross-Spectrum is unverified. Use the `url`
provider, if the operator allows the vendor's host, or download in a browser and import. A
new vendor is a `Provider` subclass in `garage_audio/ops_mcp/measure_cal.py` with its own host
allowlist and tests.

### What is verified about the miniDSP endpoint

* **Observed on 2026-11-23:** the product pages, the form (fields, honeypot, token) and the
  "not found" reply for an unknown serial.
* **Not observed: the success reply.** No real serial was available, so none was submitted.
  The page says the files "begin downloading automatically", with manual links as a
  fallback. The parser accepts the likely shapes: a direct text or attachment file, a zip, or
  an HTML page with serial-named links, a meta refresh, an iframe or a script location. This
  path is **unverified against a real serial**, and every miniDSP result and record says so
  (`verification`). An unrecognised success page returns `calibration_unparseable` with the
  manual steps.
* **robots.txt:** miniDSP's robots.txt disallows `/` for all user agents (assets are
  allowed). The server does not crawl. A fetch is a single request the user started, for the
  user's own file:
  * no retry loops and no artificial delays to get around the timestamp token;
  * no captcha solving and no browser User-Agent: it sends
    `garage-audio-ops-mcp/<version> (user-initiated calibration download)`;
  * at least `min_fetch_interval_seconds` (default 30 s) between fetches from one provider;
  * an operator switch per provider.

  An operator who does not want this turns the provider off, and the manual path remains.

### Network safety (code-enforced, tested)

* HTTPS only, port 443, no credentials in URLs, and a per-provider host allowlist.
* Redirects are followed manually, only inside the allowlist and at most 3. After a POST,
  a 301, 302 or 303 becomes a GET without the body.
* Every resolved address must be globally routable. Private, shared (CGNAT),
  loopback, link-local, multicast, reserved, unspecified and IPv4-mapped forms of these are
  refused **before** connecting. The connection is pinned to the checked address, with
  certificate and SNI verification for the host name, so DNS cannot change between the
  check and the connect.
* Known limitation: NAT64 (`64:ff9b::/96`) and 6to4 (`2002::/16`) addresses are not decoded
  to their embedded IPv4 address, so an allowlisted host resolving to one is not re-checked.
* Connect and read timeouts (`timeout_seconds`, default 15) per network operation, plus one
  wall-clock deadline of 4 × `timeout_seconds` for the whole fetch (every request, redirect,
  header and body byte), so a server dripping bytes slowly cannot hold the call open. The
  response is read in a stream and refused above `max_download_bytes` (default 1 MiB).
* Zip archives: at most 16 members, at most 4 MiB uncompressed. Absolute or `..` paths are
  refused, and only `.txt`, `.cal` and `.frd` members are used.
* Content-type sanity checks. Fetched URLs are stored as host + path only, never the query.
* The standard library only (`http.client`, `ssl`, `ipaddress`, `zipfile`).

### Validation

Fetched files and imports are checked by the shared calibration parser, plus these checks:
* The file must be text: no HTML, no binary.
* Strictly increasing frequencies, 5 Hz–48 kHz, |dB| ≤ 30, covering at least
  100 Hz–5 kHz.
* At least 10 points for downloaded and `import_dir` files. Pasted text keeps the shared
  parser's minimum, for compatibility.
* A `SERNO:` header must match the requested serial (`calibration_serial_mismatch`).

For miniDSP, a file is only accepted if it is tied to the serial, through its header or a
serial-named file or link. The file name and the URL path are checked separately, each for
one contiguous `XXXXXXX` / `XXX-XXXX` run not touching other digits.

The UMIK header (`"Sens Factor =…dB, AGain =…dB, SERNO: …"`) is kept: the sensitivity
factor and the gain are recorded.

### Storage

Calibrations live under `state_dir/mic-calibrations/` (directory 0700, files 0600, atomic
writes, a lock against concurrent imports). Each record holds:
* `model`, `serial`, `orientation` and how the orientation was decided;
* `provider`, `source` (host + path), `file_sha256` and `fetched_at`;
* the parser summary and the verification note.

Raw downloads are not kept. `active.json` maps `0` / `90` to a calibration id. Retention
(`retention`, default 20) never removes an active calibration, the one pinned by any active
measurement session or by the most recent session, or the file just stored. This protected
set is computed under the calibration lock at every store. Pins from older finished or
cancelled sessions are not kept, so the count stays bounded (a few protected files may exceed
it).

To activate a stored file again, call `measure_import_calibration` with `calibration_id`
and `orientation`. For a file whose orientation was unknown, this creates a derived record.

### Configuration

```json
"calibration": {
  "default_orientation": "90",
  "retention": 20,
  "import_dir": null,
  "max_import_bytes": 1048576,
  "timeout_seconds": 15,
  "max_download_bytes": 1048576,
  "min_fetch_interval_seconds": 30,
  "providers": {"minidsp": {"enabled": true}, "url": {"enabled": false, "allowed_hosts": []}}
}
```

This block goes inside `measurement`.

`import_dir` must be an absolute path. Path imports are resolved with `realpath` and must
stay inside it; they are refused for:
* symlinks that lead out, FIFOs and devices;
* anything over `max_import_bytes`;
* any path when `import_dir` is not configured. Server paths outside it are never read.

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
| `measure_mic_detect` | read | Find the measurement mic (model, candidate serial, or ask for it) |
| `measure_calibration_status` | read | Active calibration per orientation, session match, providers |
| `measure_calibration_fetch` | write | Download, validate, store and activate the per-serial file (network) |
| `measure_import_calibration` | local | Import pasted contents or an `import_dir` file, or re-activate a stored one |
| `measure_list_calibrations` | read | Stored calibrations with metadata and `active_for` |
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
* `tests/ops_mcp_measure_calibration.py` runs against a fake miniDSP / URL site and fake
  `/proc` + `/sys` trees from `tests/fixtures/ops_mcp/cal_site.py`. Every test blocks real
  DNS and sockets. It covers:
  * the form submitted as served, with an empty honeypot (text input or textarea), select
    options, the token and an honest UA;
  * success shapes: a direct file, a zip, serial-named links, meta refresh and script
    location;
  * `not_found`, unparseable or changed pages with manual steps, and serial mismatch;
  * redirect refusals (off-allowlist, http, credentials, port, loops) and private, loopback,
    CGNAT, multicast and IPv4-mapped addresses refused before connecting;
  * size and timeout caps (no retry), the whole-fetch deadline against a local slow-drip
    socket pair, and zip bombs or traversal;
  * serial evidence only from one contiguous run in the file name or the URL path;
  * the URL provider being off by default, its allowlist and query refusal, and config
    validation;
  * garbage files and the import-dir containment (traversal, symlink escape, FIFO, size);
  * write-tier gating, metadata and permissions, retention keeping the active and
    session-pinned files (a session sweep still uses its pinned file), concurrent imports
    and the rate limit;
  * detection (UMIK-1, UMIK-2, unknown capture, none, listing fallback), automatic
    selection by orientation, the session warning and the prompt.

Not verified:
* a real microphone, interface, PipeWire sink, REW instance or room;
* the miniDSP **success** reply for a real serial.
