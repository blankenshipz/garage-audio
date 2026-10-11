"""Isolated fixtures + actual Camilla FILE/null execution; NEVER ALSA/hardware.

SyntheticPeer is explicitly injected into an isolated FILE-only Policy. It is
not a root credential, genuine operator enrollment or production approval.
"""
import array
import copy
import hashlib
import json
import math
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import uuid
from unittest.mock import patch
from camilladsp import CamillaClient
from garage_audio.broker import Policy,serve
from garage_audio.human_authority import HumanVerifier
from garage_audio.human_playback import controlling_tty,review_operation,approve_listening
from garage_audio.ipc import request
from garage_audio.playback_lease import Lease,Peer,stereo_graph,stereo_profile,graph_digest
from garage_audio.profile import compile_profile

ROOT=Path(__file__).resolve().parents[1]
ENV={**os.environ,'LD_LIBRARY_PATH':str(ROOT/'tools/libs/usr/lib/x86_64-linux-gnu')}

class SyntheticPeer:
    uid=0
    def __init__(self,process):self.process=process
    def alive(self):return self.process.poll() is None
    def authorize(self):return self

class PureTests(unittest.TestCase):
    def test_operation_scope_precedes_approval_and_cancel_fails_closed(self):
        import io
        class Terminal:
            def __init__(self,answer):self.answer=answer;self.output=io.StringIO()
            def write(self,text):return self.output.write(text)
            def flush(self):pass
            def readline(self,size):return self.answer
        for function,answer in ((review_operation,'PREPARE\n'),(approve_listening,'LISTEN\n')):
            tty=Terminal(answer);function(tty)
            self.assertIn('OUT3/4',tty.output.getvalue());self.assertIn('-60dB',tty.output.getvalue())
            self.assertIn('120',tty.output.getvalue())
            with self.assertRaises(PermissionError):function(Terminal('\n'))
    def test_exact_120_second_binding_and_no_restored_authority(self):
        class PeerFixture:
            uid=0
            def alive(self):return True
        clock=[1.0];binding={'boot':'fixture','generation':'fixture-generation','hardware':{'serial':'FIXTURE','channels':12},'graph':'fixture-hash','outputs':[3,4]}
        lease=Lease(PeerFixture(),binding,clock=lambda:clock[0])
        self.assertEqual(lease.deadline,121)
        for key,value in [('boot','other'),('generation','other'),('hardware',{'serial':'OTHER'}),('graph','other'),('outputs',[3,4,5])]:
            changed=copy.deepcopy(binding);changed[key]=value;self.assertFalse(lease.valid(changed))
        clock[0]=121;self.assertFalse(lease.valid(binding))
        clock[0]=1;lease.revoke();self.assertFalse(lease.valid(binding))
    def test_real_nonroot_peer_and_no_tty_cannot_arm(self):
        with self.assertRaises(PermissionError):Peer(os.getpid(),12345,12345).authorize()
        with self.assertRaises(PermissionError):controlling_tty()
    def test_independent_wrong_password_rejected_and_revocation(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);state=root/'app';state.mkdir();private=root/'human';private.mkdir(mode=0o700)
            salt=b'x'*32;password='ISOLATED-FIXTURE-PASSWORD'
            value=dict(user='operator',realm='fixture',version=1,salt=salt.hex(),iterations=200000,
                       password_hash=hashlib.pbkdf2_hmac('sha256',password.encode(),salt,200000).hex())
            path=private/'verifier';path.write_text(json.dumps(value));path.chmod(0o600)
            verifier=HumanVerifier(state,str(path))
            with self.assertRaises(ValueError):verifier.authenticate('operator','WRONG-FIXTURE-PASSWORD','fixture')
            identity=verifier.authenticate('operator',password,'fixture')
            self.assertTrue(verifier.current(identity,fresh=True));verifier.revoke('fixture');self.assertFalse(verifier.current(identity))

class NativeFileTests(unittest.TestCase):
    def setUp(self):
        state_root=Path(os.environ.get('GARAGE_TEST_STATE_ROOT',str(ROOT/'state')))
        self.temp=tempfile.TemporaryDirectory(dir=state_root);self.addCleanup(self.temp.cleanup)
        self.state=Path(self.temp.name)
        self.logs=state_root/'native-playback-receipts'/('file-fixture-'+uuid.uuid4().hex)
        self.logs.mkdir(parents=True,mode=0o700)
        self.stdout=(self.logs/'dsp.stdout.log').open('wb');self.stderr=(self.logs/'dsp.stderr.log').open('wb')
        port_socket=socket.socket();port_socket.bind(('127.0.0.1',0));self.port=port_socket.getsockname()[1];port_socket.close()
        self.process=subprocess.Popen([str(ROOT/'tools/camilladsp'),'-a','127.0.0.1','-p',str(self.port),
                                       '--wait','--statefile',str(self.state/'camilla-state.yml'),'--no_config','--gain=-60','--mute'],stdin=subprocess.PIPE,stdout=self.stdout,stderr=self.stderr,env=ENV)
        self.helper=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        self.addCleanup(self.cleanup)
        self.binding={'identity':{'serial':'FIXTURE_ONLY','card':'FIXTURE_ONLY','pcm':0},'capabilities':{'channels':12,'format':'S24_3LE','samplerate':48000}}
        self.factory=lambda p:compile_profile(p,{'type':'Stdin','channels':2,'format':'F32_LE'},
                                             {'type':'File','channels':12,'format':'S24_3_LE','filename':'/dev/null'})
        self.policy=Policy(self.state,self.client,self.factory,self.process,hardware_binding=lambda:copy.deepcopy(self.binding))
        self.peer=SyntheticPeer(self.helper)
        for _ in range(40):
            c=self.client()
            try:c.connect();c.disconnect();break
            except OSError:time.sleep(.05)
        else:raise RuntimeError('FILE DSP did not start')
        threading.Thread(target=self.feed,daemon=True).start()
    def client(self):return CamillaClient('127.0.0.1',self.port)
    def feed(self):
        block=array.array('f',[.25,.125]*1024).tobytes()
        try:
            while self.process.poll() is None:self.process.stdin.write(block);self.process.stdin.flush();time.sleep(.01)
        except (OSError,ValueError):pass
    def cleanup(self):
        self.policy.revoke_stereo('fixture cleanup')
        for process in (self.helper,self.process):
            if process.poll() is None:process.kill()
            process.wait(timeout=3)
        if self.process.stdin:
            try:self.process.stdin.close()
            except BrokenPipeError:pass  # DSP exit is the expected fail-close result.
        self.stdout.close();self.stderr.close()
        (self.logs/'dsp.exit').write_text(str(self.process.returncode)+'\n')
    def open(self):return self.policy.execute({'cmd':'stereo-open','generation':self.policy.generation()},self.peer)
    def test_forged_payload_file_grant_and_legacy_strings_never_arm(self):
        generation=self.policy.generation()
        for payload in ({'cmd':'stereo-open','generation':generation,'uid':0},
                        {'cmd':'stereo-open','generation':generation,'role':'root'},
                        {'cmd':'stereo-open','generation':generation,'grant':'file-null-fixture'},
                        {'cmd':'unmute','generation':generation,'attended':'PRESENT-GAINS-LOW'},
                        {'cmd':'commission-window','generation':generation,'channel':'left','seconds':1,'attended':'PRESENT-GAINS-LOW'}):
            with self.assertRaises(ValueError):self.policy.execute(payload)
        with self.assertRaises(PermissionError):self.policy.execute({'cmd':'stereo-open','generation':generation})
        self.assertIsNone(self.policy.lease)
    def test_actual_unix_peer_cannot_supply_root_role(self):
        path=self.state/'control.sock'
        server=threading.Thread(target=serve,args=(self.policy,path,os.getuid()),daemon=True);server.start()
        for _ in range(30):
            if path.exists():break
            time.sleep(.02)
        with self.assertRaises(RuntimeError):request({'cmd':'stereo-open'},str(path))
        self.assertIsNone(self.policy.lease)
    def test_idle_deadline_ends_actual_owned_file_process(self):
        with patch('garage_audio.broker.SECONDS',.4):self.open()
        self.process.wait(timeout=3);self.assertIsNotNone(self.process.poll())
    def test_helper_sigkill_ends_actual_owned_file_process(self):
        self.open();self.helper.kill();self.helper.wait(timeout=2)
        self.process.wait(timeout=3)
    def test_stalled_readback_cannot_delay_helper_death_shutdown(self):
        entered=threading.Event();release=threading.Event()
        self.open()
        actual=self.client
        def blocked_client():
            entered.set();release.wait(10);return actual()
        self.policy.client_factory=blocked_client
        try:
            self.assertTrue(entered.wait(2),'readback thread did not enter isolated stall')
            start=time.monotonic();self.helper.kill();self.helper.wait(timeout=1)
            self.process.wait(timeout=1)
            self.assertLess(time.monotonic()-start,1)
            self.assertFalse(release.is_set(),'shutdown relied on releasing DSP readback')
        finally:release.set()
    def test_capability_identity_change_and_readback_failure_revoke(self):
        self.open();self.binding['identity']['serial']='DIFFERENT_FIXTURE'
        self.process.wait(timeout=3)
    def test_control_failure_revoke(self):
        self.open()
        self.policy.client_factory=lambda:(_ for _ in ()).throw(ConnectionError('isolated failure'))
        self.process.wait(timeout=3)
    def test_profile_change_revokes_before_mutation(self):
        self.open()
        with self.assertRaises(PermissionError):self.policy.execute({'cmd':'profile','generation':self.policy.generation(),'profile':stereo_profile()})
        self.process.wait(timeout=2);self.assertFalse((self.state/'profile-store.json').exists())
    def test_level_raise_and_graph_change_end_window(self):
        self.open();c=self.client();c.connect();c.volume.set_main_volume(-20);c.disconnect();self.process.wait(timeout=3)
    def test_graph_readback_change_ends_window(self):
        self.open();c=self.client();c.connect();config=c.config.active();config['filters']['headroom']['parameters']['gain']=-12;c.config.set_active(config);c.disconnect();self.process.wait(timeout=3)
    def test_unexpected_pcm_and_stale_generation_fail_closed(self):
        with self.assertRaises(ValueError):self.policy.execute({'cmd':'stereo-open','generation':'STALE_FIXTURE'},self.peer)
        old=self.factory
        def wrong(profile):
            config=old(profile);config['devices']['playback']['channels']=20;return config
        self.policy.config_factory=wrong
        with self.assertRaises(ValueError):self.open()
        self.assertIsNone(self.policy.lease)
    def test_finite_packed24_render_only3_4_and_ceiling(self):
        data=array.array('f')
        for i in range(48000):data.extend([.5*math.sin(2*math.pi*1000*i/48000),.25*math.sin(2*math.pi*1000*i/48000)])
        source=self.state/'input.raw';source.write_bytes(data.tobytes());output=self.logs/'output.raw'
        config=stereo_graph(compile_profile(stereo_profile(),{'type':'RawFile','channels':2,'format':'F32_LE','filename':str(source)},
                                            {'type':'File','channels':12,'format':'S24_3_LE','filename':str(output)}))
        path=self.state/'render.json';path.write_text(json.dumps(config))
        rendered=subprocess.run([str(ROOT/'tools/camilladsp'),'--gain=-60',str(path)],env=ENV,capture_output=True,timeout=10)
        (self.logs/'render.stdout.log').write_bytes(rendered.stdout);(self.logs/'render.stderr.log').write_bytes(rendered.stderr)
        (self.logs/'render.exit').write_text(str(rendered.returncode)+'\n')
        (self.logs/'render-config.json').write_text(json.dumps(config))
        self.assertEqual(rendered.returncode,0,rendered.stderr.decode())
        raw=output.read_bytes();self.assertEqual(len(raw)%(12*3),0)
        peaks=[0]*12
        for index in range(0,len(raw),3):
            sample=int.from_bytes(raw[index:index+3],byteorder='little',signed=True)
            channel=(index//3)%12;peaks[channel]=max(peaks[channel],abs(sample))
        self.assertGreater(peaks[2],0);self.assertGreater(peaks[3],0)
        self.assertTrue(all(peaks[i]==0 for i in set(range(12))-{2,3}))
        # Conservative full-scale -60dB bound includes the retained crossover's
        # start transient; it is not a claim of perfectly flat -6dB response.
        self.assertLessEqual(max(peaks),int((2**23)*10**(-60/20))+2)
        (self.logs/'render-readback.json').write_text(json.dumps({'peaks':peaks,'frames':len(raw)//36,'sha256':hashlib.sha256(raw).hexdigest(),'hardware':False}))
        print('FILE ONLY packed24/12 peaks',peaks,'ten unused outputs zero; initial native-60 and headroom-6')

if __name__=='__main__':unittest.main()
