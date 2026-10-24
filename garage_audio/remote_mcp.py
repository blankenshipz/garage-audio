"""Opt-in read-only Streamable HTTP MCP. No deployment or credential provisioning."""
import hashlib
import json
import logging
import os
import re
import stat
import time
import uuid
from contextvars import ContextVar
from datetime import datetime, timezone
from urllib.parse import urlsplit

from mcp.server.auth.middleware.bearer_auth import BearerAuthBackend, RequireAuthMiddleware
from mcp.server.auth.provider import AccessToken
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from starlette.middleware.authentication import AuthenticationMiddleware

from .mcp_server import Adapter

SCOPE = 'garage:read'
TOOLS = ('garage_status', 'garage_capabilities', 'garage_diagnostics')
READINESS = ('standby', 'network_unavailable', 'observation_unavailable',
             'receiver_unavailable', 'awaiting_source', 'waiting_for_play', 'playing', 'paused')
audit = logging.getLogger('garage.remote.audit')
request_audit = ContextVar('remote_request_audit', default=None)


def audit_operation(operation, result):
    context = request_audit.get()
    if context is not None:
        context['operation'] = operation
        context['result'] = result


class GrantVerifier:
    """Opaque bearer digest grants, reloaded per request for immediate revocation.

    This is NOT an OAuth issuer or a compatibility claim for OAuth-only clients.
    The registry holds SHA256 digests only; callers never choose paths or scope.
    """
    def __init__(self, path, audience):
        self.path, self.audience = path, audience

    async def verify_token(self, token):
        try:
            if not re.fullmatch(r'[A-Za-z0-9_-]{43,128}', token):
                return None
            fd = os.open(self.path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(fd, 'r') as source:
                info = os.fstat(source.fileno())
                if (not stat.S_ISREG(info.st_mode) or info.st_uid not in (0, os.geteuid())
                        or stat.S_IMODE(info.st_mode) != 0o600 or info.st_size > 65536):
                    return None
                registry = json.loads(source.read(65537))
            if set(registry) != {'version', 'grants'} or registry['version'] != 1:
                return None
            grants = registry['grants']
            if not isinstance(grants, list) or len(grants) > 128:
                return None
            digest = hashlib.sha256(token.encode()).hexdigest()
            matches = [g for g in grants if isinstance(g, dict) and g.get('sha256') == digest]
            if len(matches) != 1:
                return None
            grant = matches[0]
            now = time.time()
            if (set(grant) != {'sha256', 'client_id', 'audience', 'scopes', 'not_before', 'expires_at', 'revoked'}
                    or not re.fullmatch(r'[a-zA-Z0-9_-]{1,64}', grant['client_id'])
                    or grant['audience'] != self.audience or grant['scopes'] != [SCOPE]
                    or grant['revoked'] is not False
                    or type(grant['not_before']) is not int or type(grant['expires_at']) is not int
                    or not grant['not_before'] <= now < grant['expires_at']):
                return None
            return AccessToken(token=token, client_id=grant['client_id'], scopes=[SCOPE],
                               expires_at=grant['expires_at'], resource=self.audience)
        except Exception:
            return None  # No credential/path/parser details in responses or logs.


def projection(response):
    """Construct fields, never redact a raw object or forward upstream error text."""
    if not isinstance(response, dict) or response.get('ok') is not True:
        return {'ok': False, 'error': {'code': 'upstream_unavailable'}}
    result = response.get('result')
    if not isinstance(result, dict):
        return {'ok': False, 'error': {'code': 'upstream_invalid'}}
    audio = result.get('audio')
    audio = audio if isinstance(audio, dict) else {}
    readback = audio.get('readback')
    readback = readback if isinstance(readback, dict) else {}
    output = {'deployment_mode': result.get('deployment_mode') if result.get('deployment_mode') in ('docker', 'legacy-native') else 'unknown',
              'audio_available': audio.get('available') is True,
              'physical_audio_verified': False}
    for key, allowed in [('state', ('off', 'playing', 'paused')),
                         ('device_mode', ('file', 'hardware', 'standby')),
                         ('readiness', READINESS)]:
        output[key] = readback.get(key) if readback.get(key) in allowed else 'unknown'
    return {'ok': True, 'result': output, 'error': None}


class ReadOnlyMCP(FastMCP):
    async def call_tool(self, name, arguments):
        if name not in TOOLS or arguments != {}:
            audit_operation('denied', 'denied')
            return {'ok': False, 'error': {'code': 'read_only_operation_denied'}}
        audit_operation(name, 'started')
        return await super().call_tool(name, arguments)


class Audit:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            return await self.app(scope, receive, send)
        context = {'correlation': uuid.uuid4().hex, 'operation': 'protocol', 'result': 'transport'}
        reset = request_audit.set(context)
        async def observed(message):
            if message['type'] == 'http.response.start':
                user = scope.get('user')
                client = getattr(user, 'display_name', 'anonymous')
                # Only validated registry client IDs, never headers or wire IDs.
                if not isinstance(client, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', client):
                    client = 'anonymous'
                audit.info('remote_mcp correlation=%s client=%s operation=%s result=%s status=%d',
                           context['correlation'], client, context['operation'], context['result'], message['status'])
            await send(message)
        try:
            await self.app(scope, receive, observed)
        finally:
            request_audit.reset(reset)


class HTTPRequireAuth(RequireAuthMiddleware):
    async def __call__(self, scope, receive, send):
        # SDK RequireAuthMiddleware is a route wrapper, not lifespan middleware.
        if scope['type'] == 'lifespan':
            return await self.app(scope, receive, send)
        return await super().__call__(scope, receive, send)


def create_app(*, audience, grant_file, source_revision, upstream=None):
    url = urlsplit(audience)
    if (url.scheme != 'https' or not url.hostname or url.username or url.password
            or url.path != '/mcp' or url.query or url.fragment):
        raise ValueError('Exact HTTPS resource URL ending /mcp required')
    if not re.fullmatch(r'[0-9a-f]{40}', source_revision):
        raise ValueError('Exact committed source revision required')
    upstream = upstream or Adapter()
    server = ReadOnlyMCP('garage-audio-read-only', stateless_http=True, json_response=True,
                         streamable_http_path='/mcp', max_request_body_size=16384,
                         transport_security=TransportSecuritySettings(
                             allowed_hosts=[url.netloc], allowed_origins=[f'https://{url.netloc}']))

    def envelope(value):
        return {**value, 'source': {'connector_revision': source_revision,
                                  'upstream_revision': None, 'upstream_identity': 'unverified'},
                'observed_at': datetime.now(timezone.utc).isoformat()}

    def status():
        try:
            value = projection(upstream.call('status', {}))
        except Exception:
            value = {'ok': False, 'error': {'code': 'upstream_unavailable'}}
        context = request_audit.get()
        if context is not None:
            context['result'] = 'ok' if value['ok'] else 'upstream_unavailable'
        return envelope(value)

    @server.tool()
    def garage_status() -> dict:
        """Sanitized software status; never proof of audible playback or silence."""
        return status()

    @server.tool()
    def garage_capabilities() -> dict:
        """Exact connector allowlist, not the upstream mutation inventory."""
        audit_operation('garage_capabilities', 'ok')
        return envelope({'ok': True, 'result': {'tools': list(TOOLS), 'scope': SCOPE,
                                               'read_only': True, 'sound_authority': False}})

    @server.tool()
    def garage_diagnostics() -> dict:
        """Bounded sanitized availability/readiness only, no logs or private state."""
        return status()

    @server.resource('garage://remote/status')
    def status_resource() -> str:
        audit_operation('resource_status', 'started')
        return json.dumps(status(), allow_nan=False)

    app = server.streamable_http_app()
    # SDK transport lifespan remains on this Starlette app. Authentication covers
    # every route (including unknown routes), and is checked on every HTTP request.
    app.add_middleware(HTTPRequireAuth, required_scopes=[SCOPE])
    app.add_middleware(AuthenticationMiddleware,
                       backend=BearerAuthBackend(GrantVerifier(grant_file, audience)))
    app.add_middleware(Audit)
    return app


def main():
    import uvicorn
    audit.setLevel(logging.INFO)
    audit.addHandler(logging.StreamHandler())
    audit.propagate = False
    app = create_app(audience=os.environ['GARAGE_REMOTE_RESOURCE'],
                     grant_file=os.environ['GARAGE_REMOTE_GRANTS_FILE'],
                     source_revision=os.environ['GARAGE_REMOTE_SOURCE_REVISION'])
    # Private reverse-proxy upstream only. Never bind a public/LAN socket here.
    uvicorn.run(app, host='127.0.0.1', port=8101, access_log=False, proxy_headers=False)


if __name__ == '__main__':
    main()
