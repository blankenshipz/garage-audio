"""Synthetic receiver event seam + real isolated MA queue/IPC; NEVER production.

Root admission is real SO_PEERCRED. Receiver socket/daemon attestation is injected
by the isolated host fixture (separate actual-kernel/Soloist evidence is reused).
"""
import asyncio,array,json,logging,math,os,socket,tempfile,time,wave
from pathlib import Path
from types import SimpleNamespace as NS
from aiohttp import web
from music_assistant.mass import MusicAssistant
from music_assistant.models.player import PlayerMedia
from music_assistant.providers.spotify_connect.provider import SpotifyConnectProvider
from music_assistant.providers.spotify_connect.models import BackendEvent,BackendEventType
from music_assistant.providers.garage_speaker_control.connect_adapter import ConnectAdapter
from music_assistant.providers.garage_speaker_control.connect_runtime import NativeBridge,DATA,TARGET

async def main():
    first_only=os.environ.get('GARAGE_FIRST_PLAY_ONLY')=='1'
    cold_only=os.environ.get('GARAGE_COLD_PLAY_ONLY')=='1'
    restart_only=os.environ.get('GARAGE_RESTART_ONLY')=='1'
    restart_phase=0
    if restart_only:
        phasefile=Path('/data/restart-only-phase')
        restart_phase=int(phasefile.read_text()) if phasefile.exists() else 0
        phasefile.write_text(str(restart_phase+1))
    logging.basicConfig(level=logging.ERROR)
    address=socket.gethostbyname(socket.gethostname());directory=Path(tempfile.mkdtemp(dir='/test-state'))
    if cold_only:
        saved=Path('/data/cold-fixture-directory')
        if saved.exists():directory=Path(saved.read_text())
        else:saved.write_text(str(directory))
    (directory/'settings.json').write_text(json.dumps({'core':{'discovery':{'domain':'discovery','values':{'zeroconf_interfaces':'all','upnp_network_scan':False}},'streams':{'domain':'streams','values':{'bind_ip':'0.0.0.0','publish_ip':address}},'webserver':{'domain':'webserver','values':{'bind_ip':'0.0.0.0','publish_ip':address}}}}))
    for name,sign in (('old',1),('new',-1)):
        with wave.open(str(directory/(name+'.wav')),'wb') as out:
            out.setnchannels(2);out.setsampwidth(2);out.setframerate(48000)
            for second in range(10 if first_only else 120):
                # Left/right ratio is a semantic one-second media cursor,
                # independent of MA metadata and the player's wall clock.
                block=array.array('h',(value for sample in range(48000) for value in (int((1000+second*200)*math.sin(2*math.pi*440*sample/48000)),int(6000*math.sin(2*math.pi*440*sample/48000))))).tobytes()
                out.writeframesraw(block)
    app=web.Application()
    for name in ('old','new'):app.router.add_get('/'+name+'.wav',lambda request:web.FileResponse(directory/(request.match_info.route.resource.canonical[1:])))
    runner=web.AppRunner(app);await runner.setup();await web.TCPSite(runner,'0.0.0.0',18088).start()
    mass=MusicAssistant(str(directory),str(directory/'cache'),safe_mode=True);adapter=None;bridge=None;writer=None;listener=None
    class FixtureSocket:
        closed=False
        async def send_json(self,*args,**kwargs):pass
    class Client:
        def __init__(self):self.mass=mass;self.data_dir=Path(DATA);self._ws=None;self.callback=None;self.finished=asyncio.Event()
        @property
        def connected(self):return self._ws is not None and not self._ws.closed
        def _read_endpoint(self):return ('127.0.0.1',bridge.port)
        async def listen_events(self,callback):self._ws=FixtureSocket();self.callback=callback;await self.finished.wait()
        async def _send_command(self,name,**kwargs):pass
        def _fail_pending_results(self):pass
    try:
        await mass.start();await mass.config.setup_provider('squeezelite')
        print('READY',flush=True);await asyncio.to_thread(__import__('sys').stdin.readline)
        for _ in range(150):
            state=mass.players.get_player_state(TARGET)
            if state and state.available:break
            await asyncio.sleep(.1)
        else:raise AssertionError('real registered player absent')
        fifo=directory/'receiver-fixture.pcm'
        if fifo.exists():assert not fifo.is_symlink() and __import__('stat').S_ISFIFO(fifo.stat().st_mode)
        else:os.mkfifo(fifo)
        writer=os.open(fifo,os.O_RDWR|os.O_NONBLOCK);os.write(writer,b'OLD_CAPTURE_EPOCH'*100)
        backend=NS(_client=Client(),_garage_receiver_volume=None,_sink=NS(fifo_path=fifo),stream_ends_on_pause=False)
        daemon=NS(backend=backend,player_id=TARGET,active_player_id=None,in_use_by_player=None,active_session_id=None,spotify_session_active=True,playing=False,pending_play_media_task=None,pending_pause_stop_task=None,stream_metadata=NS(elapsed_time=0,elapsed_time_last_updated=0),publish_name='SYNTHETIC Garage FILE')
        class Provider:
            instance_id='SYNTHETIC-RECEIVER-SEAM';domain='spotify_connect'
            _schedule_pause_stop=SpotifyConnectProvider._schedule_pause_stop
            _cancel_pending_pause_stop=SpotifyConnectProvider._cancel_pending_pause_stop
            _on_pause_stop_done=SpotifyConnectProvider._on_pause_stop_done
            _stop_paused_player=SpotifyConnectProvider._stop_paused_player
            _schedule_play_media=SpotifyConnectProvider._schedule_play_media
            _cancel_pending_play_media=SpotifyConnectProvider._cancel_pending_play_media
            _handle_backend_event=SpotifyConnectProvider._handle_backend_event
            _remember_context_uris=SpotifyConnectProvider._remember_context_uris
            def __init__(self):self.mass=mass;self.logger=logging.getLogger('receiver-seam');self.starts=0
            async def _deferred_play_media_fire(self,value):
                if value.in_use_by_player:
                    return await SpotifyConnectProvider._deferred_play_media_fire(self,value)
                # Only this synthetic event seam starts the isolated known WAV;
                # not a real Spotify call/track/user event or production queue.
                await mass.player_queues.play_media(TARGET,f'http://{address}:18088/new.wav',option='replace')
                self.starts+=1
                value.active_player_id=TARGET;value.in_use_by_player=TARGET;value.active_session_id='NEW-SYNTHETIC-SOURCE-EPOCH-'+str(self.starts)
            async def on_source_unselected(self,*args):pass
        provider=Provider();bridge=NativeBridge(backend,provider,daemon);adapter=ConnectAdapter(backend,provider,daemon,bridge);adapter.install()
        listener=asyncio.create_task(backend._client.listen_events(lambda event:normalized(event)))
        async def normalized(event):
            if cold_only and event.type=='device_changed':
                daemon.spotify_session_active=event.data.is_active
                if event.data.is_active:provider._schedule_play_media(daemon)
                return
            if cold_only and event.type=='playback_changed' and event.data.status=='stopped':
                await mass.players.cmd_stop(TARGET)
                daemon.playing=False;daemon.active_player_id=None;daemon.in_use_by_player=None;daemon.active_session_id=None
                return
            if event.type=='playback_changed' and event.data.status=='playing':
                if daemon.in_use_by_player:await provider._handle_backend_event(daemon,BackendEvent(type=BackendEventType.PLAYING))
                else:
                    daemon.playing=True
                    if cold_only:provider._schedule_play_media(daemon)
                    else:await provider._deferred_play_media_fire(daemon)
            elif event.type=='playback_changed' and event.data.status=='paused' and daemon.in_use_by_player:
                await provider._handle_backend_event(daemon,BackendEvent(type=BackendEventType.PAUSED))
        for _ in range(50):
            if backend._client.callback:break
            await asyncio.sleep(.1)
        else:raise AssertionError('attested fixture listener absent')
        async def event(kind,**fields):await backend._client.callback(NS(type=kind,data=NS(**fields)))
        if cold_only:
            await mass.players.cmd_power(TARGET,False)
            for _ in range(50):
                if mass.players.get_player_state(TARGET).powered is False:break
                await asyncio.sleep(.1)
            assert mass.players.get_player_state(TARGET).powered is False
            assert daemon.active_session_id is None and daemon.in_use_by_player is None
            print('COLD_POWER_OFF_NO_SOURCE',flush=True)
        else:
            await mass.player_queues.play_media(TARGET,f'http://{address}:18088/old.wav',option='replace')
            await asyncio.sleep(3)
        await event('auth_state',logged_in=True,is_active=not cold_only)
        daemon.spotify_session_active=not cold_only
        await event('playback_state',status='playing',volume=27)
        assert adapter.authority._token is None
        print('OLD_SNAPSHOT_DENIED',flush=True);await asyncio.to_thread(__import__('sys').stdin.readline)
        if restart_only and restart_phase==0:
            print('MA_RESTART_ARMED',flush=True);await asyncio.to_thread(__import__('sys').stdin.readline)
            return
        await event('playback_changed',status='paused')
        await event('playback_changed',status='buffering')
        await event('playback_state',status='buffering')
        await event('playback_changed',status='playing')
        if cold_only:
            assert adapter.authority._token is None and bridge.graph is None
            await event('playback_state',status='playing')
            await asyncio.sleep(.2)
            await event('device_changed',is_active=True)
            await daemon.pending_play_media_task
        assert bridge.discarded_capture_bytes==len(b'OLD_CAPTURE_EPOCH'*100)
        assert adapter.authority._token._used
        if first_only:
            await asyncio.sleep(3)
            assert provider.starts==1 and daemon.active_session_id and bridge.stream_session==daemon.active_session_id
            if cold_only:assert mass.players.get_player_state(TARGET).powered is True
            print('NEW_STREAM_BOUND',flush=True);await asyncio.to_thread(__import__('sys').stdin.readline)
            await event('playback_changed',status='stopped',is_active=False)
            await asyncio.sleep(1)
            print('RECEIVER_STOPPED',flush=True);await asyncio.to_thread(__import__('sys').stdin.readline)
            if restart_only:
                print('COLD_PHASE_COMPLETE',flush=True)
                return
            if cold_only:
                await mass.players.cmd_power(TARGET,False)
                for _ in range(50):
                    if mass.players.get_player_state(TARGET).powered is False:break
                    await asyncio.sleep(.1)
                assert mass.players.get_player_state(TARGET).powered is False
                print('STOP_FRESH_POWER_OFF',flush=True);await asyncio.to_thread(__import__('sys').stdin.readline)
                # The second real delta ordering is active-before-Play.
                await event('device_changed',is_active=True)
                await event('playback_changed',status='playing')
                await daemon.pending_play_media_task
                await asyncio.sleep(2)
                assert provider.starts==2 and mass.players.get_player_state(TARGET).powered is True
                print('STOP_FRESH_BOUND',flush=True);await asyncio.to_thread(__import__('sys').stdin.readline)
                await event('playback_changed',status='stopped',is_active=False)
                await asyncio.sleep(1)
                print('STOP_FRESH_CLOSED',flush=True);await asyncio.to_thread(__import__('sys').stdin.readline)
                print('COLD_PHASE_COMPLETE',flush=True)
            return
        daemon.stop_called=False
        await asyncio.sleep(6) # actual muted startup/ramp completes before cursor sampling
        for duration in (3,11):
            await asyncio.sleep(2)
            print('CURSOR_BEFORE_'+str(duration),flush=True);await asyncio.to_thread(__import__('sys').stdin.readline)
            print('source_state',adapter.authority.state,bridge.status,daemon.in_use_by_player,mass.players.get_player_state(TARGET).playback_state.value,flush=True,file=__import__('sys').stderr)
            print('pause_enter',duration,flush=True,file=__import__('sys').stderr)
            await asyncio.wait_for(event('playback_changed',status='paused',is_active=True),10)
            print('pause_sleep',duration,flush=True,file=__import__('sys').stderr)
            await asyncio.sleep(duration)
            print('CURSOR_PAUSED_'+str(duration),flush=True);await asyncio.to_thread(__import__('sys').stdin.readline)
            await event('playback_changed',status='playing',is_active=True)
            await asyncio.sleep(3)
            print('CURSOR_AFTER_'+str(duration),flush=True);await asyncio.to_thread(__import__('sys').stdin.readline)
        daemon.stop_called=False
        await asyncio.sleep(3)
        oldsession=daemon.active_session_id;oldgraph=bridge.graph
        before=getattr(mass.players.get_player_state(TARGET),'elapsed_time',None)
        assert isinstance(before,(int,float)) and before>0,('actual MA nonzero cursor absent',before)
        await event('playback_changed',status='paused',is_active=True)
        await asyncio.sleep(1)
        assert daemon.active_session_id==oldsession and daemon.in_use_by_player==TARGET and provider.starts==1
        assert mass.players.get_player_state(TARGET).playback_state.value=='paused' # source retained; real transport frozen
        await event('playback_changed',status='playing',is_active=True)
        await asyncio.sleep(1)
        after=getattr(mass.players.get_player_state(TARGET),'elapsed_time',None)
        assert after>=before and provider.starts==1 and daemon.active_session_id==oldsession and bridge.graph==oldgraph
        await event('playback_changed',status='paused',is_active=True)
        # Accelerate ONLY the synthetic paused-idle deadline. Actual watchdog,
        # IPC/native closure and fresh Resume preparation execute unchanged.
        adapter.authority.pause_deadline=time.monotonic()-1
        await asyncio.sleep(2)
        assert adapter.authority._token is None and daemon.active_session_id==oldsession
        await event('playback_changed',status='playing',is_active=True)
        await asyncio.sleep(2)
        assert provider.starts==1 and daemon.active_session_id==oldsession and adapter.authority._token._used
        assert bridge.stream_session==oldsession and bridge.discarded_capture_bytes==0
        assert mass.players.get_player_state(TARGET).elapsed_time>=before
        (directory/'resume-timeline.json').write_text(json.dumps({'before_nonzero_actual_MA_cursor':before,'after_resume_actual_MA_cursor':after,'play_media_calls':provider.starts,'same_stream_session':True,'same_native_graph':True,'receiver_events':'synthetic seam','normalized_pause_play':'actual installed official SpotifyConnectProvider code','realSpotify_or_phone':False}))
        print('RESUME_CURSOR_RETAINED',flush=True);await asyncio.to_thread(__import__('sys').stdin.readline)
        print('NEW_STREAM_BOUND',flush=True);await asyncio.to_thread(__import__('sys').stdin.readline)
        await event('playback_changed',status='stopped',is_active=True)
        if bridge.stop_task:await bridge.stop_task
        print('RECEIVER_STOPPED',flush=True);await asyncio.to_thread(__import__('sys').stdin.readline)
    except BaseException:
        import traceback
        traceback.print_exc()
        raise
    finally:
        if adapter:await adapter.close()
        if bridge:await bridge.close()
        if listener:listener.cancel();await asyncio.gather(listener,return_exceptions=True)
        if writer is not None:os.close(writer)
        await asyncio.wait_for(mass.stop(),15);await runner.cleanup()

if __name__=='__main__':asyncio.run(main())
