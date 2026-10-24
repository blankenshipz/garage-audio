"""Auth0-compatible resource-server verification, not an identity/consent server."""
import hashlib
import json
import os
import stat
import time
from urllib.parse import urlsplit

import jwt
from mcp.server.auth.provider import AccessToken

from .remote_mcp import SCOPE


def private_json(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd) as source:
        info = os.fstat(source.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid not in (0, os.geteuid())
                or stat.S_IMODE(info.st_mode) != 0o600 or info.st_size > 65536):
            raise ValueError('Invalid verifier input')
        return json.loads(source.read(65537))


class OAuthVerifier:
    """Pinned operator-supplied public JWKS; no token-directed network access.

    Reload keys and deny policy each request. Rotation is an atomic pinned-file
    update by the approved deployment owner, never a jku/x5u/discovery URL fetch.
    """
    def __init__(self, issuer, audience, jwks_file, policy_file):
        parsed = urlsplit(issuer)
        if (parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password
                or parsed.path != '/' or parsed.query or parsed.fragment):
            raise ValueError('Exact HTTPS issuer with trailing slash required')
        self.issuer, self.audience = issuer, audience
        self.jwks_file, self.policy_file = jwks_file, policy_file

    async def verify_token(self, token):
        try:
            if not isinstance(token, str) or len(token) > 16384:
                return None
            header = jwt.get_unverified_header(token)
            if (header.get('alg') != 'RS256' or not isinstance(header.get('kid'), str)
                    or len(header['kid']) > 128 or any(k in header for k in ('jku','x5u','jwk','crit'))):
                return None
            keys = private_json(self.jwks_file)['keys']
            if not isinstance(keys, list) or len(keys) > 16:
                return None
            matches = [k for k in keys if isinstance(k, dict) and k.get('kid') == header['kid']]
            if len(matches) != 1:
                return None
            key = matches[0]
            if (key.get('kty') != 'RSA' or key.get('use','sig') != 'sig'
                    or key.get('alg','RS256') != 'RS256' or any(k in key for k in ('d','p','q'))):
                return None
            public = jwt.PyJWK.from_dict(key, algorithm='RS256').key
            if public.key_size < 2048:
                return None
            claims = jwt.decode(token, public, algorithms=['RS256'], audience=self.audience,
                                issuer=self.issuer, options={'require':['iss','aud','exp','iat','sub']})
            if (type(claims['exp']) is not int or type(claims['iat']) is not int
                    or claims['exp'] <= time.time() or claims['iat'] > time.time()
                    or ('nbf' in claims and type(claims['nbf']) is not int)):
                return None
            scopes = claims.get('scope')
            client = claims.get('azp', claims.get('client_id'))
            policy = private_json(self.policy_file)
            if (set(policy) != {'allowed_clients','revoked_token_sha256'}
                    or not isinstance(policy['allowed_clients'], list)
                    or not isinstance(policy['revoked_token_sha256'], list)
                    or len(policy['allowed_clients']) > 128 or len(policy['revoked_token_sha256']) > 512
                    or not isinstance(client, str) or client not in policy['allowed_clients']
                    or not isinstance(scopes, str) or SCOPE not in scopes.split()
                    or hashlib.sha256(token.encode()).hexdigest() in policy['revoked_token_sha256']):
                return None
            # Audit an opaque bounded client identity, never URL/email/sub claims.
            identity = 'oauth_' + hashlib.sha256(client.encode()).hexdigest()[:24]
            return AccessToken(token=token, client_id=identity, scopes=[SCOPE],
                               expires_at=claims['exp'], resource=self.audience)
        except Exception:
            return None
