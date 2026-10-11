# Private dashboard deployment

This dashboard is a separate service, not a replacement for Music Assistant (MA).
No paid inference or public publishing is required. Existing MA Compose and its
staging workflow remain unchanged. Do not use `scripts/stage` for dashboard-only
updates: it intentionally stops MA for a backup.

## Release preparation

Entrypoints are `garage_audio.webserver` and `garage_audio.mcp_server`;
`GARAGE_APP_STATE`, `GARAGE_APP_TOKEN_FILE` and `GARAGE_APP_URL` match current code.
`GARAGE_APP_BIND=0.0.0.0` enables the explicitly supported container bind option;
published access is restricted to loopback and the explicitly authorized private
LAN IPv4 address `192.168.68.74`, both port8098. Accepted Host/Origin authorities
must exactly match `127.0.0.1:8098`, `localhost:8098`, or `192.168.68.74:8098`. The
MA adapter honors `MA_URL` for service-network access. Do not pass
`--contract-confirmed` until final app tests/review, exact LAN authority support,
and MCP dependency lock complete. Never substitute wildcard CORS/hosts or bind the
published port to `0.0.0.0`/IPv6. No firewall/router/SSH changes are needed or authorized.
Verified linux/amd64 base image selection: `python:3.13-slim-bookworm@sha256:f040863673aea2570c3ff6a5c3fb4c673a016cbc5375005ad145915922b6b78a`
(manifest version 3.13.16). Python >=3.13 is required by preserved MA client 1.6.0;
do not use the earlier preparatory Python 3.12 selection.

1. Pause all package-input writers and reconcile required docs/tests.
2. Run `python3 scripts/dashboard-stage --freeze --writers-frozen`. This includes uncommitted app
   files, required documentation/tests and `research/` without committing them;
   returns a private receipt directory and archive
   digest. The archive and inventory are immutable candidate inputs. Changes to
   working files afterward do not change that archive and require a new freeze.
3. Retain full test/build stdout, stderr and exit status outside the release against
   that digest. Do not write gate narration into frozen package inputs.
4. Only after contract confirmation and writer freeze, run:

   ```sh
   python3 scripts/dashboard-stage --deploy RECEIPT_DIRECTORY \
     --expected-digest SHA256 --base-image python@sha256:VERIFIED_DIGEST \
     --writers-frozen --contract-confirmed
   ```

The script builds before activating; changes only the dedicated dashboard service;
never restarts MA/scanner or installs native audio. Build/activation receipts stay
in the private receipt directory. Base image must be digest-pinned; built image ID
and candidate digest are recorded. Release paths are content-addressed under
`/home/zach/garage-audio/dashboard-releases/`.

## Access and state

```sh
ssh -N -L 8098:127.0.0.1:8098 zach@192.168.68.74
sh scripts/dashboard-status
```

For a phone on the same trusted home Wi-Fi, open **`http://192.168.68.74:8098`**;
no phone SSH tunnel is required. Local tunneled access remains
`http://127.0.0.1:8098`, and MCP still uses its loopback HTTP route inside the container.
LAN HTTP is **not encrypted**: tokens/cookies can be observed by a malicious LAN
participant. Use only the trusted home LAN; prefer the SSH tunnel where practical.
Do not forward this port publicly or use untrusted/guest Wi-Fi.

Explicit user-authorized trusted-LAN mode is selected by
`GARAGE_TRUSTED_LAN_AUTHORITY=192.168.68.74:8098` in this project's Compose.
Only that exact accepted private LAN authority auto-provisions an ordinary
automation-level HttpOnly session on GET `/api/session`. The UI has no lock,
token, phone-access request or dashboard pairing ceremony. Anyone on the home LAN
can access ordinary room/photo/control data. Mutations still require session CSRF
and exact Origin/Host; cross-site Fetch Metadata is denied. Admission is capped at
128 live sessions and 60 new LAN sessions/minute without evicting existing users.
This does not auto-pair, start Setup or grant sound. Native playback pairing and
explicit Enable remain separate; see `phone-speaker-controls.md`.
Default configuration and accepted localhost/loopback authorities retain token
authentication. LAN-issued sessions are rejected on other authorities. Private
token files remain untouched for supported MCP; token login is retained below.

Authentication must use the app's confirmed login
workflow, not a token in a URL. The server generates a private 0600 token file at
`/home/zach/garage-audio/state/dashboard/auth/dashboard-token`; this is a credential
reference, never a value to paste into journals, logs, source or agent prompts.
Human browser credential entry requires a secure user-owned handoff; an automation
agent must not print the token to obtain it. Authorized Playwright verification can
read the credential entirely inside its subprocess and submit login without logging
the credential or request body. The app accepts POST `/api/login` JSON
`{token}` and issues an HttpOnly SameSite=Strict cookie. Subsequent POST operations
require exact same-origin Origin and `X-CSRF-Token`; GET `/api/session` returns CSRF.
The configured host/port must match the exact LAN or tunnel browser URL. Use secure browser
credential injection if supported, otherwise the user enters their private token.

On your own Mac terminal, copy the existing private token directly to clipboard
without printing it to terminal/chat/logs (not an agent command to expose a value):

```sh
ssh -T -o BatchMode=yes zach@192.168.68.74 \
  'head -n 1 /home/zach/garage-audio/state/dashboard/auth/dashboard-token' | pbcopy
```

Paste into the dashboard password field using your secure user-owned device handoff.
Do not put it in a URL or send it in chat. Clear clipboard after use with
`pbcopy < /dev/null`; avoid clipboard-history tools. Login Origin, session cookie,
CSRF token, audio policy and agent consent restrictions are unchanged by LAN access.

Dashboard persistent files live in `state/dashboard/data`; auth is mounted read-only.
The non-root container has no devices, Docker socket, raw DSP access or capabilities;
its root filesystem is read-only. MA connectivity uses external existing
`garage-audio_default` network and `http://music-assistant:8095`. Onboarding/MA token
remain user-owned; no credentials are discovered automatically.

## Rollback and proof

`dashboard-current` and `dashboard-previous` refer to releases; each successful
release records `image-id`. After checking any app-state migration compatibility,
run Compose from the previous directory with `DASHBOARD_IMAGE` set to its recorded
image ID and `up -d --no-deps dashboard`. Never use `down --volumes` or host prune.
Releases share persistent app state; a source rollback is not a database restore.
No automatic state restore is implemented.

The stage helper replaces each pointer symlink atomically rather than copying
through a symlink-to-directory, and preserves the prior pointer on a same-release
retry. This is not one atomic transaction across container activation and both
pointers: verify the running image and explicit release path before rollback.
Older releases may have a stale `dashboard-previous`; never infer the rollback
target from that pointer without checking it and its `image-id`.

## Agent-first screen sequence

Unlock → **Connect** (copy SSH MCP config; connection is not inferred) → **Check**
(reported readiness and missing hardware) → **Measure** (save a quiet plan, or
select an existing session; import text with analysis provenance) → **Improve**
(Analyze imports, Prepare offline proposal) → **Verify** (inspect source labels,
original acoustic imports versus electrical predictions). Synthetic fixtures must
select the synthetic source before import and stay labeled through proposal.
The configuration copy button may select text instead of clipboard-copy on plain
HTTP; use the device's Copy action. Advanced profile controls remain secondary.

No production hardware capture/executor, hardware sound approval or proposal application is available. Default
server-attached UMIK/UMC planning and laptop REW imports are specifications/import
paths, not verified hardware capture. Source workflow details and exact named MCP
tools are in [agents.md](agents.md), [measurement-workflow.md](measurement-workflow.md)
and [API_CONTRACT.md](../API_CONTRACT.md). Obtain explicit attended sound authority
only after hardware identity/calibration/output mapping and safe capture support
are independently verified; this deployment grants none.

Current analysis revision 2 retains per-file timing/drift quality and predicted
pre/post target-fit metrics. Known absolute drift >100 ppm blocks proposals;
unknown drift remains explicitly provisional. Bounded cuts must improve aggregate
fit without worsening any position on sampled analysis-grid and original points.
An unresolved peak/no acceptable cut returns an honest leave-unchanged proposal.
Old derived revisions are suppressed on reads/lists, with recompute warnings;
select the session, Analyze imports, then Prepare offline proposal. Neither fresh
electrical validation nor sampled prediction establishes measured improvement.

## Job workspace and separate human verifier

Next source UI shows one focused stage, not a page-length wall. Navigate to the
matching step and open its disclosure before interacting; safety/next action and
active-job cancellation remain available across steps. Release acceptance pending.

Source jobs UI adds Activity/action manifest, actual readback/history, Cancel and
strictly gated Execute. Select a versioned plan, choose its position/channel, then
Prepare synthetic file/null job. Inspect the hash, signal bounds, deadlines and
immutable policy. Nothing is running or approved merely because it appears in
the dashboard. Production hardware stays disabled; file/null evidence is synthetic.
The new source interface is not a claim that its next release's LAN gates passed.

Bootstrap login always yields role `automation`. On the deployed container dashboard,
ALL human routes are unavailable, including via localhost8098 publication or an
SSH tunnel. A matching loopback Host/Origin, forwarded headers, collapsed proxy
or accidentally configured verifier cannot change the wildcard listener boundary.
No human credential/approve controls may be offered on unknown/false readiness;
ordinary job prepare/read/cancel remains available. Separate native-fixture human login,
reauthentication, exact-manifest approval and revoke/logout use human-only HTTP
routes, never MCP or generic operations. Authentication is fresh for 60 seconds;
human sessions last at most 300 seconds, and approval expires no later than
60 seconds or session expiry. Logout/reauth, verifier/context changes, cancellation,
expiry and restart invalidate authority. The internal one-use grant is consumed
durably before spawning an owned child and is never returned to agents.

**No production human enrollment/provisioning was performed.** There is no
enrollment or bootstrap-elevation endpoint. An operator must separately provision
`GARAGE_HUMAN_AUTH_FILE` and a read-only accessible verifier outside application
state AND the automation credential directory. It must be an owned private regular
non-symlink file in a private owned directory. Current format is exactly
`user,realm,version,salt,password_hash,iterations`; password hashing uses PBKDF2
SHA256 (200000–1000000 iterations), separate strong password, hex salt/hash and
positive credential version. This describes separate native fixture prerequisites,
not permission to enroll a human on the deployed container. Never place the password, verifier or hashes in chat,
source, logs, release archives or MCP config. Review mounting/configuration and
realm isolation before any future manual provisioning; standard deployment does
not enroll a human. Missing verifier remains a prerequisite.

**Never enter an independent human password into deployed8098, LAN or its SSH
tunnel.** Ordinary automation access through the retained tunnel is different from
human authority. Only a standalone native FILE/null fixture with an actual
127.0.0.1 listener and local loopback peer can offer approval, after separately
provisioned fixture identity and explicit positive server readiness. This is not
a new production port/listener or authorization to start a human service here.
Source authenticated `/api/status` and capabilities report `human_approval_available`
and `human_approval_unavailable_reason`; unknown/false readiness is fail-closed.
Local backend/UI executable fixtures and scoped safety review are complete;
this source change still needs frozen/deployed acceptance gates.
Even native-fixture approval scopes only synthetic
file/null work and can never enable speakers, microphone capture or profile apply.
An isolated dummy-human native test can prove actual FILE/null execution without
production enrollment; its evidence must stay separate. SSH/Docker administrators
remain trusted; URL/Host/Origin alone never establish confidentiality or authority.

The separate actual CamillaDSP FILE adapter proof uses new synthetic sweeps and
the existing stereo 2.1 profile: exact84000-frame output, bounded levels, unused
outputs zero. It is not a job-engine native DSP/FIFO integration or acoustic test.
Left/right sweeps also reach the sub sum, and dual-mono sub sweeps reach mains;
that normal playback profile stays unchanged. New manifest2 jobs instead use a
disposable isolated software branch and 23-channel FILE diagnostics; real native127
approved left/right/sub job tests passed with other outputs zero. This is not
hardware channel verification or a production FIFO bridge. Hardware jobs stay
unconditionally disabled; no device or native host service was opened.

Next packaging includes verified official CamillaDSP4.1.3 and a pinned Debian
Bookworm ALSA library, licenses and corresponding source provenance in `vendor/dsp`.
Root-owned fixed paths and child-only library lookup passed an isolated proof
image's actual synthetic FILE execution. Real integrated-job routing/guardian,
artifact-admission and parent-owned reaping tests also passed. Final frozen image,
LAN/SSH and isolated native UI acceptance are separate release gates.
No host packages, new ports, human provisioning or hardware access were added.

Verify actual authenticated health, useful safe state write/readback and persistence
after dashboard-only restart. Inspect MA/scanner identity/uptime before and after.
Browser desktop and phone-viewport checks are different from a physical-phone test.
Verify the actual LAN URL from the Mac (a separate LAN host), including login/status
at phone viewport size and rejected unauthenticated/bad-Host/bad-Origin/missing-CSRF
requests. A physical phone remains unverified until the user opens the LAN link.
Build/typecheck/HTTP alone prove neither browser journey nor hardware functionality.
Current inspected hardware lacks UMC/UMIK and native DSP policy service; no sound,
unmute, calibration or speaker-success claims are authorized by this deployment.
