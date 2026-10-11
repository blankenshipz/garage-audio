"""Separate operator-provisioned verifier. No enrollment, bootstrap promotion or bearer grants."""
from dataclasses import dataclass
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import threading
import time

@dataclass(frozen=True)
class HumanIdentity:
    user: str
    realm: str
    version: int
    verifier_hash: str
    session: str
    authenticated_at: float
    expires_at: float
    authenticated_monotonic: float

class HumanVerifier:
    def __init__(self, state, reference=None, automation_reference=None):
        self.state = Path(state).resolve()
        self.reference = reference
        self.automation_reference = automation_reference
        self.sessions = {}; self.lock = threading.Lock()

    def load(self):
        if not self.reference: raise ValueError('human verifier not provisioned')
        p = Path(self.reference)
        if p.is_symlink() or not p.is_file() or p.stat().st_uid != os.getuid() or p.stat().st_mode & 0o077:
            raise ValueError('human verifier must be an operator-owned private regular file')
        if p.parent.is_symlink() or p.parent.stat().st_uid != os.getuid() or p.parent.stat().st_mode & 0o077:
            raise ValueError('human verifier directory must be separate and private')
        resolved = p.resolve()
        if resolved.is_relative_to(self.state): raise ValueError('human verifier cannot be in application state')
        if self.automation_reference and resolved.is_relative_to(Path(self.automation_reference).resolve().parent):
            raise ValueError('human verifier cannot share automation credential directory')
        if p.stat().st_size > 4096: raise ValueError('bounded verifier required')
        raw = p.read_bytes(); value = json.loads(raw)
        if set(value) != {'user','realm','version','salt','password_hash','iterations'}: raise ValueError('invalid verifier')
        for key in ('user','realm'):
            if not isinstance(value[key],str) or not re.fullmatch(r'[a-zA-Z0-9_.-]{1,80}',value[key]): raise ValueError('invalid identity')
        if type(value['version']) is not int or value['version'] < 1: raise ValueError('invalid credential version')
        if type(value['iterations']) is not int or not 200000 <= value['iterations'] <= 1000000: raise ValueError('invalid work factor')
        if not re.fullmatch('(?:[a-f0-9]{2}){16,32}',value['salt']) or not re.fullmatch('[a-f0-9]{64}',value['password_hash']):
            raise ValueError('invalid verifier encoding')
        return value, hashlib.sha256(raw).hexdigest()

    def authenticate(self, user, password, session):
        value, digest = self.load()
        if not isinstance(password,str) or not 16 <= len(password) <= 1024 or user != value['user']:
            raise ValueError('human authentication failed')
        calculated = hashlib.pbkdf2_hmac('sha256',password.encode(),bytes.fromhex(value['salt']),value['iterations']).hex()
        if not hmac.compare_digest(calculated,value['password_hash']): raise ValueError('human authentication failed')
        now = time.time()
        identity = HumanIdentity(user,value['realm'],value['version'],digest,session,now,now+300,time.monotonic())
        with self.lock:
            self.sessions = {k:v for k,v in self.sessions.items() if v.expires_at > now}
            if len(self.sessions) >= 64 and session not in self.sessions: raise ValueError('human session limit')
            self.sessions[session] = identity
        return identity

    def revoke(self, session):
        with self.lock: self.sessions.pop(session,None)

    def current(self, identity, fresh=False):
        if not isinstance(identity,HumanIdentity): return False
        with self.lock:
            if self.sessions.get(identity.session) != identity: return False
        try: value,digest = self.load()
        except (OSError,ValueError,TypeError): return False
        now = time.time()
        age = time.monotonic()-identity.authenticated_monotonic
        return (0 <= age < 300 and now < identity.expires_at and now >= identity.authenticated_at and
                (not fresh or (age <= 60 and now-identity.authenticated_at <= 60)) and
                (identity.user,identity.realm,identity.version,identity.verifier_hash) ==
                (value['user'],value['realm'],value['version'],digest))
