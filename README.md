# Garage audio — source checkpoint and migration planning

Dedicated headless audio, with no DAC fallback and no acoustic-correction claims.
**Do not turn amplifier gains up, unmute, play test signals, or calibrate unattended.**

## Latest recorded playback state — October 11, 2026

The separate playback work records installed native UMC1820 playback and the
official MA Spotify Connect receiver bridged by `garage_speaker_control`.
The latest provider source set is identified as `b81fbf71`; this repository retains
its matching authority/adapter/runtime modules. Component tests and passive native
processing observations are not physical-phone audibility/timeline acceptance.
That phone-only cold Play → Pause/Resume → Stop acceptance remains pending and
separately owned. This source review does not restart, redeploy or approve sound.

The [review and architecture roadmap](docs/architecture-roadmap.md) compares hidden
MA with a direct Spotify receiver, Docker-first services and chat-first app setup.
This phase commits existing source and a plan only; migration, paid provider/model
and budget remain undecided, and no paid calls are enabled.

Everyday entry is [Spotify → Garage Speakers → Play](docs/spotify-only-quickstart.md),
not the older MA manual listening-session button. The dashboard/MCP acoustic
workspace remains distinct from receiver playback. Inference is still disabled.
The historical initial-install notes below are not a current installation recipe.

## Agent-first room correction workspace

The next source release adds private room-photo maps and independent **whole-room,
seat, area and custom listening plans**, shared by dashboard and SSH MCP. Label
speakers/regions and explicit fit/held-out mic positions, confirm the map, then
follow a deterministic REW import guide. Photos do not imply real distances or
acoustic correction; plans remain unmeasured until qualified sources are bound.
Photo bytes remain local, sanitized and authenticated by default.

The official OpenAI Agents SDK harness is **provider-disabled** for this release:
the user selected “Build, no paid calls yet.” No live LLM/vision, external photo
transmission, API spend or fake chat occurs, even with ambient API credentials.
Real MCP/manual room flows remain available. See [room API](API_CONTRACT_ROOM.md),
[agent workflow](docs/agents.md) and [SDK configuration](docs/agent-sdk.md).
Local SDK scripted fixtures are unit coverage, not live model acceptance; new
deployed/browser behavior must be verified against the frozen release separately.

Private dashboard: **http://192.168.68.74:8098** on trusted home Wi-Fi only
(HTTP is not encrypted). Authentication and secure login handoff are described in
[dashboard access](docs/dashboard.md); [MCP setup](docs/agents.md) uses existing SSH,
not embedded chat, an API key or paid inference.

The new source workflow is **Connect → Check → Measure → Improve → Verify**:
one focused stage at a time, with persistent safety/next action and cross-stage
job cancellation. Raw setup/manifests stay collapsed until reviewed; form state
survives navigation. This navigation update still awaits release acceptance.
copy MCP configuration (copying does not establish agent connectivity), read actual
readiness, create a quiet plan, import REW text with explicit provenance, analyze
normalized multi-position magnitude, and prepare a conservative cut-only offline
proposal with electrical checks. Advanced profiles and original imports remain
available. See [measurement workflow](docs/measurement-workflow.md) and the
[exact HTTP/MCP schema](API_CONTRACT.md). Deployment/browser acceptance of this
workflow is a separate release gate recorded externally against an exact candidate,
not inferred from this source documentation.

No production microphone capture, hardware sweep executor or proposal apply
operation exists. Synthetic imports stay labeled synthetic; electrical
predictions are not measured after-results. Experimental multi-speaker research
is separate from production correction, not Dirac/ART equivalence or garage success.
The [primary-source research and reproducible experiment](docs/active-control-research.md)
documents reviewed papers, a causal constrained FIR prototype, synthetic tradeoffs
and observed failures. Its separate dependency pin is `research/requirements.txt`;
the prototype has no dashboard/MCP apply path and exports metrics, not filters.

Proposal selection now checks non-worsening predicted target RMS at every position
on both analysis-grid and original frequencies; unresolved narrow peaks may yield
no useful cut. Known absolute clock drift above 100 ppm blocks proposal creation;
unknown drift remains provisional magnitude-only evidence. Revision-gated reads
hide superseded analysis/proposals and require recomputation from retained originals.
These are conservative sampled model checks, not acoustic or synchronization proof.

The source now implements bounded **synthetic FILE/null jobs**: request a manifest,
inspect server progress/history, cancel, and execute actual owned file/null children
only after a separate human approves that exact manifest. The deterministic MCP
prompt is a briefing, not embedded inference or consent. Bootstrap/MCP credentials
remain automation; they cannot approve, enroll or promote a human. Hardware jobs
are unconditionally denied. File/null evidence is not microphone capture or garage
correction. Deployment and live acceptance of these new job/role interfaces remain
separate gates; see [agent jobs](docs/agents.md) and [human boundaries](docs/dashboard.md).

No human verifier has been provisioned in production by this work. Separate human
login/approval requires an independently verified standalone native file/null
fixture with an actual loopback listener and local loopback peer. The deployed
container dashboard's human routes are disabled entirely, even via localhost8098
or an SSH tunnel and even if a verifier is accidentally present. Never enter an
independent human password into deployed8098 or its tunnel. Grants are exact,
one-use/time-limited, revoked by logout/context change and invalidated by restart.
Missing provisioning is a prerequisite, not implied approval by dashboard access.
Source status/capabilities report `human_approval_available` and
`human_approval_unavailable_reason`; local backend/UI executable fixtures and scoped
safety review are complete, but deployment gates remain pending. Unknown or false
readiness hides credential entry, never infers it from URL.

Separately, newly generated bounded synthetic sweeps passed actual CamillaDSP
stdin→20-channel FILE processing with the existing safety profile: exact frame
counts, conservative levels and unused outputs zero. This is an isolated adapter
test, not job-engine integration with DSP/FIFO. The current stereo 2.1 graph sends
left/right input to the sub sum too; dual-mono sub excitation also drives mains.
That normal playback profile remains unchanged. New manifest2 FILE jobs instead
use a disposable isolated mono→left/right/sub software route: 20 physical test
channels plus three diagnostic probes, with only the requested branch nonzero.
Actual native127 independently approved job tests rendered all three branches,
84000 frames each, pinned CamillaDSP4.1.3, master-60/headroom-6/ceiling-20.
This resolves software FILE isolation, not hardware commissioning or a live FIFO
bridge. Hardware jobs remain disabled; public dashboard human authority stays off.

The next packaging input now pins the same official CamillaDSP4.1.3 binary and
Bookworm-compatible ALSA library with archive/binary hashes, licenses and source
provenance in `vendor/dsp`. An unactivated proof image passed actual FILE-only
loading/execution (8192 synthetic frames), root-owned paths and child-scoped
library lookup. Final job-integrated FILE tests also passed, including unsafe-path
admission and descendant reaping after cancellation/SIGKILL/journal failure.
Final frozen/deployed acceptance is a separate release gate. Production human
approval and hardware remain disabled; sources/licenses are retained with pins.

Next attended work: verify server-attached UMIK calibration/orientation and UMC
identity/output mapping, review independently scoped hardware sound authority and
hardware capture implementation, then obtain explicit attended authorization before sound.
Laptop REW text import remains supported. New matching measured before/after data
is required to verify any acoustic improvement; a phone viewport is not a physical
phone test. None of those hardware steps is authorized by an agent plan.

## Historical initial staging baseline (not current operations)

The remaining sections retain the original pre-hardware staging design and proof.
Statements about absent hardware, uninstalled services, 20-channel/S32 assumptions,
old ceilings and attendance-string commands are historical, not current readiness
or authorization. The current broker rejects legacy attendance-string unmute and
commissioning. Do not replay these old setup/enable/unmute commands on the live
installation; use the current Spotify quickstart and exact reviewed deployment.

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

Do not execute mutable project scripts with sudo. `scripts/admin-prerequisites` lists observed Ubuntu package candidates/lingering for administrator review; use reviewed literal commands, never grant zach audio/DAC access. Exact root-private snapshot/bootstrap and externally pinned bundle workflow is in [DAC isolation](docs/dac-isolation.md); `admin-dac-isolation` deliberately refuses the old unsafe invocation. Parent safety review must approve the hashes before provisioning. No root provisioning has been executed. After that review, refresh login for `garage-stream,garage-control`, then review `scripts/install-native` as zach. It backs up existing project units, enables definitions but does not start them. Root/user service writes remain outside this worker's current remote-write scope.

## Design

MA → direct Squeezelite `-o garage_music -r 48000-48000` → dedicated PipeWire native null sink `garage_music` → explicitly targeted sink monitor `pw-record --raw` F32_LE stereo/48k → write-only PCM FIFO → separate `garage-dsp` identity's Camilla `Stdin` → direct ALSA `hw:CARD=<verified UMC id>,DEV=<verified PCM>` S32_LE/48k. Root-owned DSP executable/service and serial-specific device ownership are staged only; current real runtime tests substitute FILE output, never ALSA.

Dedicated PW socket `garage-pipewire`, runtime and policy-state directories avoid the normal desktop graph. WirePlumber ALSA/BlueZ/MIDI/camera monitors disabled: no hardware nodes or speaker monitoring. Monitor uses both `node.dont-fallback` and `node.dont-move`: real tests caught WP metadata retargeting without the latter. Squeezelite gets a private ALSA config, no `default` PCM. Camilla direct `hw:` never substitutes `default`/`plughw`. Staged dedicated UID/serial udev isolation prevents ordinary applications' direct DAC opens; raw DSP loopback lives in a private network namespace and only immutable narrow policy IPC is exposed. Actual systemd/DAC enforcement remains unverified; root/docker administrators remain trusted.

PW graph fixed48k; Squeezelite uses the explicit48000-48000 range (a single `-r48000` is only a maximum, not48-only). MA negotiation still needs authenticated provider proof. Squeezelite initially opens its ALSA plugin at44100 before server connection; PipeWire converts that clock/format to the graph's48k. Other direct PCM sources also convert through PW. UMC DAC clock is master: Camilla AsyncSinc Balanced resampling and playback-queue rate adjustment bridge the independent PW clock. Initial quantum/chunk1024, queue target2048, adjust period5s; **live drift/underrun/load tuning is unverified**.

Main volume applies to every output; native ceiling -20 dB, startup muted/-60, no persisted unmute state. Additional -6 dB headroom. LR4 default80 Hz HP on mains/LP on sub, editable40..160Hz; sub sum0.5L+0.5R. Cut-only PEQ max ten/channel. Transactions mute/reset-60, wait400ms ramp, validate/reload/read back full graph and durably commit/read back profile state while muted. Prior bounded volume/mute is restored **last**, only for the same owned DSP generation. Existing unmute additionally requires fresh `--attended-profile PRESENT-GAINS-LOW`. Failure proves muted/-60 **before** rollback; inability to prove mute kills only the owned DSP. Initialize/profile share lifecycle lock; restart never restores unmute. Lost IPC stops only project producer/player. Controls use audited, peer-credential-authorized Unix policy IPC.

## Commissioning is a separate, attended phase

UMC exposes 20 outputs by product specification; exact Linux PCM/channel count and S32 format are **unverified**. Software mapping assumes zero-based2=L,3=R,4=sub (physical OUT3/4/5), all other channels zero. Never infer a verified jack map from this template. Attended administrator verification supplies root-owned `/etc/garage-audio/identity.json`; the daemon derives its ALSA binding from that identity. Do not write placeholders to start services. Correct schema/channel count if hardware differs; fail rather than silently downmix.

Intended wiring, **physically unverified**: **OUT3 = left → Fosi V3 → Klipsch R-28F left; OUT4 = right → Fosi V3 → R-28F right; OUT5 → Klipsch R-12SW LFE RCA input using the long TS-to-RCA cable**. Fosi V3 RCA inputs require the appropriate unbalanced line interconnect (not an assumed balanced TRS input). Never connect speaker-level signals into UMC. Disable UMC direct monitoring; start Fosi/sub gains low and verify they stay fixed. Do not assume the UMC main knob controls OUT3/4/5. Sub internal lowpass should use its verified LFE/bypass/max setting appropriate to this R-12SW; phase setting must be documented, not guessed. Investigate hum/cable routing/shielding safely before gain increases; no purchases prescribed. During calibration disable MA loudness normalization, Spotify normalization, crossfade/automatic gain, and variable source/player gains; use a documented fixed unity transport and only DSP master/branch trims. Mains never receive unfiltered bypass audio.

After explicit presence/low-gain authorization: use quiet finite material and `commission-window left --seconds 3 --attended PRESENT-GAINS-LOW`. This **pre-map exception** uses a temporary single branch at fixed-60 and a maximum5s deadline that kills the owned DSP generation; recovery loads the original durable profile muted/-60. No sound is generated automatically. See [commissioning](docs/commissioning.md). Verify all17 unused outputs and controls before an administrator records the actual map in `/var/lib/garage-audio-dsp/physical-map-verified`; never fabricate this receipt.

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

Readback contains requested volume, mute, native ceiling, effective filters and owned generation. Camilla's getter reports requested fader, not clamped signal; rendered PCM verified the native-20 ceiling. MA/Spotify volume is **not** DSP master. Raw websocket1234 stays only in the private namespace; never publish/proxy it. SSH plus permissioned Unix IPC authenticate control; root/docker administrators remain trusted.

## Operations / recovery

Emergency producer stop confirms identity-bound exit **before** disk persistence or manager calls. Disk latch failure is separately reported. An existing producer registration now remains a startup-blocking tombstone even after exit, so every subsequent producer start needs explicit muted/-60 `--rearm`; no failed latch write can silently permit restart. Supervisor errors also kill/reap before acquiring a persistence lock or writing state.

```sh
docker compose -p garage-audio ps
docker compose -p garage-audio logs --tail=50 music-assistant
PYTHONPATH=. venv/bin/python tests/offline.py  # only generated PCM/file/null
systemctl --user status garage-{pipewire,wireplumber,dsp,player}
journalctl --user -u garage-dsp --since '10 min ago'
scripts/rollback --remove  # stops only project; retains state, packages
```

Fresh DSP/player start resets mute/-60. Missing UMC identity or dedicated daemon/FIFO rejects without fallback. FIFO creation explicitly chmods/verifies0620 despite umask0022. User bridge now uses a registered PCM supervisor: producer startup and persistent fail-close latch share a lock. On unknown mute/control, stop first validates UID/boot/starttime/executable/argv/cwd/instance marker and signals the owned child's **pidfd**, verifying actual exit; manager stop follows only as best effort, with nonzero/timeout/unavailable recorded. No name/group-based or unrelated process killing. Supervisor polls narrow IPC status (2s timeout,0.5s interval), kills/reaps its child on control loss/generation change, and uses kernel parent-death SIGKILL if supervisor crashes. Fail-close latch prevents auto-restart; explicit `venv/bin/python -m garage_audio.producer --rearm` requires policy readback muted/-60 before deliberate service restart. Producer EOF ends DSP generation. Actual root/user systemd/FIFO/DAC recovery remains **unverified**; real FILE/null process termination and distinct-UID FIFO tests passed. Existing signal/native transport evidence remains applicable. Root/user service, linger/logout, USB/reboot and sustained playback remain pending. User removal stops stream/player/MA; future root rollback uses verified root-owned `/usr/local/libexec/garage-audio/admin-rollback`, never mutable user code with sudo.

`scripts/stage` archives committed releases, validates Compose, stops only MA for a consistent pre-update state backup, then updates project files/starts MA. Backups can contain credentials; keep them private. `scripts/rollback --state-reviewed` restores previous release only after migration review; same-MA-image source rollback and restore were exercised. Cross-image/schema migration may require matching state backup and is untested. Script pre-parses its transaction so restoring its own older file cannot interrupt execution. Never restarts native audio; no volume deletion/prune. `scripts/bootstrap-user` downloads hash-pinned DSP/user-local ALSA library, never host installs. `requirements.lock` pins observed client dependencies; native startup guard refuses unverified version drift. No OS upgrade/reboot.

See [calibration/video](docs/calibration-video.md), [Spotify](docs/spotify.md), [native tests](docs/native-tests.md), [DAC isolation](docs/dac-isolation.md), [commissioning](docs/commissioning.md) and [phased evidence](docs/evidence.md).
