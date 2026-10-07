# Garage audio — muted, uncalibrated

Dedicated headless 2.1 routing, with no DAC fallback and no hardware acceptance claims.
**Do not turn amplifier gains up, unmute, play test signals, or calibrate unattended.**

## Current status

- Music Assistant **2.10.5** deployed on garage-server, persistent `/home/zach/garage-audio/state/ma`, non-root UID 1000, no privileges/capabilities, project `garage-audio`.
- Official CamillaDSP **4.1.3 (05e9cfc)** amd64 release verified against GitHub's SHA256. File/ALSA backend tested, no hardware device opened.
- Official pyCamillaDSP **4.0.0**, pinned commit `fdc0d163e02dd73206a493402b43c83502ad83d7`; MA official Python client **1.6.0**, models **1.1.214**; Python **3.14.4**.
- UMC1820 and UMIK **absent**. Internal/HDMI audio is never used. Native definitions are staged, **not installed/started**.
- Host packages and linger/group access need administrator approval. MA onboarding/API token and Spotify login/terms are user actions.
- Eight DSP test methods now cover real Camilla synthetic PCM/file/null processing, crossover/PEQ edits and master-state-preserving profile reloads. Real user-local PipeWire **1.6.2**, WirePlumber **0.5.13** and Squeezelite **2.0.0-1541** runtime tests also passed: explicit virtual PCM, sink-monitor→DSP stdin→20-channel file, 17 unused channels zero, declarative sink recreation. No host packages installed. This is **not** MA playback, DAC/speaker, Spotify, or boot acceptance.

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

This lists exact observed Ubuntu package candidates and enables lingering; it **does not give zach audio/DAC access**. No reboot/network/SSH/firewall changes. Review before execution. Package availability/dependency resolution may change; failure is explicit, not substituted with a different version. Dedicated DAC ownership requires the separate attended-identity provisioning in [DAC isolation](docs/dac-isolation.md). No root provisioning has been executed. After that review, fresh login for `garage-stream`, then run `scripts/install-native` as zach. It backs up existing project units, enables definitions but does not start them. These root/user service writes are outside this worker's present remote-write scope and are left for explicit administrator follow-up.

## Design

MA → direct Squeezelite `-o garage_music -r 48000-48000` → dedicated PipeWire native null sink `garage_music` → explicitly targeted sink monitor `pw-record --raw` F32_LE stereo/48k → write-only PCM FIFO → separate `garage-dsp` identity's Camilla `Stdin` → direct ALSA `hw:CARD=<verified UMC id>,DEV=<verified PCM>` S32_LE/48k. Root-owned DSP executable/service and serial-specific device ownership are staged only; current real runtime tests substitute FILE output, never ALSA.

Dedicated PW socket `garage-pipewire`, runtime and policy-state directories avoid the normal desktop graph. WirePlumber ALSA/BlueZ/MIDI/camera monitors disabled: no hardware nodes or speaker monitoring. Monitor uses both `node.dont-fallback` and `node.dont-move`: real tests caught WP redirecting an explicit target through metadata without the latter. Squeezelite gets a private ALSA config, no `default` PCM. Camilla direct `hw:` device never substitutes `default`/`plughw`. A private graph and exclusive ALSA open alone **do not** prevent a same-user application from opening the DAC when DSP is down; the staged dedicated-UID/udev boundary addresses normal direct opens, but is not yet hardware-enforced or verified. Local unauthenticated DSP websocket remains a trusted-admin boundary, not protection against malicious host users.

PW graph fixed48k; Squeezelite uses the explicit48000-48000 range (a single `-r48000` is only a maximum, not48-only). MA negotiation still needs authenticated provider proof. Squeezelite initially opens its ALSA plugin at44100 before server connection; PipeWire converts that clock/format to the graph's48k. Other direct PCM sources also convert through PW. UMC DAC clock is master: Camilla AsyncSinc Balanced resampling and playback-queue rate adjustment bridge the independent PW clock. Initial quantum/chunk1024, queue target2048, adjust period5s; **live drift/underrun/load tuning is unverified**.

Main volume applies to every output; native ceiling -20 dB, startup `--gain=-60 -m`, no persisted unmute state. Additional -6 dB headroom. LR4 default80 Hz HP on mains/LP on sub, editable40..160Hz; sub sum0.5L+0.5R. Cut-only PEQ max ten/channel. Profile/channel/crossover/PEQ transactions save prior bounded master/mute, mute/reset-60 and wait400ms ramp, validate/reload/read back full graph, then **preserve prior volume and mute on success**. An existing unmute is preserved only with fresh `--attended-profile PRESENT-GAINS-LOW`; otherwise fail closed. Invalid/failure restores previous known-good graph **muted/-60**, never prior unmute. Restart remains muted/-60 regardless of prior state. Lost websocket stops only project stream producer/player. Normal controls serialized/audited; independent commissioning guardian may preempt them solely to emergency-mute.

## Commissioning is a separate, attended phase

UMC exposes 20 outputs by product specification; exact Linux PCM/channel count and S32 format are **unverified**. Software mapping assumes zero-based 2=L, 3=R, 4=sub (physical OUT3/4/5), all other analog/digital channels zero. Never infer a verified jack map from this template. Verify hardware identity and channel capabilities while attended before writing `state/alsa-device` with the actual `hw:CARD=UMC...,DEV=...`. Do not write a placeholder to start services. Correct schema/channel count if the attached device reports differently; fail rather than silently downmix.

Intended wiring, **physically unverified**: **OUT3 = left → Fosi V3 → Klipsch R-28F left; OUT4 = right → Fosi V3 → R-28F right; OUT5 → Klipsch R-12SW LFE RCA input using the long TS-to-RCA cable**. Fosi V3 RCA inputs require the appropriate unbalanced line interconnect (not an assumed balanced TRS input). Never connect speaker-level signals into UMC. Disable UMC direct monitoring; start Fosi/sub gains low and verify they stay fixed. Do not assume the UMC main knob controls OUT3/4/5. Sub internal lowpass should use its verified LFE/bypass/max setting appropriate to this R-12SW; phase setting must be documented, not guessed. Investigate hum/cable routing/shielding safely before gain increases; no purchases prescribed. During calibration disable MA loudness normalization, Spotify normalization, crossfade/automatic gain, and variable source/player gains; use a documented fixed unity transport and only DSP master/branch trims. Mains never receive unfiltered bypass audio.

After explicit presence/low-gain authorization: verify one channel at a time using quiet, finite auto-stopping material. `commission-window left --seconds 3 --attended PRESENT-GAINS-LOW` is the **pre-map-verification exception**: temporary single-branch graph, fixed -60 master, maximum5s unmute, detached emergency-mute timer, restore original graph muted/-60, no test sound generated automatically. See [commissioning](docs/commissioning.md). This removes the circular requirement to fabricate a marker before testing. Verify all17 unused outputs and controls, then record the real physical map. Only then create `state/physical-map-verified`; never create it merely to unlock playback.

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
venv/bin/python -m garage_audio.control crossover 80
venv/bin/python -m garage_audio.control peq sub 0 45 2 -3  # zero-based slot,Hz,Q,cut dB
venv/bin/python -m garage_audio.control peq-delete sub 0
venv/bin/python -m garage_audio.control peq-clear sub
venv/bin/python -m garage_audio.control profile profiles/uncalibrated.json
venv/bin/python -m garage_audio.control rollback
# For an already-unmuted attended session, successful edit preserves state:
venv/bin/python -m garage_audio.control --attended-profile PRESENT-GAINS-LOW profile profiles/uncalibrated.json
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

Fresh DSP/player start resets mute/-60. Missing UMC identity or dedicated daemon/FIFO rejects without a same-user hardware fallback. Staged system DSP starts waiting/no config; user bridge initializes the stored profile muted. Root-daemon loss closes FIFO reader, so producer failure should stop paired player through user systemd BindsTo; this cross-manager hardware recovery remains **unverified**, not a proved security boundary. No restart restores unmute. Real profile success preservation, invalid rollback and timer behavior were tested entirely file/null; real isolated PW sink restart tested. Docker state retention tested. Root/user systemd recovery, linger/logout, USB/reboot and sustained real playback remain pending. User removal stops stream/player and MA only; if root DAC service is later provisioned, reviewed `sudo bash scripts/admin-dac-rollback` is additionally required.

`scripts/stage` archives committed releases, validates Compose, stops only MA for a consistent pre-update state backup, then updates project files/starts MA. Backups can contain credentials; keep them private. `scripts/rollback --state-reviewed` restores the previous release only after state migration review; matching MA state backup may be required. It never restarts native audio. No volume deletion/prune. `scripts/bootstrap-user` downloads hash-pinned DSP and a user-local ALSA library if needed; it never installs host packages. `requirements.lock` pins the observed client dependency set. No OS upgrade or reboot.

See [calibration/video](docs/calibration-video.md), [Spotify](docs/spotify.md), [native tests](docs/native-tests.md), [DAC isolation](docs/dac-isolation.md), [commissioning](docs/commissioning.md) and [phased evidence](docs/evidence.md).
