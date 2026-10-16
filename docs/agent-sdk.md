# Official Agents SDK: build-only integration

Pinned `openai-agents==0.23.1`, Pillow `12.3.0`. Official reference:
https://openai.github.io/openai-agents-python/running_agents/ and
https://openai.github.io/openai-agents-python/sessions/.

**Production inference and vision are disabled unconditionally**, even with ambient
OpenAI credentials. No enable environment variable, API key input, external
photo upload, SDK cloud conversation or trace export is supported in this release.
ChatGPT subscription authorization does not authorize billed application API calls.
Future enablement requires a separately reviewed release, explicit spend/privacy
authorization and secure credential provisioning; no credential discovery is performed.

## Application adapter contract

Construct `application.agent_harness = Store(application.root)` from
`garage_audio.agent_harness`. Merge exported `SCHEMAS` and `OPERATION_SCHEMAS` into
strict shared validation/capabilities. Dispatch
`application.agent_harness.execute(operation, args, application)` under existing
Application thread/process locks; wrap returned raw values in the normal envelope.
Catch `AgentFailure` with existing domain exceptions (`code,message,requirements`).
No new authentication or unauthenticated route: retain HTTP Host/Origin, auth/CSRF
and SSH MCP controls. On process teardown call `close()` for the anchored store.

| Operation | Required | Optional | Result |
|---|---|---|---|
| agent.status | — | — | honest `inference_disabled` capabilities |
| agent.session.create | — | name,room_id | private persistent session |
| agent.session.get | id | — | current session and pending untrusted proposals |
| agent.session.list | — | — | persistent sessions, newest first |
| agent.config.get | — | — | saved provider configuration; always disabled |
| agent.config.set | provider,model,credential_ref,max_turns,timeout_seconds | — | saved configuration; never enables inference |
| agent.run | id,expected_version,message | — | stores local user message; `state:inference_disabled`, `last_result.state:inference_disabled`; **no assistant reply** |
| agent.cancel | id,expected_version | — | cancelled session, proposals cleared |

IDs are generated 32-char hex. Names 1–120 chars, messages 1–8000; stale edits
return `version_conflict`. Private root/agent-sessions JSON has no-follow anchored
directory/file access, atomic fsync+replace, 0600 files; max128 sessions, 128 history
items and 256KiB/record. No originals/photos or secrets are read into SDK tools.

Configuration accepts provider `openai`, model identifier (1–80 alphanumeric/dot/
underscore/hyphen characters), fixed `credential_ref:"application-agent"`, max_turns
1–8 and timeout_seconds1–30. No key, arbitrary URL/path, enable flag or paid fallback.
The server-owned optional reference is `GARAGE_AGENT_TOKEN_FILE`; setup checks only
private-file metadata, and this release never reads its value or contacts a provider.
Runs retain generated ID, disabled state, creation time, `provider_called:false` and
a policy event alongside user-only messages. The UI must render this as a system
status, not an assistant reply. Cancellation is versioned and preserves history.

`setup.status {}` returns `{config,device,credentials,agent,backend,
ordinary_playback_authority,prerequisites,inference_enabled:false,sound_authority:false,
raw_dsp_exposed:false}`. `credentials.music_assistant` and `.agent` contain only
`{reference,available,code}`; missing/invalid credentials and missing/unverified device
are structured prerequisites, never paths or values. Broker reachability does not
prove physical device verification. `setup.config.get {}` / `setup.config.set
{display_name}` persist the app display name (1–80 chars), not hardware/sound grants.
All operations use the existing authenticated HTTP/MCP envelope `{ok,result,error}`.

UI sequence: show disabled-state banner and manual/MCP alternative; session create
and reload are genuine local operations. Do not render canned/model-looking chat
as inference. Manual room annotation review uses `rooms.annotate` then an explicit
user action `rooms.confirm`, profiles use `listening_profiles.create` after review.
Model output, imported notes, or a text claim “user approved” never grants this
confirmation, measurement provenance, DSP application or sound authority.

## SDK-ready seam and isolated tests

`build_agent(application)` builds an actual SDK Agent with a `DisabledModel`, one
typed `function_tool` read boundary, and strict Pydantic `RoomAdvice` output.
Read allowlist: rooms.list/get and listening_profiles.list/get/guide, through the
same Application.execute validation. No generic mutation, confirmation, approval,
shell, network, arbitrary filesystem, photo bytes, device or sound tool exists.
Output supports multiple typed annotation and listening-profile suggestions. Image
coordinates are not distances; pending output is untrusted, never applied. Domain
validation and optimistic versions still apply when a user manually submits edits.

`LocalSession` implements the official SDK Session protocol over the same records.
`_run_isolated_fixture` is a test-only library seam, never operation/config/HTTP/MCP
dispatch. It uses actual Runner with a supplied scripted Model, max4 turns/default
(bounded1–8), timeout5s/default (≤30), cancellation propagation, sanitized failure
states, history rollback on failure, and both global/per-run disabled tracing.
Tests must block outbound sockets and supply scripted Models defined only in tests.
No synthetic fixture is installed as a production model/provider or exposed via UI.
Synthetic outputs are marked `synthetic:true`, `confirmation_required:true`.

This validates SDK orchestration only, **not live LLM or vision acceptance**. Real
SSH MCP, desktop LAN UI, physical-phone and sound/hardware evidence are separate.

The configurable chat integration is `agent_runtime.run_configured`: its immutable
server policy denies **before** credential resolution, provider/client construction,
SDK runner or tools. A future reviewed policy change would use the real pinned
OpenAI Responses provider and `stream_loop`, not a canned responder. Streaming runs
have bounded turns/time, token budget, ≤16 allowlisted calls, ≤512 events/64KiB,
immediate SDK cancellation and disabled tracing; supplied scripted Models test the
actual streaming core. Tools call shared `Application.execute`, preserve strict
schemas/versions and cannot start playback, raise volume, apply DSP, execute sound
jobs, confirm human approval, send photos or invoke shell/network. Stop/pause remain
manual shared operations independent of inference. Quiet room/profile/plan tools
are separately allowlisted; untrusted user/tool strings never change server policy.

Legacy session/profile records now use anchored no-follow private directories/files,
unique exclusive temp files, file+directory fsync and hard-link rejection. Detailed
records are authoritative; exact record inode/mtime/size identity regenerates manifests
after an interrupted record/manifest pair write, regardless of manifest mtime. Normal
lists remain lightweight and never parse original REW point data.
Focused persistence tests prove local behavior only, not deployed restart acceptance.

## Docker public audio boundary

The default `GARAGE_DEPLOYMENT_MODE=docker` uses only the worker's public Unix IPC.
`audio.status {}`, `audio.stop {}` and `audio.pause {}` are shared strict typed
operations; no caller socket/path, grant, Play, Resume, Seek or volume parameter.
The fixed default is `/run/garage-audio/public/control.sock`, optionally configured
server-side with the existing `GARAGE_AUDIO_CONTROL_SOCKET` environment variable.
Linux verifies worker UID1002 with SO_PEERCRED; the worker admits dashboard UID1000.
Dashboard requires supplementary GID1003 and only the public-directory mount:
`audio-public:/run/garage-audio/public:ro`. Never mount the full runtime, event socket,
PipeWire runtime, device nodes, raw DSP, privileged socket or Docker socket into the app.
No MA/backend credential is necessary to invoke these silence-only operations.

Requests are exact newline-delimited `{operation}` objects. Replies are single bounded
16KiB frames, validated and projected to non-secret status fields under a total2s
deadline. Unavailable/invalid replies fail with `audio_unavailable`; rejected worker
identity/operations have structured errors. Raw worker exception text is not returned.
Silence readback does not imply DAC sound verification or exclusive host ownership.

Authenticated+CSRF `/api/speakers/stop` and authenticated `/api/speakers/status` route
through the same shared boundary in Docker mode, independent of the agent/model.
Validated silence-only IPC bypasses the application's persistence/busy lock because
it writes no app state; the real Linux HTTP fixture checks Stop while that lock is held.
Legacy speaker session/setup/action/enable are explicitly unavailable there, never
forwarded to native services. The old broker/controller is selected only by explicit
`GARAGE_DEPLOYMENT_MODE=legacy-native`, not by socket failure. Docker DSP mute maps to
Stop; native DSP apply/volume/sub-trim cannot fall back. `status.audio` reports actual
worker availability; raw DSP remains worker-private/unavailable to the app.

`setup.status.audio` is `{available,readback,error}`. A reachable FILE worker remains
`device.available:false`, `verified:false`, `code:physical_device_unavailable`;
it is software readiness, not hardware qualification. MA credential metadata still
describes optional app-side MA readback setup, not public audio Stop authority.
Candidate deployment, matching UI rerender and real SSH MCP remain separate gates.

## App-owned official Spotify Connect setup

These operations use pinned `music-assistant-client==1.6.0` public config methods
and fixed public flow routes supported by MA2.10.5. They are not a generic MA API.
All return the shared `{ok,result,error}` envelope; all arguments are strict objects.

| Operation | Arguments | Result |
|---|---|---|
| `setup.spotify.status` | `{}` | provider/configured/player_id/player_bound/enabled/runtime_verified:false/official_setup_completed/active_flow |
| `setup.spotify.begin` | `{}` | start new official provider flow; refuses an existing garage provider |
| `setup.spotify.reconfigure` | `{}` | reconfigure the server-bound garage provider without deleting accounts or keys |
| `setup.spotify.flow.get` | `{id}` | current app-owned flow projection; terminal readback retained |
| `setup.spotify.flow.abort` | `{id}` | abort pending owned flow; retain originals and existing provider credentials |
| `setup.spotify.flow.submit` | `{id,step:"backend"}` | selects only `soloist`, not community fallback |
| same | `{id,step:"soloist_terms",accept_terms:boolean}` | explicit download/refresh terms response; never sound approval |
| same | `{id,step:"soloist_api_key",credential_ref:"spotify-soloist"}` | resolve fixed private server key reference, never echo or persist its value |
| same | `{id,step:"soloist_api_key",keep_stored_key:true}` | preserve an existing MA key only when the current public form confirms one exists |
| `setup.spotify.configure` | `{advertised_name}` | after reviewed Soloist setup, bind the fixed player/name and server-required `volume_mode=spotify_authoritative`; no caller-selectable mode |

Flow projection: `{id,type,step,expires_at,fields,requires_human_action,human_handoff,
sound_authority:false,secure_values_returned:false,validation_failed}`. `id` is an
app-generated32-hex ID, not MA's raw flow ID. Reviewed form steps are `backend`,
`soloist_terms`, `soloist_api_key`; progress/finish/abort are status-only. The terms
and key steps also return static reviewed `notice` and fixed official Spotify
documentation/policy/key-page `links`; never MA-supplied URLs, images or secure values.
The terms notice preserves upstream's Premium requirement and warning that using
Soloist through MA may violate Spotify terms. Consent is user-owned and permits
download/run/retain/automatic refresh only; no human sound authority is asserted.
Do not fabricate consent from agent text. Own-agent tools exclude all setup mutations.

Form fields are typed app names, not raw provider entries. Backend offers only
`soloist`; terms checkbox defaults false; key form offers the fixed server reference
and `keep_stored_key.available`. No API key, password, arbitrary URL/path, provider,
player, command, enabled flag, playback, seek, volume, DSP or account-deletion input.
`advertised_name` is1–60 literal ASCII letters/digits/spaces/dot/underscore/hyphen,
starts alphanumeric and cannot contain template braces. Configure sends only public
`connected_players:[fixed_player]` and `publish_name_template:advertised_name`; existing
secure setup data, volume policy, queue, and unrelated providers remain untouched.

Server references: existing `MA_TOKEN_FILE` (provider config read/write + player read scopes),
optional `GARAGE_SOLOIST_API_KEY_FILE` for separately human-owned personal-key handoff.
Neither value/path is returned. Exact fixed player comes from server `GARAGE_AUDIO_PLAYER`
(default `02:00:00:00:21:01`); optional existing provider selection is server
`GARAGE_CONNECT_PROVIDER` or a private app binding. Otherwise only a unique provider
already bound exclusively to that fixed player can be selected. Other-player or
ambiguous bindings fail without mutation. Unreviewed external/secure forms fail closed.

All remote work has a15s deadline; retained owned flows have at most30min interactive
lifetime and32-record bound. Expiry/abort do not revoke daily Spotify credentials or
disable ordinary playback. Reload uses `status.active_flow` then `flow.get`; stale
step submission requests readback instead of blindly advancing. Non-secret attempted
step metadata is journaled before submit so a lost finish response can be reconciled.
Legacy/unproven provider backend requires explicit reviewed reconfigure before the
app changes its binding/name; configured state never proves runtime login or audio.

### Required volume policy and restart response contract

Before configure writes, the exact pinned SDK1.6.0 helper
`ma.config.get_provider_config_entries("spotify_connect", instance_id=server_bound_id)`
must advertise one visible string `volume_mode` entry with an enabled enum option
`spotify_authoritative`, and the stored selected backend must be `soloist`.
The partial save includes only connected_players, publish_name_template and that fixed
volume_mode; independent public get_provider_config readback verifies backend, policy,
player and name. Unsupported/hidden/disabled enums or a non-Soloist backend yield
`required_volume_policy_unavailable` before save; wrong persisted mode/backend yields
`required_volume_policy_mismatch`. There is no silent player_only fallback or new mode,
backend, URL, target or generic config argument. Old MA status/Stop/quiet setup remains
usable; required policy availability is not a requirement to view or cancel setup.

Status adds `backend` (reviewed enum or null), `required_volume_mode`,
`configured_volume_mode` (reviewed enum or null), `volume_policy_available`,
`volume_policy_error` (null or redacted code/message), `volume_policy_configured`,
and `runtime_qualified:false`; existing `runtime_verified:false` and sound_authority:false
remain. A saved policy is not receiver/headroom/phone/device qualification.
`setup.status.spotify_readiness` is `{provider_configured, volume_policy_configured,
runtime_qualified:false, ready:false, code}`. The code distinguishes credential/offline
failure, setup_restart_required, ma_provider_setup_required,
required_volume_policy_unavailable/mismatch, or runtime_qualification_required.

Pinned MA2.10.5 reports a missing owned flow with a specific public code0 error and
exact Unknown-(or-finished)-flow message. Only that verified response terminalizes the
retained local record; offline, timeout, authentication/scope and unrelated code0 errors
do not. No raw upstream ID/error is forwarded. Terminal projection is:
`{id, type:"interrupted", step:"interrupted", expires_at, fields:[],
requires_human_action:false, sound_authority:false, secure_values_returned:false,
restart_required:true, code:"setup_restart_required", reason:"ma_flow_missing", notice}`.
`flow.get` succeeds with this readback; submit/abort of an already interrupted record
returns setup_restart_required. Abort that first discovers missing upstream ownership
returns interrupted, not a fabricated successful MA cancellation.

Status/reload reconciles a cached active step against the public get route. It returns
`active_flow:null`, `last_flow:<interrupted projection>`, `setup_restart_required:true`.
UI should display that retained interrupted notice and offer Start official setup (or
Review existing setup if a provider exists), not terms/key continuation. New begin or
reconfigure reconciles before checking active ownership and creates a new app ID with
fresh reviewed steps; previous consent/key flags are not copied. Once a new flow exists,
setup_restart_required becomes false; its later abort is last_flow:type abort. Stored
enrolled receiver credentials remain untouched; this recovery never completes setup,
grants sound, initiates authentication or disables ordinary playback.

Real isolated stockMA restart→interrupted readback→new Soloist choice→unchecked terms→abort
passed, including offline cached-step preservation and independent FILE Stop. The new
policy enum/configure integration has strict unit/readback and actual SDK/model proof;
live patched-provider qualification awaits the existing upstream/audio-owner candidate,
not a copied upstream patch or a production provider mutation.

Begin/reconfigure/submit/configure require actual public-worker OFF/device-closed/no
owned children and fixed MA source idle; configure also requires that player available.
Stop remains independent of setup's app lock; no sound is initiated.
Public MA wire logging is code-disabled before authentication/secure forms even if
ambient logging is DEBUG. Errors are redacted structured codes: `ma_credential_required`,
`credential_invalid`, `soloist_credential_required`, `ma_authentication_required`,
`ma_scope_required`, `ma_setup_timeout`, target/flow prerequisites and generic
`ma_setup_unavailable`, never raw provider errors or values.

`setup.status.spotify_setup` contains this status envelope alongside existing device
readiness. Missing app MA credential remains a prerequisite, not an invitation to a
new dashboard token ceremony. Unit tests mock MA/socket boundaries explicitly and
prove strict schemas/redaction/no-sound mapping only. Authenticated live provider
setup, UI forms and affected candidate M3 acceptance remain separate unfinished gates.

## Dependency compatibility

The full lock installs and passes `pip check` on Python3.13 (existing
`music-assistant-client==1.6.0` requires Python≥3.13). MCP remains `1.30.0`, not
silently upgraded. SDK0.23.1 supports MCP≥1.19,<3 and Pydantic≥2.12.2,<3; existing
Pydantic2.13.5 is retained. OpenAI3.26.0/httpx2 dependencies coexist with MCP's
existing httpx0.28.1; requirements.lock pins the complete added dependency graph.
Pillow12.3.0 is installed for local safe photo parsing, not provider vision.
