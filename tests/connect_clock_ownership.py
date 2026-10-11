"""Actual installed official methods; synthetic receiver/MA transport seams only.
No account, network, production command, native output or phone proof.
"""
import asyncio,json,logging,time
from types import SimpleNamespace as NS
from music_assistant.providers.spotify_connect.provider import SpotifyConnectProvider as P
from music_assistant.providers.spotify_connect.soloist.backend import SoloistBackend
from music_assistant.providers.spotify_connect.soloist.runtime import SoloistEvent,SoloistPlaybackState,SoloistPosition,SoloistPositionSync
from music_assistant.providers.spotify_connect.models import BackendEvent,BackendEventType,BackendTrackMetadata
from music_assistant.providers.garage_speaker_control.connect_adapter import ConnectAdapter
from music_assistant.providers.garage_speaker_control.connect_authority import ReceiverBinding,TARGET

async def case(adapted):
    calls=[];trace=[];now=time.time()
    class Socket:
        closed=False
        async def send_json(self,value):calls.append(('receiver_command',value))
    class Client:
        mass=None
        def __init__(self):self._ws=None;self._read_endpoint=lambda:('127.0.0.1',54321);self.ready=asyncio.Event();self.end=asyncio.Event()
        @property
        def connected(self):return self._ws is not None and not self._ws.closed
        async def listen_events(self,callback):self._ws=Socket();self.callback=callback;self.ready.set();await self.end.wait()
        async def _send_command(self,name,**kwargs):calls.append(('receiver_command',name))
        def _fail_pending_results(self):pass
    class Bridge:
        async def attest(self,*args):return ReceiverBinding('synthetic-clock-source',(1,2,'fixture','sha'),1)
        async def prepare(self,*args):return 'synthetic-zero-graph'
        async def pulse(self,*args):return True
        async def observe_volume(self,*args):pass
        def pause(self):calls.append(('native','mute'))
        def revoke(self):calls.append(('native','stop'))
    class Player:
        async def pause(self):calls.append(('player','pause'))
        async def play(self):calls.append(('player','play'))
    class Players:
        def get_player(self,*args):return Player()
        def update_source_metadata(self,*args):pass
        async def cmd_stop(self,*args):calls.append(('player','stop'))
    backend=SoloistBackend.__new__(SoloistBackend)
    backend.logger=logging.getLogger('clock');backend.name='synthetic';backend._last_track_uri=None;backend._last_context_uri=None;backend._spotify_volume=None;backend._sink=NS();backend._client=Client()
    daemon=NS(backend=backend,player_id=TARGET,active_player_id=TARGET,in_use_by_player=TARGET,active_session_id='synthetic-current-source',spotify_session_active=True,playing=True,stop_called=False,pending_pause_stop_task=None,pending_play_media_task=None,publish_name='synthetic',stream_metadata=NS(elapsed_time=31,elapsed_time_last_updated=now))
    class Provider:
        instance_id='synthetic-clock-source';domain='spotify_connect'
        _handle_backend_event=P._handle_backend_event
        _remember_context_uris=P._remember_context_uris
        _schedule_pause_stop=P._schedule_pause_stop
        _cancel_pending_pause_stop=P._cancel_pending_pause_stop
        _on_pause_stop_done=P._on_pause_stop_done
        _stop_paused_player=P._stop_paused_player
        _cancel_pending_play_media=P._cancel_pending_play_media
        _schedule_play_media=P._schedule_play_media
        _deferred_play_media_fire=P._deferred_play_media_fire
        _apply_metadata=P._apply_metadata
        def __init__(self):self.logger=logging.getLogger('clock');self.mass=NS(loop=asyncio.get_running_loop(),players=Players(),create_task=asyncio.create_task)
    provider=Provider();adapter=None
    async def normalized(event):await provider._handle_backend_event(daemon,event)
    backend._event_callback=normalized
    if adapted:adapter=ConnectAdapter(backend,provider,daemon,Bridge());adapter.install()
    listener=asyncio.create_task(backend._client.listen_events(backend._handle_event));await backend._client.ready.wait()
    async def raw(kind,data):
        await backend._client.callback(SoloistEvent(kind,data,{}))
        trace.append({'kind':kind,'raw_position_ms':getattr(getattr(data,'position',None),'position_ms',None),'raw_timestamp_ms':getattr(getattr(data,'position',None),'timestamp_ms',None),'MA_metadata_seconds':daemon.stream_metadata.elapsed_time,'daemon_playing':daemon.playing,'calls':list(calls)})
    try:
        if adapted:
            a=adapter.authority;b=a.binding
            a.event(b,1,'auth_state',logged_in=True,active=True)
            a.event(b,2,'playback_state',status='paused',active=True)
            token=a.event(b,3,'playback_changed',status='playing',active=True)
            a.consume(token,target=TARGET,graph_generation='synthetic-zero-graph');adapter.sequence=3
        for duration in (3,11):
            await raw('position_sync',SoloistPositionSync(SoloistPosition(42123,int(now*1000))))
            await raw('playback_changed',SoloistPlaybackState('paused',position=SoloistPosition(44200,int((now+2)*1000),0),is_active=True))
            await asyncio.sleep(.2) # real upstream deferred Stop executes
            # A delayed position update before the pause can arrive afterwards.
            await raw('position_sync',SoloistPositionSync(SoloistPosition(40100,int((now-2)*1000),1)))
            await raw('playback_state',SoloistPlaybackState('playing',position=SoloistPosition(44200,int((now+duration+2)*1000)),is_active=True))
            await raw('playback_changed',SoloistPlaybackState('playing',position=SoloistPosition(44200,int((now+duration+2)*1000)),is_active=True))
        await provider._handle_backend_event(daemon,BackendEvent(type=BackendEventType.METADATA,metadata=BackendTrackMetadata(track_uri='synthetic:next',title='synthetic',position=0)))
        await raw('position_sync',SoloistPositionSync(SoloistPosition(73000,int((now+30)*1000))))
        assert daemon.stream_metadata.elapsed_time==73
        assert not any(c[0]=='receiver_command' for c in calls),'incoming state must not seek/play/pause receiver'
        return {'adapter':adapted,'trace':trace,'receiver_outgoing_commands':[],'track_change_then_seek_metadata_seconds':73,'coverage':'actual official raw/normalization/provider methods; synthetic receiver/MA transport; no network/accounts/FILE/phone'}
    finally:
        if adapter:await adapter.close()
        listener.cancel();await asyncio.gather(listener,return_exceptions=True)
        provider._cancel_pending_pause_stop(daemon)

async def main():
    plain=await case(False);adapted=await case(True)
    assert [x['MA_metadata_seconds'] for x in plain['trace']]==[x['MA_metadata_seconds'] for x in adapted['trace']]
    assert any(x[0]=='player' and x[1]=='stop' for r in plain['trace'] for x in r['calls'])
    assert not any(x[0]=='player' and x[1]=='stop' for r in adapted['trace'] for x in r['calls'])
    print(json.dumps({'unmodified_official':plain,'installed_adapter':adapted,'same_official_position_behavior':True},indent=2))
if __name__=='__main__':asyncio.run(main())
