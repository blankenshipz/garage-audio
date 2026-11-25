"""MCP transports (stdio and streamable HTTP at /mcp).

This is the only module that imports the MCP SDK (``mcp``, plus ``anyio``,
``starlette`` and ``uvicorn`` that it brings). Imports happen inside functions so
the rest of the package, and the bearer middleware below, work without it.

HTTP is always bearer-protected: ``build_http_app`` and ``run_http`` refuse to
run without a token, every request (all paths and methods, before routing) is
checked with a constant-time comparison, missing/wrong tokens get 401, and the
token is never logged or echoed. uvicorn's access log is disabled.
"""
import hashlib
import hmac
import json
import logging
import math

from . import SERVER_NAME, VERSION, content, tools
from .errors import ConfigError, DependencyMissing, OpsError

LOG = logging.getLogger('garage_audio.ops_mcp')
MCP_PATH = '/mcp'
MAX_REQUEST_BODY = 1024 * 1024

INSTRUCTIONS = (
    'Operator tools for the garage audio stack (Music Assistant -> PipeWire -> CamillaDSP -> amps -> two mains '
    'and a sub, owned by the garage-audio-network supervisor). Start with get_status or diagnose. Tools marked '
    '[WRITE]/[PLAYBACK] change the system and are refused unless the operator enabled them. Never loop restarts; '
    'respect backoff. DSP changes are limit-checked and backed up automatically; note the backup_id.')


def _sdk():
    try:
        import anyio  # noqa: F401
        from mcp import types
        from mcp.server.lowlevel import Server
        from mcp.server.lowlevel.helper_types import ReadResourceContents
    except ImportError:
        raise DependencyMissing('the MCP SDK is not installed in this Python environment; install the pinned '
                                'requirements (pip install -r requirements.txt, mcp==1.30.0) to run the server') from None
    return types, Server, ReadResourceContents


def jsonable(value):
    """JSON-safe copy: non-finite floats become null, unknown objects become strings."""
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, (str, int, bool)) or value is None:
        return value
    if isinstance(value, dict):
        return {str(k): jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [jsonable(v) for v in value]
    return str(value)


def _dumps(value):
    return json.dumps(value, indent=2, allow_nan=False)


def build_server(ctx):
    """Low-level MCP server wired to the stdlib tool registry, resources and prompts."""
    types, Server, ReadResourceContents = _sdk()
    import anyio

    server = Server(SERVER_NAME, version=VERSION, instructions=INSTRUCTIONS)

    @server.list_tools()
    async def list_tools():
        out = []
        for item in tools.describe(ctx.config):
            out.append(types.Tool.model_validate({
                'name': item['name'], 'description': item['description'], 'inputSchema': item['inputSchema'],
                'annotations': item['annotations'],
                '_meta': {'garage-audio/access': item['access'], 'garage-audio/enabled': item['enabled']}}))
        return out

    # Our own validator runs inside tools.call *after* the permission gate, so a disabled
    # tool is refused before its arguments are even looked at.
    @server.call_tool(validate_input=False)
    async def call_tool(name, arguments):
        try:
            result = await anyio.to_thread.run_sync(tools.call, ctx, name, arguments)
        except OpsError as error:
            payload = {'error': jsonable(error.payload())}
            return types.CallToolResult(content=[types.TextContent(type='text', text=_dumps(payload))],
                                        structuredContent=payload, isError=True)
        result = jsonable(result)
        if not isinstance(result, dict):
            result = {'result': result}
        return types.CallToolResult(content=[types.TextContent(type='text', text=_dumps(result))],
                                    structuredContent=result, isError=False)

    @server.list_resources()
    async def list_resources():
        return [types.Resource(uri=item['uri'], name=item['name'], description=item['description'],
                               mimeType=item['mimeType']) for item in content.list_resources()]

    @server.read_resource()
    async def read_resource(uri):
        value = await anyio.to_thread.run_sync(content.read_resource, ctx, str(uri))
        return [ReadResourceContents(content=_dumps(jsonable(value)), mime_type='application/json')]

    @server.list_prompts()
    async def list_prompts():
        return [types.Prompt(name=item['name'], title=item['title'], description=item['description'],
                             arguments=[types.PromptArgument(**arg) for arg in item['arguments']])
                for item in content.list_prompts()]

    @server.get_prompt()
    async def get_prompt(name, arguments):
        prompt = content.get_prompt(name, arguments)
        return types.GetPromptResult(description=prompt['description'], messages=[
            types.PromptMessage(role='user', content=types.TextContent(type='text', text=prompt['text']))])

    return server


def run_stdio(ctx):
    _sdk()
    import anyio
    from mcp.server.stdio import stdio_server

    server = build_server(ctx)

    async def main():
        async with stdio_server() as (read_stream, write_stream):
            await server.run(read_stream, write_stream, server.create_initialization_options())

    anyio.run(main)


class BearerAuth:
    """ASGI middleware: every HTTP request needs ``Authorization: Bearer <token>``.

    Comparison is constant-time over SHA-256 digests (so token length does not leak
    either). The 401 body is fixed text; the presented value is never logged.
    """

    def __init__(self, app, token):
        if not isinstance(token, str) or not token:
            raise ConfigError('HTTP transport refuses to start without a bearer token')
        self.app = app
        self._digest = hashlib.sha256(token.encode('utf-8')).digest()

    def authorized(self, headers):
        values = [v for k, v in headers if k.lower() == b'authorization']
        if len(values) != 1:
            return False
        scheme, _, presented = values[0].partition(b' ')
        if scheme.lower() != b'bearer' or not presented.strip():
            return False
        return hmac.compare_digest(hashlib.sha256(presented.strip()).digest(), self._digest)

    async def __call__(self, scope, receive, send):
        if scope['type'] == 'lifespan':
            await self.app(scope, receive, send)
            return
        if scope['type'] != 'http':  # websockets and anything else are not served
            if scope['type'] == 'websocket':
                await send({'type': 'websocket.close', 'code': 1008})
            return
        if not self.authorized(scope.get('headers') or []):
            LOG.warning('rejected unauthenticated %s request to %s', scope.get('method'), scope.get('path'))
            body = b'{"error":"unauthorized","message":"missing or invalid bearer token"}'
            await send({'type': 'http.response.start', 'status': 401, 'headers': [
                (b'content-type', b'application/json'), (b'content-length', str(len(body)).encode()),
                (b'www-authenticate', b'Bearer realm="garage-audio-ops"'), (b'cache-control', b'no-store')]})
            await send({'type': 'http.response.body', 'body': body})
            return
        await self.app(scope, receive, send)


def allowed_hosts(config):
    hosts = [f'127.0.0.1:{config.port}', f'localhost:{config.port}', f'[::1]:{config.port}']
    if not config.loopback_only():
        hosts.append(f'{config.host}:{config.port}')
    for host in config.allowed_hosts:
        if host not in hosts:
            hosts.append(host)
    return hosts


def build_http_app(ctx, token):
    """ASGI app: bearer check -> Starlette (only POST/GET/DELETE /mcp) -> SDK session manager."""
    if not token:
        raise ConfigError('HTTP transport refuses to start without a bearer token (set server.token_file or '
                          'GARAGE_OPS_MCP_TOKEN)')
    _sdk()
    import contextlib

    from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
    from mcp.server.transport_security import TransportSecuritySettings
    from starlette.applications import Starlette
    from starlette.routing import Route

    hosts = allowed_hosts(ctx.config)
    manager = StreamableHTTPSessionManager(
        build_server(ctx), json_response=True, stateless=True, max_request_body_size=MAX_REQUEST_BODY,
        security_settings=TransportSecuritySettings(
            enable_dns_rebinding_protection=True, allowed_hosts=hosts,
            allowed_origins=[f'http://{h}' for h in hosts] + [f'https://{h}' for h in hosts]))

    class Endpoint:
        async def __call__(self, scope, receive, send):
            await manager.handle_request(scope, receive, send)

    @contextlib.asynccontextmanager
    async def lifespan(_app):
        async with manager.run():
            yield

    app = Starlette(routes=[Route(MCP_PATH, endpoint=Endpoint())], lifespan=lifespan)
    return BearerAuth(app, token)


def run_http(ctx, token):
    app = build_http_app(ctx, token)
    try:
        import uvicorn
    except ImportError:
        raise DependencyMissing('uvicorn is not installed; install the pinned requirements') from None
    config = ctx.config
    if not config.loopback_only():
        LOG.warning('HTTP transport bound to non-loopback host %s; put it behind a private network or TLS proxy',
                    config.host)
    LOG.info('ops MCP HTTP transport on %s:%s%s (writes %s, playback %s)', config.host, config.port, MCP_PATH,
             'enabled' if config.allow_write else 'disabled',
             'enabled' if config.allow_write and config.allow_playback else 'disabled')
    uvicorn.run(app, host=config.host, port=config.port, access_log=False, proxy_headers=False,
                server_header=False, log_level='info')
