"""Synthetic adapter fixtures, NOT live receiver/native/phone proof."""
import asyncio
import importlib.util
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace as NS
import unittest
import tempfile,json,os

DIRECTORY=Path(__file__).resolve().parents[1]/'ma_provider/garage_speaker_control'
pkg=ModuleType('garage_connect_fixture');pkg.__path__=[str(DIRECTORY)];sys.modules[pkg.__name__]=pkg
for name in ('connect_authority','connect_adapter'):
    spec=importlib.util.spec_from_file_location(pkg.__name__+'.'+name,DIRECTORY/(name+'.py'))
    value=importlib.util.module_from_spec(spec);sys.modules[spec.name]=value;spec.loader.exec_module(value)
adapter=sys.modules[pkg.__name__+'.connect_adapter'];authority=sys.modules[pkg.__name__+'.connect_authority']

class Socket:
    closed=False
    def __init__(self):self.commands=[]
    async def send_json(self,value):self.commands.append(value)

class Client:
    def __init__(self):self.events=asyncio.Queue();self._ws=None;self.wire=Socket()
    @property
    def connected(self):return self._ws is not None and not self._ws.closed
    def _fail_pending_results(self):pass
    async def listen_events(self,callback):
        self._ws=self.wire
        while True:
            event=await self.events.get()
            if event is None:return
            await callback(event)
    async def _send_command(self,name,**values):await self._ws.send_json(dict(type='command',command=name,**values))

class Provider:
    def __init__(self,client):self.client=client;self.queues=0
    def _schedule_pause_stop(self,daemon,player_id):pass
    async def _deferred_play_media_fire(self,daemon):
        await self.client._send_command('activate')
        await self.client._send_command('play')
        self.queues+=1

class Bridge:
    def __init__(self):self.stops=0;self.pauses=0;self.preparations=0
    async def attest(self,backend,client,epoch):return authority.ReceiverBinding('spotify_connect_fixture',(1,2,'boot','sha'),epoch)
    async def prepare(self,token):self.preparations+=1;return 'fixture-zero-graph'
    async def pulse(self,*args):return True
    def revoke(self):self.stops+=1
    def pause(self):self.pauses+=1
    async def verify_preparation_pause(self,binding):return True

class Tests(unittest.IsolatedAsyncioTestCase):
    async def test_trace_private_bounded_and_does_not_record_payloads(self):
        saved=adapter.TRACE_PATH
        try:
            with tempfile.TemporaryDirectory(dir='/private/tmp/opencode') as d:
                p=Path(d)/'trace';adapter.TRACE_PATH=str(p)
                adapter.trace('receiver',event='position_sync',position_ms=6000,timestamp_ms=1000,speed=0.)
                adapter.trace('receiver',event='sensitive-placeholder',position_ms='sensitive-placeholder')
                adapter.trace('receiver',active_field='null',raw_active=None,gate_active=False,raw_logged_in='sensitive-placeholder',local_poison=False,player_powered=False)
                adapter.trace('receiver',uri='sensitive-placeholder')
                adapter.trace('native_state',error='sensitive-placeholder',state='stopped')
                self.assertNotIn('sensitive-placeholder',p.read_text());self.assertEqual(p.stat().st_mode&0o777,0o600)
                with p.open('ab') as f:f.write(b' '*262144)
                size=p.stat().st_size;adapter.trace('native_state',state='stopped');self.assertEqual(p.stat().st_size,size)
                p.unlink();p.symlink_to(Path(d)/'other')
                adapter.trace('native_state',state='stopped');self.assertFalse((Path(d)/'other').exists())
        finally:adapter.TRACE_PATH=saved
    async def asyncSetUp(self):
        self.client=Client();self.provider=Provider(self.client);self.bridge=Bridge();self.daemon=NS(player_id=authority.TARGET,active_player_id=None,in_use_by_player=None,active_session_id=None)
        self.adapter=adapter.ConnectAdapter(NS(_client=self.client),self.provider,self.daemon,self.bridge)
        self.adapter.install()
        self.seen=[]
        async def normalized(event):
            self.seen.append(event.type)
            if getattr(self,'seen_callback',None):await self.seen_callback(event)
        self.listener=asyncio.create_task(self.client.listen_events(normalized))
        await asyncio.sleep(0)
        await self.event('auth_state',logged_in=True,is_active=True)
    async def asyncTearDown(self):await self.adapter.close()
    async def event(self,kind,**values):
        await self.client.events.put(NS(type=kind,data=NS(**values)))
        await asyncio.sleep(0);await asyncio.sleep(0)
    async def play(self):
        await self.event('playback_state',status='paused',is_active=True)
        await self.event('playback_changed',status='playing',is_active=True)
    async def test_snapshot_cannot_schedule(self):
        await self.event('playback_state',status='playing',is_active=True)
        await self.provider._deferred_play_media_fire(self.daemon)
        self.assertEqual(self.bridge.preparations,0);self.assertEqual(self.provider.queues,0)
    async def test_receiver_pause_cancels_actual_queue_then_fresh_play(self):
        started=asyncio.Event()
        async def held(token):
            self.bridge.preparations+=1;started.set();await asyncio.Future()
        self.bridge.prepare=held
        await self.play()
        queue=asyncio.create_task(self.provider._deferred_play_media_fire(self.daemon))
        await started.wait()
        # Drive the installed raw listener and normalized callback's real queue cancellation.
        async def cancel_on_pause(event):
            if event.type=='playback_changed' and event.data.status=='paused':queue.cancel()
        # The fixture's normalized callback can model upstream scheduling without replacing adapter.
        self.seen_callback=cancel_on_pause
        await self.event('playback_changed',status='paused',is_active=True)
        with self.assertRaises(asyncio.CancelledError):await queue
        self.assertFalse(self.adapter.authority._closed)
        self.assertIsNone(self.adapter.authority._token)
        await self.event('playback_state',status='playing',is_active=True)
        await self.provider._deferred_play_media_fire(self.daemon)
        self.assertEqual(self.provider.queues,0)
        await self.event('playback_changed',status='paused',is_active=True)
        await self.event('playback_changed',status='playing',is_active=True)
        async def fresh(token):self.bridge.preparations+=1;return 'new-fixture-zero'
        self.bridge.prepare=fresh
        await self.provider._deferred_play_media_fire(self.daemon)
        self.assertEqual(self.provider.queues,1)
    async def preparing_queue(self):
        started=asyncio.Event()
        async def held(token):started.set();await asyncio.Future()
        self.bridge.prepare=held
        await self.play()
        task=asyncio.create_task(self.provider._deferred_play_media_fire(self.daemon))
        await started.wait()
        return task
    async def test_unknown_cancellation_remains_terminal(self):
        task=await self.preparing_queue();task.cancel()
        with self.assertRaises(asyncio.CancelledError):await task
        self.assertTrue(self.adapter.authority._closed)
        await self.play();self.assertIsNone(self.adapter.authority._token)
    async def paused_verification(self):
        task=await self.preparing_queue();entered=asyncio.Event();release=asyncio.Event()
        async def verify(binding):entered.set();await release.wait();return True
        self.bridge.verify_preparation_pause=verify
        async def cancel(event):
            if event.type=='playback_changed' and event.data.status=='paused':task.cancel()
        self.seen_callback=cancel
        await self.event('playback_changed',status='paused',is_active=True)
        await entered.wait()
        return task,release
    async def test_paused_snapshot_during_verification_never_grants(self):
        task,release=await self.paused_verification()
        await self.event('playback_state',status='paused',is_active=True)
        release.set()
        with self.assertRaises(asyncio.CancelledError):await task
        self.assertFalse(self.adapter.authority._closed);self.assertIsNone(self.adapter.authority._token)
        with self.assertRaises(PermissionError):await self.client._send_command('play')
    async def test_auth_loss_during_cancellation_does_not_reopen(self):
        task,release=await self.paused_verification()
        await self.event('auth_state',logged_in=False,is_active=False);release.set()
        with self.assertRaises(asyncio.CancelledError):await task
        self.assertFalse(self.adapter.authority._logged_in);await self.play()
        self.assertIsNone(self.adapter.authority._token)
    async def test_socket_loss_during_cancellation_remains_closed(self):
        task,release=await self.paused_verification();self.client.wire.closed=True;release.set()
        with self.assertRaises(asyncio.CancelledError):await task
        self.assertTrue(self.adapter.authority._closed)
    async def test_shutdown_during_verification_remains_closed(self):
        task,release=await self.paused_verification()
        # Current pending verification was already called; raise at its await boundary.
        self.adapter._closed=True;release.set()
        with self.assertRaises(asyncio.CancelledError):await task
        self.assertTrue(self.adapter.authority._closed)
    async def test_daemon_verification_failure_remains_closed(self):
        task=await self.preparing_queue()
        async def verify(binding):raise ConnectionResetError('isolated protected daemon loss')
        self.bridge.verify_preparation_pause=verify
        await self.event('playback_changed',status='paused',is_active=True);task.cancel()
        with self.assertRaises(asyncio.CancelledError):await task
        self.assertTrue(self.adapter.authority._closed)
    async def test_stale_cancel_cannot_revoke_newer_remote_token(self):
        task,release=await self.paused_verification()
        await self.event('playback_changed',status='playing',is_active=True)
        fresh=self.adapter.authority._token;self.assertIsNotNone(fresh)
        stops=self.bridge.stops;release.set()
        with self.assertRaises(asyncio.CancelledError):await task
        self.assertIs(self.adapter.authority._token,fresh);self.assertFalse(self.adapter.authority._closed)
        self.assertEqual(self.bridge.stops,stops)
    async def test_replay_during_cancellation_remains_closed(self):
        task,release=await self.paused_verification();a=self.adapter.authority
        with self.assertRaises(PermissionError):a.event(a.binding,a.sequence,'playback_changed',status='playing')
        release.set()
        with self.assertRaises(asyncio.CancelledError):await task
        self.assertTrue(a._closed)
    async def test_displaced_binding_not_reopened_by_stale_cancel(self):
        task,release=await self.paused_verification();a=self.adapter.authority
        new=authority.ReceiverBinding('other-fixture',(2,3,'boot','sha'),2)
        a.attach(new,port_protected=True,daemon_alive=True);release.set()
        with self.assertRaises(asyncio.CancelledError):await task
        self.assertEqual(a.binding,new);self.assertFalse(a._logged_in)
        self.assertIsNone(a._token);self.assertEqual(a.sequence,0)
    async def test_pause_cancel_after_token_consumption_not_recoverable(self):
        entered=asyncio.Event()
        async def original(daemon):entered.set();await asyncio.Future()
        # Replace only upstream callback under a new adapter installation.
        await self.adapter.close()
        self.client=Client();self.provider=Provider(self.client)
        self.provider._deferred_play_media_fire=original
        self.bridge=Bridge();self.adapter=adapter.ConnectAdapter(NS(_client=self.client),self.provider,self.daemon,self.bridge)
        self.adapter.install()
        async def normalized(event):pass
        self.listener=asyncio.create_task(self.client.listen_events(normalized));await asyncio.sleep(0)
        await self.event('auth_state',logged_in=True,is_active=True);await self.play()
        task=asyncio.create_task(self.provider._deferred_play_media_fire(self.daemon));await entered.wait()
        self.assertTrue(self.adapter.authority._token._used)
        await self.event('playback_changed',status='paused',is_active=True);task.cancel()
        with self.assertRaises(asyncio.CancelledError):await task
        self.assertTrue(self.adapter.authority._closed)
    async def test_pending_never_prepares_and_direct_pause_stop_cancels(self):
        await self.event('auth_state',logged_in=True,is_active=False)
        await self.event('playback_changed',status='playing')
        self.assertIsNotNone(self.adapter.authority._pending_remote_play)
        await self.provider._deferred_play_media_fire(self.daemon)
        self.assertEqual(self.bridge.preparations,0)
        self.provider._schedule_pause_stop(self.daemon,authority.TARGET)
        self.assertIsNone(self.adapter.authority._pending_remote_play)
        await self.event('device_changed',is_active=True)
        await self.provider._deferred_play_media_fire(self.daemon)
        self.assertEqual(self.provider.queues,0)
    async def test_genuine_fixture_delta_one_queue_and_local_commands_blocked(self):
        await self.play();await self.provider._deferred_play_media_fire(self.daemon)
        self.assertEqual(self.provider.queues,1);self.assertEqual(self.bridge.preparations,1)
        await self.provider._deferred_play_media_fire(self.daemon);self.assertEqual(self.provider.queues,1)
        with self.assertRaises(PermissionError):await self.client._send_command('play')
    async def test_direct_ws_raw_frames_rejected(self):
        with self.assertRaises(PermissionError):await self.client._ws.send_json({'type':'command','command':'activate'})
        with self.assertRaises(PermissionError):await self.client._ws.send_str('raw play')
        with self.assertRaises(PermissionError):await self.client._ws.send_bytes(b'raw play')
        self.assertEqual(self.client.wire.commands,[])
    async def test_transport_freezes_only_owned_delta_and_snapshot_never_resumes(self):
        commands=[]
        async def pause():commands.append('pause')
        async def play():commands.append('play')
        self.provider.mass=NS(players=NS(get_player=lambda _:NS(pause=pause,play=play)))
        await self.play();await self.provider._deferred_play_media_fire(self.daemon)
        self.daemon.in_use_by_player=authority.TARGET;self.daemon.active_session_id='owned'
        await self.event('playback_changed',status='paused',is_active=True)
        self.assertEqual(commands,['pause'])
        await self.event('playback_state',status='playing',is_active=True)
        self.assertEqual(commands,['pause'])
        await self.event('playback_changed',status='playing',is_active=True)
        self.assertEqual(commands,['pause','play'])
        self.assertEqual(self.provider.queues,1)
    async def test_pause_before_normalized_callback_and_disconnect_revokes(self):
        await self.play();await self.provider._deferred_play_media_fire(self.daemon)
        await self.event('playback_changed',status='paused');self.assertEqual(self.bridge.pauses,1)
        await self.client.events.put(None);await self.listener
        self.assertEqual(self.adapter.authority.state,'stopped');self.assertGreater(self.bridge.stops,0)
        self.assertIsNone(self.client._ws)
    async def test_target_change_during_prepare_cannot_queue(self):
        await self.play();self.daemon.active_player_id='other'
        with self.assertRaises(PermissionError):await self.provider._deferred_play_media_fire(self.daemon)
        self.assertEqual(self.provider.queues,0);self.assertEqual(self.adapter.authority.state,'stopped')
    async def test_child_context_cannot_borrow_queue_capability(self):
        await self.play();token=self.adapter.authority._token
        self.adapter.authority.consume(token,target=authority.TARGET,graph_generation='fixture-zero')
        context=adapter._handoff.set((token,asyncio.current_task()))
        try:
            with self.assertRaises(PermissionError):await asyncio.create_task(self.client._send_command('play'))
        finally:adapter._handoff.reset(context)
    async def test_scoped_heartbeat_and_exact_root_endpoint(self):
        class HTTP:
            def ws_connect(self,url,*args,**kwargs):return (url,kwargs)
        original=HTTP();scope=adapter._ReceiverSession(original,54321)
        value=scope.ws_connect('ws://127.0.0.1:54321/',heartbeat=30)
        self.assertEqual(value[1]['heartbeat'],2.0);self.assertTrue(value[1]['autoping'])
        with self.assertRaises(PermissionError):scope.ws_connect('ws://127.0.0.1:54322/')
        self.assertEqual(original.ws_connect('other',heartbeat=30)[1]['heartbeat'],30)
    async def test_socket_is_guarded_before_first_receiver_frame(self):
        class Socket:
            closed=False
            def __aiter__(self):return self
            async def __anext__(self):raise StopAsyncIteration
        class Context:
            async def __aenter__(self):return Socket()
            async def __aexit__(self,*args):return False
        class HTTP:
            def ws_connect(self,*args,**kwargs):return Context()
        authority=adapter.ReceiverAuthority();revoked=[]
        scoped=adapter._ReceiverSession(HTTP(),54321,authority,lambda:revoked.append(True))
        async with scoped.ws_connect('ws://127.0.0.1:54321/') as ws:
            self.assertIsInstance(ws,adapter._GuardedWebSocket)
            with self.assertRaises(PermissionError):await ws.send_str('unauthorized-before-snapshot')
            self.assertEqual([value async for value in ws],[])

if __name__=='__main__':unittest.main()
