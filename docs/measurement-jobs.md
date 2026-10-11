# Executable measurement jobs: file/null only

An arbitrary external agent can use MCP to request one position/channel job,
read the exact server-generated manifest, ask the separately authenticated human
to review it, start the approved job, cancel it, and read durable evidence/history.
There is no embedded LLM, paid inference, fake chat or agent-authorized sound.
The dashboard uses the same HTTP state. See [API contract](../API_CONTRACT.md).

## What actually executes

The server generates bounded mono PCM16 excitation into a generated-ID private
job directory: one-second log sweep, peak at most -40 dBFS, .05-second fades,
.25-second pre-silence and .5-second tail. Sub uses 20–200 Hz; mains 20–1000 Hz.
The exact rate, frames, quantized peak and file hash are approved, with a hard
20-second inclusive setup/analysis/shutdown budget. The file/null child reads
this WAV, executes the pinned disposable CamillaDSP FILE route, then applies an
explicitly synthetic LTI fixture (0.8 direct plus 0.1
echo delayed by 24 samples), writes a capture WAV, checks it and estimates a
supported band-limited transfer/IR. This is actual file and child-process work,
**not playback, UMIK capture, calibrated SPL or garage acoustic evidence**.

Format/length/reference hash must match. Capture clipping (<.98 full scale) and
pre-noise (≤-50 dBFS) are checked. Supported estimation is zero-padded FFT
only after usable excitation RMS (1e-6..0.02), response RMS (≥1e-5), bounded RMS
gain ratio (0.02..4), ≥20 dB response/pre-noise SNR and ≥0.5 absolute normalized
shared-reference correlation. Zero, near-zero and uncorrelated noise-only
captures are rejected. These are conservative **shared-reference file/null**
eligibility checks, not a claim of supporting arbitrary delayed acoustic IRs.
The supported transform is
regularized deconvolution, `Y*conj(X)/(abs(X)^2 + 1e-6*max(abs(X)^2))`, restricted
to the approved excitation band, with an inverse FFT. Returned IR samples and
discrete-bin transfer magnitudes are explicitly synthetic band-limited estimates,
not full-band absolute impulse responses. Calibration correction, timing/drift
verification and absolute latency claims are unsupported and remain false.
Failed quality checks do not generate a supported IR; evidence/history retain
the observed failure instead of claiming success.

The dedicated compiler uses mono capture, -6 dB headroom and exactly one isolated
software branch, followed by physical-test mapping2/3/4. Twenty physical FILE
channels plus three software probes expose unrequested branches: every other
branch must be exactly zero. Master -60 and ceiling -20 are fixed. No LR4 stereo/
sub summing, production profile or network control is used. The approved manifest
binds exact config/route/binary/library/version/output bounds. Actual counts,
finite values, silent padding, peaks/RMS/isolation and hashes are verified before
capture analysis. Only the known disposable -66 dB gain is normalized for the
explicit synthetic virtual microphone; raw native electrical levels remain
independently observed. This is not microphone calibration correction.

The fixed root-owned executable is `/opt/garage-audio/dsp/camilladsp`, version
`CamillaDSP 4.1.3 (05e9cfc)`, SHA256
`e69090287d9af87eeb5720d59cf8ebf8cab1ed3adbe24de1dd60b66a47f5385b`.
Fixed root-owned Bookworm library `/opt/garage-audio/dsp/lib/libasound.so.2` has
SHA256 `9dabddc79cc29b4f69058ac7d94ac11e82a4d6d8cc8c9148cb8377705790c7ea`.
Only Camilla/version children receive fixed LD_LIBRARY_PATH, never globally.
Missing/version/hash pin or incompatible rate fails closed, without legacy fake
fallback. Readiness fields `camilla_file_available`/unavailable reason are additive
on capabilities/status/jobs. Native render evidence binds config/input/output
hashes and approved manifest; the API contract lists exact public fields.

Private generated job paths are opened through anchored non-symlink parent
directories. Config/input/log/output/receipts use exclusive no-follow creation;
existing links, FIFOs, special files, nonprivate parents or any execution artifacts
deny admission before writes or spawning. Config/excitation/version originals are
regular single-link private hash-bound files. Camilla output is an exclusively
reserved inherited FD64; config is a sealed memfd. Neither is reopened from a
caller-selected path. Artifact preservation is terminal: request a new job, never
resume or overwrite one. Parent rescue subreaper remains outside the owned PGID;
emergency guardian death/group kill/journal failure cannot release the execution
lease before owned-group descendants are reaped. It never waits for unrelated
children. `parent-reap.json` retains independent proof even if journal writes fail;
normal public jobs include additive `descendant_reaping`.

The adapter **does not exercise the production native DSP/FIFO graph**. It opens only
generated file artifacts, never ALSA, a default output, PipeWire transport, a
speaker or microphone. Native startup mute/-60 master and immutable -20 ceiling
are untouched. There is no production hardware adapter: `hardware` is always
rejected, even after a human fixture approval. A future hardware implementation
requires separately verified isolated FIFO/private DSP graph/channel isolation,
calibrated devices and real attended approval; direct ALSA/competing producers
or fallback devices are not acceptable substitutes.

## Independent human realm

Existing bootstrap authentication is explicitly **automation**, including UI
logins; it cannot enroll, approve or elevate. Human endpoints are absent from
MCP and generic operations. Human identity is passed internally from the trusted
HTTP session, never operation arguments or imported text. Registered identity
contexts must match a current verifier, realm, credential version and server epoch.

An operator must separately, manually provision `GARAGE_HUMAN_AUTH_FILE`; no
deployment auto-provisions it and no enrollment/password-writing endpoint exists.
Its regular file and containing directory must be private and operator-owned,
outside application state and the automation-token directory. The JSON verifier
has exactly `user`, `realm`, `version` (positive integer), `salt` (32–64 lowercase
hex characters), `password_hash` (64 lowercase hex), `iterations` (200000–1000000).
Hash algorithm: PBKDF2-HMAC-SHA256 over the independent human password and decoded
salt. Passwords are never stored or logged by the application; do not reuse the
bootstrap credential. Production credentials were **not** provisioned in this task.

Human login issues a separate HttpOnly SameSite cookie/CSRF, 300-second session
and 60-second fresh-auth interval. Reauthentication revokes the previous session's
authority. Both exact loopback Host/Origin and actual loopback TCP peer are required;
the listening socket must be bound exactly 127.0.0.1. Wildcard/shared/container
listeners always deny, even when accessed via localhost or a collapsed loopback
proxy peer. Channel checks precede human body parsing. `human_approval_available`
and its explicit unavailable reason are returned by capabilities/status/jobs;
UI must not transmit credentials on the shared production container. No 8099/new
listener is introduced. Forwarded headers are ignored. LAN plaintext and ordinary Docker NAT are not proof
of confidentiality/loopback. For future approvals an operator must arrange a
vetted confidential path terminating at the application's real loopback boundary;
publishing a host loopback port or simply asserting an SSH tunnel is insufficient
if the app sees a non-loopback peer. No SSH/router/firewall changes are made here.

Approve binds the exact canonical SHA256 manifest to user/session/version/realm,
server epoch and expiry. Manifest includes original session revision, synthetic
device/calibration/map/timing fields, excitation provenance, all time budgets,
ceiling/startup policy/profile and explicit unverified native-DSP/receipt hashes.
The internal one-use grant is private, never returned to MCP, and durably consumed
before spawn. No bearer grant or presence string is accepted. Input mutation,
cancel, logout, credential/version changes, policy mutation, revocation or restart
invalidates authority. No restart resumes a job or restores an approval.

## Lifecycle and bounded state

Jobs persist `awaiting_human → approved → running → completed/failed`, or terminal
cancel/revoke/expiry/invalidation. Approval and execution are serialized under a
private file lock; one global execution lease admits only one child pipeline.
The guardian inherits the lease descriptor and retains it through worker reaping,
so supervisor death does not release serialization before owned child shutdown.
The application lock is released while children run, so cancel remains callable.
The guardian binds its expected parent before admission, supervises its own
worker, enforces deadline and stops/reaps it on cancellation or parent loss.
An inherited anonymous control-pipe reader binds the actual owner lifetime and
epoch, not just PID. Only the owner retains its writer; EOF denies admission or
stops the worker, including repeated immediate death before guardian startup.
Cancel poisons authority in memory and closes control/signals the owned child
before journal writes. Persistence failure cannot skip stop or revive a grant;
the read projection marks revoked state with `journal_repair_required`, and
explicit journal retry/startup invalidation preserves fail-close behavior.
The server monitor independently verifies context/hash/credential continuity and
records actual exit status. It signals only its own still-live subprocess/group,
never a persisted PID, process name or unrelated service. Child environment is
allowlisted and excludes authentication/secret references.

At most 24 jobs, 256 KiB per record, 400000-byte bounded WAV retrieval and 32 MiB
total job artifacts; the job count reserves room for both excitation and capture.
Existing import/proposal storage limits are unchanged. Original imported responses
and conservative proposals are retained separately; synthetic captures never
silently become measured garage responses. History/progress/next actions come
from actual persisted deterministic state. Errors and failed-run evidence remain
available; no successful physical state is simulated.

## Executable proof

`tests/measurement_jobs.py` exercises actual child completion, cancellation,
crash, timeout, hash/context loss, replay, expiry, credential change, restart,
global serialization, concurrent approve/cancel, clipping and forged identity.
`tests/measurement_job_lifecycle.py` kills an actual supervisor and verifies the
guardian reaps its worker and restart cannot resume. `tests/measurement_signal.py`
checks FFT roundtrip, known synthetic transfer, bounds and quality failures.
`tests/measurement_jobs_http_mcp.py` uses the actual official SDK, authenticated
HTTP, separate isolated verifier/login/CSRF, exact manifest approval, child
execution, shared evidence, resource/prompt, reauth/logout and denials. Test
credentials are isolated fixtures only. These are local executable checks, not
deployed human authority, hardware capture, native DSP or acoustic acceptance.
Set `GARAGE_TEST_RECEIPT_DIR` to a private external test receipt directory to retain
sanitized public job state, manifests, generated WAVs, evidence, guardian exits
and complete child stdout/stderr before fixture cleanup. Internal grants,
passwords and verifier files are explicitly excluded from this retained bundle.
