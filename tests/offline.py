"""Real pinned CamillaDSP, synthetic PCM, file output only. No device opens."""
import array
import copy
import json
import math
import os
from pathlib import Path
import subprocess
import tempfile
import time
import threading
import faulthandler
import unittest
from types import SimpleNamespace
import yaml
from camilladsp import CamillaClient
from garage_audio.profile import DEFAULT, compile_profile, validate, hardware_config
from garage_audio.control import apply_graph, edit_profile, readback, commissioning_window, store
from garage_audio.rew import parse
from garage_audio.hardware import validate_identity

ROOT = Path(__file__).resolve().parents[1]
BIN = ROOT / 'tools/camilladsp'
ENV = dict(os.environ, LD_LIBRARY_PATH=str(ROOT / 'tools/libs/usr/lib/x86_64-linux-gnu'))

def rms(values): return math.sqrt(sum(v*v for v in values)/len(values))

def run(p=None, hz=80, gain=-20, mute=False, right=1, impulse=False):
    with tempfile.TemporaryDirectory(dir=ROOT / 'state') as directory:
        d = Path(directory)
        data = array.array('f')
        for i in range(96000):
            x = (0.5 if i == 1000 else 0) if impulse else .1 * math.sin(2*math.pi*hz*i/48000)
            data.extend([x, x*right])
        (d/'in.raw').write_bytes(data.tobytes())
        config = compile_profile(p or DEFAULT,
            {'type':'RawFile','filename':str(d/'in.raw'),'format':'F32_LE','channels':2},
            {'type':'File','filename':str(d/'out.raw'),'format':'F32_LE','channels':20})
        (d/'config.yaml').write_text(yaml.safe_dump(config))
        args = [str(BIN), f'--gain={gain}', str(d/'config.yaml')]
        if mute: args.insert(1, '-m')
        r = subprocess.run(args, env=ENV, capture_output=True, timeout=15)
        if r.returncode: raise RuntimeError(r.stderr.decode())
        output = array.array('f'); output.frombytes((d/'out.raw').read_bytes())
        return [list(output[c::20]) for c in range(20)]

class DSPTests(unittest.TestCase):
    def test_master_and_silent_unused(self):
        a=run(); b=run(gain=-60); muted=run(mute=True)
        for i in set(range(20))-{2,3,4}: self.assertEqual(max(map(abs,a[i])), 0)
        for i in [2,3,4]:
            self.assertAlmostEqual(rms(b[i][48000:])/rms(a[i][48000:]), .01, places=6)
            self.assertEqual(max(map(abs,muted[i])), 0)
        self.assertEqual(a[2], a[3])
    def test_lr4_and_mono_sum(self):
        a=run(hz=80)
        reference = .1/math.sqrt(2)*10**(-26/20)
        for i in [2,3,4]: self.assertAlmostEqual(rms(a[i][48000:])/reference, .5, places=4)
        anti=run(right=-1)
        self.assertEqual(max(map(abs,anti[4])),0)
        bass=run(hz=20); treble=run(hz=1000)
        self.assertLess(rms(bass[2][48000:])/rms(bass[4][48000:]),.005)
        self.assertLess(rms(treble[4][48000:])/rms(treble[2][48000:]),.0001)
    def test_channel_trim_polarity_mute_delay_peq(self):
        base=run(hz=1000)
        p=copy.deepcopy(DEFAULT); p['channels']['left']['gain']=-6; p['channels']['right']['invert']=True; p['sub_trim']=-6
        out=run(p,hz=1000)
        self.assertAlmostEqual(rms(out[2][48000:])/rms(base[2][48000:]),10**(-6/20),places=6)
        self.assertAlmostEqual(out[3][60000],-base[3][60000],places=7)
        self.assertAlmostEqual(rms(out[4][48000:])/rms(base[4][48000:]),10**(-6/20),places=5)
        for label,index in [('left',2),('right',3),('sub',4)]:
            p=copy.deepcopy(DEFAULT);p['channels'][label]['mute']=True
            self.assertEqual(max(map(abs,run(p)[index])),0)
        p=copy.deepcopy(DEFAULT);p['channels']['left']['delay_ms']=10
        a=run(impulse=True);b=run(p,impulse=True)
        self.assertEqual(max(range(len(b[2])),key=lambda i:abs(b[2][i]))-max(range(len(a[2])),key=lambda i:abs(a[2][i])),480)
        p=copy.deepcopy(DEFAULT);p['channels']['left']['peq']=[{'freq':1000,'q':2,'gain':-6}]
        out=run(p,hz=1000)
        self.assertAlmostEqual(rms(out[2][48000:])/rms(base[2][48000:]),10**(-6/20),places=4)
        p['peq_bypass']=True
        self.assertEqual(run(p,hz=1000)[2],base[2])
    def test_validator(self):
        for x in [float('nan'),float('inf'),True,1]:
            p=copy.deepcopy(DEFAULT);p['sub_trim']=x
            with self.assertRaises(ValueError):validate(p)
        with self.assertRaises(ValueError):hardware_config(DEFAULT,'default')
        # ALSA card IDs need not contain the marketing name UMC. Binding is
        # enforced by USB ancestry/owner metadata, not a guessed card spelling.
        self.assertEqual(hardware_config(DEFAULT,'hw:CARD=U1820,DEV=0')['devices']['playback']['device'],'hw:CARD=U1820,DEV=0')
        identity={'model':'UMC1820','vendor':'1234','product':'5678','serial':'SYNTHETIC_ONLY','alsa_card':'U1820','pcm':0}
        self.assertEqual(validate_identity(identity),identity)
        for key,value in [('model','internal'),('vendor','*'),('serial',''),('alsa_card','default:0'),('pcm',True)]:
            invalid=dict(identity);invalid[key]=value
            with self.assertRaises(ValueError):validate_identity(invalid)
    def test_crossover_peq_edits(self):
        p=edit_profile(DEFAULT,SimpleNamespace(cmd='crossover',hz=100))
        p=edit_profile(p,SimpleNamespace(cmd='peq',channel='left',index=0,freq=1000,q=2,gain=-6))
        a=run(p,hz=100)
        reference=.1/math.sqrt(2)*10**(-26/20)
        self.assertAlmostEqual(rms(a[3][48000:])/reference,.5,places=4)
        self.assertAlmostEqual(rms(a[4][48000:])/reference,.5,places=4)
        p=edit_profile(p,SimpleNamespace(cmd='peq',channel='left',index=0,freq=900,q=1,gain=-3))
        self.assertEqual(p['channels']['left']['peq'][0]['freq'],900)
        p=edit_profile(p,SimpleNamespace(cmd='peq-delete',channel='left',index=0))
        self.assertEqual(p['channels']['left']['peq'],[])
        p=edit_profile(p,SimpleNamespace(cmd='peq',channel='sub',index=0,freq=40,q=1,gain=-3))
        self.assertEqual(edit_profile(p,SimpleNamespace(cmd='peq-clear',channel='sub'))['channels']['sub']['peq'],[])
        for edit in [SimpleNamespace(cmd='crossover',hz=10),SimpleNamespace(cmd='peq',channel='left',index=2,freq=1000,q=2,gain=-6),
                     SimpleNamespace(cmd='peq',channel='left',index=0,freq=1000,q=0,gain=-6),
                     SimpleNamespace(cmd='peq',channel='left',index=0,freq=1000,q=2,gain=6)]:
            with self.assertRaises(ValueError):edit_profile(DEFAULT,edit)
    def test_native_effective_ceiling(self):
        capped=run(gain=0);safe=run(gain=-20)
        for i in [2,3,4]: self.assertAlmostEqual(rms(capped[i][48000:])/rms(safe[i][48000:]),1,places=5)
    def test_rew_import(self):
        eq=parse('Filter 1: ON PK Fc 80.00 Hz Gain -3.00 dB Q 2.000')
        p=copy.deepcopy(DEFAULT);p['channels']['sub']['peq']=eq;validate(p)
        for bad in ['Filter 1: ON LS Fc 80 Hz Gain -3 dB Q 2', 'Filter 1: ON PK Fc 80 Hz Gain 3 dB Q 2']:
            with self.assertRaises(ValueError):
                p['channels']['sub']['peq']=parse(bad);validate(p)
    def test_official_websocket_and_restart(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'state') as directory:
            config=compile_profile(DEFAULT,{'type':'Stdin','channels':2,'format':'F32_LE'},
                                   {'type':'File','channels':20,'format':'F32_LE','filename':'/dev/null'})
            path=Path(directory)/'null.yaml';path.write_text(yaml.safe_dump(config))
            for cycle in range(2):
                proc=subprocess.Popen([str(BIN),'-a','127.0.0.1','-p','1235','--gain=-60','-m',str(path)],env=ENV,stdin=subprocess.PIPE,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
                stop=threading.Event()
                def feed():
                    try:
                        block=array.array('f',[.01,-.01]*1024).tobytes()
                        while not stop.is_set():proc.stdin.write(block);proc.stdin.flush();time.sleep(1024/48000)
                    except BrokenPipeError:pass
                feeder=threading.Thread(target=feed,daemon=True);feeder.start()
                try:
                    c=CamillaClient('127.0.0.1',1235)
                    for attempt in range(40):
                        try:c.connect();break
                        except OSError:time.sleep(.1)
                    if proc.poll() is not None: raise RuntimeError(proc.stderr.read().decode())
                    self.assertTrue(c.volume.main_mute());self.assertEqual(c.volume.main_volume(),-60)
                    state=Path(directory)
                    cli=[str(ROOT/'venv/bin/python'),'-m','garage_audio.control','--port','1235','--isolated-file-test-state',str(state)]
                    result=subprocess.run(cli+['volume','-30'],cwd=ROOT,env=ENV,capture_output=True,text=True,timeout=10)
                    self.assertEqual(result.returncode,0,result.stderr)
                    self.assertEqual(json.loads(result.stdout)['volume_db'],-30)
                    invalid=subprocess.run(cli+['volume','0'],cwd=ROOT,env=ENV,capture_output=True,text=True,timeout=10)
                    self.assertNotEqual(invalid.returncode,0)
                    self.assertTrue(c.volume.main_mute())
                    audit=json.loads((state/'audit.jsonl').read_text().splitlines()[-1])
                    self.assertEqual(audit['failed'],'ValueError');self.assertTrue(audit['muted'])
                    c.volume.set_main_volume(0)
                    # Getter reports requested fader; native effective ceiling is
                    # independently verified using rendered PCM above.
                    self.assertEqual(c.volume.main_volume(),0)
                    c.volume.set_main_volume(-60)
                    valid=c.config.validate(config);c.config.set_active(valid)
                    time.sleep(.3)
                    self.assertTrue(c.volume.main_mute())
                    self.assertEqual(c.config.active()['devices']['volume_limit'],-20)
                    broken=copy.deepcopy(config);broken['filters']['hp']['parameters']['freq']=50000
                    with self.assertRaises(Exception):c.config.validate(broken)
                    self.assertEqual(c.config.active()['filters'],valid['filters'])
                    state=Path(directory)
                    p=copy.deepcopy(DEFAULT);p['channels']['left']['gain']=-3
                    p=edit_profile(p,SimpleNamespace(cmd='crossover',hz=100))
                    p=edit_profile(p,SimpleNamespace(cmd='peq',channel='sub',index=0,freq=40,q=2,gain=-3))
                    candidate=compile_profile(p,config['devices']['capture'],config['devices']['playback'])
                    c.volume.set_main_volume(-35)
                    apply_graph(c,p,state,candidate)
                    self.assertEqual(store(state)['active'],p)
                    self.assertEqual(c.config.active()['filters']['left_gain']['parameters']['gain'],-3)
                    self.assertTrue(c.volume.main_mute());self.assertEqual(c.volume.main_volume(),-35)
                    rb=readback(c)
                    self.assertEqual(rb['crossover']['hp']['parameters']['freq'],100)
                    self.assertEqual(rb['channel_filters']['sub_peq_0']['parameters']['gain'],-3)
                    # Existing authorized unmute restored ONLY on successful
                    # isolated FILE/null apply, never on failure or restart.
                    c.volume.set_main_volume(-40);c.volume.set_main_mute(False)
                    # Successful second transaction creates known-good receipt.
                    apply_graph(c,DEFAULT,state,config,authorized_unmuted=True,generation=lambda:proc.pid)
                    self.assertFalse(c.volume.main_mute());self.assertEqual(c.volume.main_volume(),-40)
                    self.assertEqual(store(state)['known_good'],p)
                    with self.assertRaises(Exception):apply_graph(c,p,state,broken,authorized_unmuted=True,generation=lambda:proc.pid)
                    self.assertTrue(c.volume.main_mute());self.assertEqual(c.volume.main_volume(),-60)
                    time.sleep(.2)
                    self.assertEqual(c.config.active()['filters'],valid['filters'])
                    c.volume.set_main_volume(-45);c.volume.set_main_mute(False)
                    with self.assertRaises(ValueError):apply_graph(c,p,state,candidate)
                    self.assertTrue(c.volume.main_mute());self.assertEqual(c.volume.main_volume(),-60)
                    self.assertEqual(store(state)['active'],DEFAULT)
                    if cycle == 0:
                        cli_state=state/'cli';cli_state.mkdir()
                        (cli_state/'active-profile.json').write_text(json.dumps(DEFAULT))
                        test_cli=cli+['--isolated-file-test-state',str(cli_state)]
                        c.volume.set_main_volume(-33)
                        for command in [['crossover','120'],['peq','left','0','1000','2','-6'],['peq-delete','left','0'],['peq','sub','0','40','1','-3'],['peq-clear','sub']]:
                            edited=subprocess.run(test_cli+command,cwd=ROOT,env=ENV,capture_output=True,text=True,timeout=10)
                            self.assertEqual(edited.returncode,0,edited.stderr)
                            effective=json.loads(edited.stdout)
                            self.assertTrue(effective['muted']);self.assertEqual(effective['volume_db'],-33)
                            self.assertEqual(effective['crossover']['hp']['parameters']['freq'],120)
                            if command[0]=='peq':
                                self.assertEqual(effective['channel_filters'][command[1]+'_peq_0']['parameters']['freq'],float(command[3]))
                            elif command[0] in ('peq-delete','peq-clear'):
                                self.assertNotIn(command[1]+'_peq_0',effective['channel_filters'])
                        invalid=subprocess.run(test_cli+['crossover','10'],cwd=ROOT,env=ENV,capture_output=True,text=True,timeout=10)
                        self.assertNotEqual(invalid.returncode,0)
                        self.assertTrue(c.volume.main_mute());self.assertEqual(c.volume.main_volume(),-60)
                        # Restore the original FILE graph before timer tests.
                        apply_graph(c,DEFAULT,state,config)
                        factory=lambda profile:compile_profile(profile,config['devices']['capture'],config['devices']['playback'])
                        commissioning_window(c,DEFAULT,state,factory,'left',1,1235)
                        self.assertTrue(c.volume.main_mute());self.assertEqual(c.volume.main_volume(),-60)
                        self.assertEqual(c.config.active()['filters'],valid['filters'])
                        # Detached guardian still mutes a FILE/null process
                        # when no coordinating parent completion is provided.
                        guardian=subprocess.Popen([str(ROOT/'venv/bin/python'),'-m','garage_audio.watchdog','--port','1235','--seconds','1'],cwd=ROOT,stdout=subprocess.PIPE,text=True)
                        self.assertEqual(guardian.stdout.readline().strip(),'armed');guardian.stdout.close()
                        c.volume.set_main_mute(False);guardian.wait(timeout=5)
                        self.assertTrue(c.volume.main_mute());self.assertEqual(c.volume.main_volume(),-60)
                    c.disconnect()
                finally:
                    stop.set();feeder.join(timeout=1);proc.stdin.close();proc.terminate()
                    try:proc.wait(timeout=5)
                    except subprocess.TimeoutExpired:proc.kill();proc.wait(timeout=5)
                    proc.stderr.close()

if __name__=='__main__':
    faulthandler.dump_traceback_later(20)
    (ROOT/'state').mkdir(exist_ok=True)
    unittest.main(verbosity=2)
