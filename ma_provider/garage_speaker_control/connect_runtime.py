"""Pinned package-owned receiver integration; no browser/HTTP sound authority.

Control-port protection is installed even while sound auto-start is disabled.
Root marker, binary digest, service reference and actual socket-owner attestation
must all exist before a receiver transition can prepare normal native output.
"""
import asyncio
import json
import os
import socket
import stat
import time

from .client import SOCKET,Connection
from .connect_adapter import ConnectAdapter,trace
from .connect_authority import ReceiverBinding,TARGET

DATA='/data/spotify_connect/spotify_connect_02_00_00_00_21_01/soloist-data'

def discard_capture(fifo):
    """Discard preceding receiver PCM with no concurrent MA stream consumer.

    Runs on the MA event loop without await: source selection cannot interleave
    between the consumer check and nonblocking drain. Never follow a symlink or
    treat a file/empty EOF as proof of a live capture pipe.
    """
    fd=os.open(fifo,os.O_RDONLY|os.O_NONBLOCK|os.O_NOFOLLOW)
    try:
        info=os.fstat(fd)
        if not stat.S_ISFIFO(info.st_mode) or info.st_uid!=os.geteuid():raise PermissionError('actual receiver capture FIFO required')
        total=0
        for _ in range(32):
            try:block=os.read(fd,65536)
            except BlockingIOError:return total
            if not block:raise PermissionError('receiver capture writer absent')
            total+=len(block)
        raise PermissionError('capture cannot reach current-stream boundary within bounded drain')
    finally:os.close(fd)


class NativeBridge:
    def __init__(self,backend,provider,daemon):
        self.backend=backend;self.provider=provider;self.daemon=daemon
        self.reader=None;self.writer=None;self.lock=asyncio.Lock();self.stop_task=None
        self.stream_session=None;self.previous_session=None
        self.paused_source_session=None
        self.failed_transport=False;self.denied=False;self.closed=False
        self.cancel_generation=0;self.graph=None;self.started=None;self.status='disabled'
        self.socket=socket.socket(socket.AF_UNIX);self.socket.settimeout(2);self.socket.connect(SOCKET)
        try:
            self.socket.sendall(b'{"cmd":"connect-reserve"}\n')
            result=b''
            while not result.endswith(b'\n'):
                chunk=self.socket.recv(1)
                if not chunk:raise PermissionError('native reservation disconnected')
                result+=chunk
                if not result or len(result)>4096:raise PermissionError('bounded root reservation response required')
            value=json.loads(result)
            if value.get('ok') is not True:raise PermissionError('root receiver reservation denied')
            self.port=value['result']['port'];self.stop_handle=value['result']['stop_handle'];self.initial_volume=value['result']['initial_volume'];self.armed=value['result']['armed'];self.status='waiting_for_spotify_play' if self.armed else 'disabled'
        except BaseException:self.socket.close();raise

    async def request(self,cmd,**fields):
        async with self.lock:
            if self.reader is None:
                self.socket.setblocking(False)
                self.reader,self.writer=await asyncio.open_unix_connection(sock=self.socket,limit=4096)
            self.writer.write(json.dumps(dict(cmd=cmd,**fields)).encode()+b'\n');await self.writer.drain()
            try:raw=await asyncio.wait_for(self.reader.readline(),3)
            except (OSError,TimeoutError) as error:
                trace('native_ipc_closed',command=cmd,error=type(error).__name__)
                self.failed_transport=True;raise
            if not raw or len(raw)>4096:
                trace('native_ipc_closed',command=cmd)
                self.failed_transport=True;raise PermissionError('native receiver bridge disconnected')
            value=json.loads(raw)
            if value.get('ok') is not True:
                trace('native_ipc_denied',command=cmd)
                self.denied=True;self.status='guard_denied';raise PermissionError('native receiver bridge denied operation')
            result=value['result']
            if cmd in ('connect-prepare','connect-bind','connect-pulse'):
                trace('native_state',command=cmd,state=result.get('state'),error=result.get('error'),transport=fields.get('state'))
            return result

    async def attest(self,backend,client,epoch):
        if backend is not self.backend or str(client.data_dir)!=DATA:raise PermissionError('exact installed receiver required')
        for _ in range(300):
            endpoint=await asyncio.to_thread(client._read_endpoint)
            if endpoint==('127.0.0.1',self.port):break
            await asyncio.sleep(.1)
        else:raise TimeoutError('new guarded receiver endpoint not published')
        value=await self.request('connect-attest',epoch=epoch)
        self.epoch=epoch;self.armed=value['armed'];self.status='waiting_for_spotify_play' if self.armed else 'disabled'
        return ReceiverBinding(self.provider.instance_id,tuple(value['daemon']),epoch,port=self.port)

    async def prepare(self,token):
        if not self.armed:raise PermissionError('automatic sound disabled pending reviewed deployment/first-level confirmation')
        if self.stop_task:await self.stop_task
        cancellation=self.cancel_generation
        self.previous_session=self.daemon.active_session_id;self.stream_session=None
        self.status='preparing'
        result=await self.request('connect-prepare',epoch=self.epoch,sequence=token.sequence)
        for _ in range(30):
            if result['state']!='stopping':break
            if cancellation!=self.cancel_generation:raise asyncio.CancelledError()
            await asyncio.sleep(.1)
            result=await self.request('connect-prepare',epoch=self.epoch,sequence=token.sequence)
        bound=False
        for _ in range(240):
            if cancellation!=self.cancel_generation:raise asyncio.CancelledError()
            if result['state']=='prepared' and result.get('player_registered') is True and not bound:
                # The receiver's actual current chosen level, never cached MA
                # 100 or a new per-session10 default. Change upstream only
                # after the root has proved the exact all-zero muted graph.
                chosen=getattr(self.backend,'_garage_receiver_volume',None)
                if type(chosen) is not int or not 0<=chosen<=100:raise PermissionError('actual receiver volume required before stream handoff')
                trace('volume_handoff',phase='begin')
                await self.provider.mass.players.cmd_volume_set(TARGET,chosen)
                trace('volume_handoff',phase='end')
                for _ in range(30):
                    actual=self.provider.mass.players.get_player_state(TARGET)
                    if actual and actual.volume_level==chosen:break
                    await self.request('connect-pulse',state='idle',volume=chosen)
                    await asyncio.sleep(.1)
                else:raise PermissionError('receiver-to-player digital volume readback failed')
                result=await self.request('connect-bind',generation=result['generation'])
                bound=True
            elif result['state']=='listening':
                current=self.daemon.active_session_id
                retained=(current is not None and current==self.paused_source_session
                          and self.daemon.in_use_by_player==TARGET and self.daemon.spotify_session_active)
                if current is not None and not retained:raise PermissionError('prior MA source consumer must release before fresh receiver handoff')
                sink=self.backend._sink
                if sink is None:raise PermissionError('actual receiver capture sink required')
                # Never steal FIFO bytes from a retained paused stream's reader.
                # Only a fresh real receiver Resume token may re-open native
                # output for this exact previously paused source session.
                self.discarded_capture_bytes=0 if retained else discard_capture(sink.fifo_path)
                if retained:self.stream_session=current;self.previous_session=None
                self.graph=result['generation'];self.started=time.monotonic();return self.graph
            elif result['state'] not in ('setting_up','prepared'):raise PermissionError('receiver zero preparation failed')
            await asyncio.sleep(.1)
            result=await self.request('connect-pulse',state='idle',volume=self.volume())
        raise TimeoutError('receiver preparation deadline')

    async def observe_volume(self,value):
        return await self.request('connect-volume',volume=value)

    def volume(self):
        state=self.provider.mass.players.get_player_state(TARGET)
        value=state.volume_level if state else None
        if (type(value) is not int or not 0<=value<=100) and self.graph is None:
            # During all-zero preparation MA may not have published a volume
            # yet. The observed receiver value is truthful metadata only; native
            # level remains fixed/muted and never follows this integer.
            value=getattr(self.backend,'_garage_receiver_volume',None)
        if type(value) is not int or not 0<=value<=100:raise PermissionError('actual current source/player volume required')
        return value

    async def pulse(self,authority,binding,graph):
        if binding!=authority.binding or graph!=self.graph or not authority.live():return False
        daemon=self.daemon
        state=self.provider.mass.players.get_player_state(TARGET)
        selected=(daemon.in_use_by_player==TARGET and bool(daemon.active_session_id)
                  and daemon.active_player_id==TARGET and daemon.spotify_session_active)
        if selected:
            if self.stream_session is None:
                # New handoff cannot admit the preceding queue's capture/PCM
                # merely because MA already describes that old stream as active.
                if daemon.active_session_id==self.previous_session:selected=False
                else:self.stream_session=daemon.active_session_id
            elif daemon.active_session_id!=self.stream_session:
                return False  # generic queue/source generation replacement
        if authority.state in ('paused','buffering'):
            transport='paused'  # raw receiver Pause wins over generic MA EOF/off
        elif selected and state and state.available and state.playback_state.value=='playing':
            transport='playing'
        elif self.started is not None and time.monotonic()-self.started<5:
            transport='idle'  # bounded first stream handoff, cannot unmute
        else:return False
        result=await self.request('connect-pulse',state=transport,volume=self.volume())
        self.status=authority.state
        return result['generation']==graph and result['state']=='listening'

    def pause(self):
        self.status='paused'
        self.paused_source_session=self.daemon.active_session_id if self.daemon.in_use_by_player==TARGET else None
        async def mute():
            try:await self.request('connect-pulse',state='paused',volume=self.volume())
            except Exception:self.revoke()
        asyncio.create_task(mute())

    def revoke(self):
        self.cancel_generation+=1;self.status='stopped' if self.armed else 'disabled'
        if self.stop_task and not self.stop_task.done():return
        async def stop():
            # Separate IPC so a stalled preparation request cannot hold Stop.
            connection=None
            try:
                connection=await Connection.open('')
                await connection.request('connect-stop',handle=self.stop_handle)
            finally:
                if connection:await connection.close()
                try:await self.request('connect-revoke')
                except (Exception,asyncio.CancelledError):pass
        self.stop_task=asyncio.create_task(stop())

    async def close(self):
        if self.closed:return
        self.revoke()
        try:
            if self.stop_task:await self.stop_task
        finally:
            self.closed=True
            if self.writer:
                self.writer.close()
                try:await self.writer.wait_closed()
                except (ConnectionError,OSError):pass  # transport loss already revoked native scope
            else:self.socket.close()


class ConnectRuntime:
    """Install before receiver spawn; an existing backend is restarted muted.

    No selection/Play/volume command is issued here. Official backend volume
    mode must already be sync_spotify in the reviewed configuration migration.
    """
    def __init__(self,mass):self.mass=mass;self.bridges={};self.adapters={};self.owners={};self.originals=None;self.monitor=None
    def find(self,backend):
        if backend in self.owners:return self.owners[backend]
        for provider in self.mass.providers:
            if provider.domain!='spotify_connect':continue
            daemon=getattr(provider,'_daemons',{}).get(TARGET)
            if daemon and daemon.backend is backend:return provider,daemon
        raise PermissionError('exact configured official receiver required')
    def install(self):
        from music_assistant.providers.spotify_connect.soloist.backend import SoloistBackend
        from music_assistant.providers.spotify_connect.provider import SpotifyConnectProvider
        original_args=SoloistBackend._daemon_args;original_start=SoloistBackend.start;original_stop=SoloistBackend.stop
        original_create=SpotifyConnectProvider._create_backend
        self.originals=(SoloistBackend,original_args,original_start,original_stop,SpotifyConnectProvider,original_create)
        runtime=self
        def create(provider,daemon,player_name):
            backend=original_create(provider,daemon,player_name)
            if daemon.player_id==TARGET and isinstance(backend,SoloistBackend) and str(backend._data_dir)==DATA:
                # MA registers providers AFTER handle_async_init. Capture this
                # package-owned creation seam so cold boot doesn't depend on a
                # provider already appearing in mass.providers.
                runtime.owners[backend]=(provider,daemon)
            return backend
        SpotifyConnectProvider._create_backend=create
        def args(backend):
            result=original_args(backend)
            if str(backend._data_dir)!=DATA:return result
            if backend._volume_mode!='sync_spotify':raise PermissionError('reviewed supported sync_spotify configuration required')
            provider,daemon=runtime.find(backend)
            bridge=NativeBridge(backend,provider,daemon)
            previous=runtime.bridges.get(backend)
            runtime.bridges[backend]=bridge
            if previous:asyncio.create_task(previous.close())
            index=result.index('--ws');result[index+1]=f'127.0.0.1:{bridge.port}'
            value=bridge.initial_volume
            if value is not None and (type(value) is not int or not 0<=value<=100):raise PermissionError('root-observed receiver volume metadata required')
            backend._garage_receiver_volume=None
            initial=result.index('--initial-volume')
            if value is not None:result[initial+1]=str(value)
            else:del result[initial:initial+2]  # supported engine first-use default, not MA100
            return result
        async def start(backend):
            await original_start(backend)
            if str(backend._data_dir)!=DATA:return
            provider,daemon=runtime.find(backend)
            # _daemon_args runs in the supervisor task after start returns; the
            # adapter bridge delegates to the current spawn's native connection.
            class CurrentBridge:
                def current(self):
                    if backend not in runtime.bridges:raise PermissionError('root-guarded receiver has not spawned')
                    return runtime.bridges[backend]
                async def attest(self,*values):return await self.current().attest(*values)
                async def prepare(self,*values):return await self.current().prepare(*values)
                async def pulse(self,*values):return await self.current().pulse(*values)
                async def observe_volume(self,*values):return await self.current().observe_volume(*values)
                def pause(self):self.current().pause()
                def revoke(self):
                    if backend in runtime.bridges:self.current().revoke()
            value=ConnectAdapter(backend,provider,daemon,CurrentBridge());value.install()
            runtime.adapters[backend]=value
        async def stop(backend):
            try:return await original_stop(backend)
            finally:
                value=runtime.adapters.pop(backend,None)
                if value:await value.close()
                bridge=runtime.bridges.pop(backend,None)
                if bridge:await bridge.close()
                runtime.owners.pop(backend,None)
        SoloistBackend._daemon_args=args;SoloistBackend.start=start;SoloistBackend.stop=stop
        async def watch():
            while True:
                for provider in self.mass.providers:
                    if provider.domain!='spotify_connect':continue
                    daemon=getattr(provider,'_daemons',{}).get(TARGET)
                    bridge=runtime.bridges.get(daemon.backend) if daemon else None
                    transport_lost=bridge and bridge.failed_transport and not bridge.denied
                    if daemon and (daemon.backend not in runtime.adapters or transport_lost):
                        # Supported backend stop/start, never queue/device Play.
                        await daemon.backend.stop();daemon.backend._stop_called=False
                        await daemon.backend.start()
                await asyncio.sleep(1)
        self.monitor=asyncio.create_task(watch())
    def status(self):
        if not self.bridges:return 'waiting_for_receiver'
        return next(iter(self.bridges.values())).status
    async def close(self):
        if self.monitor:self.monitor.cancel();await asyncio.gather(self.monitor,return_exceptions=True)
        for value in list(self.adapters.values()):await value.close()
        for value in list(self.bridges.values()):await value.close()
        if self.originals:
            cls,args,start,stop,provider,create=self.originals;cls._daemon_args=args;cls.start=start;cls.stop=stop;provider._create_backend=create
