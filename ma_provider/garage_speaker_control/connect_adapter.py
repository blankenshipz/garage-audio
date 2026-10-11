"""Scoped wrappers for the installed Soloist backend/client and queue scheduler.

Candidate runtime installs only after guarded reservation/daemon attestation.
Production sound remains disabled pending review and first-level confirmation.
The bridge API is deliberately private/in-process; no caller actor fields.
"""
import asyncio
import json,os,stat,time,hashlib
from contextvars import ContextVar
from types import MethodType

from .connect_authority import ReceiverAuthority, ReceiverBinding, TARGET

_handoff = ContextVar('garage_connect_queue_handoff', default=None)

TRACE_PATH='/data/garage-connect-trace-r24.ndjson'
def trace(kind,**fields):
    """Bounded private diagnostic, never authority; no payloads/URIs/secrets."""
    fd=None
    try:
        if kind not in ('receiver','receiver_command','source_handoff','source_handoff_error','watch_closed','native_ipc_closed','native_ipc_denied','native_state','volume_handoff','listener_closed','listener_error'):return
        numeric={'position_ms','timestamp_ms','speed','MA_seconds','MA_updated','MA_corrected'}
        enums={
            'event':{'auth_state','playback_state','playback_changed','position_sync','device_changed','track_changed','command_result','error','volume_changed','other'},
            'status':{'playing','paused','buffering','idle','stopped'},
            'state':{'stopped','stopping','stop_unverified','setting_up','prepared','listening'},
            'authority':{'playing','paused','buffering','preparing','stopped','waiting_for_spotify_play','disarmed'},
            'transport':{'playing','paused','idle','off'},
            'phase':{'begin','end','prepare_begin','prepare_end','official_queue_begin','official_queue_end'},
            'command':{'play','resume','activate','pause','deactivate','seek','set_volume','get_state','get_auth_state','other','connect-reserve','connect-attest','connect-prepare','connect-bind','connect-pulse','connect-revoke','connect-stop','connect-volume'},
            'error':{'PermissionError','RuntimeError','TimeoutError','ValueError','TypeError','CancelledError','ConnectionResetError','BrokenPipeError','FileNotFoundError','OSError','InterruptedError'},
        }
        clean={}
        for key,value in fields.items():
            if key in numeric:
                clean[key]=value if type(value) in (int,float) else None
            elif key in enums:clean[key]=value if value is None or value in enums[key] else 'other'
            elif key in ('token_used','source_owned','raw_active','raw_logged_in','gate_active','gate_logged_in','local_poison','player_powered'):clean[key]=value if type(value) is bool else None
            elif key=='active_field':clean[key]=value if value in ('missing','null','boolean','invalid') else 'invalid'
            elif key=='session' and (value is None or (type(value) is str and len(value)==12 and all(c in '0123456789abcdef' for c in value))):clean[key]=value
            else:return
        row={'mono':time.monotonic(),'utc':time.time(),'kind':kind,**clean}
        if not all(type(v) in (str,int,float,bool,type(None)) for v in row.values()):return
        raw=(json.dumps(row,separators=(',',':'))+'\n').encode()
        if len(raw)>2048:return
        fd=os.open(TRACE_PATH,os.O_WRONLY|os.O_APPEND|os.O_CREAT|os.O_NOFOLLOW|os.O_NONBLOCK,0o600)
        info=os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid!=os.geteuid() or info.st_mode&0o077 or info.st_size>=256*1024:return
        os.write(fd,raw)
    except (OSError,TypeError,ValueError):pass
    finally:
        if fd is not None:os.close(fd)

def _current_handoff():
    value = _handoff.get()
    return value[0] if value and value[1] is asyncio.current_task() else None

class _ReceiverSession:
    def __init__(self,session,port,authority=None,revoke=None):self.session=session;self.port=port;self.authority=authority;self.revoke=revoke
    def __getattr__(self,name):return getattr(self.session,name)
    def ws_connect(self,url,*args,**kwargs):
        if url!=f'ws://127.0.0.1:{self.port}/':raise PermissionError('root-attested receiver endpoint required')
        # A live socket/PID or stale MA metadata alone cannot renew indefinitely.
        # Original aiohttp pong watchdog closes within3sec of a stalled daemon;
        # independent native10sec lease then fails closed. Other clients retain
        # their original session/heartbeat because this proxy is instance-only.
        kwargs['heartbeat']=2.0;kwargs['autoping']=True
        context=self.session.ws_connect(url,*args,**kwargs)
        if self.authority is None:return context  # isolated geometry/signature fixtures
        return _GuardedContext(context,self.authority,self.revoke)

class _GuardedContext:
    def __init__(self,context,authority,revoke):self.context=context;self.authority=authority;self.revoke=revoke
    async def __aenter__(self):
        socket=await self.context.__aenter__()
        return _GuardedWebSocket(socket,self.authority,self.revoke)
    async def __aexit__(self,*args):return await self.context.__aexit__(*args)

class _ReceiverMass:
    def __init__(self,mass,port,authority,revoke):self.mass=mass;self.http_session=_ReceiverSession(mass.http_session,port,authority,revoke)
    def __getattr__(self,name):return getattr(self.mass,name)


class _GuardedWebSocket:
    """Protect direct runtime WS sends as well as high-level client commands."""
    def __init__(self, socket, authority, revoke):
        self.socket = socket
        self.authority = authority
        self.revoke = revoke

    def __getattr__(self, name):
        return getattr(self.socket, name)

    def __aiter__(self):return self.socket.__aiter__()

    async def send_json(self, value, *args, **kwargs):
        if not isinstance(value, dict) or value.get('type') != 'command':
            raise PermissionError('fixed installed Soloist command frame required')
        try:self.authority.local_command(value.get('command'), handoff=_current_handoff())
        except PermissionError:
            self.revoke()
            raise
        return await self.socket.send_json(value, *args, **kwargs)

    async def send_str(self, *args, **kwargs):
        raise PermissionError('raw control frames cannot bypass receiver command guard')

    async def send_bytes(self, *args, **kwargs):
        raise PermissionError('raw control frames cannot bypass receiver command guard')

    async def send_frame(self, *args, **kwargs):
        raise PermissionError('raw control frames cannot bypass receiver command guard')

class ConnectAdapter:
    """One exact installed receiver and its exact queue daemon only.

    Required root bridge methods:
      attest(backend, client, epoch) -> immutable ReceiverBinding, with actual
        daemon/socket owner and root-cgroup port enforcement already verified;
      prepare(token) -> zero graph generation (no MA Stop/no queued PCM capture);
      pulse(authority, binding, graph_generation) -> true only for actual current
        receiver stream/native graph; 1sec renewal of native 10sec lease;
      revoke() -> immediate nonblocking cancel/Stop barrier.

    Bridge failures revoke; mocked implementations prove only isolated tests.
    """
    def __init__(self, backend, provider, daemon, bridge, *, clock=None):
        self.backend = backend
        self.provider = provider
        self.daemon = daemon
        self.bridge = bridge
        self.authority = ReceiverAuthority(**({'clock': clock} if clock else {}))
        self.epoch = 0
        self.sequence = 0
        self._originals = []
        self._listener = None
        self._monitor = None
        self._queue_task = None
        self._closed = False

    def _wrap(self, instance, name, wrapper):
        original = getattr(instance, name)
        own = instance.__dict__.get(name)
        self._originals.append((instance, name, own))
        setattr(instance, name, MethodType(wrapper(original), instance))

    def install(self):
        if self._originals or self.backend._client is None:
            raise PermissionError('single initialized installed receiver adapter required')
        if self.daemon.player_id != TARGET:
            raise PermissionError('fixed Garage Speakers target required')
        client = self.backend._client

        def listener(original):
            async def wrapped(_client, callback):
                if self._listener is not None:
                    raise PermissionError('one receiver WebSocket listener required')
                self._listener = asyncio.current_task()
                self.epoch += 1
                saved_mass=getattr(client,'mass',None);saved_endpoint=client.__dict__.get('_read_endpoint')
                binding=None
                try:
                    # Root attest is required before connecting: endpoint-file
                    # presence alone is neither daemon identity nor protection.
                    binding = await self.bridge.attest(self.backend, client, self.epoch)
                    if not isinstance(binding, ReceiverBinding):
                        raise PermissionError('native observed receiver binding required')
                    self.authority.attach(binding, port_protected=True, daemon_alive=True)
                    if binding is not None and binding.port:
                        if type(binding.port) is not int or not 1<=binding.port<=65535:raise PermissionError('fixed root-reserved receiver port required')
                        # Never re-read mutable endpoint files after attestation:
                        # same-UID file tampering cannot retarget the WS to a
                        # rogue local server between attest and connect.
                        client._read_endpoint=lambda:('127.0.0.1',binding.port)
                        client.mass=_ReceiverMass(saved_mass,binding.port,self.authority,self.bridge.revoke)
                    self.sequence = 0

                    async def raw(event):
                        if not isinstance(client._ws, _GuardedWebSocket):
                            client._ws = _GuardedWebSocket(client._ws, self.authority, self.bridge.revoke)
                        self.sequence += 1
                        data = event.data
                        position=getattr(data,'position',None)
                        players=getattr(getattr(self.provider,'mass',None),'players',None)
                        state=players.get_player_state(TARGET) if players and hasattr(players,'get_player_state') else None
                        session=self.daemon.active_session_id
                        eventkind=event.type if event.type in ('auth_state','playback_state','playback_changed','position_sync','device_changed','track_changed','command_result','error','volume_changed') else 'other'
                        trace('receiver',event=eventkind,status=getattr(data,'status',None) if getattr(data,'status',None) in ('playing','paused','buffering','idle','stopped') else None,
                              position_ms=getattr(position,'position_ms',None),timestamp_ms=getattr(position,'timestamp_ms',None),speed=getattr(position,'speed',None),
                               authority=self.authority.state,token_used=bool(self.authority._token and self.authority._token._used),
                               raw_active=getattr(data,'is_active',None),raw_logged_in=getattr(data,'logged_in',None),
                               active_field=('missing' if not hasattr(data,'is_active') else 'null' if data.is_active is None else 'boolean' if type(data.is_active) is bool else 'invalid'),
                               gate_active=self.authority._active,gate_logged_in=self.authority._logged_in,local_poison=self.authority._local_poison,
                               player_powered=getattr(state,'powered',None),
                              session=hashlib.sha256(str(session).encode()).hexdigest()[:12] if session is not None else None,
                              MA_seconds=getattr(state,'elapsed_time',None),MA_updated=getattr(state,'elapsed_time_last_updated',None),MA_corrected=getattr(state,'corrected_elapsed_time',None))
                        receiver_volume=getattr(data,'volume',None)
                        if type(receiver_volume) is int and 0<=receiver_volume<=100:
                            self.backend._garage_receiver_volume=receiver_volume
                            # Persist only through the exact MA process gateway;
                            # same-UID files/local WS callers cannot replace it.
                            await self.bridge.observe_volume(receiver_volume)
                        fresh=self.authority.event(binding, self.sequence, event.type,
                            status=getattr(data, 'status', None),
                            logged_in=getattr(data, 'logged_in', None),
                            active=getattr(data, 'is_active', None))
                        # Receiver Pause is conveyed BEFORE normalized MA Stop
                        # /EOF can race queue teardown. revoke() must not await
                        # a network/DSP lock; bridge owns the native Stop barrier.
                        if self.authority.state in ('paused', 'buffering', 'stopped'):
                            self.bridge.revoke() if self.authority.state == 'stopped' else self.bridge.pause()
                        await callback(event)
                        if event.type=='playback_changed':
                            # Receiver Pause must freeze the REAL transport, not
                            # merely mute native output while Squeezelite keeps
                            # consuming PCM and advancing its clock. Call the
                            # owned player's low-level transport methods, NOT
                            # cmd_pause/cmd_play (which proxy back to Spotify and
                            # produce echoes/reselection). No snapshot can resume.
                            token=self.authority._token
                            session=self.daemon.active_session_id
                            owned=(token is not None and token._used and self.authority.live()
                                   and self.authority._source_selected and session is not None
                                   and self.daemon.in_use_by_player==TARGET)
                            status=getattr(data,'status',None)
                            if owned and status=='paused' and self.authority.state=='paused':
                                player=self.provider.mass.players.get_player(TARGET)
                                if player is None:raise PermissionError('owned source transport absent')
                                await player.pause()
                                self._paused_transport_session=session
                            elif owned and status=='playing' and self.authority.state=='playing' and getattr(self,'_paused_transport_session',None)==session:
                                player=self.provider.mass.players.get_player(TARGET)
                                if player is None:raise PermissionError('owned source transport absent')
                                await player.play()
                                self._paused_transport_session=None
                            elif status in ('stopped','idle'):
                                self._paused_transport_session=None
                        if fresh is not None and self.daemon.in_use_by_player==TARGET and self.authority._token is fresh and not fresh._used:
                            # Long paused native idle closed output, not Spotify's
                            # live stream/session. Upstream scheduling skips an
                            # already-owned source: prepare native for this real
                            # fresh Resume without replay/reselecting its queue.
                            await self.provider._deferred_play_media_fire(self.daemon)
                            # Native idle expiry preserved this paused transport;
                            # only the fresh consumed Resume token reopens it.
                            if getattr(self,'_paused_transport_session',None)==self.daemon.active_session_id:
                                await self.provider.mass.players.get_player(TARGET).play()
                                self._paused_transport_session=None
                        token=self.authority._token
                        if event.type=='volume_changed' and type(receiver_volume) is int and 0<=receiver_volume<=100 and token is not None and token._used and self.authority.live():
                            # The official backend first adjusts reciprocal sink
                            # compensation. Only THEN apply the fresh raw slider
                            # event past its initial grace, avoiding a transient
                            # new MA gain combined with old sink compensation.
                            state=self.provider.mass.players.get_player_state(TARGET)
                            if state and state.volume_level!=receiver_volume:
                                await self.provider.mass.players.cmd_volume_set(TARGET,receiver_volume)

                    self._monitor = asyncio.create_task(self._watch(client, binding))
                    return await original(raw)
                except BaseException as error:
                    trace('listener_error',error=type(error).__name__,authority=self.authority.state)
                    raise
                finally:
                    trace('listener_closed',authority=self.authority.state)
                    self.authority.revoke()
                    self.bridge.revoke()
                    if self._queue_task and not self._queue_task.done():self._queue_task.cancel()
                    if self._monitor:
                        self._monitor.cancel()
                        await asyncio.gather(self._monitor, return_exceptions=True)
                        self._monitor = None
                    client._ws = None
                    client._fail_pending_results()
                    if binding is not None and binding.port:
                        client.mass=saved_mass
                        if saved_endpoint is None:client.__dict__.pop('_read_endpoint',None)
                        else:client._read_endpoint=saved_endpoint
                    self._listener = None
            return wrapped

        def command(original):
            async def wrapped(_client, name, **kwargs):
                trace('receiver_command',command=name if name in ('play','resume','activate','pause','deactivate','seek','set_volume','get_state','get_auth_state') else 'other')
                try:self.authority.local_command(name, handoff=_current_handoff())
                except PermissionError:
                    self.bridge.revoke()
                    raise
                return await original(name, **kwargs)
            return wrapped

        def queue(original):
            async def wrapped(_provider, daemon):
                if daemon is not self.daemon:
                    return await original(daemon)  # unrelated receiver untouched
                token = self.authority._token
                if token is None or token._used or self._closed:
                    return  # stale normalized PLAYING/generic source cannot start
                task = asyncio.current_task()
                self._queue_task = task
                try:
                    trace('source_handoff',phase='prepare_begin')
                    graph = await self.bridge.prepare(token)
                    trace('source_handoff',phase='prepare_end')
                    self.authority.consume(token, target=daemon.active_player_id or daemon.player_id,
                                           graph_generation=graph)
                    context = _handoff.set((token, asyncio.current_task()))
                    try:
                        # Scoped to this exact queue invocation; local commands
                        # elsewhere cannot borrow the single private capability.
                        trace('source_handoff',phase='official_queue_begin')
                        result=await original(daemon)
                        trace('source_handoff',phase='official_queue_end')
                        return result
                    finally:
                        _handoff.reset(context)
                except BaseException as error:
                    trace('source_handoff_error',error=type(error).__name__,authority=self.authority.state)
                    self.authority.revoke()
                    self.bridge.revoke()
                    raise
                finally:
                    if self._queue_task is task:self._queue_task = None
            return wrapped

        self._wrap(client, 'listen_events', listener)
        self._wrap(client, '_send_command', command)
        self._wrap(self.provider, '_deferred_play_media_fire', queue)
        def pause_stop(original):
            def wrapped(_provider,daemon,player_id):
                if daemon is self.daemon and player_id==TARGET:
                    self.authority._pending_remote_play=None
                token=self.authority._token
                if (daemon is self.daemon and player_id==TARGET
                        and self.authority.state=='paused' and self.authority.live()
                        and token is not None and token._used and self.authority._source_selected):
                    # Soloist's capture pipe stays open while paused. Native
                    # Pause was already applied before normalization; stopping
                    # MA here needlessly tears down this stream/source and can
                    # deactivate Spotify, so its subsequent Play takes back the
                    # context from position0. Keep THIS live owned stream only.
                    # Inactive/Stop/other receivers retain upstream teardown.
                    self.provider._cancel_pending_pause_stop(daemon)
                    return
                return original(daemon,player_id)
            return wrapped
        if hasattr(self.provider,'_schedule_pause_stop'):
            self._wrap(self.provider,'_schedule_pause_stop',pause_stop)
        def unselected(original):
            async def wrapped(_provider, source_id, owner_player_id, stream_session_id):
                current=self.daemon.active_session_id
                result=await original(source_id,owner_player_id,stream_session_id)
                if source_id==self.daemon.player_id and current==stream_session_id:
                    if self.authority.state=='paused':
                        self.authority._source_selected=False
                    elif self.authority.state not in ('preparing','waiting_for_spotify_play'):
                        self.authority.revoke();self.bridge.revoke()
                return result
            return wrapped
        if hasattr(self.provider,'on_source_unselected'):
            self._wrap(self.provider,'on_source_unselected',unselected)

    async def _watch(self, client, binding):
        try:
            while not self._closed:
                await asyncio.sleep(1)
                token = self.authority._token
                if token is None or not token._used:
                    continue  # no source token, no sound renewal
                if not self.authority.live():
                    raise PermissionError('never renew an already expired source window')
                current = await self.bridge.pulse(self.authority, binding, self.authority.graph_generation)
                self.authority.renew(daemon_alive=current, ws_connected=client.connected,
                    source_current=current, graph_generation=self.authority.graph_generation)
        except BaseException as error:
            trace('watch_closed',error=type(error).__name__,authority=self.authority.state,token_used=bool(self.authority._token and self.authority._token._used),source_owned=self.daemon.in_use_by_player==TARGET)
            receiver_live=client.connected and not self._closed
            paused=self.authority.state=='paused'
            if receiver_live:
                self.authority.end_window(reopen=True)
                if not paused:self.authority._baseline=None
            else:self.authority.revoke()
            self.bridge.revoke()
            if self._queue_task and not self._queue_task.done():self._queue_task.cancel()
            if receiver_live:
                self._monitor=asyncio.create_task(self._watch(client,binding))
                return
            raise

    async def close(self):
        self._closed = True
        self.authority.revoke()
        self.bridge.revoke()
        tasks = [t for t in (self._listener, self._monitor, self._queue_task)
                 if t is not None and t is not asyncio.current_task()]
        for task in tasks:task.cancel()
        if tasks:await asyncio.gather(*tasks, return_exceptions=True)
        for instance, name, own in reversed(self._originals):
            if own is None:instance.__dict__.pop(name, None)
            else:setattr(instance, name, own)
        self._originals.clear()
