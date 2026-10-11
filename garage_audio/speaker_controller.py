"""Root-installed phone controller. No raw DSP proxy or HTTP pairing route.

Dashboard IPC accepts only fixed commands. Pairing is a separate root-only local
socket. Native connection remains open in worker for lease guardian ownership.
"""
import json
import http.client
import os
from pathlib import Path
import socket
import struct
import subprocess
import threading
import time
from .human_playback import exchange, user_command, user_units, SOCKET, prepare_transport, stop_pending_music
from .speaker_realm import BrowserRealm,digest

RUNTIME=Path('/run/garage-speakers')

class NativeChain:
    def source_status(self):
        # Fixed local MA configuration projection, not credentials or an auth
        # proxy. Saved configuration is explicitly NOT runtime/OAuth readiness.
        import stat
        path=Path('/home/zach/garage-audio/state/ma/settings.json')
        try:
            fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
            with os.fdopen(fd,'r') as f:
                info=os.fstat(f.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_size>2*1024*1024:raise ValueError('bounded MA settings required')
                value=json.load(f)
            providers=value.get('providers',{})
            rows=providers.values() if isinstance(providers,dict) else providers
            enabled={p.get('domain') for p in rows if isinstance(p,dict) and p.get('enabled') is True}
            return {'spotify_configured':'spotify' in enabled,'spotify_connect_configured':'spotify_connect' in enabled,
                    'speaker_control_configured':'garage_speaker_control' in enabled,
                    'provenance':'saved_configuration_only','human_bridge_available':False}
        except (OSError,ValueError,TypeError):return {'spotify_configured':None,'spotify_connect_configured':None,'speaker_control_configured':None,'provenance':'unavailable','human_bridge_available':False}
    def stop_pending_music(self):
        return stop_pending_music()
    def wait_zero(self,connection,cancel,timeout=20,clock=time.monotonic):
        deadline=clock()+timeout;observed=None
        while clock()<deadline:
            if cancel.is_set():raise InterruptedError('setup cancelled')
            # Initialization holds the broker transaction lock. Permit its
            # status reply to arrive within the remaining readiness budget,
            # rather than failing at the normal two-second idle RPC timeout.
            # Independent Stop/guardian never waits on this connection.
            if connection is not None:connection.settimeout(max(.01,deadline-clock()))
            status=exchange(connection,{'cmd':'status'})
            if cancel.is_set():raise InterruptedError('setup cancelled')
            if not status['muted'] or status['volume_db']!=-60:raise PermissionError('preparation mute/-60 lost')
            if status.get('preparation_zero'):
                # Two identical zero-graph generation samples, not a one-shot
                # race with Type=simple producer initialization.
                if observed==status['generation']:
                    if connection is not None:connection.settimeout(2)
                    return status
                observed=status['generation']
            else:observed=None
            if cancel.wait(.1):raise InterruptedError('setup cancelled')
        raise TimeoutError('zero preparation readiness deadline')
    def registration(self):
        # Supported Slimproto read-only inventory, not MA account credentials,
        # inferred healthy TCP, or source/provider configuration.
        c=http.client.HTTPConnection('127.0.0.1',9000,timeout=2)
        try:
            c.request('POST','/jsonrpc.js',json.dumps({'id':1,'method':'slim.request','params':['',['players',0,100]]}),{'Content-Type':'application/json'})
            r=c.getresponse();body=r.read(32769)
            if r.status!=200 or len(body)>32768:raise ValueError('bounded inventory unavailable')
            result=json.loads(body)['result'];players=result['players_loop']
            if not isinstance(players,list) or len(players)>100:raise ValueError('bounded inventory required')
            matches=[p for p in players if isinstance(p,dict) and p.get('playerid')=='02:00:00:00:21:01' and p.get('name')=='Garage Speakers' and p.get('connected') in (1,True)]
            return len(matches)==1
        except (OSError,ValueError,KeyError,TypeError,http.client.HTTPException):return None
        finally:c.close()
    def start(self,cancel,connect=False):
        if cancel.is_set():raise InterruptedError('setup cancelled')
        # Persistent player must be actually registered; discard pending PCM by
        # stopping transport (queue retained) before creating any capture reader.
        if connect:
            # Source scheduler has NOT handed the new Spotify stream to MA yet.
            # Dispose old producer/FIFO generation, never issue MA Stop against
            # incoming Connect. The persistent Squeezelite player stays alive.
            user_units('stop')
            user_command(['/usr/bin/systemctl','--user','start','garage-player.service'])
        else:prepare_transport(cancel)
        subprocess.run(['/usr/bin/systemctl','start','garage-audio-dsp.service'],check=True,timeout=20,capture_output=True)
        connection=socket.socket(socket.AF_UNIX);connection.settimeout(2)
        try:
            for _ in range(50):
                if cancel.is_set():raise InterruptedError('setup cancelled')
                try:connection.connect(SOCKET);break
                except (FileNotFoundError,ConnectionRefusedError):time.sleep(.1)
            else:raise RuntimeError('native socket unavailable')
            status=exchange(connection,{'cmd':'status'})
            if not status['muted'] or status['volume_db']!=-60:raise PermissionError('startup not muted/-60')
            # Broker establishes independent pidfd/deadline protection BEFORE
            # rearm/producer start can open the real zero-output graph.
            exchange(connection,{'cmd':'phone-start','generation':status['generation']})
            import pwd
            uid=json.loads(Path('/etc/garage-audio/identity.json').read_text())['admin_uid']
            home=Path(pwd.getpwuid(uid).pw_dir)/'garage-audio'
            if cancel.is_set():raise InterruptedError('setup cancelled')
            user_command([str(home/'venv/bin/python'),'-m','garage_audio.producer','--rearm'])
            if cancel.is_set():raise InterruptedError('setup cancelled')
            user_units('start')
            if cancel.is_set():raise InterruptedError('setup cancelled')
            status=self.wait_zero(connection,cancel)
            status=exchange(connection,{'cmd':'phone-prepare','generation':status['generation']})
            return connection,status
        except BaseException:connection.close();raise
    def stop(self):
        # Separate connection: never wait on worker/DSP/application locks.
        try:
            with socket.socket(socket.AF_UNIX) as c:
                c.settimeout(4);c.connect(SOCKET)
                exchange(c,{'cmd':'speaker-stop'})
        except (OSError,RuntimeError):
            subprocess.run(['/usr/bin/systemctl','kill','--signal=KILL','garage-audio-dsp.service'],check=False,timeout=4,capture_output=True)
        subprocess.run(['/usr/bin/systemctl','stop','garage-audio-dsp.service'],check=True,timeout=5,capture_output=True)
        result=subprocess.run(['/usr/bin/systemctl','is-active','garage-audio-dsp.service'],capture_output=True,timeout=2)
        if result.stdout.strip() not in (b'inactive',b'failed'):raise RuntimeError('native stop unverified')
        return True

class Controller:
    def __init__(self,native=None,clock=time.monotonic):
        self.native=native or NativeChain();self.clock=clock;self.realm=BrowserRealm(clock)
        self.lock=threading.RLock();self.cancel=threading.Event();self.worker=None
        self.state='stopped';self.generation=None;self.deadline=None;self.failure=None
        self.pending_enable=None;self.owner=None
        self.ma_owner=None;self.ma_available=False;self.normal_available=False;self.connect_armed=False
        self.connect_available=False;self.connect_status='disabled';self.connect_port=None
        self.registered=None
        self.music={'spotify_configured':None,'spotify_connect_configured':None,'human_bridge_available':False,'provenance':'not_observed'}
        self.shutdowns=0;self.shutdown_failed=False;self.setup_owner=None
    def binding(self):
        # Broker independently binds boot/device/capability/graph and fixed scope.
        return {'generation':self.generation,'outputs':[3,4],'ceiling_db':-60,'seconds':120}
    def status(self):
        with self.lock:
            return {'state':self.state,'generation':self.generation,'seconds_remaining':max(0,int((self.deadline or self.clock())-self.clock())),
                'spotify_connect':{'state':self.connect_status,'armed':self.connect_armed,'receiver_verified':self.connect_available},
                'outputs':[3,4],'ceiling_db':self.ma_owner.ceiling if self.ma_owner and self.state not in ('stopped','stop_unverified') else None,'error':self.failure,
                'mode':('spotify_connect' if getattr(self.ma_owner,'connect',False) else ('normal' if self.ma_owner and self.ma_owner.hours else 'diagnostic')) if self.state not in ('stopped','stop_unverified') else 'off','transport':self.ma_owner.transport if self.ma_owner and self.state=='listening' else None,'ma_volume_percent':self.ma_owner.volume if self.ma_owner and self.state=='listening' else None,
                'player_registered':self.registered,'music':self.music.copy(),'player_registration_note':'Observed Garage Speakers in real Slimproto player inventory' if self.registered else 'MA player inventory confirmation pending/unavailable'}
    def monitor_transport(self):
        # Slow network/disk reads NEVER hold Stop's lock. This survives DAC Stop.
        while True:
            registered=self.native.registration()
            music=self.native.source_status()
            with self.lock:
                self.registered=registered;self.music=music
                self.music['ma_check_available']=self.ma_available
                self.music['normal_listening_available']=self.normal_available
            time.sleep(1)
    def setup(self,cookie=None,authority=None):
        with self.lock:
            if self.shutdowns or self.state in ('stopping','stop_unverified'):raise PermissionError('shutdown barrier active')
            if self.worker and self.worker.is_alive():return self.status()
            self.cancel=threading.Event();self.pending_enable=None;self.owner=None
            self.ma_owner=authority
            self.state='setting_up';self.generation=None;self.failure=None;self.deadline=None
            self.registered=None
            self.setup_owner=digest(cookie) if cookie else None
            self.worker=threading.Thread(target=self.run,args=(self.cancel,),daemon=True);self.worker.start()
            return self.status()
    def run(self,cancel):
        connection=None
        try:
            connection,status=self.native.start(cancel,connect=True) if getattr(self.ma_owner,'connect',False) else self.native.start(cancel)
            with self.lock:
                if cancel.is_set():raise InterruptedError('setup cancelled')
                self.generation=status['generation'];self.state='prepared';self.deadline=self.clock()+min(120,status.get('preparation_seconds_remaining',120))
            while not cancel.wait(.25):
                if self.ma_owner is not None and not self.ma_owner.alive():raise PermissionError('MA owner heartbeat expired')
                if self.clock()>=self.deadline:break
                with self.lock:
                    enable=self.pending_enable;self.pending_enable=None;generation=self.generation
                if enable:
                    if self.ma_owner is not None:
                        if not self.ma_owner.alive() or not self.ma_owner.confirmed:raise PermissionError('fresh MA attended action required')
                    else:
                        with self.realm.lock:
                            self.realm.prune()
                            if self.owner not in self.realm.sessions:raise PermissionError('browser authority expired before enable')
                    scope=self.ma_owner
                    payload=({'cmd':'connect-open','generation':generation} if getattr(scope,'connect',False) else ({'cmd':'normal-open','generation':generation,'hours':scope.hours,'policy':scope.policy} if scope is not None and scope.hours else {'cmd':'phone-open','generation':generation}))
                    result=exchange(connection,payload)
                    with self.lock:
                        if cancel.is_set():raise InterruptedError('enable cancelled')
                        self.state='listening';self.deadline=self.clock()+result['seconds']
                scope=self.ma_owner
                payload=({'cmd':'connect-state' if getattr(scope,'connect',False) else 'normal-state','generation':generation,'state':scope.transport} if scope and (scope.hours or getattr(scope,'connect',False)) else {'cmd':'stereo-heartbeat','generation':generation}) if self.state=='listening' else {'cmd':'status'}
                result=exchange(connection,payload)
                if result.get('ended'):break
                if self.state=='listening' and getattr(scope,'connect',False):self.deadline=self.clock()+10
                if self.state=='listening' and self.ma_owner is None:
                    with self.realm.lock:
                        self.realm.prune()
                        if self.owner not in self.realm.sessions:raise PermissionError('browser authority expired')
                if result['generation']!=generation:raise RuntimeError('generation changed')
                if self.state=='prepared' and (not result['muted'] or result['volume_db']!=-60 or not result.get('preparation_zero')):raise RuntimeError('zero muted preparation lost')
                registered=self.native.registration()
                with self.lock:self.registered=registered
        except Exception as error:
            with self.lock:self.failure=type(error).__name__
        finally:
            self.begin_stop()
            if connection:connection.close()
            try:self.perform_stop()
            except Exception:pass # state stays stop_unverified; no new Setup
    def begin_stop(self):
        with self.lock:
            if not self.shutdowns:self.shutdown_failed=False
            self.shutdowns+=1;self.cancel.set();self.pending_enable=None;self.state='stopping'
    def perform_stop(self):
        success=False
        try:self.native.stop();success=True
        finally:
            with self.lock:
                self.shutdown_failed|=not success;self.shutdowns-=1
                if not self.shutdowns:
                    self.state='stop_unverified' if self.shutdown_failed else 'stopped'
                    if success:self.generation=None;self.deadline=None
        return self.status()
    def stop(self):
        self.begin_stop();self.perform_stop()
        with self.lock:
            # Startup may still be returning from systemd. Do not claim stopped
            # until the cancelled worker's final native-stop has completed.
            if self.worker and self.worker.is_alive() and self.state=='stopped':self.state='stopping'
            return self.status()
    def revoke(self,cookie):
        key=digest(cookie)
        with self.lock,self.realm.lock:
            self.realm.sessions.pop(key,None) # revoke actions BEFORE any waiting
            # Once Enable belongs to a browser, the original Setup requester
            # must not revoke a different browser's listening window on logout.
            owned=key==(self.owner if self.owner is not None else self.setup_owner)
            if owned:self.begin_stop()
        if owned:
            self.perform_stop()
            if self.worker and self.worker is not threading.current_thread():self.worker.join(timeout=3)
            with self.lock:
                if self.worker and self.worker.is_alive() and self.state=='stopped':self.state='stopping'
                if self.state!='stopped':raise RuntimeError('owned shutdown not yet verified; keep Stop available')
        return {'revoked':True,'owned_window_stopped':owned}
    def execute(self,payload):
        cmd=payload.get('cmd')
        fields={'session':set(),'resume':{'cookie'},'revoke':{'cookie'},'status':{'cookie'},'setup':{'cookie','csrf'},'stop':set(),
                'action':{'cookie','csrf'},'enable':{'cookie','csrf','action'}}
        if cmd not in fields or set(payload)!={'cmd'}|fields[cmd]:raise ValueError('exact speaker command schema required')
        if cmd=='session':return self.realm.create()
        if cmd=='stop':return self.stop() # automation may always reduce authority
        cookie=payload['cookie']
        if cmd=='revoke':return self.revoke(cookie)
        if cmd=='resume':return self.realm.resume(cookie)
        with self.realm.lock:self.realm.current(cookie,payload.get('csrf'))
        if cmd=='status':return dict(self.status(),browser=self.realm.status(cookie))
        if cmd=='setup':return self.setup(cookie)
        with self.lock:
            if self.ma_owner is not None:raise PermissionError('MA-owned preparation has no browser approval route')
            if self.state!='prepared' or self.pending_enable or self.registered is not True:raise PermissionError('muted prepared generation and real MA registration required')
            if cmd=='action':return {'action':self.realm.action(cookie,payload['csrf'],self.binding())}
            self.realm.consume(cookie,payload['csrf'],payload['action'],self.binding())
            self.pending_enable=True;self.owner=digest(cookie)
            return {'accepted':True,'state':'enabling'}

def request(payload,path=RUNTIME/'web/control.sock'):
    with socket.socket(socket.AF_UNIX) as connection:
        connection.settimeout(8);connection.connect(str(path));return exchange(connection,payload)

def serve_socket(controller,path,uid,group=None,pair=False):
    path.unlink(missing_ok=True)
    with socket.socket(socket.AF_UNIX) as server:
        server.bind(str(path));os.chmod(path,0o660 if group is not None else 0o600)
        if group is not None:os.chown(path,-1,group)
        server.listen(8)
        slots=threading.BoundedSemaphore(16)
        def handle(c):
            try:
                with c:
                    c.settimeout(10)
                    _,observed,_=struct.unpack('3i',c.getsockopt(socket.SOL_SOCKET,socket.SO_PEERCRED,12))
                    if observed!=uid:raise PermissionError('speaker IPC peer UID rejected')
                    data=b''
                    while not data.endswith(b'\n'):
                        part=c.recv(1)
                        if not part or len(data)>=4096:raise ValueError('bounded speaker IPC required')
                        data+=part
                    p=json.loads(data)
                    if not isinstance(p,dict):raise ValueError('object required')
                    if pair:
                        if set(p)!={'cmd','code'} or p['cmd']!='pair':raise ValueError('root pairing only')
                        result=controller.realm.pair(p['code'])
                    else:result=controller.execute(p)
                    c.sendall(json.dumps({'ok':True,'result':result}).encode()+b'\n')
            except Exception:
                try:c.sendall(b'{"ok":false,"error":"speaker operation rejected"}\n')
                except OSError:pass
            finally:slots.release()
        while True:
            c,_=server.accept()
            if not slots.acquire(False):c.close();continue
            threading.Thread(target=handle,args=(c,),daemon=True).start()

def main():
    import argparse,grp
    p=argparse.ArgumentParser();p.add_argument('action',choices=['serve','pair']);p.add_argument('code',nargs='?');a=p.parse_args()
    if os.geteuid()!=0:raise PermissionError('root installation/administration required')
    if a.action=='pair':
        print(json.dumps(request({'cmd':'pair','code':a.code},RUNTIME/'pair.sock')));return
    identity=json.loads(Path('/etc/garage-audio/speaker-web.json').read_text())
    config=Path('/etc/garage-audio/speaker-web.json');info=config.lstat()
    if config.is_symlink() or info.st_uid!=0 or info.st_mode&0o022 or set(identity)!={'web_uid'} or type(identity['web_uid']) is not int or identity['web_uid']<=0:
        raise PermissionError('immutable explicit nonroot dashboard UID admission required')
    gid=grp.getgrnam('garage-control').gr_gid
    for directory in (RUNTIME,RUNTIME/'web'):
        directory.mkdir(mode=0o750,exist_ok=True)
        info=directory.lstat()
        if directory.is_symlink() or info.st_uid!=0 or info.st_mode&0o022:raise PermissionError('immutable native runtime required')
        os.chown(directory,0,gid);os.chmod(directory,0o750)
    controller=Controller()
    ma_config=Path('/etc/garage-audio/ma-peer.json')
    if ma_config.exists():
        from .ma_bridge import Gateway,load_reference
        gateway=Gateway(controller,load_reference(ma_config))
        threading.Thread(target=gateway.serve,args=(RUNTIME/'ma/control.sock',),daemon=True).start()
        if Path('/etc/garage-audio/ma-service.json').exists():
            from .ma_reference import monitor
            threading.Thread(target=monitor,args=(controller,gateway),daemon=True).start()
    threading.Thread(target=controller.monitor_transport,daemon=True).start()
    threading.Thread(target=serve_socket,args=(controller,RUNTIME/'pair.sock',0),kwargs={'pair':True},daemon=True).start()
    serve_socket(controller,RUNTIME/'web/control.sock',identity['web_uid'],gid)
