# Operator MCP server (garage-audio-ops)

An MCP server an operator's AI agent can use to inspect, recover and tune the garage
audio stack: Music Assistant, Squeezelite and Spotify Connect in Docker, through
PipeWire and CamillaDSP to the USB interface, amplifiers, two mains and a sub, all run
by the `garage-audio-network` supervisor ([audio recovery](audio-recovery.md)).

This server is **separate** from the dashboard MCP adapter (`garage_audio/mcp_server.py`)
and the read-only remote connector ([remote MCP](remote-mcp.md)). Their contracts and
policies are unchanged. The ops server is an operator tool. It is read-only unless the
operator turns writes on in its own configuration.

The room-correction workflow is described in [ops MCP room correction](ops-mcp-room-correction.md).
Built-in room measurement (log sweeps through the stack, guided mic placement, room model)
is described in [ops MCP measurement](ops-mcp-measurement.md).

## Normal journey

1. **Setup (once):** copy the example config, create the token file (HTTP only) and
   validate it with `--check-config`. Writes stay off.
2. **Connect:** the agent starts `python -m garage_audio.ops_mcp` over stdio, or
   connects to `http://127.0.0.1:<port>/mcp` with `Authorization: Bearer <token>`.
3. **Use:** `get_status` or `diagnose` first. Then use the other read tools, or the
   write tools the operator enabled.
4. **Result:** every tool returns structured JSON. Every write returns what changed,
   a `backup_id` (DSP) and how to roll it back.
5. **Exit and reconnect:** the server is stateless per request. Backups, measurements
   and proposals live in `state_dir` and survive restarts. Reconnect by initializing
   again.

## Running it

```
python -m garage_audio.ops_mcp [--transport stdio|http] [--config PATH] [--check-config]
```

* `stdio` is the default. stdout carries the protocol and all logging goes to stderr.
* `http` serves MCP Streamable HTTP (stateless, JSON responses) at `/mcp` only.
* `--check-config` validates the configuration and, with `--transport http`, the
  token. It then prints the effective non-secret configuration and exits.
  A configuration error exits with status 2 and a one-line reason.

The core (configuration, safety policy, tool handlers, clients, room-correction math)
is standard library only. Only `garage_audio/ops_mcp/transport.py` imports the pinned
MCP SDK (`mcp==1.30.0`, which also brings Starlette and uvicorn). If the SDK is missing,
startup fails with a clear "install the pinned requirements" message.

Example stdio client entry (paths are placeholders):

```json
{"command": "<venv>/bin/python",
 "args": ["-m", "garage_audio.ops_mcp", "--config", "/etc/garage-audio-ops-mcp/config.json"],
 "cwd": "<path to garage-audio checkout>"}
```

A systemd unit for the HTTP transport is provided as
`config/system/garage-audio-ops-mcp.service.example`. It is an **EXAMPLE** that no
script installs. Read its header before copying it.

## Configuration

Start from `config/ops-mcp.example.json`. Copy it to a private location such as
`/etc/garage-audio-ops-mcp/config.json`, edit the placeholders and pass `--config`
(or set `GARAGE_OPS_MCP_CONFIG`). Keys starting with `_comment` are ignored. Unknown
keys, wrong types and out-of-range values stop startup.

| Key | Default | Meaning |
|---|---|---|
| `allow_write` | `false` | Enables write tools (recovery, DSP changes, pause/stop, room apply) |
| `allow_playback` | `false` | Together with `allow_write`: start/resume playback, player volume/mute/grouping, DSP unmute |
| `allow_measure` | `false` | Together with `allow_write`: measurement signals (noise check, level-check tones, sweeps) through the stack sink ([measurement](ops-mcp-measurement.md)) |
| `state_dir` | `$XDG_STATE_HOME/garage-audio-ops-mcp` | Backups, measurements, proposals. Created with mode 0700 if missing. An existing directory must be owned by the server user with mode 0700; otherwise state writes are refused with `state_dir_unsafe` (it is not chmod-ed for you) |
| `server.host` / `server.port` | `127.0.0.1` / `8766` | HTTP bind address |
| `server.token_file` | none | HTTP bearer token file (preferred) |
| `server.allowed_hosts` | `[]` | Extra `host:port` values accepted in the `Host` header (for example behind a proxy) |
| `limits.*` | see below | Speaker-protection limits. Can only be tightened |
| `supervisor.unit` | `garage-audio-network.service` | Owner unit managed by the service manager |
| `supervisor.status_root` | `/var/lib/garage-audio-network` | Recovery record read by `audio_recovery status` |
| `supervisor.systemctl` | `["systemctl"]` | argv prefix (no shell) |
| `supervisor.min_action_interval_seconds` | `120` | Minimum gap between recovery actions |
| `containers.docker` / `.label` / `.names` | `["docker"]` / `garage.audio.generation` / `[]` | Read-only container inspection |
| `logs.journalctl` / `.units` / `.containers` / `.max_lines` | `["journalctl"]` / owner unit / `[]` / `200` | Log sources an agent may read |
| `music_assistant.url` | `http://127.0.0.1:8095` | MA server base URL (no path or credentials) |
| `music_assistant.token_file` | none | MA long-lived token file |
| `music_assistant.players` | `[]` | If non-empty, only these player ids may be controlled |
| `camilladsp.host` / `.port` | `127.0.0.1` / `1234` | CamillaDSP websocket |
| `camilladsp.channels` | unset | Pipeline channel index for `left`, `right` and `sub` |
| `camilladsp.backup_retention` | `50` | Number of DSP backups kept (5 to 1000) |
| `room_correction.*` | see the [workflow](ops-mcp-room-correction.md) | Proposal bounds and smoothing |
| `measurement.*` | see [measurement](ops-mcp-measurement.md#configuration) | Player/recorder argv, channels, loopback, measurement limits (tighten-only), REW backend |

Environment overrides (applied after the file):

| Variable | Overrides |
|---|---|
| `GARAGE_OPS_MCP_CONFIG` | config path when `--config` is not given |
| `GARAGE_OPS_MCP_ALLOW_WRITE`, `GARAGE_OPS_MCP_ALLOW_PLAYBACK`, `GARAGE_OPS_MCP_ALLOW_MEASURE` | `allow_write`, `allow_playback`, `allow_measure` (`true`/`false`) |
| `GARAGE_OPS_MCP_STATE_DIR` | `state_dir` |
| `GARAGE_OPS_MCP_HOST`, `GARAGE_OPS_MCP_PORT` | `server.host`, `server.port` |
| `GARAGE_OPS_MCP_TOKEN_FILE` | `server.token_file` |
| `GARAGE_OPS_MCP_TOKEN` | the bearer token itself, used only when no token file is set |
| `GARAGE_OPS_MCP_MA_URL`, `GARAGE_OPS_MCP_MA_TOKEN_FILE` | Music Assistant URL and token file |
| `GARAGE_OPS_MCP_CAMILLA_HOST`, `GARAGE_OPS_MCP_CAMILLA_PORT` | CamillaDSP websocket address |
| `GARAGE_OPS_MCP_SUPERVISOR_UNIT`, `GARAGE_OPS_MCP_RECOVERY_ROOT` | `supervisor.unit`, `supervisor.status_root` |

## Security model

* **Read-only by default.** All 60 tools are always listed, so an agent can see what
  exists. Each description starts with `[read-only]`, `[local state only]`, `[WRITE]`,
  `[PLAYBACK]`, `[MEASURE]` or `[STOP]`, and disabled tools say so. A disabled tool is
  **refused server-side** with `write_disabled` before its arguments are parsed or any
  client is touched. Hiding a tool is not the protection. The gate is.
* **Access levels.** `read` and `local` tools are always available (`local` writes
  only to `state_dir`). `write` needs `allow_write=true`. `playback` needs
  `allow_write=true` **and** `allow_playback=true`. `measure` (audible measurement
  signals) needs `allow_write=true` **and** `allow_measure=true`; audible steps also need
  `user_confirmed=true`. `stop` (`measure_abort`) is always available.
* **HTTP bearer token.**
  * HTTP refuses to start without a token.
  * Prefer `server.token_file`: a regular file (not a symlink), owned by the server
    user, mode 0600. A looser file is rejected.
  * The token must be 32 to 4096 printable ASCII characters with no whitespace.
  * Comparison is constant-time (`hmac.compare_digest`). A missing or wrong token
    gets `401` with `WWW-Authenticate: Bearer`, before routing, for every path.
  * The token is never logged, echoed or included in errors.
  * Generate one with `python3 -c 'import secrets; print(secrets.token_urlsafe(48))'`.
* **Loopback by default.** The server binds to `127.0.0.1` and uses the SDK's
  DNS-rebinding protection: only loopback `Host`/`Origin` values plus
  `server.allowed_hosts` are accepted. A non-loopback bind logs a warning. If you
  need remote access, put the server behind a private network or TLS proxy you
  control. It has no TLS of its own.
* **Credentials** for Music Assistant are read from a file with the same 0600 rule
  and are never returned. `get_ops_config` shows only whether a token is configured.
  Log lines returned to the agent are bounded and credential-redacted.
* **No shell, no sudo.** Every command is an argv list from the configuration. The
  server never runs `docker stop`, `kill` or `start`, never escalates privileges and
  never edits supervisor state.
* **Outbound internet: one tool only.** `measure_calibration_fetch` (write tier) downloads a
  mic calibration file. It talks only to each provider's host allowlist, over HTTPS, with
  manual redirects inside that allowlist, and it refuses any non-public address. Responses
  have a size cap and timeouts, and there are no retries. The generic URL provider is off
  unless the operator enables it with its own hosts. Details are in
  [measurement](ops-mcp-measurement.md#microphone-calibration).

### Playback authority differs from the dashboard MCP

The dashboard MCP ([agent guide](agents.md)) does not let an agent start sound
without separate, attended approval. **This server is different by operator choice:**
setting `allow_write=true` and `allow_playback=true` grants the connected agent
playback and volume control. It can then:

* start or resume media on any allowed player
* change player volume (up to `limits.ma_volume_max`)
* group players
* unmute CamillaDSP

There is no per-action human approval. With `allow_write=true` alone, the agent can
pause or stop players, mute CamillaDSP, and change DSP settings within the limits.
That includes setting CamillaDSP main volume anywhere up to `limits.volume_ceiling_db`,
which makes already-playing audio louder (never above the ceiling). It cannot start
playback, change player volume or unmute. Leave `allow_playback` off unless you
accept unattended sound.
Restrict controllable players with `music_assistant.players`.

### Privileges the server account needs

Grant these deliberately. Without them the matching tools report "unavailable" and
everything else keeps working.

* **Recovery:** `systemctl start|restart --no-block <unit>` on the owner unit. For a
  dedicated unprivileged account, use a narrow polkit rule, for example
  `/etc/polkit-1/rules.d/50-garage-audio-ops-mcp.rules`:

  ```js
  polkit.addRule(function (action, subject) {
    if (action.id == "org.freedesktop.systemd1.manage-units" &&
        action.lookup("unit") == "garage-audio-network.service" &&
        (action.lookup("verb") == "start" || action.lookup("verb") == "restart") &&
        subject.user == "<ops-user>") {
      return polkit.Result.YES;
    }
  });
  ```

* **Container inspection:** `docker ps` and `docker inspect` need Docker socket access,
  which is **root-equivalent**. Grant it only if you accept that. Otherwise leave
  `get_containers` unavailable.
* **Logs:** membership of the `systemd-journal` group (or equivalent) for `journalctl -u`.

## Speaker-protection limits

Hard caps are code constants (`garage_audio/ops_mcp/safety.py`, `HARD`). The
`limits` section may only tighten them. A value that would loosen a hard cap stops
startup instead of being clamped.

| Limit | Hard cap | Shipped default |
|---|---|---|
| `max_boost_db` (PEQ/shelf positive gain) | +6 dB | +3 dB |
| `min_gain_db` (deepest cut) | −24 dB | −15 dB |
| `freq_min_hz` / `freq_max_hz` | 20 / 20000 Hz | 20 / 20000 Hz |
| `q_min` / `q_max` | 0.1 / 20 | 0.3 / 10 |
| `delay_max_ms` | 50 ms | 20 ms |
| `channel_gain_min_db` / `channel_gain_max_db` | −60 / +6 dB | −40 / 0 dB |
| `volume_ceiling_db` (CamillaDSP main volume) | 0 dB | −10 dB |
| `max_filters_per_channel` (room proposals) | 10 | 8 |
| `ma_volume_max` (Music Assistant 0–100) | 100 | 50 |

DSP changes are checked against these limits for the whole resulting configuration.
A change is rejected if it adds any violation, or if a filter it edits would still
be outside the limits. Violations that already exist in filters the change does not
touch are reported, not fixed. `dsp_get_config` lists them.

The server can only bound filters whose level effect it understands: `Gain` (dB or
linear), `Delay`, `Volume` on the Main fader, `Dither`, `Limiter`, Biquad
Peaking/shelf/pass/notch/bandpass/allpass, and Butterworth/Linkwitz-Riley
`BiquadCombo` crossovers. Any other filter (Biquad `Free` or `LinkwitzTransform`, `Conv`,
`DiffEq`, `Loudness`, `Volume` on an Aux fader, other `BiquadCombo` shapes, unknown
types) is reported as an "unbounded filter type" violation. Second-order shelves also
need `q` within the Q limits or `slope` within 1–12 dB/octave.

The same applies outside `filters`. Every entry in the `processors` section (for
example a `Compressor`, whose `makeup_gain` can add level), every `Processor` pipeline
step, any pipeline step other than `Filter` and `Mixer`, and any top-level section other
than `title`, `description`, `devices`, `filters`, `mixers`, `processors` and
`pipeline` is reported as unbounded. No write can introduce any of these.

## DSP backups and rollback

Every DSP write (`dsp_update_peq`, `dsp_set_channel_delay`, `dsp_set_channel_gain`,
`dsp_set_main_volume`, `dsp_set_mute`, `dsp_restore_backup`, `room_apply_correction`)
runs under one lock (in-process plus a `flock` in `state_dir`) and follows these steps:

1. Read the active config.
2. Build the change on a copy.
3. Check it against the limits.
4. Ask CamillaDSP to validate it (`ValidateConfigJson`). If unsupported, CamillaDSP
   validates on apply.
5. **Write a backup** of the previous config, volume and mute. The backup file is
   atomic, timestamped, mode 0600 in `state_dir/dsp-backups`.
6. Apply the change.
7. Read it back.

If applying fails, nothing changes and the backup id is reported. Retention keeps the
newest `camilladsp.backup_retention` backups and never prunes the one just written,
even if the clock stepped back.

`dsp_list_backups` shows them. `dsp_restore_backup` restores one:

* The restore is itself validated against the **current** limits and backed up first,
  so a restore can be undone.
* The restored config must be bounded as a whole. If it contains anything unbounded
  (filter, processor, pipeline step or section, as above), or any value is outside the
  hard caps, the restore is refused with `safety_limit` before anything is applied or
  backed up. This applies even if the same item is live now. Fix such configs through
  the reviewed configuration process.
* `restore_volume=true` also restores the volume. The volume is checked against the
  ceiling before anything is applied.
* It restores an unmuted state only when `allow_playback` is on.

## Recovery semantics

`recover_audio_stack` uses the supervisor's sanctioned path only:
`systemctl start|restart --no-block <supervisor.unit>`. The `action` argument
defaults to `auto`, which means restart if the unit is active, otherwise start.
`plan_recovery` is the dry run. Following [audio recovery](audio-recovery.md):

| Supervisor state | Behaviour |
|---|---|
| `ready` | allowed. The plan notes that a restart interrupts current playback |
| `starting`, or unit activating/deactivating/reloading | refused unless `force=true` |
| `backoff` | refused unless `force=true`. The supervisor is already retrying, and a restart resets its bounded retry accounting |
| `terminal` / `exhausted` | refused unless `force=true`. The supervisor stopped retrying on purpose; the refusal includes the recorded explanation and guidance. Find and fix the cause first, then use `force=true` |
| unknown / unavailable | allowed. The service-manager state is the only evidence |

Separately, a recovery action within `supervisor.min_action_interval_seconds` of the
previous one is always refused. `force` does not bypass this. Each action is recorded
in `state_dir`.

## Tools

| Tool | Access | Purpose |
|---|---|---|
| `get_status` | read | Overall status: recovery state (`audio_recovery status`), unit, containers, MA and CamillaDSP reachability |
| `get_supervisor_state` | read | Owner recovery record plus service-manager unit state |
| `get_heartbeat` | read | Guard heartbeat age and tolerance (diagnostic only) |
| `get_containers` | read | Container states via `docker ps`/`inspect` for the configured label/names |
| `check_music_assistant` | read | Is the MA server reachable? |
| `get_recent_logs` | read | Bounded, redacted recent log lines from a configured unit or container |
| `diagnose` | read | Plain-words diagnosis and suggested next steps |
| `get_ops_config` | read | Effective configuration and limits (no credential values) |
| `plan_recovery` | read | Dry run of `recover_audio_stack` |
| `recover_audio_stack` | write | Start/restart the owner unit via the service manager |
| `ma_list_players` | read | Players with state, volume and current media |
| `ma_get_queue` | read | A player's queue |
| `ma_search` | read | Search the MA library and providers |
| `ma_play_media` | playback | Play a media URI on a player |
| `ma_player_command` | playback | Resume, next or previous |
| `ma_stop_playback` | write | Pause or stop a player (reduces output) |
| `ma_set_volume` | playback | Player volume 0–100, capped at `ma_volume_max` |
| `ma_set_mute` | playback | Mute or unmute a player |
| `ma_group_players` | playback | Group (sync) players to a target |
| `ma_ungroup_player` | playback | Remove a player from its group |
| `dsp_get_config` | read | Active CamillaDSP config and existing limit violations |
| `dsp_get_filters` | read | Filters, mixers and pipeline with the left/right/sub map |
| `dsp_get_levels` | read | Levels, peaks, clipped samples, load, volume and mute |
| `dsp_set_main_volume` | write | Main volume in dB, never above the ceiling |
| `dsp_set_mute` | write | Mute (unmute additionally needs `allow_playback`) |
| `dsp_update_peq` | write | Change or create a Peaking filter on a mapped channel |
| `dsp_set_channel_delay` | write | Per-channel delay via an `ops_<channel>_delay` filter |
| `dsp_set_channel_gain` | write | Per-channel trim via an `ops_<channel>_gain` filter |
| `dsp_list_backups` | read | Automatic DSP backups |
| `dsp_restore_backup` | write | Restore a backup (validated and backed up) |
| `room_import_measurement` | local | Import a REW text frequency-response export |
| `room_list_measurements` | read | Measurement history |
| `room_compare_to_target` | read | Deviation from a target curve |
| `room_propose_peq` | local | Cut-biased PEQ proposal |
| `room_propose_alignment` | local | Mains and sub delay and level alignment |
| `room_preview_yaml` | read | CamillaDSP YAML fragment for a proposal |
| `room_apply_correction` | write | Apply a proposal (re-validated, backed up first) |
| `room_profile_get` | read | Room profile (dimensions, speakers, seats, notes) and missing fields |
| `room_profile_update` | local | Partial room-profile update in m/cm/ft/in |
| `room_modes` | read | Axial modes, seat coupling and placement advice |
| `measure_plan` | read | Mic positions around a seat with plain metric/imperial descriptions |
| `measure_status` | read | Measurement extra installed?, running measurement, confirmed levels, limits |
| `measure_list_devices` | read | Capture-device listings from configured read-only commands |
| `measure_mic_detect` | read | Find the measurement mic (read-only `/proc` + `/sys` + listing); candidate serial or ask the user |
| `measure_calibration_status` | read | Active mic calibration per orientation (0°/90°), session match, providers, manual steps |
| `measure_calibration_fetch` | write | Download the per-serial mic calibration (miniDSP UMIK-1/2; operator-enabled URL), validate, store, activate. The only tool that uses the internet |
| `measure_import_calibration` | local | Import a UMIK-style or generic calibration from pasted contents or the operator's `import_dir`, or re-activate a stored one |
| `measure_list_calibrations` | read | Stored mic calibrations with metadata and `active_for` |
| `measure_noise_check` | measure | Record background noise (plays nothing) |
| `measure_level_check` | measure | Stepwise level check with a short tone (audible) |
| `measure_sweep` | measure | Log-sweep measurement of one channel (audible) |
| `measure_abort` | stop | Stop the running measurement (only this server's children) |
| `measure_get` | read | A stored measurement with files and optional re-smoothing |
| `measure_average` | local | Power or vector average of stored measurements |
| `measure_session_start` | local | Start a guided measurement session (plan + state machine) |
| `measure_session_status` | read | Session progress and the exact next step |
| `measure_session_confirm_placement` | local | The person confirmed the mic is at the position |
| `measure_session_advance` | local | Next position, redo a channel/position, or skip |
| `measure_session_finish` | local | Average across positions and store the result |
| `measure_session_cancel` | local | Cancel a session (aborts its running measurement) |

The server also exposes six read-only resources and four prompts:

* Resources: `garage-audio://status`, `garage-audio://dsp/config`,
  `garage-audio://measurements`, `garage-audio://room/profile`,
  `garage-audio://measure/plan` and `garage-audio://measure/session`.
* Prompts: diagnose a problem, a room-correction session, play something, and
  `guided-room-measurement`.

Errors are structured as `{"error": {"code", "message", "details"?}}`. Codes include:

* `write_disabled`, `playback_disabled`
* `recovery_refused`
* `safety_limit` (speaker-protection violation)
* `camilladsp_unreachable`, `ma_unreachable`
* `docker_unavailable`, `logs_unavailable`
* `invalid_argument`, `busy`
* measurement: `measurement_extra_missing`, `measurement_unconfigured`, `confirmation_required`,
  `stack_not_ready`, `stack_muted`, `stack_busy`, `sink_unavailable`, `measurement_aborted`,
  `measurement_timeout`, `playback_failed`, `capture_failed`, `session_state`, `session_quality`

Raw exception text is never returned.

## Known limits and open risks

* **Isolated CamillaDSP.** In the hardened deployment ([DAC isolation](dac-isolation.md)),
  CamillaDSP's raw websocket listens on `127.0.0.1:1234` **inside a private network
  namespace** that host applications cannot reach. There the `dsp_*` and
  `room_apply_correction` tools report `camilladsp_unreachable` by design. **Do not
  proxy or publish the raw websocket to make them work.** That would undo the
  isolation. The DSP tools are for deployments where the websocket is already
  reachable on the host's loopback. Health, recovery, Music Assistant, measurement
  import, the room model and the proposal tools work either way. `diagnose` explains that an
  unreachable private websocket alone is not a fault, but it does not report
  `healthy: true` without DSP evidence.
* **Unverified against real hardware.** The tools are tested against fakes for
  CamillaDSP, Music Assistant, `systemctl`, `docker` and `journalctl`. No live
  deployment, audible result or amplifier behaviour is claimed by this document.
* **Measurement needs a reachable CamillaDSP.** The measurement tools refuse to play
  when CamillaDSP cannot be checked (mute, state, active delays), so in the isolated
  deployment above they report `stack_not_ready`. Measurement is also verified only
  against a simulated room ([measurement](ops-mcp-measurement.md#verification)).
* **Limits protect against bad parameters, not every hazard.** Keep amplifier gain
  structure and hardware protection in place.
