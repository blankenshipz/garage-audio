# App-owned Music Assistant enrollment

This is a human-only dashboard flow, not an MCP/agent operation, measurement-human
login, Spotify authorization, provider creation, sound grant or inference enablement.
The explicitly configured trusted LAN HTTP dashboard origin is authorized here;
it is not TLS and must be disclosed as a trusted-LAN credential exchange. Existing
MA accounts/roles/player/provider filters are retained. No MA account is created.

## Browser contract

`GET /api/session` on the configured trusted LAN Host obtains the existing HttpOnly
SameSite=Strict dashboard session and its CSRF token. The human presses Connect
after entering their **existing MA username/password** and approving this disclosure:
“Connect signs into your existing Music Assistant account and creates one
garage-service integration token with your account's existing permissions. It is
stored privately for the garage app and observer. This trusted-LAN HTTP connection
is not encrypted. It does not accept Spotify/Soloist terms, start music, change
volume, grant measurement authority or enable AI.”

Deployment sets `GARAGE_APP_AUTHORITIES=192.168.68.74:8098` and
`GARAGE_TRUSTED_LAN_AUTHORITY=192.168.68.74:8098` (plus any explicitly needed
separate loopback authority in AUTHORITIES only). Enrollment requires that exact
LAN authority; ordinary page rendering does not require an MA credential or an
available installer. The app image can be deployed independently while audio
authorization remains pending, with status truthfully unavailable/setup_required.

Dedicated request (no URL/user ID/provider/scopes/path/UID overrides):

```text
POST /api/setup/ma/enroll
Host: <configured LAN authority>
Origin: http://<same configured LAN authority>
Cookie: garage_session=<browser cookie>
X-CSRF-Token: <dashboard-session CSRF>
Content-Type: application/json

{"username":"<human input>","password":"<human input>",
 "request_id":"<random 16–64 ASCII alnum/underscore/hyphen id>","confirm":true}
```

Username1–100 characters/no control characters, password1–1024 characters,
body≤16KiB, exactly those four keys/no duplicates. Host/Origin/session/CSRF are
checked **before body processing**. Bootstrap-token sessions, localhost instead
of configured LAN authority, cross-site requests and MCP cannot enroll. Credentials
must never enter chat, general operations, URLs, localStorage, telemetry, console,
screenshots/HAR/browser storage exports or agent prompts. Clear password fields and
in-memory request credentials when the submit finishes; never persist them for retry.
Keep the request ID for a pending action, not its password. Do not optimistically mark
connected on POST initiation; await verified result/status. UI changes are separate.

Success is the ordinary wrapper, containing ONLY:

```json
{"ok":true,"result":{"state":"ready","code":null,
 "enrollment_id":"<request id>","scope_verified":true},"error":null}
```

Failure uses `{ok:false,result:null,error:{code,message,requirements:[]}}` with a
generic credential-free message. Codes: `invalid_enrollment_request`,
`ma_enrollment_origin_required`, `ma_enrollment_session_required`, `csrf_rejected`,
`ma_authentication_failed`, `ma_scope_required`, `ma_unavailable`,
`ma_installer_unavailable`, `ma_enrollment_busy`, `ma_enrollment_rate_limited`,
`ma_enrollment_interrupted`. Last means an uncertain token-create/install response;
repeated requests or a new request ID NEVER blindly create another token. Operator
reconciliation of the retained MA integration tokens/root transaction is required
before clearing an uncertain issuance record; never fabricate completion or terms.

Read status at `GET /api/setup/ma/status` (dashboard session) or existing typed
`setup.status` → `ma_enrollment`. Only safe projection is returned:

- `state: setup_required`, `code: ma_credential_required` (not yet provisioned),
- `state: unavailable`, `code: ma_installer_unavailable` (app may still render),
- `state: ready`, `scope_verified:true`, `enrollment_id` only after BOTH files match
  the durable transaction and supported MA authentication/access readback succeeds,
- revoked/expired credentials or scope loss → `setup_required`, never stale ready.

## Supported MA2.10.5 API

Fixed app endpoint `http://music-assistant:8095/api`, no redirects or environment
proxies. `auth/login` receives username/password, builtin provider and fixed device
name; actual response has `success`, `access_token`, `user`. Password is discarded
after that call. `auth/me`, `auth/scopes`, `players/all`, `player_queues/all` and
`config/providers(provider_domain=spotify_connect,include_values=false)` verify
identity/role and actual permitted access. `auth/token/create(name=garage-service)`
omits user_id, so it issues for the authenticated current user only.

Scopes needed: config.providers.read/write, players.read, queues.read. MA admins
legitimately report `['*']`; its native `has_scope` honors Scope.ALL, and so do we.
There is no invented per-token downscope selector: tokens inherit the current role
and existing player/provider filters, expire after one year, and do not auto-renew.
No token/session/user/filter/settings object is returned to the dashboard or MCP.
MA's upstream verbose request logger can log credential-bearing login bodies: keep
the matching backend request log level **info or stricter**, not debug/verbose,
and prohibit request/body/header capture in reverse proxies. The isolated default
log-level fixture verifies that issued values do not appear in its retained logs.

## Root installer / deployment contract

Install the three backend modules plus private_records in a root-owned immutable
host bootstrap directory, with no app-writable PYTHONPATH. Run root Python:
`python3 -m garage_audio.ma_enrollment_installer --config <root0600 config>`.
No root web listener, Docker socket mount or arbitrary command/path/UID is exposed.
Root deployment admission builder alone may inspect Docker metadata:

```text
python3 -m garage_audio.ma_enrollment_admission
 --container <current rebuilt dashboard container>
 --image-id sha256:<frozen expected image>
 --manifest <root0600 JSON map of frozen /app/garage_audio and /app/web path→SHA256>
 --ma-endpoint http://<current legitimate MA lease>:8095
 --output <root0600 admission config>
```

The manifest must include webserver.py, setup.py, ma_enrollment.py and
ma_enrollment_installer.py; use the whole frozen source inventory. This helper
finds the unique canonical `python -m garage_audio.webserver` descendant, not
Docker State.Pid (which is init:true's reaper). It verifies expected image ID,
running container, USER1000, then records PID/start time, executable inode/device/
SHA256, command-line SHA256, mount/PID namespaces, cgroup hash/full container ID
and current frozen source digests. Root config fields are `ma_endpoint` and
`admission` containing those fields plus uid1000. Installer uses kernel
SO_PEERCRED and independently rechecks these identities and file hashes per
connection; UID1000 alone is rejected. App restart needs fresh root admission
against the SAME approved image/inventory and actual current PID/lease. Never
admit arbitrary new source/image from an app request. Admission builders and
root service installation remain deployment-owner actions, not browser/API tools.

Installer socket `/run/garage-ma-enrollment/install.sock`, root:GID1000 mode0660,
root:GID1000 directory0710; singleton root0600 flock owner. Mount ONLY this private
directory into app; no observer/MCP tool gets an install operation. Status may be
projected by setup.status, but bearer install is reached only by the human handler.
Strict protocol≤8KiB: `{op:status}` or
`{op:install,enrollment_id:<strict id>,bearer:<ASCII24–4096 no whitespace>}`.
No caller endpoint/path/UID/scopes/user/command. Installer independently performs
the same read-only MA authentication/scope/access validation using its root-owned
fixed current-lease endpoint; it never logs in or creates tokens.

Fixed publication root `/home/zach/garage-audio/state/ma-enrollment`, root-owned:

- `app/token`, UID1000:GID1000,0600,single regular link, no symlink;
- `observer/token`, UID1001:GID1001,0600,single regular link, no symlink.

Both reader directories root-owned0711 and readonly-bind-mounted as **directories**:
app `/auth-ma` with `MA_TOKEN_FILE=/auth-ma/token`; observer `/run/ma-reference`
with `GARAGE_MA_TOKEN_FILE=/run/ma-reference/token`. Individual file binds would
retain a stale inode after atomic publication and MUST NOT be used. The same
human-approved issued bearer is authorized for both UID-private readers; no
blanket token-copy prohibition applies. Root journal retains approved transactions
root0600, never in shipped artifacts/receipts/browser. All writes are anchored
O_NOFOLLOW/exclusive private temp/fsync/atomic replacement. A prepared durable
transaction precedes either publication; restart/status completes BOTH before
ready. Root owner/shape/mode/singlelink checks reject unsafe existing files, not
silently repair them. Failed/previous transactions remain private for recovery.

App non-secret issuance journal lives under its existing private state root in
ma-enrollment/request.json. No password/bearer is stored there. A crash between
token-create and root receipt is surfaced as interrupted, not a blind retry.
Installer/helper/browser exit does not delete credentials or disable daily use.
No enrollment action starts the observer, audio stack, music or provider; deployment
owner independently starts prerequisites only after the remaining agreed gates.
