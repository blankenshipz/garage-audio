"""Ops MCP streamable HTTP transport: bearer auth (401 on missing/wrong token), refusal without a token,
DNS-rebinding host check and an authenticated MCP round trip. In-process ASGI only (no socket is opened).

Needs the pinned MCP SDK (mcp==1.30.0, which brings starlette/httpx); skipped when it is not installed.
"""
import asyncio
import importlib.util
import json
import logging
import unittest
from pathlib import Path

from garage_audio.ops_mcp import transport
from garage_audio.ops_mcp.errors import ConfigError

_spec = importlib.util.spec_from_file_location('ops_mcp_fakes', Path(__file__).parent / 'fixtures/ops_mcp/fakes.py')
fakes = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fakes)

HAVE_SDK = all(importlib.util.find_spec(m) for m in ('mcp', 'starlette', 'httpx'))
TOKEN = fakes.TOKEN
ACCEPT = 'application/json, text/event-stream'


def rpc(method, params=None, ident=1):
    return {'jsonrpc': '2.0', 'id': ident, 'method': method, 'params': params or {}}


INIT = rpc('initialize', {'protocolVersion': '2025-06-18', 'capabilities': {},
                          'clientInfo': {'name': 'ops-mcp-test', 'version': '0'}})


class Capture(logging.Handler):
    def __init__(self):
        super().__init__(logging.DEBUG)
        self.messages = []

    def emit(self, record):
        self.messages.append(record.getMessage())


class NoToken(unittest.TestCase):
    def test_build_refuses_without_token(self):
        h = fakes.Harness(self)
        for token in (None, ''):
            with self.subTest(token=token), self.assertRaises(ConfigError) as caught:
                transport.build_http_app(h.ctx, token)
            self.assertEqual(caught.exception.code, 'config_invalid')
        with self.assertRaises(ConfigError):
            transport.BearerAuth(object(), '')

    def test_middleware_without_sdk(self):
        """The bearer check itself is stdlib: exercise it directly with a stub app."""
        calls = []

        async def app(scope, receive, send):
            calls.append(scope['path'])
            await send({'type': 'http.response.start', 'status': 200, 'headers': []})
            await send({'type': 'http.response.body', 'body': b'ok'})

        auth = transport.BearerAuth(app, TOKEN)

        def run(headers, scope_type='http'):
            sent = []

            async def send(message):
                sent.append(message)

            async def receive():
                return {'type': 'http.request', 'body': b''}
            asyncio.run(auth({'type': scope_type, 'path': '/mcp', 'method': 'POST', 'headers': headers}, receive, send))
            return sent

        ok = run([(b'authorization', f'Bearer {TOKEN}'.encode())])
        self.assertEqual(ok[0]['status'], 200)
        for headers in ([], [(b'authorization', b'Bearer wrong-token-' + b'y' * 32)],
                        [(b'authorization', f'Basic {TOKEN}'.encode())], [(b'authorization', b'Bearer ')],
                        [(b'authorization', f'Bearer {TOKEN}'.encode())] * 2,
                        [(b'authorization', f'Bearer {TOKEN}x'.encode())]):
            with self.subTest(headers=headers):
                sent = run(headers)
                self.assertEqual(sent[0]['status'], 401)
                self.assertNotIn(TOKEN.encode(), sent[1]['body'])
        self.assertEqual(calls, ['/mcp'])
        closed = run([(b'authorization', f'Bearer {TOKEN}'.encode())], 'websocket')
        self.assertEqual(closed, [{'type': 'websocket.close', 'code': 1008}])


@unittest.skipUnless(HAVE_SDK, 'MCP SDK not installed in this interpreter (pip install -r requirements.txt)')
class Http(unittest.TestCase):
    def setUp(self):
        from starlette.testclient import TestClient
        self.h = fakes.Harness(self)
        self.h.recovery_state('ready')
        self.capture = Capture()
        root = logging.getLogger()
        root.addHandler(self.capture)
        self.addCleanup(root.removeHandler, self.capture)
        self.client = TestClient(transport.build_http_app(self.h.ctx, TOKEN), base_url='http://127.0.0.1:8766')
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)

    def post(self, body, token=TOKEN, path='/mcp', **headers):
        headers = {'accept': ACCEPT, 'content-type': 'application/json', **headers}
        if token is not None:
            headers['authorization'] = f'Bearer {token}'
        return self.client.post(path, content=json.dumps(body), headers=headers)

    def tearDown(self):
        self.assertFalse([m for m in self.capture.messages if TOKEN in m], 'token leaked into logs')

    def test_missing_and_wrong_token_get_401(self):
        for token in (None, 'not-the-token-' + 'z' * 40, TOKEN[:-1]):
            with self.subTest(token=token):
                response = self.post(INIT, token=token)
                self.assertEqual(response.status_code, 401)
                self.assertIn('Bearer', response.headers['www-authenticate'])
                self.assertNotIn(TOKEN, response.text)
        # Auth runs before routing: unknown paths are 401 too without a token.
        self.assertEqual(self.client.get('/anything').status_code, 401)
        self.assertEqual(self.client.get('/anything', headers={'authorization': f'Bearer {TOKEN}'}).status_code, 404)

    def test_correct_token_round_trip(self):
        response = self.post(INIT)
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()['result']
        self.assertEqual(result['serverInfo']['name'], 'garage-audio-ops')
        self.assertIn('Start with get_status', result['instructions'])
        listed = self.post(rpc('tools/list', ident=2), **{'mcp-protocol-version': '2025-06-18'})
        self.assertEqual(listed.status_code, 200, listed.text)
        names = {t['name']: t for t in listed.json()['result']['tools']}
        self.assertEqual(len(names), 37)
        self.assertFalse(names['dsp_set_main_volume']['_meta']['garage-audio/enabled'])
        status = self.post(rpc('tools/call', {'name': 'get_status', 'arguments': {}}, 3),
                           **{'mcp-protocol-version': '2025-06-18'}).json()['result']
        self.assertFalse(status['isError'])
        self.assertEqual(status['structuredContent']['recovery']['state'], 'ready')
        refused = self.post(rpc('tools/call', {'name': 'dsp_set_main_volume', 'arguments': {'volume_db': -30}}, 4),
                            **{'mcp-protocol-version': '2025-06-18'}).json()['result']
        self.assertTrue(refused['isError'])
        self.assertEqual(refused['structuredContent']['error']['code'], 'write_disabled')
        # The refused write never reached the DSP: no config, volume or mute change was sent.
        self.assertNotIn('set_config', self.h.camilla.events)
        self.assertNotIn('validate', self.h.camilla.events)
        self.assertFalse([e for e in self.h.camilla.events if isinstance(e, tuple)])
        resources = self.post(rpc('resources/list', ident=5), **{'mcp-protocol-version': '2025-06-18'})
        self.assertEqual(len(resources.json()['result']['resources']), 3)
        prompts = self.post(rpc('prompts/list', ident=6), **{'mcp-protocol-version': '2025-06-18'})
        self.assertEqual(len(prompts.json()['result']['prompts']), 3)

    def test_foreign_host_header_rejected(self):
        response = self.post(INIT, host='attacker.example.invalid')
        self.assertIn(response.status_code, (400, 403, 421))


if __name__ == '__main__':
    unittest.main()
