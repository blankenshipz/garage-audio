"""Actual native Connect FILE controls; synthetic peer/source, NEVER ALSA/Spotify."""
import array,json,math,sys,threading,time,unittest
from native_playback import NativeFileTests
from garage_audio.profile import compile_profile

class ConnectFile(NativeFileTests):
    def feed(self):
        self.level=.25
        try:
            while self.process.poll() is None:
                self.process.stdin.write(array.array('f',[self.level,self.level*.5]*1024).tobytes())
                self.process.stdin.flush();time.sleep(.01)
        except (OSError,ValueError):pass
    def open_connect(self):
        self.output=self.logs/'connect.packed24'
        self.policy.config_factory=lambda p:compile_profile(p,{'type':'Stdin','channels':2,'format':'F32_LE'}, {'type':'File','channels':12,'format':'S24_3_LE','filename':str(self.output)})
        self.peer.realm='phone'
        def execute(command,**fields):return self.policy.execute(dict(cmd=command,generation=self.policy.generation(),**fields),self.peer)
        execute('phone-start');execute('initialize');execute('phone-prepare')
        prepared=execute('status');self.assertTrue(prepared['preparation_zero']);self.assertTrue(prepared['muted'])
        self.assertEqual(prepared['volume_db'],-60)
        opened=execute('connect-open');self.assertEqual(opened['seconds'],10)
        self.assertTrue(execute('status')['muted'])
        return execute
    def samples(self,a,b):
        raw=self.output.read_bytes()[a-a%36:b-b%36];channels=[[] for _ in range(12)]
        for i in range(0,len(raw),3):channels[(i//3)%12].append(int.from_bytes(raw[i:i+3],'little',signed=True))
        return channels
    def test_native_connect_pause_resume_headroom(self):
        execute=self.open_connect();execute('connect-state',state='playing');time.sleep(.7)
        a=self.output.stat().st_size;time.sleep(.2);b=self.output.stat().st_size
        values=self.samples(a,b)
        self.assertTrue(all(not any(values[i]) for i in set(range(12))-{2,3}))
        self.assertLessEqual(max(map(abs,values[2])),int(2**23*.25*10**(-6/20))+3)
        self.assertGreater(sum(values[2]),0);self.assertGreater(sum(values[3]),0)
        plateau=math.sqrt(sum(v*v for v in values[2])/len(values[2]))
        execute('connect-state',state='paused');time.sleep(.5)
        a=self.output.stat().st_size;time.sleep(.2);b=self.output.stat().st_size
        self.assertFalse(any(any(channel) for channel in self.samples(a,b)))
        command=[]
        original=self.policy.client_factory
        class Client:
            def __init__(self):
                self.actual=original();self.volume=Volume(self.actual.volume)
            def __getattr__(self,name):return getattr(self.actual,name)
        class Volume:
            def __init__(self,actual):self.actual=actual
            def __getattr__(self,name):return getattr(self.actual,name)
            def set_main_volume(inner,value):
                if value==0:command.append(self.output.stat().st_size)
                return inner.actual.set_main_volume(value)
        self.policy.client_factory=Client
        execute('connect-state',state='playing');time.sleep(.8)
        start=command[-1];end=self.output.stat().st_size;step=960*36
        bins=[]
        for offset in range(start,end-step,step):
            values=self.samples(offset,offset+step)[2]
            bins.append(math.sqrt(sum(v*v for v in values)/len(values)))
        measured=next(i for i,value in enumerate(bins) if value>=plateau*.9)*20
        (self.logs/'connect-readback.json').write_text(json.dumps({'source':'synthetic PCM/peer; real Camilla FILE','resume_ms':measured,'resume_bins':bins,'plateau':plateau,'outputs':[3,4],'unused_outputs_zero':True,'hardware':False},indent=2))
        self.assertTrue(280<=measured<=520,(measured,bins[:30]))
        self.policy.execute({'cmd':'speaker-stop'},self.peer);self.process.wait(timeout=3)
    def test_native_connect_missing_renewal_closes(self):
        self.open_connect();self.process.wait(timeout=12);self.assertTrue(self.policy.lease.revoked)
    def test_old_epoch_pipe_samples_never_audible(self):
        execute=self.open_connect()
        # Old positive-marker PCM has flowed through actual stdin pipe/capture
        # while the graph is muted. A fresh source generation switches negative
        # BEFORE the guarded handoff; no positive queued sample may emerge.
        time.sleep(.2);self.level=-.25
        start=self.output.stat().st_size
        execute('connect-state',state='playing');time.sleep(.8)
        values=self.samples(start,self.output.stat().st_size)
        self.assertTrue(any(values[2]));self.assertTrue(all(v<=0 for v in values[2]))
        self.assertTrue(all(not any(values[i]) for i in set(range(12))-{2,3}))
        (self.logs/'old-epoch-readback.json').write_text(json.dumps({'old_positive_pcm_audible':False,'new_negative_pcm_audible':True,'source':'synthetic epoch markers / actual pipe-CamillaFILE','MA_PW_Squeezelite_boundary':False,'hardware':False}))
        self.policy.execute({'cmd':'speaker-stop'},self.peer);self.process.wait(timeout=3)
    def test_native_connect_helper_death_closes(self):
        self.open_connect();self.helper.kill();self.helper.wait(timeout=2);self.process.wait(timeout=3)
    def test_manual_heartbeat_cannot_renew_connect(self):
        execute=self.open_connect()
        with self.assertRaises(PermissionError):execute('normal-state',state='playing')
        self.process.wait(timeout=3)

if __name__=='__main__':
    names=('test_native_connect_pause_resume_headroom','test_native_connect_missing_renewal_closes','test_native_connect_helper_death_closes','test_manual_heartbeat_cannot_renew_connect','test_old_epoch_pipe_samples_never_audible')
    if len(sys.argv)>1:
        requested=tuple(sys.argv[1:]);assert set(requested)<=set(names);names=requested
    result=unittest.TextTestRunner(verbosity=2).run(unittest.TestSuite(ConnectFile(name) for name in names))
    raise SystemExit(0 if result.wasSuccessful() else 1)
