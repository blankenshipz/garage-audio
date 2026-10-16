# Partial app-only enrollment deployment

This installs the phone Connect form/backend at `http://192.168.68.74:8098`
independently of the full audio cutover. It is **not Docker/audio completion**.
Existing MA/music/accounts/queues/rooms/profiles/dashboard state and old rollback
remain in place. No playback, provider creation, token issuance or login is part
of packaging/installation. Only the human phone form may issue an integration
token; see [the enrollment contract](ma-enrollment.md) for trusted-LAN HTTP
disclosure, Host/Origin/session/CSRF, persistence and uncertain-issuance handling.

## Packaging phase (no deployment)

Backend216bb79a and UIgen7 `93c2e67e…` are paused. The final app image is now
pinned in `config/docker-release-inputs.json`;88 runtime files/imports as UID1000
and the complete86-file app/web admission map were verified against the small
cached-parent derivative. Actual peer/render/restart/phone gates remain open.
New gen7 image `ae4c300d…` is built, not yet deployed: it repairs visible required
field validation/sticky-aware focus and the actual insecure-LAN HTTP pre-request
failure (`crypto.randomUUID` unavailable) using secure `getRandomValues` IDs.
Only ma-enrollment.js/style.css and the targeted UI test changed; backend/root
bootstrap remain byte-identical. Current production/rollback `a83582fe…`/`85273126…`
keeps its identity and the reported Connect regression remains open until actual
new-image LAN before/after-click verification. No user credential is part of this
package or agent acceptance. `1f3b9081…` is superseded only for affected app,
enrollment/bootstrap and packaging assets; MA `a7af5932…`, audio `5c62cb0f…`
and their original FILE/hardware-zero receipts keep their original identities.
Never relabel those receipts as new-app or physical-phone enrollment proof.

Use `scripts/dashboard-cached-context --receipt PRIVATE_NEW_DIRECTORY` with the
existing audited440383 parent metadata/direct-URL files. It creates all runtime
modules/web, immutable image provenance, a **full** `/app/garage_audio` + `/app/web`
SHA256 admission map (including private_records and all imports), and a seven-file
stdlib root bootstrap inventory. No four-module admission shortcut or source
overlay is valid. Root admission must use the unique actual webserver descendant
of init:true, not the reaper's Docker State.Pid. Root sources are separately
immutable, not imported from an app-writable PYTHONPATH.

After fresh capacity/image inspection and a durable build receipt, the minimal
small-layer build command on the garage host is:

```sh
docker build --network=none --pull=false --platform=linux/amd64 \
  --build-arg SOURCE_REVISION=EXACT_SNAPSHOT_REVISION \
  -t garage-audio-dashboard:EXACT_NEW_CANDIDATE - < PRIVATE_CONTEXT_TAR
```

The recipe's default verified440383 parent needs no apt/pip/download/new base.
Do not rebuild MA/audio or repeat cold-cache extraction. Verify the actual new
image ID, exact parent layer prefix, all runtime hashes and imports as UID1000,
and new-layer/physical growth. Reserve2GiB plus256MiB receiver allowance and
512MiB bounded tests; inspect current free space before each gate. A build is not
phone/process/root-installer/render/restart acceptance.

Then replace the pending dashboard image pin in `config/docker-release-inputs.json`,
complete final packaging edits, pause this writer, freeze/inventory once and
record later gate results externally. A pending status/null dashboard pin prevents
strict release freeze while UI/build is pending; the reconciled manifest now pins
the actual enrollment image and both generated root inventory/map digests.
Do not modify frozen inputs to record tests or root endpoint/admission changes.

## Operator-only app activation (separate exclusive phase)

First preserve the old dashboard image/config/state rollback and ensure only one
dashboard writer/container owns existing state and8098. Do not stop MA/audio or
unrelated services. Stage verified frozen source root-owned/nonwritable under
`/opt/...`; externally install the generated bootstrap inventory root0600 below
`/etc/garage-audio` and match its exact digest. The installer performs no admission,
token/login, service start/enable or Docker operation:

```sh
sudo -n python3 FROZEN_RELEASE/scripts/install-ma-enrollment.py \
  --release FROZEN_RELEASE --inventory ROOT0600_BOOTSTRAP_INVENTORY \
  --expected-digest EXACT_BOOTSTRAP_INVENTORY_SHA256
sudo -n systemctl daemon-reload
```

It copies the exact stdlib dependencies into versioned root0555 directories/files
0444, installs the fixed service and no-aging tmpfiles directory rule, and creates
only root-owned reader/socket/config anchors. Existing unsafe modes/files or an
existing installer/service are rejected, not silently repaired/overwritten. Partial
installation is retained for operator diagnosis. No automatic deletion/cleanup.
`tmpfiles` recreates only the socket directory at host boot; never reader tokens
or journals. It does not replace process admission or enable the service.

External deployment.env has **references, not bearer values**:

```text
DASHBOARD_IMAGE=sha256:EXACT_NEW_IMAGE
GARAGE_ENROLLMENT_MA_URL=http://192.168.68.74:8095
```

The endpoint above is the retained MA listener observed on the host network on
October12; reverify its actual ownership/listen address and info-or-stricter request
logging before activation. It is an explicit partial-phase endpoint, not an old-MA
fallback when the full private audio generation fails. The app-only overlay keeps
Host/Origin authorities/session token/state mounts, Docker deployment mode and
public silence/status-only IPC. Missing audio remains truthfully unavailable.
Only the app gets the two root-precreated **readonly directory** binds:
`.../ma-enrollment/app`→`/auth-ma`, `MA_TOKEN_FILE=/auth-ma/token`, and
`/run/garage-ma-enrollment`→same private socket path. `create_host_path:false`
prevents Docker accidentally creating unsafe root publication directories.

After isolated candidate gates, during the parent's exclusive dashboard handoff:

```sh
docker compose -p garage-audio-dashboard --env-file EXTERNAL_DEPLOYMENT_ENV \
  -f FROZEN_RELEASE/compose.dashboard.yaml \
  -f FROZEN_RELEASE/compose.dashboard-enrollment.yaml \
  up -d --no-build --pull never dashboard
```

Only after the new app process is running, install the generated **full** source
map root0600, then run from the installed immutable bootstrap directory:

```sh
sudo -n /usr/bin/python3 -B -m garage_audio.ma_enrollment_admission \
  --container garage-audio-dashboard --image-id sha256:EXACT_NEW_IMAGE \
  --manifest ROOT0600_FULL_APP_WEB_MAP \
  --ma-endpoint http://192.168.68.74:8095 \
  --output /etc/garage-audio/ma-enrollment/admission.json
sudo -n systemctl start garage-ma-enrollment.service
```

Do not admit another image or arbitrary UID1000 process. No Docker socket mounts,
privileged app/root web listeners or generic root API are introduced. Installer
uses actual private MA8095 read-only authentication/access verification; it never
logs in or creates a token. App/process restart needs fresh admission to the same
approved image/source and current PID; restart only this private installer after
admission. Do not silently re-admit or make the browser responsible. Existing
token files persist independently of bootstrap/session expiry and test shutdown.

Verify actual admitted app peer/root rejection branches, desktop/390px form,
quiet real SSH MCP→HTTP/UI same-state mutation/readback, token directory modes
and atomic replacement, Stop and restart persistence before acceptance. Human
phone Connect is separate live observation—agents never enter real MA credentials
or mint a production token. Preserve private complete logs without HTTP credentials,
root transaction dumps, token fixtures, HAR or unredacted request logs.

## Later full audio phase

For a later UI-only app release, do not reinstall the exclusive bootstrap helper
or discard existing private reader/state directories. Compare the unchanged
seven-file bootstrap digest, retain old app/image/config and quiescent state,
stop only the current app and its two private installer/admission services during
the exclusive swap, and use the existing verified bridge. Root owner atomically
installs the **new full map plus new exact approved image/map watcher pins** together;
the watcher still admits only that image and measured actual webserver descendant.
Keep current MA endpoint, readonly directory binds, trusted Host/Origin/CSRF and
dashboard token references. Re-freeze external activation inputs, validate actual
new LAN click/feedback and restart/persistence, and leave prior receipts at their
original identities. Never silently admit another image or infer live submission
from isolated tests/sandbox rendering.

Audio image remains `5c62cb0f…`. Bind root-owned observer publication **directory**
`/home/zach/garage-audio/state/ma-enrollment/observer` to `/run/ma-reference:ro`;
`GARAGE_MA_TOKEN_FILE=/run/ma-reference/token`. The parent supplies this external
overlay/config when full audio is ready; the old individual-file bind is not used
for enrolled token publication. The installer socket is never mounted into the
observer/audio/MCP container. Switch app MA_URL to fixed private
`http://music-assistant:8095` only as part of the qualified full audio generation.
No automatic observer startup, music restoration or fabricated provider/terms
consent follows from enrollment or app-only deployment.
