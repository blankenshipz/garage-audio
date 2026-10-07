# Garage audio — muted, uncalibrated

Dedicated headless 2.1 routing, with no DAC fallback and no hardware acceptance claims.
**Do not turn amplifier gains up, unmute, play test signals, or calibrate unattended.**

## Current status

- Music Assistant **2.10.5** deployed on garage-server, persistent `/home/zach/garage-audio/state/ma`, non-root UID 1000, no privileges/capabilities, project `garage-audio`.
- Official CamillaDSP **4.1.3 (05e9cfc)** amd64 release verified against GitHub's SHA256. File/ALSA backend tested, no hardware device opened.
- Official pyCamillaDSP **4.0.0**, pinned commit `fdc0d163e02dd73206a493402b43c83502ad83d7`; MA official Python client **1.6.0**, models **1.1.214**; Python **3.14.4**.
- UMC1820 and UMIK **absent**. Internal/HDMI audio is never used. Native definitions are staged, **not installed/started**.
- Host packages and linger/group access need administrator approval. MA onboarding/API token and Spotify login/terms are user actions.
- Seven executable tests passed against the real Camilla binary with synthetic PCM and file/null outputs, including websocket reload/rollback/restart. This is **not** speaker, PipeWire, Squeezelite, or Spotify acceptance.

## Access and onboarding

```sh
ssh -N -L 8095:127.0.0.1:8095 zach@192.168.68.74
# Open http://127.0.0.1:8095
```

UI sequence still pending: initial onboarding → create your administrator account → Settings → Player Providers → add **Squeezelite** (only transport). Set discovery off, Slimproto TCP 3483, classic CLI 0, JSON-RPC 0. After native commissioning the fixed-MAC player appears as **Garage Speakers**. There is no LMS or Home Assistant prerequisite. Add Local Files `/music` after placing your own media under `state/music`. The built-in URL source accepts radio URLs. Do not play until the attended phase.

Create a limited MA API token in the authenticated UI and store it privately at `secrets/ma-token` (chmod 600); never paste it into a chat/log/commit. The live client reached schema **65** and correctly rejected unauthenticated status; authenticated queue/playback remains unverified.

## Administrator prerequisite — user executed only

On garage-server:

```sh
sudo bash /home/zach/garage-audio/scripts/admin-prerequisites
```

This lists exact observed Ubuntu package candidates, adds zach to `audio`, and enables lingering; no reboot/network/SSH/firewall changes. Review before execution. Package availability/dependency resolution may change; failure is explicit, not substituted with a different version. Start a fresh SSH login to refresh groups, then run `scripts/install-native` as zach. It backs up existing project units, enables definitions but does not start them. It writes user service definitions outside the present worker's remote-write scope; that step is expressly left for the user/admin follow-up.

## Design

MA → direct Squeezelite `-o garage_music -r 48000` → dedicated PipeWire native null sink `garage_music` → explicitly targeted sink monitor `pw-record` F32_LE stereo/48k → Camilla `Stdin` → exclusive ALSA `hw:CARD=<verified UMC id>,DEV=<verified PCM>` S32_LE/48k.

Dedicated PW socket `garage-pipewire` and runtime directory prevent accidental use of normal desktop graph. WirePlumber ALSA/BlueZ monitors disabled: no hardware nodes, speaker monitoring, or bypass. Explicit target and `node.dont-fallback` reject missing virtual input. Squeezelite gets a private ALSA config, no `default` PCM. Camilla direct `hw:` device never substitutes `default`/`plughw`.

PW graph fixed 48k; Squeezelite/MA negotiate 48k and PW converts other PCM sources if needed. UMC DAC clock is master: Camilla AsyncSinc Balanced resampling and playback-queue rate adjustment bridge the independent PW clock. Initial quantum/chunk 1024, queue target 2048, adjust period 5s; **live drift/underrun/load tuning is unverified**.

Main volume applies to every output; native ceiling -20 dB, startup `--gain=-60 -m`, no persisted unmute state. Additional -6 dB headroom. LR4 80 Hz HP on mains and LP on sub; sub sum is 0.5L + 0.5R. Cut-only PEQ (max ten/channel) avoids hidden boost. All profile/channel changes mute and reset master -60, validate through official client, read back effective graph, and persist only after readiness; failure restores prior graph muted. Lost websocket stops only project player/DSP. Every CLI command is serialized with flock and audited without tokens/media URLs.

## Commissioning is a separate, attended phase

UMC exposes 20 outputs by product specification; exact Linux PCM/channel count and S32 format are **unverified**. Software mapping assumes zero-based 2=L, 3=R, 4=sub (physical OUT3/4/5), all other analog/digital channels zero. Never infer a verified jack map from this template. Verify hardware identity and channel capabilities while attended before writing `state/alsa-device` with the actual `hw:CARD=UMC...,DEV=...`. Do not write a placeholder to start services. Correct schema/channel count if the attached device reports differently; fail rather than silently downmix.

Wiring: balanced TRS from OUT3/4 to main amplifier line inputs; OUT5 to sub line input with appropriate balanced/unbalanced wiring prescribed by that equipment. No speaker-level outputs into UMC. Disable UMC direct monitoring; analog volume/gain knobs at minimum, amplifier/sub gains low. No assumption that UMC main knob controls these individual outputs. Sub internal crossover/bypass and phase must be verified. Mains must not receive unfiltered bypass audio.

After explicit presence/low-gain authorization: verify one channel at a time using quiet, brief, auto-stopping material, verify the 17 unused outputs remain silent, master mute/volume/sub trim/profile behavior, then record the physical map. Only then create `state/physical-map-verified`; this file is a commissioning receipt, not permission to fabricate acceptance.

## Controls

Run inside `/home/zach/garage-audio` over authenticated SSH:

```sh
venv/bin/python -m garage_audio.control status
venv/bin/python -m garage_audio.control mute
venv/bin/python -m garage_audio.control volume -60  # allowed -90..-20
venv/bin/python -m garage_audio.control channel left gain -3
venv/bin/python -m garage_audio.control channel right delay_ms 1.2
venv/bin/python -m garage_audio.control channel sub invert true
venv/bin/python -m garage_audio.control channel sub mute true
venv/bin/python -m garage_audio.control sub-trim -6
venv/bin/python -m garage_audio.control peq-bypass true
venv/bin/python -m garage_audio.control profile profiles/uncalibrated.json
venv/bin/python -m garage_audio.control rollback
# ONLY after physical commissioning + fresh attended authorization:
venv/bin/python -m garage_audio.control unmute --attended PRESENT-GAINS-LOW
MA_TOKEN_FILE=secrets/ma-token venv/bin/python -m garage_audio.music status
MA_TOKEN_FILE=secrets/ma-token venv/bin/python -m garage_audio.music queue PLAYER_ID
MA_TOKEN_FILE=secrets/ma-token venv/bin/python -m garage_audio.music url PLAYER_ID https://your-radio/stream
MA_TOKEN_FILE=secrets/ma-token venv/bin/python -m garage_audio.music pause PLAYER_ID
MA_TOKEN_FILE=secrets/ma-token venv/bin/python -m garage_audio.music stop PLAYER_ID
```

Readback contains requested volume, mute, native ceiling and effective channel filters. Camilla's getter reports the requested fader, not the clamped signal; steady rendered PCM independently verified the native -20 ceiling. Always start below the ceiling and muted (clamping is ramped). MA/Spotify player-volume controls are **not** the DSP master; use DSP mute/volume as the safety boundary. Local Camilla websocket 1234 has no native auth; never publish it to LAN. SSH is the external authentication boundary; other local host accounts remain a trust boundary.

## Operations / recovery

```sh
docker compose -p garage-audio ps
docker compose -p garage-audio logs --tail=50 music-assistant
PYTHONPATH=. venv/bin/python tests/offline.py  # only generated PCM/file/null
systemctl --user status garage-{pipewire,wireplumber,dsp,player}
journalctl --user -u garage-dsp --since '10 min ago'
scripts/rollback --remove  # stops only project; retains state, packages
```

Fresh DSP/player start always resets mute/-60; missing UMC identity fails before launching PW capture. DSP failure kills the paired player through systemd BindsTo; neither gains a route around DSP. No startup state restores unmute. Profile rollback is tested with official-client file/null processing. Docker MA stop/start and state retention were tested; native systemd restart/failure behavior, actual linger/logout persistence, USB disconnect/reconnect, reboot recovery and sustained real playback load remain pending.

`scripts/stage` archives committed releases, validates Compose, stops only MA for a consistent pre-update state backup, then updates project files/starts MA. Backups can contain credentials; keep them private. `scripts/rollback --state-reviewed` restores the previous release only after state migration review; matching MA state backup may be required. It never restarts native audio. No volume deletion/prune. `scripts/bootstrap-user` downloads hash-pinned DSP and a user-local ALSA library if needed; it never installs host packages. `requirements.lock` pins the observed client dependency set. No OS upgrade or reboot.

See [calibration/video](docs/calibration-video.md), [Spotify](docs/spotify.md), and [phased evidence](docs/evidence.md).
