---
name: garage-audio
description: Inspect, diagnose and work with a Garage Audio installation (Spotify Connect receiver, Music Assistant, CamillaDSP, rooms, listening profiles, measurements, DSP proposals) through its MCP server. Use when asked why the speakers are silent or missing from Spotify, about audio status, rooms, profiles, measurements or calibration.
---

# Garage Audio (MCP)

The MCP server is SDK stdio over SSH: `sh <repo>/scripts/dashboard-mcp` runs
`ssh -T $GARAGE_HOST docker exec -i garage-audio-dashboard python -m garage_audio.mcp_server`.
Set `GARAGE_HOST=user@audio-host` for your server. The full contract is `docs/agents.md`.
Never put the dashboard token in client config, prompts or evidence.

## Start every session the same way

1. Call `garage_status`, then `garage_capabilities`. Capabilities lists every operation with its
   exact `input_schema`; use only advertised operations and those schemas. Rediscover after any
   reconnect or update.
2. Prefer the dedicated tools (`room_*`, `listening_profile_*`, `calibration_*`, `measurement_*`).
   Use `garage_operation` only for operations without a dedicated tool.
3. Responses are `{"ok", "result", "error"}`; `ok: false` is a failure even though the MCP call
   succeeded. Act on `error.requirements`. On `version_conflict`, reread and replan; never
   overwrite newer state.

## Hard boundaries (do not work around)

- **Never start sound.** `playback_play` / `playback_play_url` require independent human
  attended authority; a refusal is expected, not a bug. Never self-attest consent, invent
  approval strings or replay old grants.
- On `human_approval_required`, stop and tell the user which physical or human action is needed.
- `dsp.mute` and `audio.readback.state` describe software state only. Never claim the room is
  silent or audible from them; `physical_verified: false` means exactly that.
- Proposals (`profile_edit`, `calibration_propose`, correction prepare) are not apply authority.
  Keep measured and synthetic data labelled; a predicted curve is not an improvement until
  re-measured.
- There is no shell tool through MCP by design. Do not ask for one.

## "The speakers are missing from Spotify" runbook

MCP is served from the dashboard container, so if MCP cannot connect the stack is probably down.
Check the host lifecycle owner over SSH, read-only first:

```sh
ssh $GARAGE_HOST 'systemctl status garage-audio-network --no-pager; docker ps --filter label=garage.audio.generation'
ssh $GARAGE_HOST 'sudo -n sh -c "g=\$(ls -td /var/lib/garage-audio-network/generation-* | head -1); echo \$g; cat \$g/pairing-listeners.json \$g/driver-failure.json 2>/dev/null"'
dns-sd -B _spotify-connect._tcp local.   # macOS; avahi-browse -r _spotify-connect._tcp on Linux
```

- The owner is `garage-audio-network.service`. Its generation containers have `restart: no` by
  design: **never** add restart policies, `docker start` them, or `docker rm -f` carriers.
  Recover only with `systemctl start` / `restart` of the owner
  (see `docs/audio-recovery.md`). `/var/lib/garage-audio-network/recovery-state.json` holds the
  last owner result; `python3 -B -m garage_audio.audio_recovery status` summarizes it read-only
  (state, last guard failure such as `guard_heartbeat_stall`, heartbeat age, restart guidance).
- Starting or restarting the owner is hardware mode, and the provider may resume audibly. Tell
  the user before doing it, unless they have already accepted that risk for routine recovery.
- `driver-failure.json` with `"recovery": "terminal"` and a permission or ownership error: do not
  loop restarts; diagnose and report.
- `PermissionError` right after a reboot or USB replug: check `ls -ln /dev/snd/` on the host. The
  UMC playback/control nodes must be `1002:1003` mode 0600 via the udev rule, which needs the
  `garage-dac` account from `/etc/sysusers.d/garage-audio.conf` (installed by
  `scripts/bootstrap-container-audio.py`). Never chown the nodes by hand as a fix.
- Library upgrades must not restart the owner: install
  `config/system/needrestart-garage-audio.conf` as `/etc/needrestart/conf.d/90-garage-audio.conf`.
