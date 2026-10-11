"""Isolated actual MA→Squeezelite→private PipeWire→protected FIFO→Camilla FILE.
Known synthetic program; FILE-only identity, no device nodes/production accounts.
Run host-side via installed native venv, fixed external MA image test container.
"""
import array,copy,hashlib,http.client,json,math,os,select,socket,subprocess,sys,threading,time,shutil,faulthandler,re
from pathlib import Path
import native_playback as fixtures
from ma_bridge_file import FileBackend,read_line
from garage_audio.ma_bridge import Gateway,process_reference
from garage_audio.ma_reference import verify_description,reconcile,publish
from garage_audio.speaker_controller import Controller
from garage_audio.profile import compile_profile

ROOT=Path(__file__).resolve().parents[1]
fixtures.ROOT=ROOT;fixtures.ENV={**os.environ,'LD_LIBRARY_PATH':str(ROOT/'tools/libs/usr/lib/x86_64-linux-gnu')}
IMAGE='sha256:28023f8c0d96ca2496391f3218a6d70d7ef3ba84db846b3a3f3cd9ce7ef8aedc'
NAME=sys.argv[1]
MODE=sys.argv[2] if len(sys.argv)>2 else 'journey'
assert re.fullmatch('garage-normal-file-[a-z0-9-]+',NAME)
state=Path(os.environ.get('GARAGE_TEST_STATE_ROOT',str(ROOT/'state')))/('case-'+hashlib.sha256(NAME.encode()).hexdigest()[:8]);state.mkdir(parents=True,mode=0o700,exist_ok=False)
runtime=state/'runtime';runtime.mkdir(mode=0o700);socketdir=state/'ma-socket';socketdir.mkdir(mode=0o700);madata=state/'ma';madata.mkdir(mode=0o700)
image_data=state/'image-data';image_data.mkdir(mode=0o700)
os.chown(madata,1000,1000);os.chown(image_data,1000,1000)
assert len(str(socketdir/'control.sock').encode())<108,'bounded private AF_UNIX fixture path required'
logs=state/'logs';logs.mkdir(mode=0o700);children=[];handles=[];worker=None;fixture=None
def launch(argv,env=None,stdout=None):
    log=(logs/(str(len(children))+'.stderr.log')).open('wb');handles.append(log)
    process=subprocess.Popen(argv,env=env,stdout=stdout or subprocess.DEVNULL,stderr=log);children.append(process);return process
def cli(ip,player,command):
    c=http.client.HTTPConnection(ip,9000,timeout=3)
    try:
        c.request('POST','/jsonrpc.js',json.dumps({'id':1,'method':'slim.request','params':[player,command]}),{'Content-Type':'application/json'})
        reply=c.getresponse();body=reply.read(32769);assert reply.status==200 and len(body)<=32768
        value=json.loads(body);assert 'error' not in value;return value['result']
    finally:c.close()
def go():worker.stdin.write('GO\n');worker.stdin.flush()
def milestone(expected):assert read_line(worker,90)==expected

try:
    faulthandler.dump_traceback_later(20,repeat=False)
    log=(logs/'ma.stderr.log').open('wb');handles.append(log)
    args=['docker','run','-i','--name',NAME,'--network','garage-ma-context-r16','--read-only','--memory','1g','--cpus','2','--tmpfs','/tmp:rw,nosuid,size=64m','--tmpfs','/run:rw,nosuid,size=8m','-v',str(ROOT)+':/fixture:ro','-v',str(madata)+':/test-state','-v',str(socketdir)+':/run/garage-speakers-ma:ro','-v',str(ROOT/'ma_provider/garage_speaker_control')+':/app/venv/lib/python3.14/site-packages/music_assistant/providers/garage_speaker_control:ro','-e','PYTHONPATH=/fixture','--entrypoint','/app/venv/bin/python',IMAGE,'/fixture/tests/normal_ma_worker.py']
    args[2:2]=['--user','1000:1000','--label','com.docker.compose.project=garage-audio-isolated-test','--label','com.docker.compose.service=music-assistant','--label','com.docker.compose.container-number=1','--label','com.docker.compose.project.working_dir='+str(ROOT),'-v',str(image_data)+':/data']
    args.append(MODE)
    if MODE in ('connect_combined','connect_first_play'):
        args[-2]='/fixture/tests/connect_combined_worker.py';args.pop()
        if MODE=='connect_first_play':args[2:2]=['-e','GARAGE_FIRST_PLAY_ONLY=1','-e','GARAGE_COLD_PLAY_ONLY=1']
        if os.environ.get('GARAGE_RESTART_ONLY')=='1':args[2:2]=['-e','GARAGE_RESTART_ONLY=1']
    worker=subprocess.Popen(args,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=log,text=True,bufsize=1)
    milestone('READY')
    info=json.loads(subprocess.check_output(['docker','inspect',NAME]))[0];ip=info['NetworkSettings']['Networks']['garage-ma-context-r16']['IPAddress'];pid=info['State']['Pid']
    env={**os.environ,'PIPEWIRE_RUNTIME_DIR':str(runtime),'PIPEWIRE_REMOTE':'garage-pipewire','XDG_RUNTIME_DIR':str(runtime),'XDG_STATE_HOME':str(state/'wp-state'),'XDG_CACHE_HOME':str(state/'wp-cache'),'WIREPLUMBER_CONFIG_DIR':str(ROOT/'config')+':/usr/share/wireplumber','ALSA_CONFIG_PATH':str(ROOT/'config/asound.conf')}
    pw=launch(['/usr/bin/pipewire','-c',str(ROOT/'config/pipewire.conf')],env)
    for _ in range(80):
        if (runtime/'garage-pipewire').exists():break
        time.sleep(.1)
    wp=launch(['/usr/bin/wireplumber'],env);time.sleep(2)
    player=launch(['/usr/bin/squeezelite','-n','Garage Speakers','-m','02:00:00:00:21:01','-s',ip+':3483','-o','garage_music','-r','48000-48000','-a','80:4:32:1'],env)
    for _ in range(120):
        if worker.poll() is not None:raise AssertionError('MA exited during transport registration')
        try:
            inventory=cli(ip,'',['players',0,100])['players_loop']
            if any(p.get('playerid')=='02:00:00:00:21:01' and p.get('connected')==1 for p in inventory):break
        except Exception:pass
        time.sleep(.1)
    else:raise AssertionError('actual isolated MA/Squeezelite registration failed')
    fifo=state/'capture.fifo';os.mkfifo(fifo,0o600);fifo_fd=os.open(fifo,os.O_RDWR)
    monitor=launch(['/usr/bin/pw-cat','--record','--raw','--target','garage_music','--properties=stream.capture.sink=true node.dont-fallback=true node.dont-move=true','--rate','48000','--channels','2','--format','f32','-'],env,stdout=fifo_fd)
    class TransportFixture(fixtures.NativeFileTests):
        def feed(self):
            try:
                while self.process.poll() is None:
                    raw=os.read(fifo_fd,8192)
                    if raw:self.process.stdin.write(raw);self.process.stdin.flush()
            except (OSError,ValueError):pass
    fixture=TransportFixture();fixture.setUp();fixture.helper.terminate();fixture.helper.wait(timeout=3)
    fixture.helper=subprocess.Popen([sys.executable,'-c','import time;time.sleep(600)']);fixture.peer=fixtures.SyntheticPeer(fixture.helper)
    output=logs/'packed24.raw'
    fixture.policy.config_factory=lambda p:compile_profile(p,{'type':'Stdin','channels':2,'format':'F32_LE'},{'type':'File','channels':12,'format':'S24_3_LE','filename':str(output)})
    ramp_commands=[]
    class ObservedVolume:
        def __init__(self,actual):self.actual=actual
        def __getattr__(self,name):return getattr(self.actual,name)
        def set_main_volume(self,value):
            if value==0 and output.exists():ramp_commands.append({'at':time.monotonic(),'output_bytes':output.stat().st_size})
            return self.actual.set_main_volume(value)
    class ObservedClient:
        # Passive timing observation of the actual SDK/FILE cursor; no commands,
        # results or samples are mocked or replaced.
        def __init__(self):
            self.actual=fixture.client();self.volume=ObservedVolume(self.actual.volume)
        def __getattr__(self,name):return getattr(self.actual,name)
    fixture.policy.client_factory=ObservedClient
    class ActualBackend(FileBackend):
        def registration(self):return any(p.get('playerid')=='02:00:00:00:21:01' and p.get('name')=='Garage Speakers' and p.get('connected')==1 for p in cli(ip,'',['players',0,100])['players_loop'])
        def start(self,cancel,connect=False):
            if connect:
                assert self.registration()
                if MODE in ('connect_combined','connect_first_play') and fixture.process.poll() is not None:
                    # Recreate the FILE-only DSP owner just as production start
                    # creates a fresh muted process after paused idle expiry.
                    fixture.process=subprocess.Popen([str(ROOT/'tools/camilladsp'),'-a','127.0.0.1','-p',str(fixture.port),'--wait','--statefile',str(fixture.state/'camilla-state.yml'),'--no_config','--gain=-60','--mute'],stdin=subprocess.PIPE,stdout=fixture.stdout,stderr=fixture.stderr,env=fixtures.ENV)
                    oldpolicy=fixture.policy
                    fixture.policy=type(oldpolicy)(fixture.state,oldpolicy.client_factory,oldpolicy.config_factory,fixture.process,hardware_binding=lambda:copy.deepcopy(fixture.binding))
                    for _ in range(40):
                        c=fixture.client()
                        try:c.connect();c.disconnect();break
                        except OSError:time.sleep(.05)
                    else:raise RuntimeError('fresh FILE DSP did not start')
                    threading.Thread(target=fixture.feed,daemon=True).start()
                return super().start(cancel) # no MA Stop in receiver preparation
            assert self.registration();cli(ip,'02:00:00:00:21:01',['stop']);s=cli(ip,'02:00:00:00:21:01',['status',0,1])
            assert s.get('mode')=='stop' or ('mode' not in s and s.get('power')==0 and s.get('player_connected')==1 and s.get('player_name')=='Garage Speakers' and s.get('waitingToPlay')==0)
            return super().start(cancel)
    provider='/app/venv/lib/python3.14/site-packages/music_assistant/providers/garage_speaker_control'
    assets={n:hashlib.sha256((ROOT/'ma_provider/garage_speaker_control'/n).read_bytes()).hexdigest() for n in ('__init__.py','client.py','manifest.json','connect_authority.py','connect_adapter.py','connect_runtime.py')}
    reference={'process':process_reference(pid),'provider_path':provider,'assets':assets}
    pin={k:reference['process'][k] for k in ('exe','exe_sha256','argv_sha256')};pin.update(image=IMAGE,assets=assets)
    expected={'name':NAME,'network':'garage-ma-context-r16','project':'garage-audio-isolated-test','directory':str(ROOT),'provider':str(ROOT/'ma_provider/garage_speaker_control'),'socket':str(socketdir),'data':str(image_data)}
    assert verify_description(info,pin,expected)==reference
    bad=copy.deepcopy(info);bad['Config']['Labels']['com.docker.compose.project']='forged-project'
    try:verify_description(bad,pin,expected)
    except PermissionError:pass
    else:raise AssertionError('service-project drift accepted')
    controller=Controller(ActualBackend(fixture));gateway=Gateway(controller,reference)
    if MODE in ('connect_combined','connect_first_play'):
        from connect_combined_host import inject_receiver_seam
        inject_receiver_seam(gateway,logs)
    controller.normal_available=True # actual observed isolated service pin above, not caller JSON authority
    threading.Thread(target=gateway.serve,args=(socketdir/'control.sock',),daemon=True).start()
    for _ in range(80):
        if controller.ma_available:break
        time.sleep(.05)
    assert controller.ma_available,'actual gateway listener did not bind'
    assert socketdir.stat().st_uid==0 and socketdir.stat().st_mode&0o777==0o711
    assert (socketdir/'control.sock').stat().st_uid==1000 and (socketdir/'control.sock').stat().st_mode&0o777==0o600
    for caller in ('1000:1000','0:0'):
        # An unrelated process with the same UID/container or root cannot act
        # as the exact pinned MA process. No grant or Stop reaches controller.
        probe="import socket,json\ns=socket.socket(socket.AF_UNIX)\ns.connect('/run/garage-speakers-ma/control.sock')\ntry:\n s.sendall(b'{\"cmd\":\"stop\"}\\n'); raw=s.recv(4096); assert not raw or not json.loads(raw).get('ok')\nexcept ConnectionResetError:pass\nprint('DENIED')"
        denied=subprocess.check_output(['docker','exec','--user',caller,NAME,'/app/venv/bin/python','-c',probe],text=True).strip()
        assert denied=='DENIED' and controller.state=='stopped'
    if MODE in ('browser','browser_add'):
        print('BROWSER_READY',json.dumps({'container':NAME,'ip':ip,'state_directory':str(state),'credential_reference':str(madata/'browser-login.json')}),flush=True)
        go();time.sleep(180);sys.exit(0)
    if MODE in ('connect_combined','connect_first_play'):
        faulthandler.cancel_dump_traceback_later()
        from connect_combined_host import journey
        if os.environ.get('GARAGE_RESTART_ONLY')=='1':
            go();milestone('COLD_POWER_OFF_NO_SOURCE');milestone('OLD_SNAPSHOT_DENIED')
            assert controller.state=='stopped' and fixture.policy.lease is None
            go();milestone('MA_RESTART_ARMED')
        else:journey(controller,fixture,player,go,milestone,output,logs,first_only=MODE=='connect_first_play')
        if MODE=='connect_first_play':
            if os.environ.get('GARAGE_RESTART_ONLY')!='1':milestone('COLD_PHASE_COMPLETE')
            # Official slimproto.stop waits for the intentionally persistent
            # player's connection; restart this isolated MA owner explicitly.
            subprocess.check_call(['docker','stop','--time','3',NAME],stdout=subprocess.DEVNULL)
            worker.wait(timeout=10)
            worker=subprocess.Popen(['docker','start','-i',NAME],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=log,text=True,bufsize=1)
            milestone('READY')
            fresh=json.loads(subprocess.check_output(['docker','inspect',NAME]))[0]
            updated=verify_description(fresh,pin,expected);assert updated['process']['pid']!=pid
            ref_path=logs/'fixture-ma-peer.json';reconcile(controller,gateway,updated,publisher=lambda ref:publish(ref,ref_path))
            assert controller.state=='stopped' and controller.ma_owner is None and fixture.process.poll() is not None
            journey(controller,fixture,player,go,milestone,output,logs,first_only=True,stop_fresh=os.environ.get('GARAGE_RESTART_ONLY')!='1')
            milestone('COLD_PHASE_COMPLETE')
            subprocess.check_call(['docker','stop','--time','3',NAME],stdout=subprocess.DEVNULL)
            worker.wait(timeout=10)
            (logs/'cold-restart-proof.json').write_text(json.dumps({'initial_player_OFF':True,'native_closed_no_manual_grant':True,'warmup':False,'late_active_first_Play_FILE':True,'Stop_OFF_freshPlay_FILE':('reused qp21ki2l, same b81fbf71 product revision' if os.environ.get('GARAGE_RESTART_ONLY')=='1' else True),'actual_MA_container_restart':True,'new_PID_and_kernel_reference':True,'restart_cached_snapshot_denied':True,'restart_freshPlay_FILE':True,'receiver_and_attestation':'synthetic seams','hardware_phone':False},indent=2)+'\n')
        sys.exit(0)
    if MODE=='integration':
        go();milestone('DENIED_INTEGRATION');assert fixture.policy.lease is None and controller.state=='stopped'
        assert controller.native.registration();go()
        faulthandler.cancel_dump_traceback_later()
        print('PASS normal interactive-action integration token denied; actual MA player still registered; no native approval',flush=True)
        sys.exit(0)
    go();milestone('STARTED_MUTED');assert fixture.policy.lease.normal;assert fixture.policy.lease.ceiling==0
    faulthandler.cancel_dump_traceback_later()
    c=fixture.client();c.connect();assert c.volume.main_mute();c.disconnect()
    if MODE=='restart_only':
        # Kill the actually active warm-ready MA owner before replacing its pin;
        # the native process must independently close while transport persists.
        old_pid=pid
        subprocess.check_call(['docker','stop','--time','3',NAME],stdout=subprocess.DEVNULL)
        fixture.process.wait(timeout=5)
        worker.wait(timeout=10)
        worker=subprocess.Popen(['docker','start','-i',NAME],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=log,text=True,bufsize=1)
        milestone('READY')
        fresh=json.loads(subprocess.check_output(['docker','inspect',NAME]))[0]
        updated=verify_description(fresh,pin,expected);assert updated['process']['pid']!=old_pid
        ref_path=logs/'fixture-ma-peer.json';reconcile(controller,gateway,updated,publisher=lambda ref:publish(ref,ref_path))
        assert ref_path.stat().st_mode&0o777==0o600
        assert controller.state=='stopped' and controller.ma_owner is None and fixture.process.poll() is not None
        assert controller.normal_available and controller.ma_available
        print('PASS real isolated MA restart: old warm-ready output independently closed, newly pinned actual PID/namespace and atomic root reference, no approval/output restored',flush=True)
        sys.exit(0)
    start=time.monotonic();go();milestone('PLAYING10');time.sleep(4)
    if MODE not in ('journey','controls_only','controls100'):
        if MODE in ('logout','permission','expired'):
            go();milestone('REVOKED_REGISTERED');fixture.process.wait(timeout=5);assert player.poll() is None and controller.native.registration();go()
        elif MODE=='worker_death':
            subprocess.check_call(['docker','kill',NAME],stdout=subprocess.DEVNULL);fixture.process.wait(timeout=5);assert player.poll() is None
        elif MODE=='ref_drift':
            reference['process']['exe_sha256']='0'*64;fixture.process.wait(timeout=5);assert controller.native.registration()
        elif MODE=='deadline':
            fixture.policy.lease.deadline=fixture.policy.lease.clock()+.5;fixture.process.wait(timeout=3);assert controller.native.registration()
        elif MODE=='device_drift':
            fixture.binding['identity']['serial']='FAULT_INJECTED_FILE_IDENTITY';fixture.process.wait(timeout=5);assert controller.native.registration()
        elif MODE=='watchdog_stall':
            fixture.policy.lock.acquire()
            try:
                tick=time.monotonic();fixture.process.wait(timeout=12);assert time.monotonic()-tick<12
            finally:fixture.policy.lock.release()
            assert controller.native.registration()
        else:raise AssertionError('unknown scenario')
        print('PASS normal-session actual MA/PW/FIFO/FILE revocation',MODE,'owned DSP exit; no production hardware; deadline/device fault injections synthetic',flush=True)
        sys.exit(0)
    markers={'ten_start':output.stat().st_size};time.sleep(3);markers['ten_end']=output.stat().st_size
    go();milestone('PLAYING50');time.sleep(4);markers['fifty_start']=output.stat().st_size;time.sleep(3);markers['fifty_end']=output.stat().st_size
    if MODE=='controls100':
        go();milestone('PLAYING100');time.sleep(2);markers['hundred_start']=output.stat().st_size;time.sleep(2);markers['hundred_end']=output.stat().st_size
    go();milestone('PAUSED');time.sleep(2);markers['pause_start']=output.stat().st_size;time.sleep(2);markers['pause_end']=output.stat().st_size
    c=fixture.client();c.connect();assert c.volume.main_mute();c.disconnect()
    markers['resume_start']=output.stat().st_size;go();milestone('RESUMED50');time.sleep(5);markers['resume_end']=output.stat().st_size
    remaining=(132 if MODE=='journey' else 0)-(time.monotonic()-start)
    if remaining>0:time.sleep(remaining)
    assert fixture.process.poll() is None and fixture.policy.lease.valid(fixture.policy.lease.binding)
    elapsed=time.monotonic()-start;go();milestone('STOPPED_REGISTERED');fixture.process.wait(timeout=5)
    assert player.poll() is None and controller.native.registration()
    go()
    def stats(a,b):
        with output.open('rb') as f:f.seek(a-a%36);data=f.read(b-b%36-(a-a%36))
        peaks=[0]*12;squares=[0]*12;frames=len(data)//36
        for i in range(0,len(data),3):
            sample=int.from_bytes(data[i:i+3],'little',signed=True);channel=(i//3)%12;peaks[channel]=max(peaks[channel],abs(sample));squares[channel]+=sample*sample
        return {'frames':frames,'peaks':peaks,'rms':[math.sqrt(v/max(1,frames)) for v in squares]}
    results={n:stats(markers[n+'_start'],markers[n+'_end']) for n in ('ten','fifty','pause','resume')}
    if MODE=='controls100':results['hundred']=stats(markers['hundred_start'],markers['hundred_end'])
    for result in results.values():assert all(result['peaks'][i]==0 for i in set(range(12))-{2,3}) and max(result['peaks'])<2**23*10**(-6/20)+4
    assert max(results['pause']['peaks'])==0
    assert results['fifty']['rms'][2]>results['ten']['rms'][2]>0
    if MODE=='controls100':assert results['hundred']['rms'][2]>results['fifty']['rms'][2]
    assert results['resume']['rms'][2]<=results['fifty']['rms'][2]*1.05
    step=960*36;offset=markers['resume_start']-markers['resume_start']%36
    bins=[stats(a,a+step)['rms'][2] for a in range(offset,markers['resume_end']-step,step)]
    first=next(i for i,v in enumerate(bins) if v>0)
    plateau=results['fifty']['rms'][2]
    reached=next(i for i,v in enumerate(bins[first:],first) if v>=plateau*.9)
    low=next(i for i,v in enumerate(bins) if v>=plateau*.01)
    high=next(i for i,v in enumerate(bins) if v>=plateau*.5)
    # Measure the audible dB-ramp phase, not leading muted settling/packed24
    # quantization floor. -90→0 is90dB; infer slope over a34dB central band.
    ramp_ms=(high-low)*20*90/(20*math.log10(.5/.01))
    assert len(ramp_commands)==2
    command=ramp_commands[-1]['output_bytes'];command-=command%36
    command_bins=[stats(a,a+step)['rms'][2] for a in range(command,markers['resume_end']-step,step)]
    measured_ms=next(i for i,v in enumerate(command_bins) if v>=plateau*.9)*20
    receipt={'elapsed_seconds':elapsed,'packed24_channels':12,'sample_rate':48000,'markers':markers,'segments':results,'resume_rms_20ms_bins':bins,'resume_gain_slope_equivalent_ms':ramp_ms,'resume_ramp_ms_from_actual_command':measured_ms,'native_ramp_commands':ramp_commands,'hardware':False,'source':'synthetic-program/actual MA-Squeezelite-privatePW-FIFO-CamillaFILE','registered_after_stop':True}
    (logs/'readback.json').write_text(json.dumps(receipt,indent=2))
    assert bins[first+1]<plateau*.5 and 280<=measured_ms<=520,('measured native resume command→PCM fade',measured_ms,command_bins[:30])
    print('PASS actual MA→Squeezelite→private PipeWire→protected FIFO→Camilla FILE',round(elapsed,2),'seconds; measured10→50 attenuation, Pausezero, Resume≤lastuserlevel, Stop closesDSP/playerregistered; 12packed24/48k/otheroutputs zero. Synthetic program, no hardware.',flush=True)
    # Test shutdown is explicit and separate from ordinary Stop/registration.
    # MA can wait on the synthetic HTTP stream task during graceful teardown;
    # do not let fixture cleanup relabel a completed listening journey failure.
    subprocess.check_call(['docker','stop','--time','3',NAME],stdout=subprocess.DEVNULL);worker.wait(timeout=10)
    old_pid=pid
    worker=subprocess.Popen(['docker','start','-i',NAME],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=log,text=True,bufsize=1)
    milestone('READY')
    fresh=json.loads(subprocess.check_output(['docker','inspect',NAME]))[0]
    updated=verify_description(fresh,pin,expected);assert updated['process']['pid']!=old_pid
    ref_path=logs/'fixture-ma-peer.json'
    reconcile(controller,gateway,updated,publisher=lambda ref:publish(ref,ref_path))
    assert ref_path.stat().st_mode&0o777==0o600
    assert controller.state=='stopped' and controller.ma_owner is None and fixture.process.poll() is not None
    assert controller.normal_available and controller.ma_available
    print('PASS actual isolated MA container restart: new verified PID/namespace, root atomic reference refresh, old authority ended before reload, no restored approval/output; production observer fixed-scope dependency tested with explicit isolated scope',flush=True)
finally:
    if worker and worker.poll() is None:subprocess.run(['docker','kill',NAME],capture_output=True);worker.wait(timeout=10)
    if fixture:
        if 'controller' in locals():print('FINAL native fixture',json.dumps(controller.status()),flush=True)
        shutil.copytree(fixture.state,logs/'native-state-retained',dirs_exist_ok=True)
        fixture.doCleanups()
    for process in reversed(children):
        if process.poll() is None:process.kill()
        process.wait(timeout=3)
    for handle in handles:handle.close()
