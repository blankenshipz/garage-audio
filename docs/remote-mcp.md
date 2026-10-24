# Remote read-only MCP connector (source only, unactivated)

**OAuth correction:** the authentication/installation sections below describe the
historical custom-bearer candidate, not a ChatGPT-compatible setup. Current OAuth
source work and its unresolved integration gate are in [remote-oauth.md](remote-oauth.md).
The executable now requires the OAuth issuer/JWKS/policy configuration there; it
does not fall back to the historical opaque-grant environment configuration.

This is a separate remote connector, not a replacement for SSH stdio MCP.
Production remains the previously observed `64440af`; source and deployment are
different identities. No Dot compatibility or remote access is verified yet.

## Everyday journey and exact contract

If Dot confirms the transport/authentication contract below, and after independently
approved installation and credential delivery, the intended client journey connects
to **`https://<approved-host>/mcp`** using MCP Streamable HTTP and an HTTP
`Authorization: Bearer <credential-reference>` header. The implementation uses
the MCP Python SDK, stateless requests and JSON responses. Initialize, list tools,
call `garage_status`, `garage_capabilities` or `garage_diagnostics`, and optionally
read `garage://remote/status`. All tools take exactly `{}`. Disconnect and reconnect
by initializing again; there is no stored sound authority, job or resumable action.

Only those three tools and one resource exist. No `garage_operation`, mutation,
shell, logs, file access, playback, Stop/Pause, volume or administration interface
is published. Capabilities describe this connector, not the upstream operation
inventory. Status/diagnostics project only enumerated software mode/state/readiness
and availability, with configured **connector** revision and UTC observation
time. Upstream/deployed revision is separately null/unverified: the configured
connector SHA is not application deployment proof. Unknown data stays unknown.
Physical audio verification is always false;
software playing does not prove audibility and off does not prove silence.
Upstream failures return fixed structured codes, never raw exception text.

## Authentication compatibility gate

This version supports **pre-provisioned opaque bearer tokens**, not an OAuth
authorization server, authorization-code flow, discovery or dynamic registration.
SDK authentication middleware checks every request. The private registry stores
only SHA256 token digests and binds each grant to the exact HTTPS resource URL,
one `garage:read` scope, not-before time, finite expiry and revocation flag.
Missing/invalid/expired/future/wrong-audience/wrong-scope/revoked grants fail closed.
Registry is reloaded on every request, including reconnect; deletion, corruption
or insecure permissions deny access. No ongoing credential is created by source
installation or by this task. Do not reuse dashboard bootstrap tokens remotely.

**Unresolved Dot inputs:** confirm actual connector transport support, ability to
send a custom bearer header, endpoint reachability requirements, OAuth-only versus
pre-provisioned authentication policy, and supported TLS trust. If Dot requires
OAuth, this implementation is not yet compatible; a separately reviewed approved
issuer/verifier integration is required, not a fabricated provider or fallback.

## Approval packet: installation versus network/credential activation

Before enabling anything, record exact approved public hostname/URL, ingress/TLS
owner, allowed Dot clients, token lifetime, issuer/delivery/revocation operator,
audit destination and shutdown owner. Approval must cover specific DNS, firewall,
tunnel or reverse-proxy changes and credential issuance. None is implemented or
authorized merely by committing this source.

Candidate installation contract (not executed):

- Supported Python >=3.13 with repository pinned dependencies (SDK MCP 1.30.0).
  Install the exact reviewed committed package into an isolated runtime. Freeze
  its source revision and dependency/image digest, separately from audio service.
- Run `python -m garage_audio.remote_mcp` as a dedicated unprivileged account.
  It binds **127.0.0.1:8101 only**, no public or LAN listener. Do not mount Docker,
  audio devices, provider state or arbitrary project-private directories.
- Set `GARAGE_REMOTE_RESOURCE=https://<approved-host>/mcp`,
  `GARAGE_REMOTE_SOURCE_REVISION=<exact-40-hex-reviewed-sha>` and
  `GARAGE_REMOTE_GRANTS_FILE=<private-registry-reference>`. No secret in command
  arguments, URLs, repository files, logs or approval packet.
- The existing adapter requires `GARAGE_APP_URL=http://127.0.0.1:<app-port>` and
  `GARAGE_APP_TOKEN_FILE=<private-upstream-reference>`. This is a privileged
  upstream bootstrap credential: isolate the connector and do not expose it to
  Dot. The connector calls only application `status`; private login/session setup
  is internal. No new upstream deployment or credential access has been approved.
- A separately approved HTTPS reverse proxy must preserve the exact public Host,
  handle TLS, cap request sizes/timeouts/rates, and forward only `/mcp` to the private
  listener. Do not log Authorization, request/response bodies or query strings.
  Public Host and Origin are validated against the configured resource. Browser
  origins other than that exact HTTPS origin are refused. No CORS workaround.
- Runtime registry: owner root or service UID, regular non-symlink file, mode0600,
  <=64KiB; JSON `{"version":1,"grants":[...]}`. Each grant has exactly `sha256`,
  `client_id` (bounded identifier), `audience` (exact resource), `scopes`
  (`["garage:read"]`), `not_before` and `expires_at` (integer Unix times), and
  `revoked` (boolean). Create and deliver high-entropy tokens only after explicit
  credential approval. No real grant/example credential is supplied here.

Audit records server-generated correlation ID, validated registry client ID (or
anonymous), exact allowlisted operation/resource label (or protocol/denied), bounded
result category and HTTP status. It never records payloads, headers, token values,
wire request IDs, unknown operation names or client-supplied paths. Configure a
bounded private audit sink at deployment;
uvicorn access logging is disabled. Revocation: atomically remove the digest grant
or set `revoked:true`, retaining correct ownership/permissions. Verify next request
fails. Expiry is enforced per request; neither reconnect nor upstream login renews
it. Shutdown: stop only this dedicated connector service and withdraw its approved
ingress; do not restart audio or the existing dashboard/MCP. Roll back connector
package/config only, preserving audit and failed-check receipts.

## Acceptance and current evidence

Local isolated tests use ephemeral synthetic grants, loopback-only HTTP inside a
network-disabled container, real SDK initialize/list/call/resource/reconnect and
authentication denials. They do not prove TLS, proxy deployment, Dot installation,
live upstream authentication or physical audio.
The first capacity preflight failed before tests and remains retained. A distinct
no-build runner reuses the already installed supported image with read-only root,
no Docker logs, no host networking/ports and 48MiB total tmpfs scratch. Its measured
source archive must be <8MiB and capacity must retain a 1GiB emergency reserve plus
twice archive size and 64MiB metadata/receipt allowance. This does not authorize
builds, cleanup or production writes. The failed lifespan attempt is also retained;
authentication middleware was corrected to preserve SDK lifespan startup while
requiring authentication for every HTTP route.

Final supported-platform gate: **six connector tests passed, zero skips**, on
Linux x86_64, Python **3.13.16**, MCP **1.30.0**, Agents SDK **0.23.1** (no inference).
The real existing stdio MCP journey also passed: exact **40 tools, five resources,
one prompt**, authenticated quiet synthetic state create/readback, strict argument
validation and sound-action refusal. Its stale 34-tool fixture was corrected by
verifying the exact six already implemented capture/correction tools; no assertion
was weakened or skipped. This does not authorize these stdio mutation tools remotely.

Canonical focused security findings:

- **S1 resolved:** connector revision is explicit; upstream revision stays null and
  identity unverified. Wire assertions enforce this separation.
- **S2 resolved:** successful reads, resource access, mutation/argument denial and
  anonymous authentication denial have bounded audit rows. Tests assert validated
  caller, exact operation/result, unique server correlation and absence of bearer,
  digest, payload, wire ID, arbitrary name/path and upstream sentinel leakage.
- **S3 resolved locally:** initialize then expire/revoke; subsequent calls, resource
  requests, unknown routes and reconnect initialization fail authentication. No
  prior initialized session extends a grant.
- **S4 resolved locally:** projection is checked against actual `Application.execute`
  status shape with an isolated policy seam, asserting concrete mode/availability/
  state/device/readiness values, not just unknown placeholders. The remote wire
  journey uses a synthetic upstream and does not establish live upstream access.
- **S5 conditional:** Dot custom-bearer and Streamable HTTP compatibility remains
  unverified. No OAuth compatibility, deployed HTTPS or Dot working claim is made.

Complete private receipts and failed attempts are retained outside the repository.
The final source inventory and protocol/stdio/audit checks are separate from the
still-unrun external installation/authentication/network approvals.
After separately approved activation, record installed source/image identities,
TLS/Host/auth denial, sanitized readback, expiry/revocation, disconnect/reconnect
and shutdown without mutations. Never relabel local synthetic upstream evidence
as a deployed connector or Dot acceptance.
