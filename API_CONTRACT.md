# Dashboard / MCP application contract

## Room/photo and listening-profile operations

The additive [room contract](API_CONTRACT_ROOM.md) defines strict `rooms.*` and
`listening_profiles.*` schemas. They use the existing authenticated, same-origin,
CSRF-protected POST `/api/operations` and `{ok,result,error}` envelope, under the
same thread/process Application lock. Optimistic `expected_version` conflicts
return `error.code:"version_conflict"`; domain errors never include private paths.
The existing 1MiB request limit remains; photo base64 is capped at700000 characters.
Photo bytes are read using `rooms.photo_get {id,photo_id}`, never a public GET URL.

Named MCP tools/resources and the quiet REW binding journey are documented in
[agent access](docs/agents.md). The additive source inventory is34tools/5resources/
1prompt; original sound/approval operations are unchanged. Listening plans are
distinct from the existing offline DSP `profiles.*`: no automatic acoustic EQ,
hardware capture, filter application, master-volume or mute mutation occurs.
Confirmed maps and measured imports are required for valid source binding; photos
alone cannot supply response measurements, exact geometry or delay corrections.

Provider/model/vision inference is deliberately disabled for the no-paid-calls
release. Official SDK scripted-model unit fixtures are not live-model acceptance.
See [SDK configuration](docs/agent-sdk.md) for exact pinned version and boundaries.
The same locked operation boundary exposes `agent.status`, `agent.session.create`,
`agent.session.get`, `agent.run` and `agent.cancel` with the SDK document's strict
schemas; MCP uses `garage_operation`, never a separate runner or inference route.
Status/capabilities include honest `result.agent` readiness. `agent.run` stores only
a local user message and disabled-state result, not an assistant response or model
call. Unknown provider/key/model/enable fields fail validation.

## Executable file/null measurement jobs (next journey)

Implemented additive manifest revision2: disposable pinned CamillaDSP4.1.3 FILE route,
one mono excitation → isolated left/right/sub software branch → physical test
index2/3/4. FILE-only diagnostics append three software probes after20 physical
channels (23-channel F32LE output); only the requested software/physical branch
may be nonzero, all other outputs exactlyzero. Native master -60, headroom -6,
ceiling -20; no production profile/service changes or device/FIFO/default opens.
Readiness adds `camilla_file_available` / `camilla_file_unavailable_reason`.
Manifest binds binary/version/hash, disposable config/route hash, formats and
fixed level policy. Evidence adds `native_file_render` with observed counts,
physical/software peaks/RMS/isolation, complete output/config/input hashes and
actual child exit. Hardware remains disabled; human approval still native127
fixture-only and unavailable on shared/container listener. `native_file_route`
contains `binary_version`, `binary_sha256`, `library_sha256`, `config_sha256`,
`route_sha256`, `channel`, `software_index`, `physical_index`,
`physical_channels:20`, `software_probe_channels:3`, `output_channels:23`,
`format:"F32_LE"`, `output_artifact`, min/max output frames, `master_db:-60`,
`output_fd_role:64`, `version_stdout_sha256`, `version_stderr_sha256`,
`headroom_db:-6`, `volume_ceiling_db:-20`, and no-production/no-hardware flags.
The fixed root-owned binary/library are hash/version checked; missing pin or
incompatible48000Hz plan returns `camilla_file_unavailable`, never the old
standalone synthetic renderer. No caller-controlled paths or loader environment.
LD_LIBRARY_PATH is fixed and passed only to owned Camilla/version children.

`evidence.native_file_render` includes `kind`, `source:"synthetic"`,
`measured_acoustically:false`, binary/library/version, input/output frame counts,
`output_channels:23`, `physical_peaks/rms` (20 values), `software_peaks/rms`
(3 values), requested indices, `all_other_outputs_exact_zero`, input/output/config
hashes, exit status, `native_child_exit_verified`, `native_pid` and policy fields.
Overall evidence binds `approved_manifest_sha256`. Explicit synthetic virtual
microphone normalization of known disposable -66 dB gain is recorded as
`synthetic_reference_normalization_db:66`; it is not calibration correction or
acoustic authority. Raw output independently proves actual -60 master/-6 headroom.
Native output is bounded by (approved frames+1024)*23*4 bytes. A9MiB reservation
is required before consume within existing32MiB job cap; operator archive may be
required. There is no eviction, apply, production-profile restore or resume.

Artifact admission walks anchored non-symlink directories; the generated job and
its parent must be private and owned. Fresh artifacts are created exclusively
with O_NOFOLLOW/O_CREAT_EXCL, mode0600. Existing symlinks, hardlinks, special files,
FIFOs or any preexisting execution output/log/receipt are rejected before write
or spawn. Original config/excitation/version evidence must be private single-link
regular files and match hashes. Camilla opens only inherited descriptor64 for
output and a sealed in-memory descriptor for approved config, never an arbitrary
output/config path. No existing job artifacts are truncated or resumed.
Normal job readback adds `descendant_reaping:{owned_pgid,reaped_descendants,
owned_group_children_reaped:true}`. Parent rescue subreaper lives outside the job
group and waits only on that group's adopted children, holding the execution
lease through reaping even after guardian SIGKILL or journal failure. Private
`parent-reap.json` independently retains shutdown proof if job journal is broken;
no stored PID enumerations or unrelated-process waits/kills are used.

Implemented contract: automation POST `/api/operations` adds `jobs.request`
`{session_id,position,channel,adapter?}` (adapter `file-null`, default; `hardware`
always denied), `jobs.get` `{id}`, `jobs.list` `{}`, `jobs.execute` `{id}`,
`jobs.cancel` `{id}`. Named MCP tools `measurement_request`, `measurement_read`,
`measurement_execute`, `measurement_cancel`, `measurement_history`; resource
`garage://measurement-jobs` and prompt `measurement_next_action` (no inference).
All use shared persisted server state and `{ok,result,error}`. Jobs return `id`,
`state`, `manifest`, `manifest_sha256`, `sound_disabled:true`,
`hardware_enabled:false`, `source:"synthetic"`, `next_action`, `progress`,
`approval` (public metadata only, never grant), `authority_consumed`,
`human_authority` (`provisioned`, `enrollment_available:false`,
`bootstrap_role:"automation"`, `fresh_auth_seconds:60`, `session_seconds:300`,
`origin_required`), `evidence?`, `guardian?`, `error?`, `history`.
`progress` is `{stage:<state>,terminal:<boolean>}`; `next_action` is a deterministic
instruction, not generated LLM chat. `jobs.request/get/execute/cancel` return one
full public job; `jobs.list` returns a bounded array (maximum 24 jobs / 32 MiB
artifacts). State changes retain server timestamp/reason history. Poll readback;
execute starts asynchronously and does not hold the application lock while the
child runs. Terminal fields include `owned_child_exit_verified` and
`child_exit_status`; guardian records owned-worker exit/deadline/parent loss.

Separate human endpoints (not MCP/generic operations): `/api/human/login`
`{user,password}`, GET `/api/human/session`, `/api/human/logout` `{}`,
`/api/human/approve` `{id,manifest_sha256}`, `/api/human/revoke` `{id}`. Human
cookie `garage_human`, separate short-lived CSRF/fresh authentication; login and
approval are allowed only on a standalone native file/null service whose listening
socket is bound **exactly 127.0.0.1**, with accepted actual socket local address
and peer both 127.0.0.1 and exact loopback Host/Origin. Wildcard/shared/container
listeners always deny, even with spoofed matching loopback Host/Origin, localhost
access, collapsed proxy peer or accidentally provisioned verifier. Channel is
checked before parsing human POST bodies. No 8099/new production listener is added.
Capabilities/status/jobs expose `human_approval_available` and
`human_approval_unavailable_reason`; jobs also include these in `human_authority`.
Production shared container reports false. UI must not transmit human credentials
when unavailable. Forwarded headers/Docker NAT cannot establish authority.
Bootstrap is always automation and cannot approve, enroll or elevate. No
enrollment endpoint or automatic provisioning; missing independently provisioned
private `GARAGE_HUMAN_AUTH_FILE` is a prerequisite, not fabricated consent.
Verifier must be outside application state and automation credential directory.
`/api/human/reauth` `{user,password}` requires current human cookie+human CSRF,
refreshes a 300-second session/60-second fresh-auth interval and revokes the old
session's authority. Login/reauth/session result is `{user,realm,role:"human",
csrf,expires_at,fresh_until}`. Approve/revoke returns the same full public job
shape. Logout returns `{logged_out:true}` and stops associated active child.
Automation login/session result additionally says `role:"automation"`; its CSRF
cannot approve. Human login is the only pre-auth human POST; every other human
POST requires separate human session+CSRF and exact same Origin/Host. No supplied
role/identity/realm/version/consent/grant fields are accepted.

States: awaiting_human → approved → running → completed/failed; cancellation,
revocation, expiry and restart invalidate authority. Exact canonical manifest
binds one position/channel, session revision, realm/server epoch, synthetic
device/calibration/map/timing, WAV format/frames/hash/peak/band/fades/pre/post,
setup/shutdown and total deadline, immutable ceiling/startup master/profile and
DSP generation/receipt hashes. One-use internal grant is durably consumed before
spawn and never returned to MCP. Session mutations, human logout/credential
changes, cancellation/revocation/restart invalidate it. Jobs execute actual owned
file/null children, with guardian deadline, parent-loss stop and global execution
serialization; hardware remains unconditionally disabled, no audio device opens.
An anonymous lifetime control pipe (writer retained only by owner) plus expected
owner/epoch binds guardian/worker admission; EOF denies/stops even if parent dies
before startup or a numeric PID is reused. Cancellation closes this channel and
signals the owned child **before** journal writes, with an in-memory revocation
tombstone preventing revival if writes fail; restart invalidates all old grants.
`journal_repair_required` may appear on projected revoked state after write failure.
Generated-ID bounded WAV/capture/evidence remain synthetic. Evidence does not
become a measured garage response, calibration correction or clock-quality proof.
The implemented offline job signal is mono PCM16LE at the session rate, one-second
-40 dBFS maximum log sweep, 0.05-second fades, 0.25-second pre/0.5-second post,
20–200 Hz sub / 20–1000 Hz mains. Exact quantized peak/frames/hash are in manifest.
Total hard deadline is 20 seconds inclusive of setup, computation and shutdown
budgets. No real-time playback occurs. Capture is a fixed explicitly synthetic
0.8 direct + 0.1 delayed24-sample file transform. Quality checks: exact WAV length/
format/reference hash, clipping <0.98 full scale, pre-noise ≤-50 dBFS. IR is a
shared-reference file/null-only estimate requiring excitation RMS 1e-6..0.02,
response RMS ≥1e-5, RMS gain ratio 0.02..4, response/pre-noise SNR ≥20 dB and
absolute normalized shared-reference correlation ≥0.5. Zero/near-zero or
uncorrelated noise-only captures are unsupported (`supported_ir:false`). These
checks do not establish microphone calibration or arbitrary room-IR support.
IR is a
zero-padded FFT regularized (1e-6 maximum excitation spectral power) band-limited
transfer estimate; evidence `checks`, `supported_ir`, `ir_kind`,
`ir_first_samples`, `transfer_points`, reference/capture hashes and warnings.
Calibration/timing/drift verification are false. It cannot validate a physical
microphone calibration or clock. Production native DSP/isolated FIFO graph is **not** exercised
by this disposable FILE adapter; future hardware bridge remains unavailable rather than
using direct ALSA/default-device fallbacks. Existing native policy is unchanged.

## Agent measurement workflow v1 (shared HTTP/MCP)
### Speaker output controls / music-first UI (intent 16 candidate)

Intent18 line candidate (`docs/normal-listening.md`): same MA provider domain,
explicit human1/2/4-hour scope and fixed fosi-line-v1 policy, unity master with
one −6dB headroom stage, flat separate stereo OUT3/4 and Fosi primary volume.
Fresh approval requires Fosi fully down; old gain actions/messages are rejected.
Zero/stopped preparation and MA10%
readback before internal confirmation. No auto song. Private typed normal messages
are normal-prepare/normal-heartbeat/normal-confirm; normal broker commands are
normal-open(hours,policy) and normal-state(state) on the same admitted controller
connection. No arbitrary caller role, gain, target, interval or renewal duration.
Independent10s liveness/absolute deadline/idle guardians; Pause muted≤10min, Stop
ends consent, 400ms native fade on user Resume. Root-only fixed-service reference
refresh closes old authority before current PID/namespace admission; no restored
approval. Dashboard status adds mode/transport/ma_volume_percent and verified
normal_listening_available, with saved speaker_control_configured clearly only
configuration metadata. No physical phone, music, SPL or acoustic success implied.

MA-owned attended-check candidate: see `docs/ma-attended-check.md`. A separate private
root-only Unix listener admits only the pinned MA process/namespace/provider assets.
It accepts fixed prepare/heartbeat/confirm/stop schemas, never caller actor/role/gain/
duration or raw DSP commands. MA revalidates real interactive session context; a
second explicit consent ACTION consumes one generation/session-bound native challenge.
Browser pairing cannot authorize this MA-owned window; dashboard/MCP cannot connect
to the private listener. No production approval, new gain or ordinary unlimited
listening is implied by installation or silent preparation.

These are not generic operations/MCP or the old FILE/null `/api/human` realm.
The internal preparation/action protocol remains guarded; UI no longer displays
browser codes or presents it as normal playback. Status includes `music` saved
configuration projection and `human_bridge_available:false` until a reviewed MA
human-session adapter is connected. No automatic role promotion. Player inventory
is monitored independently of finite DAC output; saved Spotify configuration is
not runtime authorization/music delivery proof. Native Stop/expiry closes output
and capture but leaves the independent project Squeezelite/PipeWire transport.
Preparation stops current transport audio (queue retained), requires stopped-mode
readback before capture, and never starts a song automatically.
Existing dashboard session remains `role:automation`. Same exact Host/Origin and
ordinary dashboard CSRF are required on speaker POSTs. All request bodies are
empty objects except Enable `{action}`. No role, duration, gain or output fields.

Explicit `GARAGE_TRUSTED_LAN_AUTHORITY` enables no-lock ordinary access only for
one accepted private LAN authority (never localhost, wildcard or public address).
GET `/api/session` automatically issues ordinary automation-level HttpOnly
SameSiteStrict cookie/CSRF for that authority, not human listening authority.
Other authorities/default mode still require token login. LAN-issued sessions
cannot cross authorities. Up to128 live sessions/60new LAN admissions per minute;
expired sessions prune without evicting existing users. Ordinary data is available
to LAN users by explicit choice. POSTs still require exact Origin/sessionCSRF;
Host/Origin/cross-site Fetch Metadata and body bounds apply to all routes.
No `/api/phone` unlock/enrollment endpoints; no automatic Setup/pairing/sound.

* POST `/api/speakers/session`: create/resume separate native possession session;
  HttpOnly `garage_speakers_v1`, SameSite=Strict, Path=/api/speakers, 600s. Returns
  separate CSRF/public pending browser code, never the cookie in JSON. Duplicate
  cookies/headers fail closed; cookie scope does not isolate other same-IP ports.
* GET `/api/speakers/status`: live controller phase/generation, fixed OUT3/4,
  −60dB, seconds remaining, observed real Slimproto registration, paired/code
  metadata. No password/account/provider state or authority values are returned.
* POST `/api/speakers/setup`: separate possession/CSRF; administrative fixed
  zero-output/muted setup of the real native player, never a sound grant.
* POST `/api/speakers/action`: paired browser only, separate CSRF and actually
  prepared/registered generation; fresh one-use 30s Enable action.
* POST `/api/speakers/enable`: paired possession, separate CSRF, exact one-use
  action and prepared generation; fixed native −60dB/120s stereo window only.
* POST `/api/speakers/stop`: authenticated automation may reduce authority;
  bypasses long setup/DSP locks; reports `stopping` until actual exit verified.

Pairing exists only on separate root-only native administration IPC, not HTTP.
Only a genuinely user-returned public pending code permits root pairing; pairing
alone grants no sound. Agents cannot pair their own production browser or Enable.
The accepted trusted-home-LAN HTTP threat model does not defend against LAN
interception/modification. No TLS, password or event.isTrusted authority claim.
Root-controller restart invalidates every pairing/action and never resumes.
Ordinary `/api/logout` revokes its own associated speaker possession/actions and
stops that browser's owned window before clearing both cookies. The narrow
speaker cookie is not sent on the logout path, so the server associates it during
speaker session create/resume. Revocation failure rejects logout and preserves
the ordinary session/Stop; other-browser logout does not stop another owner.
Setup is rejected while any native shutdown is in progress or unverified.
`phone-start` admits an independent native pidfd/deadline guardian before producer
startup; bounded stable-zero readiness polling precedes `phone-prepare` without
resetting the early deadline or granting sound.
See `docs/phone-speaker-controls.md` for native guardian and installation scope.
This section describes a candidate; deployed/device acceptance is recorded
separately, never inferred from local tests.


Algorithm, limitations and executable checks: [measurement workflow](docs/measurement-workflow.md).

New operations use the existing authenticated POST `/api/operations` envelope;
no new unauthenticated route. `calibration.plan` `{name,positions?,channels?,sample_rate?,orientation?,target?}`
creates a persisted session and returns its full record. Defaults: positions
`["seat"]`, channels `["left","right","sub"]`, 48000 Hz, orientation `90`,
target `server-umik-umc` (alternative `laptop-rew`). `calibration.get` `{id}`
reads that session. `calibration.analyze` `{id}` analyzes imported responses;
`calibration.propose` `{id}` stores an offline cut-only proposal and electrical
validation; `calibration.cancel` `{id}` terminally cancels workflow. Import with
the existing `measurements.import` operation and the returned session `id`.
Explicit named MCP tools: `calibration_create_plan`, `calibration_readback`,
`calibration_analyze`, `calibration_propose`, `calibration_cancel`,
`calibration_import_response` (same import schema).

Workflow records include `workflow_version:1`, `state` (planned/imported/analyzed/
proposed/cancelled), `plan`, `analysis?`, `proposal?`, and `sound_disabled:true`.
No hardware approval, microphone capture, play, sweep execution or apply route is
implemented here. The separate file/null job workflow above is synthetic only.
The plan is a bounded offline specification, not proof of connected hardware.
Response metadata additionally supports `source` (`measured`/`synthetic`),
`calibration_sha256`, `calibration_applied`, `timing_quality`
(`unknown`/`unverified`/`verified`), `clock_drift_ppm`, `microphone_serial`.
Workflow analysis requires explicit source, matching sample rate/orientation,
calibration hash/applied status, named channel/position and timing reference/
quality. Unknown timing or absent drift remains magnitude-only: no inferred
phase, delay, crossover, clock synchrony or acoustic success. Synthetic labeling
propagates through analysis/proposal; originals remain retained.

Analysis `analysis_revision:2` adds `timing.clock_drift_ppm_by_file` and
`timing.quality_by_file`, and `proposal_eligibility` with `eligible`,
`maximum_known_absolute_drift_ppm:100`, `high_drift_file_ids`,
`compensation_supported:false`. Known absolute drift >100 ppm blocks proposal
with `clock_quality_required`; analysis/originals remain available. This is a
conservative policy, not independent synchronization proof. Unknown drift remains
provisional magnitude-only with persistent warning. No compensation provenance
is accepted or assumed. Old persisted analyses require fresh analyze before
propose (`analysis_required`). UI should render analysis/proposal warnings and
eligibility honestly, rather than treating unknown timing as verified.

Freshness applies to **all** session readbacks (`calibration.get`, `sessions.get`,
`sessions.list`) and mutation responses, not just recomputation. Proposals also
carry `analysis_revision:2`. A stale analysis OR stale proposal causes both
derived payloads to be omitted, `analysis_available:false`,
`proposal_available:false`, `derived_results_superseded:true`, and effective
state `imported` (or `planned` if there are no imports). Cancelled stays cancelled.
The projection adds `derived_results_warning`, `derived_results_requirements`,
and `recompute_required:true` except for cancelled sessions. Next action is
`calibration.analyze` then `calibration.propose`. Original imports and private
stored historical results are retained, but old `validation_passed` fields are
never returned as current. Fresh results set `derived_results_superseded:false`
and `recompute_required:false`. Legacy non-workflow sessions remain compatible.
Lightweight manifests carry `projection_version:1`, `analysis_revision` and
`proposal_analysis_revision` (null when superseded). Older manifests are refreshed
once using existing bounded private session retrieval, maximum 128 sessions /
4 MiB per record / existing 32 MiB aggregate storage; no unbounded migration.
Status does not parse session curves/manifests or advertise per-session derivative
availability. It stays `calibration.state:"unverified"` and adds current
`analysis_revision` / `derived_results_validity` describing revision-gated reads.

All five workflow operations return `{ok:true,result:<full session>,error:null}`.
The session has `id`, `name`, `metadata`, `created_at`, `files`, plus workflow
fields above. Import returns only the newly appended file (existing compatibility),
with `id`, `filename`, `format`, `points`, `original_text`, `sha256`, `metadata`,
`interpretation`, `headers`, `calibration_applied` (parser does not apply mic EQ).
Metadata's `calibration_applied` records the source-export claim separately.
List manifests have `analysis_available`/`proposal_available` flags but omit those
large derived curves; `calibration.get` gives full details. UI should show source
label from `analysis.source`/`proposal.source` and each file's `metadata.source`;
missing file source is unverified, not measured. `analysis.channels[channel]`
contains `points` with frequency_hz/db/minimum_db/maximum_db/spread_db,
`normalization_offsets_db`, `file_ids`, `positions`, `position_curves` (per-file
`file_id`, `position`, normalized log-grid `points` and native-band `import_points`
plus band endpoints). `proposal.channels[channel]`
contains `peq` (freq/q/gain), `electrical_points`, `maximum_electrical_db`,
`validation_passed`, `predicted_normalized_points`, `useful_cut_available`,
`selection_message`, `skipped_candidates`, `target_fit`. `target_fit` includes
`target_db:0`, `before`/`after` with `aggregate_rms_db` and per-file `positions`
(`file_id`, `position`, `grid_rms_db`, `import_rms_db`),
`nonworsening_each_position:true`, `aggregate_improved`. Width-aware bounded cuts
are accepted only if predicted aggregate RMS improves and no position worsens
on either grid or native-band samples. Zero cuts are an honest valid outcome,
not proof of acoustic improvement. Proposal `applied` and
`verified_acoustically` are always false. Prediction and electrical curves
must never be rendered as measured after-results.

Minimal fixture sequence (explicitly **synthetic**, not real measurement):
1. `calibration.plan` `{name:"Synthetic demo",channels:["left"]}` → session `id`.
2. `measurements.import` `{session_id:id,filename:"synthetic.txt",format:"rew-response",
   text:"20 70\n40 70\n80 76\n100 78\n120 76\n200 70\n500 70\n1000 70",
   metadata:{source:"synthetic",channel:"left",position:"seat",sample_rate:48000,
   orientation:"90",calibration_sha256:"<64 lowercase hex fixture hash>",
   calibration_applied:true,timing_reference:"unknown synthetic fixture",timing_quality:"unknown"}}`.
3. `calibration.analyze` `{id}` → state analyzed and source synthetic.
4. `calibration.propose` `{id}` → state proposed, offline bounded cuts.
5. `calibration.get` `{id}` → persisted identical readback.

Unmatched/missing/duplicate positions or incomplete provenance return
`measurement_quality_required`; propose before current analysis returns
`analysis_required`. Cancel is terminal (`workflow_cancelled` for mutations),
but get/readback remains allowed. New imports clear previous analysis/proposal,
requiring fresh analysis. No supplied path, sound approval or execution argument
is accepted. Plan limits: 1–8 unique named positions, 1–3 unique channels;
sample rates 44100/48000/96000 and orientation 0/90 only. Legacy metadata can
retain other supported rates; versioned workflow only uses these rates.

The native application defaults to loopback8098. Container publication is restricted
to loopback and explicitly authorized private LAN IPv4 `192.168.68.74:8098` for
trusted home-Wi-Fi phone access; no wildcard/public bind. LAN HTTP is not encrypted.
`GARAGE_APP_TOKEN_FILE` refers to a private mode-0600 bootstrap token file. GET
`/` serves `web/index.html`; static files under `/assets/` are restricted to `web/`.
POST `/api/login` with JSON `{token}` exchanges the user-entered token for an
HttpOnly SameSite=Strict session cookie and returns `{ok:true,result:{csrf}}`.
Tokens must never be placed in URLs. GET `/api/session` returns the session CSRF
token. POST `/api/logout` revokes the session. POSTs require exact same-origin
Origin and `X-CSRF-Token` (except login). Host must exactly match configured
`localhost:8098`, `127.0.0.1:8098`, or authorized `192.168.68.74:8098`;
no wildcard authorities/CORS. JSON only; request body maximum 1 MiB.

GET `/api/status` requires session authentication. POST `/api/operations` accepts
`{operation,args}`. All responses use `{ok,result,error}`; error is null on
success or `{code,message,requirements:[...]}` on failure. No exception/credential
strings are returned. An unavailable broker is not simulated hardware success.

Operation names and arguments (unknown fields rejected):

* `status`, `capabilities`, `sessions.list`, `profiles.list`: `{}`
* `sessions.create`: `{name,metadata?}`; metadata fields: room, position, channel,
  profile, sample_rate, orientation, timing_reference, notes. Returns a session.
* `sessions.get`: `{id}`
  Detailed retrieval is bounded to 4 MiB per persisted record. `sessions.list`
  uses lightweight manifests: file metadata/hash/point_count, never original text
  or point arrays. Status counts files without parsing measurement records.
* `measurements.import`: `{session_id,filename,format,text,metadata?}`;
  format = `rew-response` or `mic-calibration`. Plain UTF-8 numeric frequency,
  dB, optional phase rows; REW comments/header lines accepted. No MDAT or arbitrary
  path access. Original bytes and SHA256 provenance retained. Returns file record
  including points `{frequency_hz,db,phase_deg?}`. Text limit 256 KiB / 20000 rows.
  UTF-8 BOM is preserved in original text/hash but removed from the parsing view.
  Comments begin `*`, `#`, `;`, `//`; recognized REW frequency/calibration and mic
  sensitivity headers are accepted. Unknown nonnumeric lines and nonfinite data
  in any column are rejected, not silently skipped.
* `measurements.compare`: `{session_id,before_id,after_id}`; measured curves only,
  no inferred correction or delay. Returns both original point series.
* `profiles.save`: `{profile}`; existing strict left/right/sub profile schema,
  gains and EQ cut-only. Stores offline; does not apply hardware.
* `profiles.get`, `profiles.preview`, `profiles.apply`: `{id}`; preview is only
  electrical magnitude response, never predicted/measured acoustic response.
* `profiles.import_rew`: `{id,channel,text}`; existing strict cut-only ON PK
  importer; produces a newly stored offline profile.
* `profiles.edit`: `{id,channel?,gain?,mute?,delay_ms?,invert?,peq?,crossover_hz?,sub_trim?,peq_bypass?}`;
  creates a NEW offline profile, preserving source. Channel is left/right/sub and
  required only when channel fields are supplied. gain -60..0 dB, mute/invert
  booleans, delay_ms 0..50 (explicit value, never inferred), peq maximum ten
  `{freq:20..20000,q:0.1..20,gain:-20..0}` cuts; crossover_hz 40..160,
  sub_trim -30..0, peq_bypass boolean. At least one edit required. Apply separately
  via `profiles.apply`, which mutes first and never restores unmute.
  Profiles.list uses lightweight manifests without original REW source text.
  Aggregate record/manifest storage is capped at 32 MiB, each detailed record at
  4 MiB, sessions/profiles at 128 each, imports at 32 per session. Limit violations
  return `storage_limit` with archive/new-session guidance; no silent truncation,
  eviction or deletion. A comparison returns only the two selected file records
  from a bounded session, never unbounded histories.
* `dsp.mute`: `{}`; `dsp.volume`: `{db}` range -90..-20;
  `dsp.sub_trim`: `{db}` range -30..0. These forward only narrow broker operations.
* `playback.status`: `{}`; `playback.queue`: `{player_id}`; official MA adapter,
  operator-provided private `MA_TOKEN_FILE`; missing credential is actionable.
* `playback.pause`, `playback.stop`: `{player_id}`; official adapter, only the
  operator-configured `GARAGE_MA_PLAYER_ID` target is allowed.
* `playback.play`: `{player_id}`; request official MA queue play.
* `playback.play_url`: `{player_id,url}`; URL is bounded HTTP(S) without embedded
  credentials, fragment or whitespace. Future authorized execution must use
  official MA `play_media(..., option=QueueOption.REPLACE)`, NOT fetch the URL in
  this application. No network fetch or playback occurs in the current locked
  implementation. This is not SSRF authorization for future implementations.
* `playback.play`, `playback.play_url`, `playback.next`, `playback.clear`, `dsp.unmute`, `commissioning.start`:
   unavailable: separate trusted attended hardware authority is not implemented;
   file/null job approval never grants it. Agent
  supplied presence strings cannot grant it. `calibration.guide` returns steps.

MCP stdio uses the pinned official Python SDK. It authenticates to this same HTTP
service through the configured bootstrap credential reference, not a second state
store. Tools expose the same operations; resources expose status/capabilities.
All state mutations are serialized under a process-independent file lock; storage
uses generated IDs and atomic private files. No supplied URLs, shell commands,
raw DSP endpoints, paths or consent attestations are accepted.

Hardware volume/sub/profile operations explicitly mute through the broker first;
failure to mute aborts the mutation. They never send restore-unmute authority.
Comparison accepts only two rew-response records belonging to the same session;
timing metadata alone never establishes delay. `GARAGE_APP_AUTHORITIES` is a
comma-separated exact host:port allowlist: loopback and the explicitly authorized
private LAN IPv4 address above. Other hosts/origins are rejected. Origin must equal
the actual request Host, including login. `GARAGE_APP_BIND=0.0.0.0` is only for the
isolated container with explicit host loopback/private-LAN publication; native
default remains 127.0.0.1. `MA_URL` accepts only fixed operator
endpoints http://127.0.0.1:8095 or http://music-assistant:8095. HTTP and MCP each
need an independently accessible private owned non-symlink token reference.
`capabilities.operations[*].input_schema` provides exact JSON schemas with
`additionalProperties:false`, required fields, enums and bounds. MCP exposes
explicit typed `playback_play`, `playback_play_url`, `profile_edit` tools alongside
`garage_operation`, `garage_status`, `garage_capabilities`, plus calibration/job
tools above (17 tools total), three resources and one next-action prompt.
MCP validates raw wire arguments against the published schemas before SDK
conversion: additional fields, numeric strings, booleans in numeric fields and
extra PEQ keys return structured `invalid_arguments`, not coerced/discarded data.
Unicode login credentials and CSRF values return bounded 401/403 JSON errors;
generated credentials are ASCII only.
Current sound tools always return `attended_authority_required`: enabling them
requires implementing independent trusted human approval, bounded duration and
revocation, then rechecking operator credential, commissioned allowlisted target
and hardware safety. No caller presence string substitutes for those checks.
Full pinned dependencies require Python 3.13 or later because the preserved
music-assistant-client 1.6.0 requires it. MCP SDK is pinned at 1.30.0.
# Additive offline commissioning-proof operations (v1, source only)

The shared strict operation boundary adds three operations, automatically exposed
by generic MCP `garage_operation`; dedicated MCP tool count is unchanged.
No deployment, UI, sound, human authority or device/capture change is implied.

- `commissioning_proof.freeze {spec}` creates an immutable prospective protocol.
- `commissioning_proof.analyze {id,pairs}` finalizes one immutable receipt.
- `commissioning_proof.get {id}` returns current readback with stale-result guards.

All IDs are generated 32-hex store IDs, not paths. Unknown fields are rejected.
`spec` requires: `listening_profile_id`, `baseline_profile_id`,
`guided_profile_id`, `rollback_profile_id`, `proposal_session_id`, `fit_sources`
(1–24 `{session_id,file_id}` references), distinct `baseline_session_id` and
`guided_session_id` (empty legacy sessions), `channels` (unique 1–4 of
left/right/sub/combined), strictly increasing `grid_hz` (2–256 unique values,
20–20000), `target_db`, `reference_offset_db` (finite -150…150), `chain`,
`minimum_improvement_db`, `maximum_worsening_db`, `maximum_level_change_db`
(0…20), `minimum_noise_margin_db` (0…80), `capture_budget` (integer 1…128).
The budget covers exact fit-source count plus both condition slot counts; extra
condition imports are rejected, not ignored. It cannot verify external captures.

`chain` requires `microphone_serial`, `calibration_sha256` (64-hex), `sample_rate`
(44100/48000/88200/96000/176400/192000), `orientation` (0/90 strings),
`setup_id`, finite `hardware_gain_db` (-120…120), `transport_normalization:"off"`,
`timing_reference`. All other text is nonempty and at most 120 characters.
This source-chain statement is not calibrated SPL or verified-clock proof.

`pairs` has at most32 entries, exactly one for each frozen verify-position/channel:
`{position,channel,baseline:{session_id,file_id},guided:{session_id,file_id},
baseline_qc,guided_qc}`. Each QC object requires `provenance`
(`operator-export-attestation`/`unknown`), `clipped` (boolean/`unknown`),
`noise_margin_db` (-100…150), `level_chain_known` (boolean), and exact `chain`.
An unknown QC value is not a pass. Timing quality remains import provenance;
unknown timing is permitted for magnitude-only comparison, never phase inference.

Success readback includes protocol/receipt digests, fixed spec/positions,
`required_capture_count`, `stale`, `report_available`, and report or null.
Reports include per-slot fixed-reference RMS/peak/variation/bin-worsening,
level-change/attenuation/curves, aggregate improvement, QC issues,
`combined_system_imports_complete`, and recommendation keep/reject/inconclusive.
All readbacks retain `verified_acoustically:false`, `applied:false`,
`sound_disabled:true`. `rollback_reference` is metadata; `rollback_performed:false`.
Changed references hide stale reports but preserve persisted receipts. Re-finalize
with changed pairs returns `proof_immutable`; stale references `proof_stale`;
early imports `protocol_order`; missing pairs `pairs_incomplete`; extra captures
`budget_exceeded`; chain mismatch `source_chain_mismatch`; incomplete coverage
`band_missing`; invalid/unverified/synthetic/reused imports `source_invalid`.
Existing strict argument/storage/not-found errors remain applicable.

Fixtures may exercise self-reported measured paths but never establish actual
measurement. Independent physical capture, attended apply, calibrated level and
real 2.1 benefit require separate evidence. See measurement-workflow.md for order.
# Additive quiet-journey bridges (source update)

`candidates.save {baseline_id,proposal_session_id,name}`: all IDs 32-hex, name
nonempty <=120 characters. Requires current supported validated cut-only proposal
and an explicitly selected non-derived baseline with PEQ bypass off. Saves a new
offline profile with existing + proposed filters under the existing ten-per-channel
cap. Copies baseline crossover/delay/polarity/channel gain; compiler -6dB headroom
is not subtracted twice. Returns candidate provenance hashes, source kind,
`candidate_stale:false`, `applied:false`, `verified_acoustically:false`. Profile
list/get revalidate baseline/proposal snapshots and mark stale candidates.
Bad/stale/unsupported sources return `candidate_source_invalid`; profile/schema/
filter caps retain strict validation errors. No provider or sound operation occurs.
Synthetic proposals retain synthetic provenance, never become measured truth.

`commissioning_proof.list {listening_profile_id}`: profile-scoped bounded saved
protocol summaries sorted newest first, with id, frozen_at, protocol hash, current
stale/report-available state and recommendation or null. Readback retains all
existing freeze/fit/provenance/QC/coverage/immutable constraints; no stale candidate
or expired proposal can acquire a current verification report. Generic MCP exposes
both bridges through garage_operation; no additional dedicated tools are added.

The frontend forms are deterministic guidance, not live chat or AI inference.
QC/export/chain inputs are explicit self-reported declarations; no checkbox or
form can independently authenticate hardware/capture/human presence. Existing
SDK0.23.1 provider-disabled state and authenticated/same-origin/CSRF boundaries
remain unchanged. Updates require separately frozen/deployed acceptance.
