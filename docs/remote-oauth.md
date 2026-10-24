# OAuth MCP integration — source only, unactivated

This supersedes the custom-bearer authentication proposal at `7fbcfb6`.
OpenAI's [authentication contract](https://developers.openai.com/plugins/build/auth)
requires OAuth 2.1 and does not support customer-supplied API keys. The prior
opaque-bearer connector is **not ChatGPT-compatible**. No deployed compatibility
is claimed by the new source or its synthetic tests.

## Selected provider and libraries

Use **Auth0**, the MCP Python SDK **1.30.0** and **PyJWT 2.15.1**. Production
login, consent, authorization-code issuance, PKCE and registration belong to
Auth0, not Garage Audio. No homemade identity/consent server is shipped. The
[OpenAI Auth0 scaffold](https://github.com/openai/openai-mcpkit/tree/main/python-authenticated-mcp-server-scaffold)
and [Auth0 MCP quickstart](https://auth0.com/ai/docs/mcp/get-started/authorization-for-your-mcp-server.md)
are the selected integration references. The scaffold's vector store, API key,
inference and general data tools are NOT required or adopted.

## Intended normal journey

Client discovers public `GET /.well-known/oauth-protected-resource` (also linked
from unauthenticated `/mcp` 401 WWW-Authenticate), then the exact Auth0 issuer's
OIDC/OAuth metadata. Human signs in and consents at Auth0. Client uses code+PKCE
S256 with the exact `resource` in both authorization and token requests. The
RS256 access token's audience must include `https://<approved-host>/mcp`.
Authenticated Streamable HTTP reads the same three read-only tools and one status
resource; disconnect/reconnect never changes audio or renews an expired grant.
Connector revision and unknown upstream revision remain separate.

## Exact activation packet (not executed)

1. Approve exact resource hostname/HTTPS URL, Auth0 tenant issuer with trailing
   slash, browser-reachable issuer, allowed client/user population, TLS/ingress,
   credential owner, audit sink, revocation/rotation and shutdown owner.
2. In Auth0 create one RS256 API whose identifier equals the exact HTTPS `/mcp`
   resource; expose only `garage:read`, with per-app delegated authorization, no
   blanket default third-party grants or machine-to-machine access. Enable
   **Resource Parameter Compatibility Profile**. Do not substitute a default
   audience that silently ignores a mismatched resource.
3. Enable issuer identification only if Auth0 actually returns the exact issuer
   `iss` on successful AND error authorization responses. Verify published
   metadata and real responses before claiming RFC9207 support.
4. Select Auth0 **manual CIMD registration** as documented by the OpenAI guide
   and [Auth0's registration instructions](https://auth0.com/docs/get-started/auth0-overview/create-applications/register-applications-with-cimd):
   enable CIMD registration, import the exact client metadata URL displayed by
   ChatGPT's connector management UI, inspect its metadata and grant only this API's
   read scope. Copy the exact displayed callback URI; never guess a stable or
   callback-ID URI or allow wildcard redirects. Require a supported intersecting
   token authentication method and S256. If tenant CIMD is unavailable, stop and
   specify a separately reviewed predefined-client configuration; no silent DCR.
   Keep **Dynamic CIMD Registration** disabled; manual import does not require it.
   Auth0 supports `none` (PKCE required) and `private_key_jwt` (Enterprise entitlement
   required), not shared-secret CIMD authentication. Do not purchase/upgrade. Use
   the UI preview to confirm the imported method is supported by this tenant and
   intersects the exact ChatGPT document; if not, stop rather than rewrite metadata.
   A domain-level login connection is required for third-party clients; restrict
   membership/consent to the approved human users, not everyone using this client.
5. Confirm issuer discovery advertises `S256`, correct authorization/token
   endpoints, enabled OIDC scopes if advertised, and actual registration support.
   Exercise exact resource in both requests, redirect mismatch and code replay
   denial. No `offline_access`, refresh grant, persistent token storage or new
   user/client credential is authorized by this source work.
6. Install reviewed source with pinned requirements. Run
   `python -m garage_audio.remote_mcp`, private **127.0.0.1:8101** only, with:
   `GARAGE_REMOTE_RESOURCE`, `GARAGE_REMOTE_SOURCE_REVISION`,
   `GARAGE_REMOTE_ISSUER`, `GARAGE_REMOTE_JWKS_FILE`, `GARAGE_REMOTE_POLICY_FILE`.
   Retain private `GARAGE_APP_URL` and `GARAGE_APP_TOKEN_FILE` references from the
   existing upstream adapter; the latter is privileged and must never reach clients.
7. JWKS and policy inputs are regular non-symlink owner/root mode0600 files <=64KiB.
   JWKS contains only approved issuer public RSA signing keys (no private key).
   Policy shape: `{"allowed_clients":[<approved azp/client_id>],
   "revoked_token_sha256":[]}`. These are references/configuration, not new grants.
   No token-directed `jku`, `x5u`, embedded key or client-metadata URL fetch occurs.
   Auth0 has both an internal generated client ID and external CIMD URL: configure
   the exact signed `azp` (or `client_id` when `azp` is absent) observed in a verified
   acceptance token, not a guessed identifier or unverified JWT. This allowlist
   identifies the client application, NOT the human user; approved-user entitlements
   and consent must be enforced at Auth0 before it issues the read scope.
   Approved deployment tooling must retrieve keys from the exact issuer's verified
   JWKS endpoint and atomically rotate the pinned public-key file, overlap old/new
   keys as appropriate, then remove retired keys. Unknown keys deny access.
8. Approve TLS reverse proxy for `/mcp` AND the public metadata path only. Preserve
   public Host, bound sizes/rates/timeouts, redact Authorization and bodies, never
   expose dashboard/admin or issuer-management APIs. Public resource metadata is
   intentionally unauthenticated; all application routes stay authenticated.

Expiry/nbf/iat, signature, exact issuer/audience, read scope, allowed client and
local token deny digest are checked on each request. Revoking an Auth0 refresh
grant alone does not immediately invalidate an already-issued JWT: use short
approved access lifetimes and the local deny policy (or disable the client) for
immediate cutoff. No refresh support/persistence is enabled here. Audit remains
bounded, with hashed validated OAuth client identity rather than email/sub/URL.
Shutdown stops only this connector and removes its approved ingress; no audio,
dashboard, stdio MCP or unrelated service restart. Keep failure/audit receipts.

## Evidence and remaining gate

`tests/oauth_flow.py` uses the established MCP SDK authorization routes over real
isolated loopback HTTP. Its test-only synthetic provider models pre-attended
authorization, exact resource binding and single-use code storage; it does not
implement production identity/consent. It exercises SDK **DCR**, discovery, S256
code exchange, signed tokens, actual MCP status/readback and reconnect, plus wrong
verifier, plain PKCE, missing/wrong resource, redirect, unknown client and code replay
denials. The SDK fixture schema requires refresh in the registered grant list but
issues no refresh token and implements no refresh flow. No persistence is enabled.

**This does not test Auth0's selected manual CIMD path.** Its capability is backed
by the linked provider documentation, not by DCR fixture results. Actual tenant
import/preview, registration method negotiation, issuer response success/error
`iss`, resource/code binding, login/consent and ChatGPT/Dot linking are unrun.
Before activation, capture provider discovery and sanitized rejection outcomes,
verify manual CIMD preview and approved-user restrictions, then repeat the real
code/token/readback/reconnect/expiry/revocation journey. Do not infer provider
acceptance from the local fixture. HTTPS/TLS/proxy/tunnel also remain unrun.

`tests/remote_oauth.py` separately covers pinned signed-token issuer/audience,
expiry/nbf/iat, scope/client/signature/algorithm/missing-claim denials, local token
revocation, removed keys/permissions, public metadata/challenge and authenticated
MCP. `nbf` is optional for Auth0; when present its type and activation time are
validated. Required `exp` and `iat` are integer timestamps; there is zero clock
leeway. Refresh-grant revocation cannot invalidate an issued JWT by itself.
All tests are source evidence, not a deployment identity. Production `64440af`
has not been freshly checked or changed; install the separately reviewed pushed
source revision only after the activation packet is approved.

## Focused source security review

- Tokens: only pinned RS256 public keys >=2048 bits; no algorithm negotiation,
  header-selected fetch, unknown/duplicate key acceptance or missing required
  claims. Exact issuer/audience and per-request scope/time/client/deny policy.
- Identity: signed client identity is not a user entitlement. Auth0's approved-user
  access/consent gate is required. Audit uses a bounded hashed client identifier.
- Discovery: only the exact public GET metadata path bypasses authentication;
  `/mcp`, unknown routes and mutations remain behind the deny-by-default guard.
- Rotation/revocation: keys/policy reload per request, missing/bad files fail closed;
  approved operator handles atomic updates. No claim of JWT introspection or
  automatic refresh-grant revocation. No attacker-controlled network discovery.
- Registration: the production resource server does not fetch CIMD. Auth0 handles
  URL/JSON validation and consent. Documented manual import is not locally proven;
  no fake CIMD implementation or permissive production issuer is included.
- Packaging: PyJWT's direct pin matches the existing lock; project dependencies
  load from requirements.txt. Existing stdio/three-tool surface are unchanged.

Local acceptance: two SDK wire-flow tests, four signed-resource tests, six existing
connector tests, and the existing stdio journey (40 tools, five resources, one
prompt), no skips, on Linux x86_64 / Python 3.13.16 / MCP 1.30.0 / PyJWT 2.15.1.
This is not rendered UI, real Auth0, ChatGPT, TLS or deployed-device evidence.

Secure MCP Tunnel is optional transport only, not an OAuth issuer. Its runtime API
key and Tunnels Read/Use (Read/Manage for creation), workspace association and
availability require separate exact approval. No key is provisioned or used, and
the browser must still reach Auth0. No inference or purchases occur.
