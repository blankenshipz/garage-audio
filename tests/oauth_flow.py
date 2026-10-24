"""Real HTTP SDK OAuth fixture, NOT Auth0/CIMD/login/consent acceptance.

SDK routes implement DCR, redirect and PKCE validation. This test-only provider
models pre-attended authorization, resource policy and single-use code storage.
It is deliberately not imported by any production module.
"""
import asyncio
import base64
from contextlib import asynccontextmanager
import hashlib
import json
from pathlib import Path
import secrets
import socket
import tempfile
import threading
import time
import unittest
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx
import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client
from mcp.server.auth.provider import AuthorizationCode, AuthorizeError, TokenError
from mcp.server.auth.routes import create_auth_routes
from mcp.server.auth.settings import ClientRegistrationOptions
from mcp.shared.auth import OAuthToken
from pydantic import AnyHttpUrl
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Host
import uvicorn

from garage_audio.remote_mcp import create_app

ISSUER = 'https://issuer.example/'
RESOURCE = 'https://connector.example/mcp'
CALLBACK = 'https://client.example/fixture-callback'


class SyntheticProvider:
    def __init__(self, key):
        self.key, self.clients, self.codes = key, {}, {}

    async def get_client(self, client_id):
        return self.clients.get(client_id)

    async def register_client(self, client_info):
        self.clients[client_info.client_id] = client_info

    async def authorize(self, client, params):
        if params.resource != RESOURCE or params.scopes != ['garage:read']:
            raise AuthorizeError('invalid_request', 'Exact fixture resource and scope required')
        code = secrets.token_urlsafe(32)
        self.codes[code] = AuthorizationCode(code=code, scopes=params.scopes,
            expires_at=time.time()+60,client_id=client.client_id,code_challenge=params.code_challenge,
            redirect_uri=params.redirect_uri,redirect_uri_provided_explicitly=True,resource=params.resource)
        return str(params.redirect_uri)+'?'+urlencode({'code':code,'state':params.state})

    async def load_authorization_code(self, client, code):
        return self.codes.get(code)

    async def load_refresh_token(self, client, token):
        return None  # SDK DCR schema requires this grant type; no fixture refresh is issued.

    async def exchange_authorization_code(self, client, code):
        if self.codes.pop(code.code, None) is None:
            raise TokenError('invalid_grant', 'Code already consumed')
        now = int(time.time())
        token = jwt.encode(dict(iss=ISSUER,aud=code.resource,sub='synthetic-attended-fixture',
                         client_id=client.client_id,scope='garage:read',iat=now,nbf=now-1,exp=now+60),
                         self.key,algorithm='RS256',headers={'kid':'fixture'})
        return OAuthToken(access_token=token,expires_in=60,scope='garage:read')


class OAuthFlow(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        key = rsa.generate_private_key(public_exponent=65537,key_size=2048)
        jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()))
        self.private('jwks',{'keys':[{**jwk,'kid':'fixture','alg':'RS256'}]})
        self.private('policy',{'allowed_clients':[],'revoked_token_sha256':[]})
        self.provider = SyntheticProvider(key)
        routes = create_auth_routes(self.provider,AnyHttpUrl(ISSUER),
                 client_registration_options=ClientRegistrationOptions(enabled=True,
                    valid_scopes=['garage:read'],default_scopes=['garage:read']))
        # The SDK token route does PKCE/redirect validation; resource binding is
        # provider policy, explicitly modeled here, NOT a claim about Auth0.
        token_route = next(r for r in routes if r.path == '/token')
        original = token_route.endpoint
        class ResourceToken:
            async def __call__(self, scope, receive, send):
                from starlette.requests import Request
                request = Request(scope, receive)
                body = await request.body()
                form = await request.form()
                if form.get('resource') != RESOURCE:
                    return await JSONResponse({'error':'invalid_target'},status_code=400)(scope, receive, send)
                async def replay_body():
                    return {'type':'http.request','body':body,'more_body':False}
                return await original(scope, replay_body, send)
        from starlette.routing import Route
        routes = [Route('/token',ResourceToken(),methods=['POST']) if r is token_route else r for r in routes]
        issuer_app = Starlette(routes=routes)
        class Upstream:
            def call(self, operation, args):
                assert (operation,args)==('status',{})
                return {'ok':True,'result':{'deployment_mode':'docker','audio':{'available':True,
                           'readback':{'state':'off','device_mode':'standby','readiness':'standby'}}}}
        resource_app = create_app(audience=RESOURCE,issuer=ISSUER,jwks_file=self.root/'jwks',
                         policy_file=self.root/'policy',source_revision='c'*40,upstream=Upstream())
        @asynccontextmanager
        async def lifespan(app):
            async with resource_app.router.lifespan_context(resource_app):
                yield
        app = Starlette(routes=[Host('issuer.example',issuer_app),Host('connector.example',resource_app)],
                        lifespan=lifespan)
        self.sock = socket.socket();self.sock.bind(('127.0.0.1',0))
        self.url = f'http://127.0.0.1:{self.sock.getsockname()[1]}'
        self.server = uvicorn.Server(uvicorn.Config(app,access_log=False,log_level='critical',proxy_headers=False))
        self.thread = threading.Thread(target=self.server.run,kwargs={'sockets':[self.sock]})
        self.thread.start()
        self.addAsyncCleanup(self.shutdown)
        for _ in range(100):
            if self.server.started:break
            await asyncio.sleep(.02)
        self.assertTrue(self.server.started)
        self.http = httpx.AsyncClient(base_url=self.url,headers={'Host':'issuer.example'})
        discovery = (await self.http.get('/.well-known/oauth-authorization-server')).json()
        self.assertIn('S256',discovery['code_challenge_methods_supported'])
        self.assertFalse(discovery.get('authorization_response_iss_parameter_supported',False))
        registration = await self.http.post(urlsplit(discovery['registration_endpoint']).path,
            json={'redirect_uris':[CALLBACK],'token_endpoint_auth_method':'none',
                  'grant_types':['authorization_code','refresh_token'],'response_types':['code'],'scope':'garage:read'})
        self.assertEqual(registration.status_code,201)
        self.client_id = registration.json()['client_id']
        self.private('policy',{'allowed_clients':[self.client_id],'revoked_token_sha256':[]})
        self.verifier = secrets.token_urlsafe(48)
        self.challenge = base64.urlsafe_b64encode(hashlib.sha256(self.verifier.encode()).digest()).decode().rstrip('=')

    async def shutdown(self):
        if hasattr(self,'http'):
            await self.http.aclose()
        self.server.should_exit = True
        await asyncio.to_thread(self.thread.join,10)
        self.assertFalse(self.thread.is_alive())
        self.sock.close();self.tmp.cleanup()

    def private(self, name, value):
        path=self.root/name;path.write_text(json.dumps(value));path.chmod(0o600)

    async def authorize(self, **changes):
        params=dict(response_type='code',client_id=self.client_id,redirect_uri=CALLBACK,
                    scope='garage:read',resource=RESOURCE,state='synthetic-state',
                    code_challenge=self.challenge,code_challenge_method='S256')
        params.update(changes)
        return await self.http.get('/authorize',params={k:v for k,v in params.items() if v is not None})

    async def code(self):
        result=await self.authorize()
        self.assertEqual(result.status_code,302)
        query=parse_qs(urlsplit(result.headers['location']).query)
        self.assertEqual(query['state'],['synthetic-state'])
        return query['code'][0]

    async def exchange(self, code, **changes):
        form=dict(grant_type='authorization_code',code=code,client_id=self.client_id,
                  redirect_uri=CALLBACK,code_verifier=self.verifier,resource=RESOURCE)
        form.update(changes)
        return await self.http.post('/token',data={k:v for k,v in form.items() if v is not None})

    async def test_registered_code_token_mcp_reconnect_and_replay(self):
        metadata=await self.http.get('/.well-known/oauth-protected-resource',headers={'Host':'connector.example'})
        self.assertEqual(metadata.json()['authorization_servers'],[ISSUER])
        code=await self.code();result=await self.exchange(code)
        self.assertEqual(result.status_code,200)
        token=result.json()['access_token']
        self.assertEqual((await self.exchange(code)).status_code,400)
        for _ in range(2):
            async with streamablehttp_client(self.url+'/mcp',headers={'Host':'connector.example',
                                  'Authorization':'Bearer '+token}) as (read,write,_):
                async with ClientSession(read,write) as client:
                    await client.initialize()
                    self.assertEqual(len((await client.list_tools()).tools),3)
                    result=await client.call_tool('garage_status',{})
                    self.assertEqual(json.loads(result.content[0].text)['result']['readiness'],'standby')

    async def test_pkce_resource_redirect_and_client_denials(self):
        for changes in ({'code_challenge_method':'plain'},{'resource':None},
                        {'resource':'https://wrong.example/mcp'},{'redirect_uri':'https://wrong.example/callback'},
                        {'client_id':'unsupported'}):
            result=await self.authorize(**changes)
            self.assertTrue(result.status_code==400 or 'error=' in result.headers.get('location',''))
        code=await self.code()
        for changes in ({'code_verifier':'wrong'*12},{'resource':None},
                        {'resource':'https://wrong.example/mcp'},{'redirect_uri':'https://wrong.example/callback'},
                        {'client_id':'unsupported'}):
            result=await self.exchange(code,**changes)
            self.assertIn(result.status_code,(400,401))
        self.assertEqual((await self.exchange(code)).status_code,200)


if __name__=='__main__':
    unittest.main()
