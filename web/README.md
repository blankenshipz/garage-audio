# Garage Speakers frontend

Dependency-free ES modules, local fonts and CSS. Served by the application at `/`
and `/assets/*`. Run isolated adapter/plot/file tests with `npm test` in this folder.
These synthetic tests do not establish connected hardware or live browser acceptance.

## Intended journey

The dashboard now displays **one active stage** at a time. Existing section/form
IDs remain, but integration tests must navigate the matching stage before clicking
controls. Sidebar anchors retain `#connect`, `#check`, `#calibration`, `#improve`,
`#verify`, expose `aria-current="step"` and support Arrow keys/Home/End; Enter/Tab
remain native. Navigation changes no server state and does not recreate forms.
Persistent summary shows current stage, next action, hardware/no-sound boundary
and actual human-readiness limitation. `#journey-cancel-job` cancels the selected
active job from any stage, including while its Measure workspace is hidden.

Measure leads with the selected job when one exists; `#measurement-workspace`
then holds the preserved plan/import UI (default closed after job selection).
Open `#plan-preparation` for the plan form, `#measurement-import` for
imports, `#measurement-comparison` for plots, `#job-request-details` for job
preparation, `#job-authority-details` for native human controls when available,
and `#job-history-details` for history. Check retains diagnostics and advanced
profiles behind actual details disclosures. Raw MCP JSON, brief, manifest,
format/policy bounds, event history, quality checks and per-channel plots default
closed. Copy setup/brief remain prominent in Connect. No hidden-button clicks
count as journey verification; scripts must open these real disclosures.
When revision2 data is returned, the existing job card reads actual
`manifest.native_file_route` and `evidence.native_file_render`: bound version and
branch indexes are distinct from observed child exit, frame counts, peak/RMS,
reported exact-zero isolation and file hashes. Detailed FILE report remains a
closed disclosure; missing evidence never becomes renderer/isolation success.
The visible FILE result headline requires both `exit_status:0` and
`native_child_exit_verified:true` for verified successful child exit; zero
isolation is stated only when explicitly reported. Physical test indices are
not garage commissioning. The details include owned-group reaping and the
native result's 66 dB synthetic virtual-microphone normalization, never
calibration correction. Exact manifest hashes now live in
`#job-manifest-binding`; human-review harnesses should open that disclosure
alongside bounds and canonical manifest before exact-hash review.

The agent workspace adds real `jobs.request/list/get/execute/cancel` file/null
jobs. Additive `camilla_file_available` / `camilla_file_unavailable_reason` status
fields appear as concise FILE readiness copy in the existing activity card. Missing
or nonboolean readiness stays unknown; availability alone is never render/isolation
proof. No legacy/fake FILE fallback is allowed. Real disposable Camilla file-render
evidence remains separate from synthetic capture and physical garage acceptance.

Jobs share state with MCP. Choose an active planned position/channel; prepare a job;
review its server-owned canonical manifest, exact hash, peak/band, signal and total
deadline, file/null output, history, next action and synthetic evidence. Missing
human authority, approved-not-executed, running, cancelled, revoked, expired,
interrupted, failed and completed states remain distinct. Completion is explicitly
synthetic, not garage or acoustic proof. Hardware execution stays disabled.

Normal bootstrap/MCP access is automation and cannot approve. The separate human
card reads actual authenticated `/api/status` fields `human_approval_available`
and `human_approval_unavailable_reason` on every job refresh. It requires explicit
backend `human_approval_available:true` readiness AND an
exact loopback hostname before offering login/re-authentication/approval/revocation.
False/unknown/stale readiness hides credential entry and prevents password requests.
The shared/container dashboard denies human approval entirely, even through a
localhost SSH tunnel: hostname and Docker NAT are not confidentiality proof. Only
a standalone native local file/null server with actual OS-local listener and peer
checks may report availability. No extra human port is provided in this release.
`/api/human/*` uses a separate client CSRF value and HttpOnly
cookie, never the automation CSRF as authority. No verifier enrollment/provisioning
UI exists; production is unprovisioned. Login fields clear on submit, credentials
never enter storage/briefs/logs. A human must review the exact manifest hash and
bounds, be fresh and realm-matched; backend validates authority independently.
Reauthentication/logout invalidate prior grants. Stale readbacks disable approve
and execute; execute rereads exact approval/manifest before sending. Duplicate
actions are single-flight while cancellation remains usable during execution.
Running state is read back every second only within a bounded window (server job
deadline plus ten seconds, maximum sixty); errors/deadline stop auto-refresh and
require manual readback, never synthetic completion. Switching/locking invalidates
late responses. Cancel/revoke stay separate from an execution request in flight.

The copyable agent task brief is only a safe template for the user's own MCP client,
not LLM inference, embedded chat, a verified agent connection or sound consent.
Configuration details are secondary to the copy action, with manual-copy fallback.

The primary navigation is **Connect → Check → Measure → Improve → Verify**.
Connect provides a token-free SSH MCP configuration and clipboard/manual selection
fallback for trusted-LAN HTTP. Copying never claims agent connection. Check reads
published capabilities and separates dashboard authentication from unavailable DSP,
microphone/output discovery and human sound authority. No embedded paid chat exists.

Measure creates `calibration.plan` records through the existing authenticated
operation envelope (only when advertised by capabilities). Select a session and
import REW data with explicit measured/synthetic source, calibration hash/application,
sample rate/orientation and timing provenance. Improve reads the same session state
as MCP, offers analyze/propose/cancel, and displays plan bounds/prerequisites,
normalized magnitude analysis and cut-only electrical proposals. No sound approval,
capture or apply button is part of this workflow. Missing provenance is a real server
error, not synthetic success. Synthetic source badges persist on imports, analyses
and proposals; imported data, electrical predictions and research simulations are
never interchangeable proof. Verify points back to original before/after imports.

Superseded analysis/proposals are never displayed as current: the session warning
and next action require reanalysis then reproposal (cancelled plans stay terminal).
Optional `clock_drift_ppm` import provenance accepts finite −1000..1000 numeric
values; blank is omitted and stays unknown, never zero. Known absolute drift above
100 ppm disables proposing; unknown drift remains explicitly provisional. Proposal
results show aggregate and per-position grid/native-band target-fit predictions,
including the valid no-useful-cut / leave-unchanged outcome. Electrical validation
alone does not demonstrate target fit or acoustic improvement.

Existing output/playback/profile tools remain under **Advanced controls & profile
library**; legacy sessions and REW imports remain supported. The detailed legacy
journey below describes these retained controls, not the primary agent flow.

1. Open the authorized trusted-LAN or private SSH-tunneled URL. The locked dashboard reports unknown state,
   not a pretend muted system. Enter token in the password form; it is exchanged
   for the HttpOnly session and in-memory CSRF token, never browser storage or URL.
2. Read actual status and separate DSP availability, MA authentication/player list,
   and unverified interface/microphone/transport states. When the broker is absent,
   controls are disabled and its real requirements appear in System notes.
3. Use mute/level/sub trim only when DSP readback is available. Every action waits
   for server acknowledgement and a fresh status readback. No unmute, playback
   start, sweep or attended-authority flow exists. Pause is server-target guarded.
4. Inspect/import a strict profile JSON, prepare cut-only electrical edits offline,
   and preview the calculated transfer function in a separately labelled plot.
   Forms load actual selected-channel gain, delay, mute and polarity plus profile
   crossover, sub trim and PEQ bypass. PEQ slots support append/replace/remove.
   Typed `profiles.edit` saves a new offline profile, preserving source provenance;
   optional read-only expert JSON is not required to edit. Delay is never inferred.
   Import or preparation never implies application or acoustic verification.
5. Create a named session with position, channel, orientation, timing reference and
   setup/profile notes. Import real REW response or mic calibration UTF-8 exports
   (256 KiB, 20,000 numeric rows) or cut-only REW filters into a selected profile.
   Original UTF-8 text (including any BOM) is preserved for server-owned provenance.
   Session lists use only ID/name summaries; full measurement data is fetched with
   `sessions.get` for the selected ID, not expected in the list response.
   Mic calibration files never
   enter the measured-acoustic-response comparison selector. Compare actual before
   and after series; metadata differences are surfaced, not silently normalized.
6. Read commissioning checklist and structured prerequisites; lock revokes the
   cookie and clears private plots/readbacks from the current view.

## Contract and known gaps

Bound to root `API_CONTRACT.md`: JSON envelopes, `/api/session`, login/logout,
same-origin cookie credentials and `X-CSRF-Token`, exact operation args.
The initial contract does not report hardware discovery, active profile ID,
sub-trim readback or now-playing/transport state; these are explicitly unverified.
MA browse URL is not reported, so the external browsing link stays hidden rather
than assuming the browser host can reach the remote loopback port. Profile data
is imported, not seeded. Sound-producing playback remains locked. The current
player-list API does not identify the commissioned target; any selected-player
pause still requires server-side target validation. No acoustic delay proposals.
Published play/play_url operations remain safety-locked; their UI prerequisites
are visible, but no sound or URL request is dispatched. Sub trim is explicitly
marked unknown when the backend does not supply an actual readback.

All imported filenames and strings enter `textContent`/DOM properties, not HTML.
No external scripts/fonts, browser storage, credential console output or fake data.
Desktop and phone viewport live verification belongs to the integration lane and
must use the actual service. Phone viewport evidence is not a physical-phone test.
