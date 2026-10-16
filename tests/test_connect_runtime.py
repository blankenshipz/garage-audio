"""Synthetic native IPC/MA fixtures; no real receiver/phone acceptance."""
import asyncio,importlib.util,os,sys,tempfile,unittest,json,socket
from pathlib import Path
from types import ModuleType,SimpleNamespace as NS
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
pkg=ModuleType('garage_runtime_fixture');pkg.__path__=[str(ROOT/'ma_provider/garage_speaker_control')];sys.modules[pkg.__name__]=pkg
spec=importlib.util.spec_from_file_location(pkg.__name__+'.connect_runtime',Path(pkg.__path__[0])/'connect_runtime.py')
runtime=importlib.util.module_from_spec(spec);sys.modules[spec.name]=runtime;spec.loader.exec_module(runtime)
spec=importlib.util.spec_from_file_location('connect_config_fixture',ROOT/'scripts/prepare-spotify-connect.py')
config=importlib.util.module_from_spec(spec);spec.loader.exec_module(config)

class ReservationEOF(unittest.TestCase):
    def test_partial_reply_eof_fails_once_and_happy_reply_unchanged(self):
        import json
        class Socket:
            def __init__(self,raw):self.raw=raw;self.calls=0;self.closed=False
            def settimeout(self,value):pass
            def connect(self,path):pass
            def sendall(self,value):pass
            def recv(self,size):
                self.calls+=1
                if self.calls>len(self.raw)+1:raise AssertionError('retried an EOF instead of failing closed')
                return self.raw[self.calls-1:self.calls]
            def close(self):self.closed=True
        broken=Socket(b'{"ok":')
        with patch.object(runtime.socket,'socket',return_value=broken):
            with self.assertRaisesRegex(PermissionError,'native reservation disconnected'):runtime.NativeBridge(None,None,None)
        self.assertTrue(broken.closed);self.assertEqual(broken.calls,len(broken.raw)+1)
        reply=Socket((json.dumps({'ok':True,'result':{'port':54321,'stop_handle':'synthetic','initial_volume':27,'armed':False}})+'\n').encode())
        with patch.object(runtime.socket,'socket',return_value=reply):value=runtime.NativeBridge(None,None,None)
        self.assertEqual(value.port,54321);self.assertEqual(value.initial_volume,27);self.assertEqual(value.status,'disabled');self.assertFalse(reply.closed)

class Tests(unittest.IsolatedAsyncioTestCase):
    def bridge(self,armed=True):
        b=runtime.NativeBridge.__new__(runtime.NativeBridge)
        b.paused_source_session=None
        b.armed=armed;b.stop_task=None;b.cancel_generation=0;b.epoch=1;b.status='waiting_for_spotify_play';b.graph=None;b.started=None
        self.volumes=[]
        async def volume(target,value):self.volumes.append((target,value))
        directory=tempfile.TemporaryDirectory(dir='/private/tmp/opencode');self.addCleanup(directory.cleanup)
        fifo=Path(directory.name)/'source.pcm';os.mkfifo(fifo)
        writer=os.open(fifo,os.O_RDWR|os.O_NONBLOCK);self.addCleanup(os.close,writer)
        os.write(writer,b'old-source-fixture')
        b.backend=NS(_garage_receiver_volume=27,_sink=NS(fifo_path=fifo))
        b.daemon=NS(active_session_id=None)
        players=NS(cmd_volume_set=volume,get_player_state=lambda _:NS(volume_level=27))
        b.provider=NS(mass=NS(players=players));return b
    async def test_one_bind_only_and_actual_receiver_volume_not_MA100(self):
        b=self.bridge();calls=[];states=iter(('prepared','prepared','listening'))
        async def request(command,**values):
            calls.append((command,values))
            return {'state':next(states),'generation':'zero-1','player_registered':True}
        b.request=request
        result=await b.prepare(NS(sequence=2))
        self.assertEqual(result,'zero-1');self.assertEqual(sum(c[0]=='connect-bind' for c in calls),1)
        self.assertEqual(self.volumes,[(runtime.TARGET,27)])
        self.assertEqual(b.discarded_capture_bytes,len(b'old-source-fixture'))
    async def test_disabled_cannot_prepare_or_write_volume(self):
        b=self.bridge(False)
        with self.assertRaises(PermissionError):await b.prepare(NS(sequence=2))
        self.assertEqual(self.volumes,[])
    async def test_resume_already_muted_graph_never_resets_volume(self):
        b=self.bridge()
        async def request(command,**values):return {'state':'listening','generation':'same-zero-1'}
        b.request=request
        self.assertEqual(await b.prepare(NS(sequence=3)),'same-zero-1');self.assertEqual(self.volumes,[])
    async def test_unknown_receiver_volume_cannot_bind(self):
        b=self.bridge();b.backend._garage_receiver_volume=None
        async def request(command,**values):return {'state':'prepared','generation':'zero','player_registered':True}
        b.request=request
        with self.assertRaises(PermissionError):await b.prepare(NS(sequence=2))
        self.assertEqual(self.volumes,[])
    async def test_old_source_consumer_denies_handoff(self):
        b=self.bridge();b.daemon.active_session_id='old-source'
        async def request(command,**values):return {'state':'listening','generation':'zero'}
        b.request=request
        with self.assertRaises(PermissionError):await b.prepare(NS(sequence=2))
    async def test_capture_regular_file_is_not_current_pcm_boundary(self):
        b=self.bridge();file=b.backend._sink.fifo_path.with_suffix('.regular');file.write_bytes(b'not-fifo')
        with self.assertRaises(PermissionError):runtime.discard_capture(file)

class Framing(unittest.IsolatedAsyncioTestCase):
    async def connected(self):
        a,b=socket.socketpair();a.setblocking(False);b.setblocking(False)
        reader,writer=await asyncio.open_unix_connection(sock=a,limit=4096)
        peer_reader,peer_writer=await asyncio.open_unix_connection(sock=b,limit=4096)
        value=runtime.NativeBridge.__new__(runtime.NativeBridge)
        value.reader=reader;value.writer=writer;value.socket=a;value.lock=asyncio.Lock()
        value.closed=False;value.failed_transport=False;value.denied=False;value.armed=True
        value.epoch=1;value.port=54321;value.status='preparing'
        value.volume=lambda:27
        self.addAsyncCleanup(self.close_stream,writer);self.addAsyncCleanup(self.close_stream,peer_writer)
        return value,peer_reader,peer_writer
    async def close_stream(self,writer):
        writer.close()
        try:await writer.wait_closed()
        except OSError:pass
    async def scenario(self,allowed):
        bridge,reader,writer=await self.connected();seen=asyncio.Event();release=asyncio.Event()
        async def server():
            request=json.loads(await reader.readline());self.assertEqual(request['cmd'],'connect-prepare');seen.set()
            await release.wait()
            writer.write(b'{"ok":true,"result":{"state":"prepared","generation":"old-preparation"}}\n');await writer.drain()
            request=json.loads(await reader.readline());self.assertEqual(request['cmd'],'connect-pulse');self.assertEqual(request['state'],'paused')
            reply={'ok':True,'result':{'state':'prepared','generation':'genuine-paused'}} if allowed else {'ok':False,'error':'isolated daemon admission denied'}
            writer.write(json.dumps(reply).encode()+b'\n');await writer.drain()
        owner=asyncio.create_task(server());first=asyncio.create_task(bridge.request('connect-prepare',epoch=1,sequence=2));await seen.wait()
        first.cancel();await asyncio.sleep(0)
        verification=asyncio.create_task(bridge.verify_preparation_pause(NS(epoch=1,port=54321)))
        release.set()
        with self.assertRaises(asyncio.CancelledError):await first
        try:verified=await verification
        except PermissionError:verified=False
        await owner
        return verified,bridge
    async def test_cancelled_old_success_cannot_mask_actual_pause_denial(self):
        verified,bridge=await self.scenario(False)
        self.assertFalse(verified);self.assertTrue(bridge.denied)
    async def test_cancelled_old_success_drained_then_genuine_pause_verifies(self):
        verified,bridge=await self.scenario(True)
        self.assertTrue(verified);self.assertFalse(bridge.failed_transport)
    async def test_cancelled_partial_frame_socket_death_retires_transport(self):
        bridge,reader,writer=await self.connected()
        first=asyncio.create_task(bridge.request('connect-prepare'));await reader.readline();first.cancel()
        writer.write(b'{"ok":true');await writer.drain();writer.close()
        with self.assertRaises(asyncio.CancelledError):await first
        self.assertTrue(bridge.failed_transport)
        self.assertFalse(await bridge.verify_preparation_pause(NS(epoch=1,port=54321)))
    async def test_repeated_cancellation_timeout_retires_no_next_write(self):
        bridge,reader,writer=await self.connected()
        with patch.object(runtime,'IPC_REPLY_TIMEOUT',.05):
            first=asyncio.create_task(bridge.request('connect-prepare'));await reader.readline()
            first.cancel();await asyncio.sleep(0);first.cancel()
            with self.assertRaises(asyncio.CancelledError):await asyncio.wait_for(first,1)
        self.assertTrue(bridge.closed);self.assertTrue(bridge.failed_transport)
        self.assertFalse(await bridge.verify_preparation_pause(NS(epoch=1,port=54321)))
        self.assertEqual(await reader.readline(),b'')
    async def test_cancelled_old_denial_prevents_later_recovery(self):
        bridge,reader,writer=await self.connected()
        first=asyncio.create_task(bridge.request('connect-prepare'));await reader.readline();first.cancel()
        writer.write(b'{"ok":false,"error":"isolated old request denied"}\n');await writer.drain()
        with self.assertRaises(asyncio.CancelledError):await first
        self.assertTrue(bridge.denied)
        self.assertFalse(await bridge.verify_preparation_pause(NS(epoch=1,port=54321)))
    async def adapter_scenario(self,allowed):
        from tests.test_connect_adapter import adapter,authority,Client,Provider
        transport,reader,writer=await self.connected();seen=asyncio.Event();release=asyncio.Event()
        client=Client();client.mass=NS(http_session=NS())
        provider=Provider(client);daemon=NS(player_id=authority.TARGET,active_player_id=None,in_use_by_player=None,active_session_id=None)
        binding=authority.ReceiverBinding('socket-fixture',(1,2,'boot','sha'),1,port=54321)
        calls=0
        class Bridge:
            async def attest(self,*args):return binding
            async def prepare(self,token):
                nonlocal calls
                calls+=1
                if calls==1:return await transport.request('connect-prepare',epoch=1,sequence=token.sequence)
                return 'fresh-fixture-graph'
            async def verify_preparation_pause(self,b):return await transport.verify_preparation_pause(b)
            async def pulse(self,*args):return True
            def pause(self):pass
            def revoke(self):pass # Synthetic daemon closure; framing and adapter are actual implementations.
        value=adapter.ConnectAdapter(NS(_client=client),provider,daemon,Bridge());value.install();task=None
        async def normalized(event):
            if event.type=='playback_changed' and event.data.status=='paused' and task:task.cancel()
        listener=asyncio.create_task(client.listen_events(normalized));await asyncio.sleep(0)
        async def event(kind,**fields):
            await client.events.put(NS(type=kind,data=NS(**fields)));await asyncio.sleep(0);await asyncio.sleep(0)
        async def server():
            self.assertEqual(json.loads(await reader.readline())['cmd'],'connect-prepare');seen.set();await release.wait()
            writer.write(b'{"ok":true,"result":{"state":"prepared","generation":"delayed-old"}}\n');await writer.drain()
            current=json.loads(await reader.readline());self.assertEqual(current['state'],'paused')
            writer.write((json.dumps({'ok':True,'result':{'state':'prepared'}} if allowed else {'ok':False,'error':'real next framed Pause denied'})+'\n').encode());await writer.drain()
        server_task=asyncio.create_task(server())
        try:
            await event('auth_state',logged_in=True,is_active=True);await event('playback_state',status='paused',is_active=True);await event('playback_changed',status='playing',is_active=True)
            task=asyncio.create_task(provider._deferred_play_media_fire(daemon));await asyncio.wait_for(seen.wait(),1)
            await event('playback_changed',status='paused',is_active=True);release.set()
            with self.assertRaises(asyncio.CancelledError):await task
            await server_task
            self.assertEqual(value.authority._closed,not allowed)
            await event('playback_state',status='playing',is_active=True);await provider._deferred_play_media_fire(daemon);self.assertEqual(provider.queues,0)
            await event('playback_changed',status='paused',is_active=True);await event('playback_changed',status='playing',is_active=True)
            await provider._deferred_play_media_fire(daemon);self.assertEqual(provider.queues,1 if allowed else 0)
        finally:await value.close();await asyncio.gather(listener,return_exceptions=True)
    async def test_actual_adapter_old_success_actual_pause_denied_never_reopens(self):
        await asyncio.wait_for(self.adapter_scenario(False),5)
    async def test_actual_adapter_drained_success_genuine_pause_then_fresh_delta(self):
        await asyncio.wait_for(self.adapter_scenario(True),5)

class Migration(unittest.TestCase):
    def test_only_volume_mode_changes_and_original_is_unchanged(self):
        value={'providers':{'connect':{'domain':'spotify_connect','enabled':True,'values':{'connected_players':[runtime.TARGET],'volume_mode':'player_only','unrelated':'preserved'}}},'dashboard':'preserved'}
        result=config.migrate(value)
        self.assertEqual(value['providers']['connect']['values']['volume_mode'],'player_only')
        expected={'providers':{'connect':{'domain':'spotify_connect','enabled':True,'values':{'connected_players':[runtime.TARGET],'volume_mode':'sync_spotify','unrelated':'preserved'}}},'dashboard':'preserved'}
        self.assertEqual(result,expected)
    def test_other_target_denied(self):
        with self.assertRaises(PermissionError):config.migrate({'providers':[{'domain':'spotify_connect','enabled':True,'values':{'connected_players':['other'],'volume_mode':'player_only'}}]})

if __name__=='__main__':unittest.main()
