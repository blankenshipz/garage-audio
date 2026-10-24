"""Real loopback MCP transport; synthetic upstream and ephemeral bearer grants."""
import asyncio
import hashlib
import json
import os
import re
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import tempfile
import time
import unittest

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

from garage_audio.remote_mcp import GrantVerifier, create_app, projection
from garage_audio.application import Application
from unittest.mock import patch

RESOURCE = 'https://connector.example/mcp'
REVISION = 'a' * 40


class Upstream:
    def call(self, operation, args):
        assert (operation, args) == ('status', {})
        return {'ok': True, 'result': {'deployment_mode': 'docker',
                'secret': 'LEAK_SENTINEL', 'audio': {'available': True,
                'readback': {'state': 'off', 'device_mode': 'standby', 'readiness': 'standby',
                             'private': 'LEAK_SENTINEL', 'physical_verified': True}}}}


class Connector(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.grants = Path(self.tmp.name) / 'grants.json'
        self.token = secrets.token_urlsafe(32)
        self.grant = dict(sha256=hashlib.sha256(self.token.encode()).hexdigest(),
                          client_id='synthetic', audience=RESOURCE, scopes=['garage:read'],
                          not_before=int(time.time())-1, expires_at=int(time.time())+300, revoked=False)
        self.write()
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0)); self.port = sock.getsockname()[1]
        self.process = subprocess.Popen([sys.executable, __file__, '--serve', str(self.grants), str(self.port)],
                                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.url = f'http://127.0.0.1:{self.port}/mcp'
        for _ in range(100):
            try:
                async with httpx.AsyncClient() as client:
                    await client.get(self.url)
                break
            except httpx.ConnectError:
                await asyncio.sleep(.05)
        else:
            self.process.terminate();self.process.wait();self.tmp.cleanup()
            self.fail('isolated connector did not start')

    async def asyncTearDown(self):
        self.process.terminate()
        self.process.wait(timeout=10)
        self.tmp.cleanup()

    def write(self, **changes):
        value = {**self.grant, **changes}
        self.grants.write_text(json.dumps({'version': 1, 'grants': [value]}))
        self.grants.chmod(0o600)

    def headers(self, token=None):
        return {'Host': 'connector.example', 'Authorization': 'Bearer '+(token or self.token)}

    async def session_check(self):
        async with streamablehttp_client(self.url, headers=self.headers()) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools = await session.list_tools()
                self.assertEqual({t.name for t in tools.tools},
                                 {'garage_status', 'garage_capabilities', 'garage_diagnostics'})
                for name in ('garage_status', 'garage_capabilities', 'garage_diagnostics'):
                    result = await session.call_tool(name, {})
                    text = result.model_dump_json()
                    self.assertNotIn('LEAK_SENTINEL', text)
                    self.assertIn(REVISION, text)
                    self.assertIn('observed_at', text)
                    value = json.loads(result.content[0].text)
                    self.assertEqual(value['source'], {'connector_revision':REVISION,
                                     'upstream_revision':None,'upstream_identity':'unverified'})
                resources = await session.list_resources()
                self.assertEqual([r.uri.unicode_string() for r in resources.resources], ['garage://remote/status'])
                result = await session.read_resource('garage://remote/status')
                self.assertNotIn('LEAK_SENTINEL', result.model_dump_json())
                for name, args in [('garage_operation', {'operation':'audio.stop','args':{}}),
                                   ('playback_play', {}), ('garage_status', {'command':'stop'})]:
                    result = await session.call_tool(name, args)
                    self.assertIn('read_only_operation_denied', result.model_dump_json())

    async def test_protocol_reconnect(self):
        await self.session_check()
        await self.session_check()

    async def test_authenticated_session_expiry_revocation(self):
        for change in ({'expires_at':int(time.time())-1}, {'revoked':True}):
            self.write()
            async with streamablehttp_client(self.url, headers=self.headers()) as (read, write, _):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    self.write(**change)
                    with self.assertRaises(Exception):
                        await session.call_tool('garage_status', {})
            async with httpx.AsyncClient() as client:
                for path in ('/mcp', '/unknown'):
                    for method, params in [('initialize', {}), ('resources/read', {'uri':'garage://remote/status'})]:
                        result = await client.post(self.url.replace('/mcp', path), headers=self.headers(),
                                               json={'jsonrpc':'2.0','id':1,'method':method,'params':params})
                        self.assertEqual(result.status_code, 401)

    async def test_bounded_audit_success_and_denials(self):
        await self.session_check()
        async with httpx.AsyncClient() as client:
            await client.post(self.url, headers={'Host':'connector.example'}, json={'private':'BODY_SENTINEL'})
            await client.post(self.url, headers=self.headers(), json={'jsonrpc':'2.0','id':'WIRE_SENTINEL',
                              'method':'tools/call','params':{'name':'NAME_SENTINEL','arguments':{'private':'BODY_SENTINEL'}}})
            await client.get(self.url.replace('/mcp','/PATH_SENTINEL'), headers=self.headers())
        text = (Path(self.tmp.name)/'audit.log').read_text()
        for value in (self.token, 'LEAK_SENTINEL', 'BODY_SENTINEL', 'WIRE_SENTINEL', 'NAME_SENTINEL', 'PATH_SENTINEL',
                      self.grant['sha256'], 'Authorization'):
            self.assertTrue(value not in text, 'private value found in audit')
        rows = []
        for line in text.splitlines():
            match = re.fullmatch(r'remote_mcp correlation=([0-9a-f]{32}) client=(synthetic|anonymous) '
                                 r'operation=(protocol|denied|garage_status|garage_capabilities|garage_diagnostics|resource_status) '
                                 r'result=(transport|denied|started|ok|upstream_unavailable) status=([1-5][0-9]{2})', line)
            self.assertIsNotNone(match, 'audit row not bounded')
            rows.append(match.groups())
        self.assertEqual(len({r[0] for r in rows}), len(rows))
        self.assertTrue(any(r[1:] == ('synthetic','garage_status','ok','200') for r in rows))
        self.assertTrue(any(r[1:] == ('synthetic','resource_status','ok','200') for r in rows))
        self.assertTrue(any(r[1:] == ('synthetic','denied','denied','200') for r in rows))
        self.assertTrue(any(r[1]=='anonymous' and r[-1]=='401' for r in rows))

    async def test_actual_application_status_shape(self):
        with patch.dict(os.environ, {'GARAGE_DEPLOYMENT_MODE':'docker'}):
            app = Application(Path(self.tmp.name)/'app')
            with patch.object(app, 'policy', return_value={'state':'paused','device_mode':'hardware',
                              'readiness':'paused','private':'LEAK_SENTINEL'}):
                value = projection(app.execute('status', {}))
        self.assertTrue(value['ok'])
        self.assertEqual(value['result'], {'deployment_mode':'docker','audio_available':True,
                         'state':'paused','device_mode':'hardware','readiness':'paused',
                         'physical_audio_verified':False})

    async def test_denials_and_revocation(self):
        async with httpx.AsyncClient() as client:
            for headers in ({'Host':'connector.example'}, self.headers('x'*43)):
                result = await client.post(self.url, headers=headers, json={})
                self.assertEqual(result.status_code, 401)
                self.assertTrue(self.token not in result.text, 'credential found in response')
            for change in ({'expires_at':int(time.time())-1}, {'not_before':int(time.time())+60},
                           {'audience':'https://wrong.example/mcp'}, {'scopes':['garage:write']},
                           {'revoked':True}):
                self.write(**change)
                result = await client.post(self.url, headers=self.headers(), json={})
                self.assertEqual(result.status_code, 401)
            self.write()
            result = await client.post(self.url, headers={**self.headers(), 'Host':'evil.example'}, json={})
            self.assertEqual(result.status_code, 421)
            result = await client.post(self.url, headers={**self.headers(), 'Origin':'https://evil.example'}, json={})
            self.assertEqual(result.status_code, 403)
            self.grants.write_text('{}')
            result = await client.post(self.url, headers=self.headers(), json={})
            self.assertEqual(result.status_code, 401)

    async def test_verifier_private_file_and_projection(self):
        verifier = GrantVerifier(str(self.grants), RESOURCE)
        self.assertIsNotNone(await verifier.verify_token(self.token))
        self.grants.chmod(0o644)
        self.assertIsNone(await verifier.verify_token(self.token))
        self.grants.unlink(); self.grants.symlink_to('/nonexistent')
        self.assertIsNone(await verifier.verify_token(self.token))
        self.assertNotIn('LEAK_SENTINEL', json.dumps(projection(Upstream().call('status', {}))))
        for value in ({'ok':False,'error':{'message':'LEAK_SENTINEL'}}, None, {'ok':True,'result':[]}):
            self.assertNotIn('LEAK_SENTINEL', json.dumps(projection(value)))


if __name__ == '__main__':
    if len(sys.argv)>1 and sys.argv[1]=='--serve':
        import uvicorn
        import logging
        logger = logging.getLogger('garage.remote.audit')
        logger.setLevel(logging.INFO)
        logger.addHandler(logging.FileHandler(Path(sys.argv[2]).parent/'audit.log'))
        uvicorn.run(create_app(audience=RESOURCE, grant_file=sys.argv[2], source_revision=REVISION,
                               upstream=Upstream()), host='127.0.0.1', port=int(sys.argv[3]),
                    access_log=False, log_level='critical', proxy_headers=False)
    else:
        unittest.main()
