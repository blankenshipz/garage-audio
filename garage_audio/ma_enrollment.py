"""Human HTTP-only MA enrollment. No typed operation, token response or sound authority."""
import json
import os
import re
import socket
import threading
import time
import urllib.error
import urllib.request
from . import private_records

SOCKET = '/run/garage-ma-enrollment/install.sock'
LOCK = threading.Lock()
ATTEMPTS = []
SCOPES = {'config.providers.read','config.providers.write','players.read','queues.read'}

class EnrollmentFailure(Exception):
    def __init__(self,code,status=409):
        self.code,self.status=code,status
        super().__init__(code)

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs): return None

class MA:
    """Bounded supported JSON RPC, no environment proxies/redirects/raw errors."""
    def __init__(self,endpoint='http://music-assistant:8095'):
        self.endpoint=endpoint
        self.opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect())

    def call(self,command,args=None,bearer=None):
        headers={'Content-Type':'application/json'}
        if bearer: headers['Authorization']='Bearer '+bearer
        data=json.dumps({'command':command,'args':args or {},'message_id':'garage-enrollment'}).encode()
        request=urllib.request.Request(self.endpoint+'/api',data=data,headers=headers,method='POST')
        try:
            with self.opener.open(request,timeout=4) as response:
                if response.status!=200: raise EnrollmentFailure('ma_unavailable')
                raw=response.read(1048577)
            if len(raw)>1048576: raise EnrollmentFailure('ma_response_invalid')
            return json.loads(raw)
        except urllib.error.HTTPError as error:
            raise EnrollmentFailure('ma_authentication_failed' if error.code==401 else
                                    'ma_scope_required' if error.code==403 else 'ma_unavailable') from None
        except (OSError,ValueError,UnicodeError):
            raise EnrollmentFailure('ma_unavailable') from None

def token_valid(token):
    return isinstance(token,str) and 24<=len(token)<=4096 and token.isascii() and not any(c.isspace() for c in token)

def verify(ma,bearer):
    if not token_valid(bearer): raise EnrollmentFailure('ma_credential_invalid')
    user=ma.call('auth/me',bearer=bearer)
    roles=ma.call('auth/scopes',bearer=bearer)
    if not isinstance(user,dict) or not isinstance(roles,dict): raise EnrollmentFailure('ma_response_invalid')
    role=user.get('role');scopes=roles.get(role,[])
    if role=='guest' or not isinstance(scopes,list) or ('*' not in scopes and not SCOPES.issubset(set(scopes))):
        raise EnrollmentFailure('ma_scope_required')
    # Enforce actual access in addition to advertised role scopes. Do not return players,
    # provider configs/values, usernames, filter contents or media to the browser.
    ma.call('players/all',bearer=bearer)
    ma.call('player_queues/all',bearer=bearer)
    ma.call('config/providers',{'provider_domain':'spotify_connect','include_values':False},bearer)
    return {'role':role,'scope_verified':True,
            'player_filter_active':bool(user.get('player_filter')),
            'provider_filter_active':bool(user.get('provider_filter'))}

def installer(payload):
    try:
        with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as connection:
            connection.settimeout(40);connection.connect(SOCKET)
            connection.sendall(json.dumps(payload).encode()+b'\n');connection.shutdown(socket.SHUT_WR)
            raw=b''
            while len(raw)<=8192:
                chunk=connection.recv(8193-len(raw))
                if not chunk:break
                raw+=chunk
        value=json.loads(raw)
        if not isinstance(value,dict) or set(value)-{'state','code','enrollment_id','scope_verified'}:
            raise ValueError()
        return value
    except (OSError,ValueError,UnicodeError):
        raise EnrollmentFailure('ma_installer_unavailable',503) from None

def status():
    try:return installer({'op':'status'})
    except EnrollmentFailure as error:return {'state':'unavailable','code':error.code,'scope_verified':False}

def record(root,value=None):
    with private_records.directory(root,'ma-enrollment') as fd:
        if value is not None:private_records.write(fd,'request.json',json.dumps(value).encode())
        try:return private_records.read(fd,'request.json',4096)
        except FileNotFoundError:return None

def enroll(root,body):
    if set(body)!={'username','password','request_id','confirm'} or body.get('confirm') is not True:
        raise EnrollmentFailure('invalid_enrollment_request',400)
    username,password,request_id=body['username'],body['password'],body['request_id']
    if (not isinstance(username,str) or not 1<=len(username)<=100 or any(ord(c)<32 for c in username)
        or not isinstance(password,str) or not 1<=len(password)<=1024
        or not isinstance(request_id,str) or not re.fullmatch(r'[a-zA-Z0-9_-]{16,64}',request_id)):
        raise EnrollmentFailure('invalid_enrollment_request',400)
    if not LOCK.acquire(blocking=False):raise EnrollmentFailure('ma_enrollment_busy',429)
    try:
        previous=record(root)
        current=status()
        if current['state']=='unavailable':raise EnrollmentFailure(current['code'],503)
        if current['state']=='ready':return current
        if previous and previous.get('stage') in ('issuing','installing','uncertain'):
            # A lost create response must NEVER cause blind token minting on retry/new ID.
            # Root installer may have durably received it; its ready readback recovers that.
            raise EnrollmentFailure('ma_enrollment_interrupted')
        if previous and previous.get('request_id')==request_id:
            raise EnrollmentFailure(previous.get('code','ma_enrollment_interrupted'))
        now=time.monotonic();ATTEMPTS[:]=[t for t in ATTEMPTS if t>now-300]
        if len(ATTEMPTS)>=5:raise EnrollmentFailure('ma_enrollment_rate_limited',429)
        ATTEMPTS.append(now)
        state={'request_id':request_id,'stage':'login'};record(root,state)
        ma=MA()
        try:
            login=ma.call('auth/login',{'username':username,'password':password,
                         'provider_id':'builtin','device_name':'garage-setup'})
            body.clear();password=None
            if not isinstance(login,dict) or login.get('success') is not True:
                raise EnrollmentFailure('ma_authentication_failed',401)
            session=login.get('access_token');login.clear()
            verify(ma,session)
            state['stage']='issuing';record(root,state)
            bearer=ma.call('auth/token/create',{'name':'garage-service'},session)
            session=None
            if not token_valid(bearer):raise EnrollmentFailure('ma_enrollment_interrupted')
            state['stage']='installing';record(root,state)
            result=installer({'op':'install','enrollment_id':request_id,'bearer':bearer})
            bearer=None
            if result.get('state')!='ready':raise EnrollmentFailure(result.get('code','ma_enrollment_interrupted'))
            state['stage']='ready';record(root,state)
            return result
        except Exception as error:
            uncertain=state['stage'] in ('issuing','installing')
            code='ma_enrollment_interrupted' if uncertain else (
                error.code if isinstance(error,EnrollmentFailure) else 'ma_unavailable')
            state.update(stage='uncertain' if uncertain else 'failed',code=code);record(root,state)
            raise EnrollmentFailure(code,409 if uncertain else getattr(error,'status',409)) from None
        finally:body.clear();password=None
    finally:LOCK.release()
