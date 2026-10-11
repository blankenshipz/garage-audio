"""Separate browser possession realm, owned exclusively by native controller.

Public code is an identifier, NEVER a credential. Only root-only local pair
operation may confirm a genuinely user-returned code. No HTTP pairing endpoint.
No passwords. Restart discards all browser sessions/actions; no autoresume.
"""
import hashlib
import secrets
import threading
import time

def digest(value):
    if not isinstance(value,str) or not 20<=len(value)<=128:
        raise PermissionError('separate browser possession required')
    return hashlib.sha256(value.encode('ascii')).hexdigest()

class BrowserRealm:
    def __init__(self,clock=time.monotonic):
        self.clock=clock;self.sessions={};self.lock=threading.RLock()
    def prune(self):
        self.sessions={k:v for k,v in self.sessions.items() if v['expires']>self.clock()}
    def create(self):
        with self.lock:
            self.prune()
            if len(self.sessions)>=32:raise PermissionError('browser session limit')
            cookie=secrets.token_urlsafe(32);csrf=secrets.token_urlsafe(32)
            code=secrets.token_hex(6).upper()
            self.sessions[digest(cookie)]={'csrf':digest(csrf),'code':code,'paired':False,
                'expires':self.clock()+600,'action':None}
            return {'cookie':cookie,'csrf':csrf,'code':code,'expires_in':600}
    def current(self,cookie,csrf=None):
        self.prune();session=self.sessions.get(digest(cookie))
        if session is None:raise PermissionError('browser session expired')
        if csrf is not None and not secrets.compare_digest(digest(csrf),session['csrf']):
            raise PermissionError('separate speaker CSRF required')
        return session
    def resume(self,cookie):
        with self.lock:
            s=self.current(cookie);csrf=secrets.token_urlsafe(32)
            s['csrf']=digest(csrf);s['action']=None
            return {'csrf':csrf,**self.status(cookie)}
    def pair(self,code):
        # Caller is admitted separately over root-only administration IPC.
        with self.lock:
            self.prune()
            matches=[v for v in self.sessions.values() if v['code']==code and not v['paired']]
            if len(matches)!=1:raise PermissionError('pending browser code unavailable')
            matches[0]['paired']=True;matches[0]['code']=None
            matches[0]['action']=None
            return {'paired':True,'sound_granted':False}
    def status(self,cookie):
        with self.lock:
            s=self.current(cookie)
            return {'paired':s['paired'],'code':s['code'],'expires_in':max(0,int(s['expires']-self.clock()))}
    def action(self,cookie,csrf,binding):
        with self.lock:
            s=self.current(cookie,csrf)
            if not s['paired']:raise PermissionError('user confirmation of this browser required')
            nonce=secrets.token_urlsafe(32)
            s['action']={'hash':digest(nonce),'binding':binding.copy(),'expires':self.clock()+30}
            return nonce
    def consume(self,cookie,csrf,nonce,binding):
        with self.lock:
            s=self.current(cookie,csrf);action=s['action'];s['action']=None
            if not s['paired'] or not action or action['expires']<=self.clock() or action['binding']!=binding or not secrets.compare_digest(action['hash'],digest(nonce)):
                raise PermissionError('fresh one-use prepared-generation approval required')
            return True
