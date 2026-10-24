"""Resource-server checks with synthetic signed tokens, not live Auth0 proof."""
import hashlib
import json
import tempfile
import time
import unittest
from pathlib import Path

import httpx
import jwt
from cryptography.hazmat.primitives.asymmetric import rsa

from garage_audio.remote_mcp import create_app
from garage_audio.remote_oauth import OAuthVerifier

ISSUER = 'https://issuer.example/'
RESOURCE = 'https://connector.example/mcp'


class OAuthResource(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.jwks, self.policy = [Path(self.tmp.name)/name for name in ('jwks','policy')]
        self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(self.key.public_key()))
        self.write(self.jwks, {'keys':[{**jwk,'kid':'fixture','alg':'RS256','use':'sig'}]})
        self.write(self.policy, {'allowed_clients':['synthetic'], 'revoked_token_sha256':[]})
        self.verifier = OAuthVerifier(ISSUER, RESOURCE, self.jwks, self.policy)

    async def asyncTearDown(self):
        self.tmp.cleanup()

    def write(self, path, value):
        path.write_text(json.dumps(value));path.chmod(0o600)

    def token(self, **changes):
        now = int(time.time())
        claims = dict(iss=ISSUER,aud=RESOURCE,sub='synthetic-user',azp='synthetic',
                      iat=now,nbf=now-1,exp=now+300,scope='garage:read')
        claims.update(changes)
        return jwt.encode(claims, self.key, algorithm='RS256', headers={'kid':'fixture'})

    async def test_signed_claim_denials_and_policy(self):
        token = self.token()
        self.assertIsNotNone(await self.verifier.verify_token(token))
        for changes in ({'iss':'https://wrong.example/'}, {'aud':'https://wrong.example/mcp'},
                        {'exp':int(time.time())-1}, {'nbf':int(time.time())+60},
                        {'scope':'garage:write'}, {'azp':'unapproved'}, {'iat':int(time.time())+60}):
            self.assertIsNone(await self.verifier.verify_token(self.token(**changes)))
        forged = token.rsplit('.',1)[0]+'.'+'A'*342
        self.assertIsNone(await self.verifier.verify_token(forged))
        self.write(self.policy, {'allowed_clients':['synthetic'],
                               'revoked_token_sha256':[hashlib.sha256(token.encode()).hexdigest()]})
        self.assertIsNone(await self.verifier.verify_token(token))

    async def test_rotation_and_no_untrusted_key_fetch(self):
        token = self.token()
        self.write(self.jwks, {'keys':[]})
        self.assertIsNone(await self.verifier.verify_token(token))
        jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(self.key.public_key()))
        self.write(self.jwks, {'keys':[{**jwk,'kid':'fixture','alg':'RS256'}]})
        self.assertIsNotNone(await self.verifier.verify_token(token))
        supplied = jwt.encode({'exp':int(time.time())+300}, self.key, algorithm='RS256',
                              headers={'kid':'fixture','jku':'https://untrusted.example/jwks'})
        self.assertIsNone(await self.verifier.verify_token(supplied))
        self.jwks.chmod(0o644)
        self.assertIsNone(await self.verifier.verify_token(token))

    async def test_required_claims_algorithms_and_optional_nbf(self):
        claims = jwt.decode(self.token(), options={'verify_signature':False})
        for field in ('iss','aud','exp','iat','sub'):
            missing = {k:v for k,v in claims.items() if k != field}
            token = jwt.encode(missing,self.key,algorithm='RS256',headers={'kid':'fixture'})
            self.assertIsNone(await self.verifier.verify_token(token))
        claims.pop('nbf')  # Auth0 need not emit nbf; when present it must be enforced.
        token = jwt.encode(claims,self.key,algorithm='RS256',headers={'kid':'fixture'})
        self.assertIsNotNone(await self.verifier.verify_token(token))
        for algorithm in ('HS256','none'):
            token = jwt.encode(claims,'fixture-not-a-key' if algorithm=='HS256' else None,
                               algorithm=algorithm,headers={'kid':'fixture'})
            self.assertIsNone(await self.verifier.verify_token(token))

    async def test_public_discovery_challenge_and_mcp(self):
        class Upstream:
            def call(self, operation, args):
                assert (operation,args)==('status',{})
                return {'ok':True,'result':{'deployment_mode':'docker','audio':{'available':False}}}
        app = create_app(audience=RESOURCE,issuer=ISSUER,jwks_file=self.jwks,
                         policy_file=self.policy,source_revision='b'*40,upstream=Upstream())
        headers = {'Host':'connector.example','Accept':'application/json, text/event-stream',
                   'MCP-Protocol-Version':'2025-11-25'}
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app),base_url='http://private') as client:
                result = await client.get('/.well-known/oauth-protected-resource',headers=headers)
                self.assertEqual(result.status_code,200)
                self.assertEqual(result.json()['authorization_servers'],[ISSUER])
                self.assertEqual(result.json()['resource'],RESOURCE)
                result = await client.post('/mcp',headers=headers,json={})
                self.assertEqual(result.status_code,401)
                self.assertIn('resource_metadata="https://connector.example/.well-known/oauth-protected-resource"',
                              result.headers['www-authenticate'])
                headers['Authorization']='Bearer '+self.token()
                for method,params in [('initialize',{'protocolVersion':'2025-11-25','capabilities':{},
                              'clientInfo':{'name':'synthetic','version':'1'}}),('tools/list',{}),
                              ('tools/call',{'name':'garage_status','arguments':{}})]:
                    result = await client.post('/mcp',headers=headers,
                                               json={'jsonrpc':'2.0','id':1,'method':method,'params':params})
                    self.assertEqual(result.status_code,200)
                    self.assertIn('result',result.json())
                self.write(self.policy,{'allowed_clients':[],'revoked_token_sha256':[]})
                result = await client.post('/mcp',headers=headers,json={})
                self.assertEqual(result.status_code,401)


if __name__ == '__main__':
    unittest.main()
