# Review and Docker-first, chat-first roadmap

October 11, 2026. **Implementation authorized; migration gates pending; inference disabled.**
Shared intent `garage-audio-docker-chat`, revision 3 selects hidden Music Assistant
(MA) as the retained Docker playback backend, chat-first UI and app-owned setup.
Implement configurable agent integration, but make no application inference calls,
even with ambient credentials. Existing music, accounts, queues, private state,
acoustic workspace and rollback must survive the staged migration.

**Checkpoint221e7a7 remains historical, not production-ready.** Revision2 user
authorization permits preparing/testing/submitting the upstream opt-in volume PR
and using its identical pinned narrow patch locally pending release. Owners are
active again; affected patch/network/app inputs require a new inventory/refreeze
after pause. Real DHCP allocation was obtained and released; use fresh renewable
leases, never the old address. M0/M5/U1 still require actual patch/namespace/receiver
qualification and clean-install/runtime inputs. No PR acceptance/merge, safe cold
Play or production readiness is inferred from approval or prior FILE/UI proof.
Revision3 now explicitly prioritizes deploying the new version: complete essential
volume/identity/private-boundary/exclusive-owner/candidate/rollback gates, then
cut over quietly. Do not spend that journey on optional old-stack repairs. A verified
immutable cached-project derivative is an allowed existing-target build route;
fresh-install availability/reproducibility claims remain separate and truthful.

## What exists, rather than a new greenfield app

* `compose.yaml` runs pinned official MA2.10.5 with host networking for LAN
  discovery. `compose.dashboard.yaml` runs a restricted, read-only dashboard image.
* `garage_audio.application.Application.execute` is the strict typed operation
  boundary. HTTP and the official Python MCP SDK stdio server share it and its
  persistent state. Existing SSH MCP needs no new public agent listener.
* `web/` is a dependency-free frontend. The legacy home was Connect → Check →
  Measure → Improve → Verify; this source now has chat-primary and direct setup.
  Production still retains its original image. Rooms/photos/annotations, independent
  listening profiles, REW imports, analysis and offline proposals already exist.
* `agent_harness.py`/`agent_runtime.py` use the official OpenAI Agents SDK, bounded
  local sessions and shared typed tools/streaming/cancellation. Inference is unconditionally
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

**Selected: A, hidden MA.** B remains architectural context, not an assigned
implementation or release dependency in this phase. Keep official Spotify Connect,
retained MA account/queue state and Spotify-only ordinary playback. Replace the
custom control-plugin dependency with supported container audio/lifecycle
integration; hiding its UI alone does not satisfy that requirement. Do not remove
the live integration before the replacement passes its assigned gates.

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
2. Retain the user's message and show truthful disabled-inference state, never a
   canned assistant answer. Direct setup/manual actions remain usable and show
   real server acknowledgements/readbacks. The configurable streaming/tool/cancel
   integration is implemented behind disabled policy; real provider responses
   require future authorization and are not acceptance in this phase.
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
* **Playback backend:** selected hidden MA/official Connect (A), not receiver B.
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
| M0 / audio + packaging | Credential-free deployed/config inventory, supported hidden-MA container topology, required assets/pins, Linux device/platform/capacity and retained rollback; document narrow host bootstrap. | Live inventory; no production edit during investigation. |
| M1 / app + agent | Chat-primary persisted conversation; truthful disabled inference, direct manual/setup actions and configurable bounded streaming/tool/cancel implementation sharing typed operations. | Local executable checks; no provider calls, fake answers or ambient-key bypass. |
| M2 / audio lifecycle | Actual isolated Docker MA/transport/device worker/Camilla FILE: OFF→fresh start→PCM, Pause/Resume/volume/Stop/restart, snapshot denial/loss watchdog; fixed -6 dB full-range OUT3/4, spare outputs zero. | Local executable software proof; label synthetic receiver seams; no custom MA control plugin. |
| M3 / app + integration | App-owned setup and real SSH MCP initialize/list/status→useful quiet mutation→same-state MCP and HTTP/UI readback, structured credential/device failures; retain room/photo/profile/calibration provenance. | Live transport, server-owned capabilities; no new token ceremony/public MCP listener or agent sound grant. |
| M4 / UI | Actual desktop and 390px chat-first geometry, keyboard focus, selected/navigation/setup/error/disabled/cancel/Stop states and reload/reconnect persistence. | Real rendered app/backend; unit checks are not appearance proof. |
| M5 / audio + integration | Freeze inventoried candidate and matching backend/platform/capacity; quiet exclusive UMC Docker cutover, private raw DSP, restart/no-autoplay, identity/watchdog/recovery/rollback without native app service dependencies. | Paused package writers; M0–M4 relevant gates, narrow host bootstrap, reserve for growth; preserve old deployment and other projects. |
| M6 / user + root | Real user phone Spotify-only cold Play audible OUT3/4→Pause/Resume correct position→Stop→return/reconnect; second account where accessible. No MA plugin/session/button prerequisite. | M5 and user-performed physical phone/sound observations; FILE/HTTP/browser proof cannot substitute. |
| U1 / upstream + audio + packaging | Opt-in Spotify-authoritative volume preserves defaults, scoped old-bug/event/grace/echo/reconnect regression proof, focused public PR URL/commit, identical pinned local patch/provenance and actual receiver qualification. | User approved PR/local narrow patch; no private garage material published, no claim merged, no production agent Play. |

Keep old playback available until candidate proof warrants a scoped switch. An
off-device container may test FILE first without taking the live DAC. Preserve
failed logs outside shipped artifacts; never relabel old proofs after input changes.
Disk/cache growth is a deployment prerequisite, not permission to prune private
receipts, MA accounts/cache or unrelated services. The separate existing playback
task still owns its pending physical-phone acceptance; this review does not close it.

Latest bounded checkpoints, with original identities retained:

* Actual garage MA2.10.5 app-owned Soloist protocol: status/begin/backend selection,
  terms unchecked, identical MCP/HTTP flow readback and abort passed. No consent,
  key, download, authentication or provider creation occurred. Source13e30891;
  `app-spotify-real-protocol-sdegbput`, private
  `/private/tmp/opencode/garage-spotify-protocol.Wufy7q/result.json`. FILE stayed
  OFF/closed/no children. Owned app/MA stop required timeout termination; clean
  app/MA shutdown/restart is not proven by that fixture.
* New UI web3d38be09 passed actual desktop/390px missing-credential rendering,
  focus/selected navigation/reload and disabled-flow/Stop availability. The later
  real authenticated unchecked-terms sandbox also passed desktop/390px refresh,
  checkbox/nav focus, independent FILE Stop and Cancel; no terms acceptance/key/
  provider creation. Its original source/frozen identities remain separate. Private render:
  `/private/tmp/opencode/garage-chat-ui.ngpUH5/spotify-setup-HNj090`.
  Earlier real FILE-success UI stays attached to original web7851, not relabeled.
* Changed audio ticket/child wrappers passed actual MA/transport/Camilla FILE at
  inputs87b8a2a9 on source-mounted cached imageece60bd7; receiver/WAV seam synthetic.
  `/private/tmp/opencode/garage-audio-audio-next-r1/handoff.md`. Hardware identity,
  exclusivity/bootstrap code is implemented and metadata checked, but unactivated:
  no hardware open, unplug/replug, cutover or sound proof.
* Public source research found the initial MA volume push/three-second grace still
  present in2.10.6/nightly/dev, capable of raising lower Spotify volume. No supported
  fix found; holding output OFF does not prevent the upstream slider write. Canonical
  `/private/tmp/opencode/garage-audio-volume-research/findings.md`. The user now
  approved upstream PR plus the identical local pinned narrow patch;
  no zero-volume ritual, second gesture or connect-only guard shortcut substitutes.

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
| R1 | Partial / revision2 integration active | Legacy MA/image/state/rollback retained. Real isolated DHCP allocation/release verified; renewable carrier/loss/discovery/private management path still needs qualification. User approved upstream PR+same local pinned patch; exact artifacts awaited. Baseline Compose must not overwrite production. Clean-install config/cutover remain M0/M5 gates. |
| R2 | Implemented / isolated topology and IPC verified | Dashboard bridges are project-owned, not external `garage-audio_default`; candidate MA uses service DNS, not the stale LAN override. Authorized 8098 bindings remain. App sees only read-only public IPC (UID1000/GID1003), no native/event/transport/DAC mounts. Real r13 FILE topology and staged target app/public IPC passed. Clean production installation/discovery is not inferred from test topology; no production network edit performed. |
| R3 | Partial / approved upstream patch work | Prior actual FILE replacement proof retains inputs87b8a2a9 and synthetic receiver/WAV label. Current stable/nightly/dev retain upward startup overwrite; user approved focused upstream PR and identical local narrow patch pending release. Upstream owner implements/tests/publishes; audio owner pins/tests identical patch. PR URL/commit/patch SHA and actual Soloist volume/clock remain U1/M0/M5/M6 work. B remains deferred. |
| R4 | Resolved / docs | Historical README/normal-listening/DAC setup now clearly labeled; latest recorded Connect source set `b81fbf71` is not claimed as phone acceptance. Local authority/adapter/runtime SHA256 prefixes match the recorded deployed set: `52fc05aa`, `1c994a24`, `5d800758`. |
| R5 | Resolved / historical checkpoint, new manifests pending | Historical archive221e7a7 and old component identities retained. Revision2 resumes affected writers; discovered Dockerfile/Compose plus recursive module/config/scripts/vendor/docs inputs avoid silent new-asset omission. Required patch/carrier/apt/official-base manifests must reconcile before strict release-input gate/refreeze. No current new candidate is frozen or deployed. |
| R6 | Resolved / test environment | Reused local environment had MCP2.x, causing four FastMCP failures. Retained failures; isolated MCP1.30.0/SDK0.23.1 environment passed affected checks. Do not upgrade project MCP implicitly. |
| R7 | Partial / final enrollment app image built; live app/audio cutover open | Original capacity failure retained; specifically authorized existingLV/ext4 +4GiB completed online to104GiB with observederrors0/no deletion/restart. Final backend216bb79a+UIgen6 appa83582fe source990f8baa:88hashes/importsUID1000, full86-file admission map inclprivate_records;4layers803548B/physicalgrowth5283840B, free4395323392B retains2GiB+256MiBreceiver+512MiBtests. No apt/pip/pull/coldcache/newMA/audio build. MAa7af/audio5c62 original1f3 matchingFILE/quietUMCzero receipts reused at original identities. New app-only enrollment/root bootstrap/assets reconciled for final freeze; actual admittedwebserverpeer/negativeHTTP/render/restart/phone and ordinaryaudio credentials/receivergain/exclusivecutover remain open. Fresh-install apt availability separate; see ma-enrollment-deploy and external candidate receipts. |
| R8 | Implemented / source hardening, bounded evidence | Current Application reads/writes use `private_records` anchored directory FDs, no-follow/private-owner/regular-single-link checks, exclusive temporary creation, file fsync, relative replace and directory fsync; the historical path-based write finding no longer describes this source. App-owner focused storage evidence remains its original checkpoint; staged setup persistence is additional quiet evidence, not comprehensive crash/race certification. Reconcile matching storage receipts at final release. |
| R9 | Partial / bounded setup/render and clean app lifecycle | Original source13e30891/frozen221e7a7 sandbox identities retained; web3d38 unchanged. App lifecycle scopedbdae7860/webf5959ba6 sourcebound fixture passed pending drain/independent FILEStop/exit0/cancelledchat+uncheckedterms restart/abort; stock official MA separately exit0. Dashboard init retained/grace30s with targeted Compose check; prior137/143/failed runner receipts retained, not relabeled. No consent/key/download/provider creation, patchedMA restart, new immutable candidate, physical activation/Safari/phone/sound proof. |
| R10 | Accepted / upstream license | Staged whitespace checking flags original spaces/EOF in `vendor/dsp/licenses/ALSA-LGPL-COPYING`. Preserve the upstream license bytes; project-owned files pass the check with that exact license excluded. |

Historical review-stage evidence (not relabeled as current candidate gates):
all 210 initial repository inputs inventoried/hashed and scanned for
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
