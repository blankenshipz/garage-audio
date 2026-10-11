"""Authenticated loopback/private-LAN dashboard; no raw control endpoints."""
import argparse
import hmac
import ipaddress
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
import os
import re
from pathlib import Path
import secrets
import threading
import time
from urllib.parse import urlsplit, unquote
from .application import Application, private_token
from .measurement_jobs import JobFailure
from .speaker_controller import request as speaker_request

def credential_equal(candidate, expected):
    # compare_digest(str,str) raises on non-ASCII. Reject rather than crash a
    # request thread; Unicode/escaped Unicode is never a valid generated token.
    return isinstance(candidate,str) and candidate.isascii() and len(candidate)<=4096 and hmac.compare_digest(candidate,expected)

PRIVATE_NETWORKS = tuple(ipaddress.IPv4Network(n) for n in ('10.0.0.0/8','172.16.0.0/12','192.168.0.0/16'))

def validate_authority(authority):
    """Canonical exact localhost/loopback or RFC1918 IPv4 host:port only.

    Configuration is operator-owned, not supplied by a request. This does not
    publish any interface or grant wildcard access; deployment must bind only
    its verified private address. Forwarded headers never select an authority.
    """
    if not isinstance(authority,str): raise ValueError('invalid configured authority')
    match=re.fullmatch(r'(localhost|[0-9]+(?:\.[0-9]+){3}):([1-9][0-9]{0,4})',authority)
    if not match or int(match[2]) > 65535:
        raise ValueError('exact canonical private IPv4 or localhost host:port required')
    host=match[1]
    if host not in ('localhost','127.0.0.1'):
        try: address=ipaddress.IPv4Address(host)
        except ipaddress.AddressValueError:
            raise ValueError('invalid private IPv4 authority') from None
        if not any(address in network for network in PRIVATE_NETWORKS):
            raise ValueError('only RFC1918 private IPv4 authorities are supported')

class Server(ThreadingHTTPServer):
    def __init__(self, address, app, token, web, allowed_authorities=None, *, trusted_lan_authority=None):
        super().__init__(address, Handler)
        self.app, self.token, self.web = app, token, Path(web).resolve()
        self.app.human_approval_available = self.server_address[0] == '127.0.0.1'
        port = self.server_address[1]
        self.authorities = set(allowed_authorities or [f'127.0.0.1:{port}',f'localhost:{port}'])
        for authority in self.authorities:
            try: validate_authority(authority)
            except ValueError:
                self.server_close()
                raise
        self.sessions = {}
        self.trusted_lan_authority=trusted_lan_authority
        if trusted_lan_authority:
            validate_authority(trusted_lan_authority)
            if trusted_lan_authority not in self.authorities or trusted_lan_authority.split(':')[0] in ('localhost','127.0.0.1'):
                self.server_close();raise ValueError('trusted LAN mode requires one explicit accepted private LAN authority')
        self.lan_admissions=[]
        self.human_sessions = {}
        self.session_lock = threading.Lock()

    def server_close(self):
        super().server_close()
        # The SDK store pins a private directory descriptor; release it on HTTP
        # teardown. No model/provider or sound operation is involved.
        harness = getattr(getattr(self,'app',None),'agent_harness',None)
        if harness is not None: harness.close()

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass  # URLs, tokens and bodies must not enter request logs.

    def send(self, status, value, cookie=None):
        body = json.dumps(value,allow_nan=False).encode()
        self.send_response(status)
        self.send_header('Content-Type','application/json')
        self.send_header('Content-Length',str(len(body)))
        self.security_headers()
        if cookie:
            for value in ([cookie] if isinstance(cookie,str) else cookie):self.send_header('Set-Cookie',value)
        self.end_headers(); self.wfile.write(body)

    def security_headers(self):
        self.send_header('Cache-Control','no-store')
        self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('Referrer-Policy','no-referrer')
        self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data: blob:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")

    def error(self,status,code,message):
        self.send(status,{'ok':False,'result':None,'error':{'code':code,'message':message,'requirements':[]}})

    def boundary(self, post=False):
        host = self.headers.get('Host','')
        if len(self.headers.get_all('Host',[]))!=1 or host not in self.server.authorities or self.path != urlsplit(self.path).path:
            self.error(403,'host_rejected','Exact configured authority required'); return False
        origin = self.headers.get('Origin')
        if len(self.headers.get_all('Origin',[]))>1 or (post and origin != 'http://'+host) or (origin and origin != 'http://'+host):
            self.error(403,'origin_rejected','Same origin required'); return False
        if self.headers.get('Sec-Fetch-Site')=='cross-site':
            self.error(403,'origin_rejected','Cross-site requests rejected');return False
        return True

    def trusted_lan(self):
        return bool(self.server.trusted_lan_authority and self.headers.get('Host')==self.server.trusted_lan_authority)

    def session(self):
        cookies = SimpleCookie()
        try: cookies.load(self.headers.get('Cookie',''))
        except Exception: return None
        sid = cookies.get('garage_session')
        with self.server.session_lock:
            now = time.time()
            self.server.sessions = {k:v for k,v in self.server.sessions.items() if v['expires']>now}
            session=self.server.sessions.get(sid.value) if sid else None
        if session and session.get('access_mode')=='trusted-lan' and not self.trusted_lan():return None
        return session

    def do_GET(self):
        if not self.boundary(): return
        if self.path == '/api/human/session':
            session = self.human_session()
            if not session: self.error(401,'human_authentication_required','Separate human authentication required'); return
            identity = session['identity']
            self.send(200,{'ok':True,'result':{'user':identity.user,'realm':identity.realm,'role':'human',
                'csrf':session['csrf'],'expires_at':identity.expires_at,'fresh_until':identity.authenticated_at+60},'error':None}); return
        if self.path.startswith('/api/'):
            session = self.session()
            cookie=None
            if not session and self.path=='/api/session' and self.trusted_lan():
                with self.server.session_lock:
                    now=time.time();self.server.sessions={k:v for k,v in self.server.sessions.items() if v['expires']>now}
                    self.server.lan_admissions=[t for t in self.server.lan_admissions if t>now-60]
                    if len(self.server.sessions)>=128 or len(self.server.lan_admissions)>=60:
                        self.error(429,'session_limit','Please wait before opening another session');return
                    sid,csrf=secrets.token_urlsafe(32),secrets.token_urlsafe(32)
                    session={'csrf':csrf,'expires':now+3600,'role':'automation','access_mode':'trusted-lan'}
                    self.server.sessions[sid]=session;self.server.lan_admissions.append(now)
                    cookie='garage_session='+sid+'; HttpOnly; SameSite=Strict; Path=/; Max-Age=3600'
            if not session: self.error(401,'authentication_required','Enter the private bootstrap token'); return
            if self.path == '/api/session': self.send(200,{'ok':True,'result':{'csrf':session['csrf'],'role':'automation','access_mode':'trusted-lan' if self.trusted_lan() else 'token'},'error':None},cookie)
            elif self.path == '/api/speakers/status':
                try:self.send(200,{'ok':True,'result':speaker_request({'cmd':'status','cookie':self.speaker_cookie()}),'error':None})
                except (OSError,RuntimeError,ValueError,PermissionError):self.error(409,'speakers_unavailable','Speaker controller or browser session unavailable')
            elif self.path == '/api/status': self.send(200,self.server.app.execute('status',{}))
            else: self.error(404,'not_found','Unknown endpoint')
            return
        relative = 'index.html' if self.path == '/' else unquote(self.path.removeprefix('/assets/'))
        if self.path != '/' and not self.path.startswith('/assets/'):
            self.error(404,'not_found','Unknown asset'); return
        p = (self.server.web/relative).resolve()
        if not p.is_relative_to(self.server.web) or not p.is_file() or p.suffix not in ('.html','.css','.js','.svg','.png','.ico'):
            self.error(404,'not_found','Unknown asset'); return
        body = p.read_bytes()
        if self.path=='/' and self.trusted_lan():
            body=body.replace(b'<body>',b'<body data-trusted-lan="true">').replace(b'<section id="login-panel"',b'<section hidden id="login-panel"')
        self.send_response(200);self.security_headers()
        self.send_header('Content-Type',mimetypes.guess_type(p.name)[0] or 'application/octet-stream')
        self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)

    def do_POST(self):
        if not self.boundary(True): return
        if self.path.startswith('/api/human/') and not self.human_local():
            self.error(403,'confidential_origin_required','Human authority disabled on shared/wildcard/container listener; do not send human credentials'); return
        try:
            length = int(self.headers.get('Content-Length','0'))
            if not 0 < length <= 1048576 or self.headers.get('Transfer-Encoding') or self.headers.get('Content-Type','').split(';')[0] != 'application/json': raise ValueError()
            body = json.loads(self.rfile.read(length),parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
            if not isinstance(body,dict): raise ValueError()
        except (ValueError,UnicodeError):
            self.error(400,'invalid_request','Bounded JSON object required');return
        if self.path.startswith('/api/human/'):
            self.human_post(body); return
        if self.path == '/api/login':
            if set(body) != {'token'} or not credential_equal(body['token'],self.server.token):
                self.error(401,'authentication_required','Invalid bootstrap credential');return
            sid, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
            with self.server.session_lock:
                if len(self.server.sessions)>128: self.server.sessions.clear()
                self.server.sessions[sid]={'csrf':csrf,'expires':time.time()+3600,'role':'automation'}
            self.send(200,{'ok':True,'result':{'csrf':csrf,'role':'automation'},'error':None},'garage_session='+sid+'; HttpOnly; SameSite=Strict; Path=/; Max-Age=3600');return
        session = self.session()
        if not session:
            self.error(401,'authentication_required','Session required');return
        if not credential_equal(self.headers.get('X-CSRF-Token',''),session['csrf']):
            self.error(403,'csrf_rejected','CSRF token required');return
        if self.path == '/api/logout':
            # Narrow speaker cookie is not sent to /api/logout. The automation
            # session remembers only its own associated possession for revoke.
            if session.get('speaker_cookie'):
                try:speaker_request({'cmd':'revoke','cookie':session['speaker_cookie']})
                except (OSError,RuntimeError,PermissionError,ValueError):
                    self.error(409,'speaker_revocation_unverified','Logout not completed: keep dashboard open and press Stop.');return
            self.server.app.jobs.invalidate_all('automation_logout')
            with self.server.session_lock:
                self.server.sessions = {k:v for k,v in self.server.sessions.items() if v is not session}
            self.send(200,{'ok':True,'result':{'logged_out':True},'error':None},['garage_session=; Max-Age=0; Path=/; HttpOnly; SameSite=Strict','garage_speakers_v1=; Max-Age=0; Path=/api/speakers; HttpOnly; SameSite=Strict']);return
        if self.path.startswith('/api/speakers/'):
            self.speaker_post(body);return
        if self.path != '/api/operations':
            self.error(404,'not_found','Unknown endpoint');return
        if set(body) != {'operation','args'}:
            self.error(400,'invalid_request','Expected operation and args');return
        self.send(200,self.server.app.execute(body['operation'],body['args']))

    def speaker_cookie(self):
        raw=self.headers.get('Cookie','')
        # Cookie scope ignores ports on the same IP. Never choose between
        # duplicate narrow/broad-path cookies; reject ambiguous possession.
        if len(self.headers.get_all('Cookie',[]))!=1 or len(re.findall(r'(?:^|;)\s*garage_speakers_v1=',raw))!=1:
            raise PermissionError('exact separate speaker cookie required')
        cookies=SimpleCookie();cookies.load(raw)
        return cookies['garage_speakers_v1'].value

    def speaker_post(self,body):
        command=self.path.removeprefix('/api/speakers/')
        if command not in ('session','setup','stop','action','enable') or set(body)!=({'action'} if command=='enable' else set()):
            self.error(400,'invalid_request','Exact speaker operation required');return
        try:
            cookie=None
            if command=='session':
                try:result=speaker_request({'cmd':'resume','cookie':self.speaker_cookie()})
                except (PermissionError,RuntimeError):
                    result=speaker_request({'cmd':'session'});cookie=result.pop('cookie')
                ordinary=self.session()
                if ordinary is None:raise PermissionError('automation session expired')
                with self.server.session_lock:ordinary['speaker_cookie']=cookie or self.speaker_cookie()
            else:
                payload={'cmd':command} if command=='stop' else {'cmd':command,'cookie':self.speaker_cookie(),'csrf':self.headers.get('X-Speaker-CSRF','')}
                if command=='enable':payload['action']=body['action']
                result=speaker_request(payload)
            header=('garage_speakers_v1='+cookie+'; HttpOnly; SameSite=Strict; Path=/api/speakers; Max-Age=600') if cookie else None
            self.send(200,{'ok':True,'result':result,'error':None},header)
        except (OSError,RuntimeError,PermissionError,ValueError):
            self.error(409,'speaker_operation_rejected','Speaker operation rejected; refresh readiness. Phone pairing and a fresh prepared action are required to enable.')

    def human_session(self):
        if not self.human_local(): return None
        cookies = SimpleCookie()
        try: cookies.load(self.headers.get('Cookie',''))
        except Exception: return None
        sid = cookies.get('garage_human')
        with self.server.session_lock:
            session = self.server.human_sessions.get(sid.value) if sid else None
        if session and self.server.app.humans.current(session['identity']): return session
        if session: self.server.app.jobs.revoke_identity(session['identity'].session)
        return None

    def human_post(self, body):
        if not self.human_local():
            self.error(403,'confidential_origin_required','Use loopback through a confidential SSH tunnel; LAN plaintext is not human authority'); return
        path = self.path; session = self.human_session()
        if path != '/api/human/login':
            if not session: self.error(401,'human_authentication_required','Bootstrap automation cannot elevate to human authority'); return
            if not credential_equal(self.headers.get('X-CSRF-Token',''),session['csrf']):
                self.error(403,'csrf_rejected','Separate human CSRF required'); return
        if path in ('/api/human/login','/api/human/reauth'):
            if set(body) != {'user','password'} or not isinstance(body['user'],str) or credential_equal(body.get('password'),self.server.token):
                self.error(401,'human_authentication_required','Independent human credentials required; bootstrap is always automation'); return
            try:
                sid = secrets.token_urlsafe(32)
                identity = self.server.app.humans.authenticate(body['user'],body['password'],sid)
            except (OSError,ValueError,TypeError):
                self.error(401,'human_authentication_required','Independent operator-provisioned human verifier and correct credentials required'); return
            if session: self.server.app.jobs.revoke_identity(session['identity'].session)
            csrf = secrets.token_urlsafe(32)
            with self.server.session_lock:
                # Never silently clear authorized sessions at capacity.
                self.server.human_sessions = {k:v for k,v in self.server.human_sessions.items() if v['identity'].expires_at > time.time()}
                if len(self.server.human_sessions) >= 32:
                    self.server.app.humans.revoke(sid)
                    self.error(429,'human_session_limit','Human session limit reached'); return
                self.server.human_sessions[sid] = {'identity':identity,'csrf':csrf}
                if session: self.server.human_sessions.pop(session['identity'].session,None)
            self.send(200,{'ok':True,'result':{'user':identity.user,'realm':identity.realm,'role':'human','csrf':csrf,
                'expires_at':identity.expires_at,'fresh_until':identity.authenticated_at+60},'error':None},
                'garage_human='+sid+'; HttpOnly; SameSite=Strict; Path=/; Max-Age=300'); return
        if path == '/api/human/logout':
            if body: self.error(400,'invalid_request','Empty object required'); return
            self.server.app.jobs.revoke_identity(session['identity'].session)
            with self.server.session_lock: self.server.human_sessions.pop(session['identity'].session,None)
            self.send(200,{'ok':True,'result':{'logged_out':True},'error':None},'garage_human=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0'); return
        if path in ('/api/human/approve','/api/human/revoke'):
            expected = {'id','manifest_sha256'} if path.endswith('approve') else {'id'}
            if set(body) != expected or not isinstance(body.get('id'),str) or not re.fullmatch('[a-f0-9]{32}',body['id']):
                self.error(400,'invalid_request','Exact bounded approval/revoke schema required'); return
            try:
                if path.endswith('approve'):
                    if not isinstance(body['manifest_sha256'],str) or not re.fullmatch('[a-f0-9]{64}',body['manifest_sha256']): raise ValueError()
                    result = self.server.app.jobs.approve(body['id'],body['manifest_sha256'],session['identity'])
                else: result = self.server.app.jobs.cancel(body['id'],'human_revoked')
                self.send(200,{'ok':True,'result':result,'error':None})
            except JobFailure as e:
                self.send(409,{'ok':False,'result':None,'error':{'code':e.code,'message':e.message,'requirements':e.requirements}})
            except (OSError,ValueError,TypeError): self.error(400,'invalid_request','Approval failed bounded validation')
            return
        self.error(404,'not_found','No human enrollment or elevation endpoint exists')

    def human_local(self):
        # Host/Origin alone can be forged by a non-browser LAN client. Do not
        # trust forwarded headers or Docker NAT to prove a confidential origin.
        return (self.server.server_address[0] == '127.0.0.1' and self.server.app.human_approval_available and
                self.connection.getsockname()[0] == '127.0.0.1' and self.client_address[0] == '127.0.0.1' and
                self.headers.get('Host','').split(':')[0] in ('127.0.0.1','localhost'))

def main():
    p = argparse.ArgumentParser()
    p.add_argument('--port',type=int,default=8098)
    p.add_argument('--bind',choices=('127.0.0.1','0.0.0.0'),default=os.environ.get('GARAGE_APP_BIND','127.0.0.1'))
    p.add_argument('--state',default=os.environ.get('GARAGE_APP_STATE','/var/lib/garage-dashboard'))
    p.add_argument('--web',default=str(Path(__file__).resolve().parents[1]/'web'))
    a = p.parse_args()
    token = private_token(os.environ['GARAGE_APP_TOKEN_FILE'])
    authorities = os.environ.get('GARAGE_APP_AUTHORITIES')
    Server((a.bind,a.port),Application(a.state),token,a.web,authorities.split(',') if authorities else None,
           trusted_lan_authority=os.environ.get('GARAGE_TRUSTED_LAN_AUTHORITY')).serve_forever()

if __name__ == '__main__': main()
