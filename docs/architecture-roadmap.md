# Review and Docker-first, chat-first roadmap

October 11, 2026. **Review and plan only: no migration or inference enablement.**
Music Assistant (MA) retention, paid provider/model and spending budget are still
open decisions. Existing working music, user accounts, queues, private state and
the acoustic workspace must survive any later migration.

## What exists, rather than a new greenfield app

* `compose.yaml` runs pinned official MA2.10.5 with host networking for LAN
  discovery. `compose.dashboard.yaml` runs a restricted, read-only dashboard image.
* `garage_audio.application.Application.execute` is the strict typed operation
  boundary. HTTP and the official Python MCP SDK stdio server share it and its
  persistent state. Existing SSH MCP needs no new public agent listener.
* `web/` is a dependency-free frontend. Its current home is Connect → Check →
  Measure → Improve → Verify, not chat. Rooms/photos/annotations, independent
  listening profiles, REW imports, analysis and offline proposals already exist.
* `agent_harness.py` uses the official OpenAI Agents SDK, bounded local sessions
  and a read-only room-planner seam. Production inference is unconditionally
  disabled. `agent.run` records a message and an honest disabled result, not an
  assistant answer. The deterministic measurement prompt is not model inference.
* Hardware microphone capture and correction application are not implemented by
  the room workflow. FILE/null jobs and research remain explicitly synthetic;
  electrical predictions are never measured acoustic improvement.

Current audio path:

```text
Spotify phone → official MA Spotify Connect / Soloist
                 ↕ custom garage_speaker_control receiver/native lifecycle bridge
MA playback → Squeezelite → private PipeWire monitor → PCM supervisor / FIFO
            → private policy broker + CamillaDSP → identity-bound UMC1820
```

The custom plugin is NOT the official Spotify Connect plugin. Its runtime hooks
official backend creation/start/stop and receiver events (`connect_runtime.py`,
`connect_adapter.py`); the root-private bridge binds them to native leases and
fail-close behavior (`ma_bridge.py`, `connect_port.py`, `playback_lease.py`).
Deleting the plugin today would remove working integration, not just a settings
page. It is version-coupled custom code we should aim to replace, not silently
carry forward as a permanent user-facing control surface.

## MA hidden behind our app, or no MA?

| Choice | Benefit | Cost and proof still needed |
|---|---|---|
| A. Keep MA as an internal Docker backend | Retains official Spotify Connect/Soloist integration, source providers, queues, upstream volume/pause control and a path to other sources/players. Existing account and queue state can remain intact. | Another service and state store; version-coupled current hooks. App-owned setup needs supported APIs or a clearly identified provider-owned authorization handoff. Replacing the custom bridge requires an equivalent supported lifecycle boundary, not just moving its page. |
| B. Direct Spotify receiver → DSP | Could remove MA, Squeezelite and perhaps PipeWire for a single Spotify-only room; phone Spotify remains the daily music UI. Smaller potential runtime and fewer service interactions. | Must qualify a maintained receiver (for example a librespot-based candidate), its supported authentication/account requirements, Spotify compatibility/terms, discovery, volume mapping, stream clocks and event semantics. No direct-receiver implementation or live proof exists here. Our device worker still needs correct cold-start/stop/reconnect enforcement. |

MA is useful if source/library/queue management, additional rooms or other media
matter. It is not inherently needed for DSP or a single Spotify receiver. For a
strictly single-room Spotify target, **evaluate B before committing to MA-heavy
migration**. If broader sources/rooms matter or B cannot meet playback proof,
choose A as the lower-disruption route. Do not remove the live MA service while
this comparison is unresolved.

For either option, evaluate actual first cold Play, Pause/Resume, Stop, seek and
position ownership, receiver discovery, reconnect, guest/cross-account use and
multi-user takeover. Receiver/account/DRM limitations are candidate-specific,
not promises that a browser OAuth form or either architecture automatically
solves. Preserve Spotify's digital attenuation semantics; do not invent a linear
percent-to-dB mapping or restore a stale louder level. Fosi remains the physical
volume control. A direct receiver must feed the DSP, never bypass it to the DAC.

## Intended everyday app journey

1. Open the app: **chat is the primary page**, with actual connection/readiness
   state, retained conversation and a persistent independent Stop control.
2. Ask for a useful action. A real agent reads current capabilities/state,
   explains prerequisites and proposes typed operations. Responses stream from
   the chosen provider; tool cards show real server acknowledgements/readbacks.
3. Missing setup opens inline UI cards/forms: device identity and readiness,
   provider credential references/authorization, room/photo labeling or plan
   selection. Advanced details are secondary, not a page of raw MCP JSON.
4. Execute permitted operations through the same application boundary used by
   MCP and ordinary UI controls. Show progress, results, provenance and errors;
   cancellation remains usable while a task or response is running.
5. Exit or Stop. Return/reconnect reloads saved conversation and current backend
   jobs/state, distinguishes interrupted work, and never resumes sound or a
   stale grant merely because chat history says it was approved.

Chat is not the only way to configure, cancel or Stop. Keep direct forms/manual
operations usable when inference is disabled, offline, over budget or unavailable.
Until real inference is explicitly enabled, show disabled status and MCP/manual
alternatives—not canned assistant replies dressed as chat.

Separate one-time setup from daily Spotify → Garage Speakers → Play. Hardware
identity/device permissions require a bounded operator bootstrap; routine use
must not ask for a MA session button, terminal grant or repeated setup approval.
Provider token refresh, conversation expiry and shutdown of test fixtures must
not disable the enrolled everyday receiver. Measurement/test-signal authority
remains separate; neither agent text nor a room-map confirmation grants sound.

Retain rooms, sanitized private photos, labels, independent fit/held-out profiles,
original REW imports, offline correction/electrical previews and separate research.
No fabricated measurement, automatic research-filter application or proprietary
ART/Dirac equivalence follows from a friendlier UI.

## Proposed Docker service boundaries

* **App/API/UI:** private state and typed operations; no raw sound device or
  Docker socket. Keep exact Host/Origin/session/CSRF and strict schema validation.
* **Agent worker:** bounded real inference/tool loop with cancellation, streaming,
  retained session and explicit operation allowlist. Shares app operations, not
  independent stores or shell tools. Readiness and approvals remain server-owned.
* **Playback backend:** hidden MA/official Connect (A), or qualified receiver (B).
  LAN discovery may require host networking for this specific service; that does
  not justify host networking for every service or exposing all listeners.
* **Audio engine/device worker:** fixed DSP graph, identity-bound USB access,
  stream lifecycle, bounded leases, watchdog and independent emergency shutdown.
  PipeWire/player containers exist only if the selected transport needs them.
* **MCP:** authenticated SSH stdio into the same app; no public MCP port, token
  pasted to clients or second operation implementation.

Minimal host prerequisites remain: Docker/Compose, stable USB identity and device
ownership/udev provisioning, explicit UID/GID/device permissions and measured
realtime/memlock scheduling requirements where necessary. Docker cannot create
those host policies from a powerless web UI. Keep a small documented installer
for these prerequisites instead of a native Python app/service stack.

Use narrow devices, fixed non-root identities, dropped capabilities, read-only
code, private persistent volumes and bounded resources. No blanket privileged
containers, dashboard Docker socket, general sudo helper or fallback default DAC.
Current systemd calls, root-host `/proc` process/namespace/hash admission, BPF
socket guards and pidfd lifecycle assumptions must be **redesigned and tested**
for the new container boundary—not copied verbatim or removed to make Compose run.
Keep raw DSP control private and one exclusive DAC owner. Preserve fail-close
behavior when app/agent/backend fails or credentials and device identities change.

## Paid chat options — selection, not authorization to call

| Option | Fit | Integration consequence |
|---|---|---|
| OpenAI API | Closest fit to the existing official Agents SDK seam and typed tools. | Still needs actual streaming, reviewed mutation allowlists, server-side credential references and spend/privacy policy. Subscription coding quota is not an application API key. |
| Anthropic API | Alternative real chat/tool provider. | Add an explicit provider adapter or its SDK; preserve the shared operation contracts rather than letting the provider own device policy. Existing OpenAI SDK usage alone proves no Anthropic support. |
| Gemini or another reviewed provider | Another possible chat/tool option. | Verify streaming/tool/cancellation compatibility and privacy/retention terms for the chosen model before promising support. No current integration is claimed. |

Provider/model and budget remain **unchosen**. No price quote or quality ranking
is asserted here. Before calls: choose provider/model, define per-request tokens,
bounded turns/timeouts/concurrency, daily/monthly budget, usage accounting and a
fail-closed exhaustion rule (including in-flight reservations), and provision a
private credential reference. No automatic provider fallback, top-ups or spend.
Keep tracing/external photo sharing disabled by default; photos require separate
explicit privacy opt-in, never an accidental attachment to every chat message.
Imported labels, notes, photos and tool results remain untrusted input. Actual
paid-provider testing requires separate explicit authorization; this phase makes
no model, vision or billing calls.

## Migration acceptance, staged without disrupting current playback

| Gate / lane | User-visible journey and required evidence | Dependency |
|---|---|---|
| M0 / product + packaging | Choose A/B and provider/budget. Inventory real installed source/image/config mounts without credentials; reconcile clean setup, required docs/assets and disk/RAM capacity. | Decisions; preserve live installation and rollback state. |
| M1 / app + agent | Real chat request → allowed typed useful operation → visible server result → cancel/Stop → return/reconnect. Same state read back through real MCP and HTTP/UI. Include denied/missing prerequisites and real provider failures. | Explicit paid-call authorization; isolated app first, no live audio migration. |
| M2 / audio lifecycle | Candidate container stream → real Camilla FILE output, fixed gain/routing/unused outputs, pause/resume/stop, clocks, stale-event rejection, owner-loss/deadline shutdown and restart. | Chosen backend and redesigned enforcement; synthetic receiver seams stay labeled synthetic. |
| M3 / target device | Attended UMC exclusive ownership and identity mapping; no fallback/dual owner. Verify device unplug/reconnect, cold start and independent stop/fault guards on target Linux. | M2; explicit attended scope; reserve space for growth/receipts. |
| M4 / real listening | Genuine phone Spotify cold Play → audible stereo/progress → Pause/Resume without rewind/paused-time jump → Stop → fresh return/reconnect. Also actual second-phone guest/account/takeover checks appropriate to the chosen receiver. | M3 and genuine user observations; desktop/simulator/FILE proof is not phone proof. |
| M5 / UI | Actual desktop and 390px rendered geometry, keyboard focus/selection/navigation, setup cards, streaming/error/cancel/Stop and retained state after reload. | Real app/backend; unit tests or screen mockups do not prove appearance. |
| M6 / cutover + rollback | Freeze input inventory/candidate digest; retain private matching backups. Stop old DAC owner before candidate takes it. Verify journey, then exercise rollback without dual ownership or restored stale sound grants. | M1–M5; matching backend/platform, capacity and user-visible interruption plan. |

Keep old playback available until candidate proof warrants a scoped switch. An
off-device container may test FILE first without taking the live DAC. Preserve
failed logs outside shipped artifacts; never relabel old proofs after input changes.
Disk/cache growth is a deployment prerequisite, not permission to prune private
receipts, MA accounts/cache or unrelated services. The separate existing playback
task still owns its pending physical-phone acceptance; this review does not close it.

## Review register and bounded validation

Baseline: `main` at `6ad983008b9d4d6705d118c0246fb4628af88819`; 52 tracked files,
20 modified tracked files and 158 untracked files, no staged changes or remote.
Source checkpoint: `e7b6cddcda4419f2614c23249806e555d62ac34c` (178 changed files).
The interdependent source was committed together: tracked broker/services/installers
depend on previously untracked modules/scripts. Intentional vendor sources,
binary pins and licenses belong with packaging; private state/credentials/receipts
and caches do not. The separate acoustic-copilot-gym repository is unchanged.

| ID | State | Finding, evidence and next action |
|---|---|---|
| R1 | Open / deployment | Local `compose.yaml` lacks the installed read-only provider/bridge mounts described by `docs/normal-listening.md`. Do not apply it to production. Reconcile a credential-free installed inventory before a migration candidate. |
| R2 | Open / clean setup | `compose.dashboard.yaml` requires external `garage-audio_default`; host-network MA Compose does not create it. Existing deployment can have a retained network. Define/test one explicit clean-install network topology in M0, not a blind live network edit. |
| R3 | Open / backend | Custom MA runtime uses private upstream seams. Replace only after A's supported lifecycle interface or B's receiver meets M2–M4. Removing only its UI does not eliminate that dependency. |
| R4 | Resolved / docs | Historical README/normal-listening/DAC setup now clearly labeled; latest recorded Connect source set `b81fbf71` is not claimed as phone acceptance. Local authority/adapter/runtime SHA256 prefixes match the recorded deployed set: `52fc05aa`, `1c994a24`, `5d800758`. |
| R5 | Resolved / checkpoint | Immediate dependency omissions avoided by including project-owned modules, scripts, tests, web and vendor inputs in one coherent source checkpoint. No migration edits to authority/gain architecture. |
| R6 | Resolved / test environment | Reused local environment had MCP2.x, causing four FastMCP failures. Retained failures; isolated MCP1.30.0/SDK0.23.1 environment passed affected checks. Do not upgrade project MCP implicitly. |
| R7 | Open / bootstrap | Native builder still requires ignored `tools/` downloads/wheels and Ubuntu library assets; dashboard vendors Bookworm FILE dependencies separately. A Git checkout is not a complete offline native installer. M0 must pin/document matching platform prerequisites; no fresh installation/build proof here. |
| R8 | Open / storage hardening | Legacy Application session/profile writes use path-based `.tmp` replacement without directory fsync, unlike anchored room/photo/job stores. Private owner-controlled state is assumed. Before broadening writers/import trust, review crash durability and symlink/race handling with focused tests. |
| R9 | Open / live evidence | Native Linux process/device enforcement, deployed SSH MCP, actual UI geometry and phone/acoustic behavior not rerun here. No production acceptance inferred from local checks. |
| R10 | Accepted / upstream license | Staged whitespace checking flags original spaces/EOF in `vendor/dsp/licenses/ALSA-LGPL-COPYING`. Preserve the upstream license bytes; project-owned files pass the check with that exact license excluded. |

Review depth: all 210 initial repository inputs inventoried/hashed and scanned for
common private-key/provider-key/JWT/credential-URL patterns (no hits); all111 Python
files parsed, JSON syntax checked, all six vendor archive/package hashes verified.
Focused source reading covered shared operation/auth/HTTP boundaries, disabled
inference, anchored room/photo/job artifacts, job execution/reaping, native broker,
MA private bridge and Connect runtime, changed installers/services and packaging.
This is not a line-by-line security certification of every file or proof that
arbitrary secrets cannot evade a pattern scan. No credential stores were read.

Local validation passed: hardware capability fixtures; Connect authority/adapter/
runtime/lease and normal-policy unit tests; room/photo/profile domain, disabled SDK,
application, measurement workflow/signal, trusted-LAN HTTP and research tests;
58 frontend unit tests; commissioning proof fixtures; actual local MCP stdio/HTTP
inventory and quiet mutation/readback, measurement and room journeys. MCP source
inventory was verified as34 tools, five resources and one prompt, not asserted for
the deployed installation. All inputs/evidence remain measured/synthetic as labeled.

Portable tests used Python3.11 on macOS; affected MCP transport checks used a new
isolated pinned application subset (MCP1.30.0, SDK0.23.1). Other passing checks used
existing local environments and were not unnecessarily rerun. These checks do not
validate the full production lock, which needs Python≥3.13 for the MA
client. Linux Camilla/MA-container/USB/BPF/pidfd service tests, Docker builds,
physical sound, live inference and rendered UI checks were deliberately not run.
Full logs, exits and source identities are retained privately outside this repo,
including the initial environment failures. This checkpoint is reviewed source,
not a newly deployed or fully commissioned release.
