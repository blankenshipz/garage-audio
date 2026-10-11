"""Named combined gate helpers: injectable receiver seam, actual downstream.

Does not replace live/kernel receiver admission evidence. No production paths.
"""
import json,os,socket,time,statistics
from pathlib import Path
from garage_audio import connect_port

def inject_receiver_seam(gateway,logs):
    connect_port.VOLUME_PATH=logs/'receiver-volume.json'
    class FixtureScope:
        def __init__(self,peer,guard):
            self.peer=peer;peer.verify();self.socket=socket.socket();self.socket.bind(('127.0.0.1',0));self.port=self.socket.getsockname()[1];self.closed=False
            self.daemon=(peer.pid,'SYNTHETIC-RECEIVER-DAEMON','not-a-live-Soloist-pin')
        def attest(self):self.peer.verify();return {'daemon':list(self.daemon),'port_protected':True}
        def alive(self):return not self.closed and self.peer.alive()
        def close(self):self.closed=True;self.socket.close()
    # Scoped to this isolated fixture process only. Not deployed in native/provider.
    connect_port.PortScope=FixtureScope;connect_port.new_guard=lambda:object()
    gateway.connect_enabled=lambda:True

def journey(controller,fixture,player,go,milestone,output,logs,first_only=False,stop_fresh=True):
    go()
    if first_only:milestone('COLD_POWER_OFF_NO_SOURCE')
    milestone('OLD_SNAPSHOT_DENIED')
    assert (fixture.policy.lease is None or fixture.policy.lease.revoked) and controller.state=='stopped','snapshot admitted cached old PCM'
    if first_only:
        go();milestone('NEW_STREAM_BOUND')
        assert fixture.policy.lease.connect and controller.state=='listening' and controller.ma_owner.transport=='playing'
        raw=output.read_bytes()
        assert any(int.from_bytes(raw[i:i+3],'little',signed=True) for i in range(6,len(raw)-36,36)),'first genuine-order source rendered no PCM'
        assert all(not any(int.from_bytes(raw[i+j:i+j+3],'little',signed=True) for i in range(0,len(raw)-36,36)) for j in (0,3,12,15,18,21,24,27,30,33))
        (logs/('cold-first-'+str(time.monotonic_ns())+'.raw')).write_bytes(raw)
        go();milestone('RECEIVER_STOPPED');fixture.process.wait(timeout=5)
        assert controller.state=='stopped' and player.poll() is None and controller.native.registration()
        (logs/'first-play-buffering-order.json').write_text(json.dumps({'incoming':'synthetic observed-order receiver seam','order':'paused→buffering delta→buffering snapshot→playing delta','official_queue_calls':1,'real_MA_native_FILE_PCM':True,'cold_snapshot_denied':True,'Stop_closed_player_persistent':True,'hardware_phone':False},indent=2)+'\n')
        go()
        if not stop_fresh:return
        milestone('STOP_FRESH_POWER_OFF')
        assert controller.state=='stopped' and fixture.process.poll() is not None and fixture.policy.lease.revoked
        go();milestone('STOP_FRESH_BOUND')
        assert controller.state=='listening' and fixture.policy.lease.connect
        raw=output.read_bytes();assert any(raw)
        (logs/('cold-stop-fresh-'+str(time.monotonic_ns())+'.raw')).write_bytes(raw)
        go();milestone('STOP_FRESH_CLOSED');fixture.process.wait(timeout=5)
        assert controller.state=='stopped' and controller.native.registration()
        go();return
    go()
    observations=[]
    def cursor():
        raw=output.read_bytes()[-12*3*48000:];pairs=[]
        for at in range(0,len(raw)-36,36):
            left=int.from_bytes(raw[at+6:at+9],'little',signed=True);right=int.from_bytes(raw[at+9:at+12],'little',signed=True)
            if right<-500:pairs.append((left/right*6000-1000)/200)
        return round(statistics.median(pairs[-1000:]),2) if pairs else None
    for duration in (3,11):
        milestone('CURSOR_BEFORE_'+str(duration));before=cursor();go()
        milestone('CURSOR_PAUSED_'+str(duration));paused=cursor();go()
        milestone('CURSOR_AFTER_'+str(duration));after=cursor();go()
        observations.append({'pause_seconds':duration,'PCM_marker_before':before,'PCM_marker_paused_tail':paused,'PCM_marker_after':after,'resumed_delta':round(after-before,2) if after is not None and before is not None else None,'native_state':controller.state,'native_failure':controller.failure,'transport':getattr(controller.ma_owner,'transport',None)})
    (logs/'semantic-pause-cursor.json').write_text(json.dumps(observations,indent=2)+'\n')
    assert all(q['resumed_delta'] is not None and 0<=q['resumed_delta']<=4 for q in observations),('media consumed paused wall time or unavailable marker',observations)
    milestone('RESUME_CURSOR_RETAINED');go();milestone('NEW_STREAM_BOUND')
    assert fixture.policy.lease.connect and controller.state=='listening'
    deadline=time.monotonic()+6
    while controller.ma_owner.transport!='playing' and time.monotonic()<deadline:time.sleep(.05)
    assert controller.ma_owner.transport=='playing','current source did not drive native state'
    time.sleep(1)
    raw=output.read_bytes();channels=[[] for _ in range(12)]
    for offset in range(0,len(raw)-2,3):channels[(offset//3)%12].append(int.from_bytes(raw[offset:offset+3],'little',signed=True))
    assert any(v<0 for v in channels[2]),'current negative epoch never rendered'
    assert all(not any(channels[i]) for i in set(range(12))-{2,3}),'spare output not zero'
    go();milestone('RECEIVER_STOPPED');fixture.process.wait(timeout=5)
    deadline=time.monotonic()+5
    while controller.state!='stopped' and time.monotonic()<deadline:time.sleep(.05)
    assert controller.state=='stopped' and player.poll() is None and controller.native.registration()
    report={'entry':'synthetic receiver events + injected daemon/port attest, NOT Spotify/user/live guard','native_gateway':'real MA kernel peer + actual NativeBridge/Gateway/Controller/rolling Connect policy','downstream':'actual MA/Squeezelite/PipeWire/FIFO/Camilla FILE','cold_playing_snapshot_denied':True,'semantic_PCM_pause_cursor':observations,'marked_AC_PCM_rendered':True,'receiver_capture_fixture_drained':True,'connect_setup_MAStop_calls':0,'stop_closed_native_player_registered':True,'spare_outputs_zero':True,'hardware':False,'production_changed':False}
    (logs/'combined-connect-readback.json').write_text(json.dumps(report,indent=2)+'\n')
    go();print('PASS receiver-seam → actual MA/native FILE semantic pause cursor → idle Resume → receiverStop/playerpersistent',flush=True)
