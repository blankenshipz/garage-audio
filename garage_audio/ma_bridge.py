"""MA-process private gateway. Fixed scope; no bearer/role/header authority."""
import hashlib,hmac,json,os,secrets,select,socket,stat,struct,threading,time
from pathlib import Path

# Exact existing production MA Unix identity, not a configurable UID fallback.
MA_UID = 1000

def process_reference(pid):
    root=Path('/proc')/str(pid)
    fields=(root/'stat').read_text().rsplit(')',1)[1].split()
    return {'pid':pid,'uid':root.stat().st_uid,'starttime':fields[19],
            'boot':Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
            'mount_namespace':os.readlink(root/'ns/mnt'),'exe':os.readlink(root/'exe'),
            'exe_sha256':hashlib.sha256((root/'exe').read_bytes()).hexdigest(),
            'argv_sha256':hashlib.sha256((root/'cmdline').read_bytes()).hexdigest()}

class MAProcessPeer:
    def __init__(self,connection,reference):
        pid,uid,_=struct.unpack('3i',connection.getsockopt(socket.SOL_SOCKET,socket.SO_PEERCRED,12))
        self.reference=reference;self.pid=pid;self.fd=None
        if uid!=MA_UID or reference['process']['uid']!=MA_UID or pid!=reference['process']['pid']:
            raise PermissionError('MA process admission denied')
        self.verify();self.fd=os.pidfd_open(pid)
    def verify(self):
        if process_reference(self.pid)!=self.reference['process']:raise PermissionError('MA process identity changed')
        for name,expected in self.reference['assets'].items():
            if name.startswith('/') or '..' in Path(name).parts:raise PermissionError('relative provider asset required')
            path=Path('/proc')/str(self.pid)/'root'/self.reference['provider_path'].lstrip('/')/name
            info=path.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_uid!=0 or info.st_mode&0o022 or hashlib.sha256(path.read_bytes()).hexdigest()!=expected:
                raise PermissionError('immutable installed provider required')
    def alive(self):return self.fd is not None and not select.select([self.fd],[],[],0)[0]
    def close(self):
        if self.fd is not None:os.close(self.fd);self.fd=None

def load_reference(path):
    path=Path(path);info=path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid!=0 or info.st_mode&0o077:raise PermissionError('private root-owned MA reference required')
    value=json.loads(path.read_text())
    if set(value)!={'process','provider_path','assets'} or set(value['assets'])!={'__init__.py','manifest.json','client.py','connect_authority.py','connect_adapter.py','connect_runtime.py'}:raise ValueError('exact MA reference schema required')
    if value['provider_path']!='/app/venv/lib/python3.14/site-packages/music_assistant/providers/garage_speaker_control':raise ValueError('fixed provider path required')
    return value

class Window:
    def __init__(self,peer,session,clock=time.monotonic,hours=None,policy=None):
        self.peer=peer;self.session=session;self.clock=clock;self.last=clock()
        self.closed=False;self.confirmed=False;self.generation=None;self.handle=None
        self.secret=secrets.token_bytes(32)
        from .playback_lease import listening_scope
        self.hours=hours;self.policy=policy
        self.ceiling=listening_scope(hours,policy)[1] if hours is not None else -60
        self.transport='idle';self.volume=10
    def alive(self):return not self.closed and self.peer.alive() and self.clock()-self.last<(10 if self.hours else 4)
    def challenge(self,generation):
        if self.generation is None:
            self.generation=generation
            message=json.dumps({'nonce':secrets.token_hex(32),'session':self.session,'generation':generation,
                                 'outputs':[3,4],'policy':self.policy,'ceiling_db':self.ceiling,'seconds':self.hours*3600 if self.hours else 120},sort_keys=True).encode()
            self.handle=hmac.new(self.secret,message,hashlib.sha256).hexdigest()

class ConnectWindow(Window):
    def __init__(self,peer,port,session):
        super().__init__(peer,session)
        self.connect=True;self.port=port;self.ceiling=0;self.transport='idle'
    def alive(self):return not self.closed and self.port.alive() and self.clock()-self.last<10

class Gateway:
    def __init__(self,controller,reference):
        self.controller=controller;self.reference=reference;self.connect_guard=None;self.guard_lock=threading.Lock();self.connect_ports={}
    def connect_enabled(self):
        path=Path('/etc/garage-audio/spotify-connect-enabled')
        try:
            info=path.lstat()
            return stat.S_ISREG(info.st_mode) and info.st_uid==0 and not info.st_mode&0o077 and path.read_text()=='enabled\n'
        except OSError:return False
    def serve(self,path):
        path=Path(path);path.parent.mkdir(mode=0o711,exist_ok=True)
        info=path.parent.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid!=0 or stat.S_IMODE(info.st_mode) not in (0o700,0o711):raise PermissionError('root-owned non-writable MA runtime required')
        # Read-only directory bind gives MA traversal, never listing/write. Its
        # host parent stays root0700. SocketDAC only permits exact MA_UID; kernel
        # PID/start/namespace/assets still authenticate every connection.
        os.chmod(path.parent,0o711)
        path.unlink(missing_ok=True)
        with socket.socket(socket.AF_UNIX) as server:
            server.bind(str(path));os.chown(path,MA_UID,MA_UID);os.chmod(path,0o600);server.listen(4)
            self.controller.ma_available=True
            slots=threading.BoundedSemaphore(8)
            while True:
                connection,_=server.accept()
                if not slots.acquire(False):connection.close();continue
                def run(c):
                    try:self.handle(c)
                    finally:slots.release()
                threading.Thread(target=run,args=(connection,),daemon=True).start()
    def handle(self,connection):
        peer=None;window=None;port=None;epoch=0;sequence=0;controller=self.controller
        try:
            with connection:
                peer=MAProcessPeer(connection,self.reference);connection.settimeout(4)
                while True:
                    data=b''
                    while not data.endswith(b'\n'):
                        part=connection.recv(1)
                        if not part:return
                        data+=part
                        if len(data)>2048:raise ValueError('bounded gateway request required')
                    peer.verify();payload=json.loads(data);cmd=payload.get('cmd')
                    connect_fields={'connect-reserve':set(),'connect-attest':{'epoch'},'connect-prepare':{'epoch','sequence'},'connect-bind':{'generation'},'connect-pulse':{'state','volume'},'connect-revoke':set(),'connect-stop':{'handle'},'connect-volume':{'volume'}}
                    if cmd in connect_fields:
                        if set(payload)!={'cmd'}|connect_fields[cmd]:raise PermissionError('fixed receiver gateway schema required')
                        if cmd=='connect-stop':
                            with self.guard_lock:target=self.connect_ports.get(payload['handle'])
                            with controller.lock:owned=target is not None and target.peer.pid==peer.pid and getattr(controller.ma_owner,'port',None) is target
                            if owned:controller.stop()
                            result={'stopped':True}
                        elif cmd=='connect-volume':
                            if port is None or not port.alive():raise PermissionError('attested receiver required for volume metadata')
                            from .connect_port import save_volume
                            save_volume(payload['volume']);result={'stored':True}
                        elif cmd=='connect-reserve':
                            if port is not None or window is not None:raise PermissionError('single receiver reservation per connection')
                            from .connect_port import PortScope,new_guard,saved_volume
                            with self.guard_lock:
                                if self.connect_guard is None:self.connect_guard=new_guard()
                            port=PortScope(peer,self.connect_guard);port.stop_handle=secrets.token_hex(32)
                            # Receiver discovery/idle is normal everyday use,
                            # not an approval window. Keep its authenticated IPC
                            # attached while idle; any SOUND window still has its
                            # independent10sec liveness/deadline and pidfd guards.
                            connection.settimeout(None)
                            with self.guard_lock:self.connect_ports[port.stop_handle]=port
                            result={'port':port.port,'stop_handle':port.stop_handle,'armed':self.connect_enabled(),'initial_volume':saved_volume()}
                            with controller.lock:
                                controller.connect_port=port;controller.connect_armed=result['armed'];controller.connect_status='guarding_receiver' if result['armed'] else 'disabled'
                        elif cmd=='connect-attest':
                            if port is None or type(payload['epoch']) is not int or payload['epoch']<=epoch:raise PermissionError('fresh receiver WebSocket epoch required')
                            if window is not None:
                                window.closed=True;controller.stop();window=None
                            result=port.attest();epoch=payload['epoch'];sequence=0
                            result.update(epoch=epoch,armed=self.connect_enabled())
                            with controller.lock:
                                controller.connect_available=True;controller.connect_armed=result['armed'];controller.connect_status='waiting_for_spotify_play' if result['armed'] else 'disabled'
                        elif cmd=='connect-revoke':
                            if window:
                                window.closed=True
                                with controller.lock:owned=controller.ma_owner is window
                                if owned:controller.stop()
                                window=None
                            result={'stopped':True}
                            with controller.lock:controller.connect_status='stopped' if self.connect_enabled() else 'disabled'
                        else:
                            if port is None or not port.alive() or not self.connect_enabled():raise PermissionError('armed protected current receiver required')
                            if cmd=='connect-prepare':
                                if payload['epoch']!=epoch or type(payload['sequence']) is not int or payload['sequence']<=sequence:raise PermissionError('one fresh receiver transition required')
                                if window is not None:
                                    with controller.lock:
                                        if controller.ma_owner is not window or controller.state!='listening' or window.transport!='paused' or not window.alive():raise PermissionError('only current muted paused receiver can rebind')
                                        window.last=window.clock();result=controller.status()
                                    sequence=payload['sequence']
                                    connection.sendall(json.dumps({'ok':True,'result':result}).encode()+b'\n')
                                    continue
                                session=hashlib.sha256(json.dumps([port.daemon,epoch,payload['sequence']]).encode()).hexdigest()
                                with controller.lock:
                                    if controller.state=='stopping' or controller.shutdowns:
                                        connection.sendall(json.dumps({'ok':True,'result':controller.status()}).encode()+b'\n')
                                        continue
                                sequence=payload['sequence']
                                window=ConnectWindow(peer,port,session)
                                with controller.lock:
                                    if controller.state!='stopped' or controller.shutdowns or (controller.worker and controller.worker.is_alive()):raise PermissionError('native window already owned')
                                    result=controller.setup(authority=window)
                                    controller.connect_status='preparing'
                            elif cmd=='connect-bind':
                                if window is None or not window.alive():raise PermissionError('fresh receiver preparation required')
                                with controller.lock:
                                    if controller.ma_owner is not window or controller.state!='prepared' or window.confirmed or payload['generation']!=controller.generation or controller.registered is not True:raise PermissionError('current zero graph and real registered player required')
                                    window.confirmed=True;controller.pending_enable=True;result=controller.status()
                            else:
                                if window is None or not window.alive() or payload['state'] not in ('idle','playing','paused','off') or type(payload['volume']) is not int or not 0<=payload['volume']<=100:raise PermissionError('exact receiver-source transport readback required')
                                window.last=window.clock();window.transport=payload['state'];window.volume=payload['volume']
                                with controller.lock:
                                    if controller.ma_owner is not window:raise PermissionError('receiver native ownership changed')
                                    result=controller.status()
                                    controller.connect_status=payload['state'] if payload['state'] in ('playing','paused') else 'waiting_for_stream'
                        connection.sendall(json.dumps({'ok':True,'result':result}).encode()+b'\n')
                        continue
                    fields={'prepare':{'session'},'normal-prepare':{'session','hours','policy'},'heartbeat':{'session'},'normal-heartbeat':{'session','state','volume'},'confirm':{'session','handle','generation'},'normal-confirm':{'session','handle','generation'},'stop':set()}
                    if cmd not in fields or set(payload)!={'cmd'}|fields[cmd]:raise ValueError('fixed MA gateway schema required')
                    if cmd!='stop' and (not controller.ma_available or (cmd.startswith('normal-') and not controller.normal_available)):raise PermissionError('verified current MA service readiness required')
                    if cmd=='stop':result=controller.stop()
                    else:
                        session=payload['session']
                        if not isinstance(session,str) or len(session)!=64 or any(c not in '0123456789abcdef' for c in session):raise PermissionError('validated session fingerprint required')
                        if cmd in ('prepare','normal-prepare'):
                            if window is not None:raise PermissionError('one preparation per connection')
                            hours=payload.get('hours');policy=payload.get('policy')
                            if cmd=='normal-prepare':
                                from .playback_lease import listening_scope
                                listening_scope(hours,policy)
                            window=Window(peer,session,hours=hours,policy=policy)
                            if hours is not None:connection.settimeout(10)
                            with controller.lock:
                                if controller.state!='stopped' or controller.shutdowns or (controller.worker and controller.worker.is_alive()):raise PermissionError('native window already owned')
                                result=controller.setup(authority=window)
                        else:
                            if window is None or window.session!=session or not window.alive():raise PermissionError('live MA window required')
                            if cmd.startswith('normal-')!=(window.hours is not None):raise PermissionError('distinct normal/diagnostic messages required')
                            if cmd=='normal-heartbeat':
                                if payload['state'] not in ('playing','paused','idle','off') or type(payload['volume']) is not int or not 0<=payload['volume']<=100:raise PermissionError('exact actual MA player readback required')
                                window.transport=payload['state'];window.volume=payload['volume']
                            window.last=window.clock()
                            with controller.lock:
                                if controller.ma_owner is not window:raise PermissionError('native ownership changed')
                                if controller.state=='prepared':window.challenge(controller.generation)
                                if cmd in ('confirm','normal-confirm'):
                                    if window.confirmed or controller.state!='prepared' or controller.pending_enable or controller.registered is not True:raise PermissionError('fresh zero prepared registration required')
                                    if payload['generation']!=controller.generation or not isinstance(payload['handle'],str) or not window.handle or not hmac.compare_digest(payload['handle'],window.handle):raise PermissionError('stale or forged attended action')
                                    if window.hours is not None and (window.transport!='idle' or window.volume!=10):raise PermissionError('stopped MA and verified initial volume10 required')
                                    window.confirmed=True;controller.pending_enable=True
                                result=controller.status()
                                if controller.state=='prepared':result=dict(result,handle=window.handle)
                        result=dict(result,session_bound=True)
                    connection.sendall(json.dumps({'ok':True,'result':result}).encode()+b'\n')
        except Exception:
            try:connection.sendall(b'{"ok":false,"error":"MA gateway operation denied"}\n')
            except OSError:pass
        finally:
            if window is not None:
                window.closed=True
                with controller.lock:owned=controller.ma_owner is window
                if owned:
                    try:controller.stop()
                    except Exception:pass
            if port:
                with controller.lock:
                    if controller.connect_port is port:
                        controller.connect_available=False;controller.connect_status='receiver_unavailable';controller.connect_port=None
                with self.guard_lock:self.connect_ports.pop(port.stop_handle,None)
                port.close()
            if peer:peer.close()
