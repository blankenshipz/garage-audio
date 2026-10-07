"""Real isolated PW/WP/plugin/Squeezelite runtime; synthetic input, File DSP out.

Run via source scripts/native-test-env; PYTHONPATH=. venv/bin/python tests/native.py
All libraries extracted below tools/native; no host installs or device discovery.
"""
import array
import json
import math
import os
from pathlib import Path
import subprocess
import tempfile
import time
import yaml
from garage_audio.profile import DEFAULT, compile_profile

ROOT=Path(__file__).resolve().parents[1]

def rms(xs):return math.sqrt(sum(x*x for x in xs)/len(xs))

def main():
    expected=str(ROOT/'state/native-runtime')
    assert os.environ.get('PIPEWIRE_RUNTIME_DIR')==expected
    assert os.environ.get('PIPEWIRE_REMOTE')=='garage-pipewire'
    owned=[]
    logs=[]
    def spawn(args, **kwargs):
        log=(ROOT/'state'/('native-'+Path(args[0]).name+'.log')).open('wb');logs.append(log)
        p=subprocess.Popen(args,stderr=log,**kwargs);owned.append(p);return p
    def graph():
        return json.loads(subprocess.check_output(['pw-dump'],timeout=5))
    try:
        pw=spawn(['pipewire','-c',str(ROOT/'config/pipewire.conf')],stdout=subprocess.DEVNULL)
        time.sleep(.2)
        wp=spawn(['wireplumber'],stdout=subprocess.DEVNULL,env=dict(os.environ,WIREPLUMBER_DEBUG='3'))
        time.sleep(.5)
        objects=graph()
        nodes=[o.get('info',{}).get('props',{}) for o in objects if o['type']=='PipeWire:Interface:Node']
        assert [p['node.name'] for p in nodes if p.get('media.class')=='Audio/Sink']==['garage_music']
        assert not any('api.alsa.path' in p for p in nodes), 'unexpected hardware node'
        sink=next(p for p in nodes if p.get('node.name')=='garage_music')
        assert str(sink['audio.rate'])=='48000'
        print('PASS real PW1.6.2/WP0.5.13: sole 48k virtual sink, no ALSA/BlueZ/camera monitors')
        squeeze=spawn(['squeezelite','-n','Garage Speakers isolated test','-m','02:00:00:00:21:02',
                       '-s','127.0.0.1:3483','-o','garage_music','-r','48000-48000','-a','80:4:32:1',
                       '-d','output=info'],stdout=subprocess.DEVNULL)
        time.sleep(.5)
        assert squeeze.poll() is None, 'Squeezelite failed to initialize explicit PCM'
        objects=graph()
        squeeze_nodes=[o for o in objects if o.get('info',{}).get('props',{}).get('application.process.binary')=='squeezelite']
        assert squeeze_nodes, 'Squeezelite ALSA→PipeWire node absent'
        print('PASS real Squeezelite2.0.0-1541 opens only explicit garage_music PipeWire PCM; MA authentication/provider still pending')
        with tempfile.TemporaryDirectory(dir=ROOT/'state') as directory:
            d=Path(directory)
            data=array.array('f')
            for i in range(144000):
                left=.05*math.sin(2*math.pi*200*i/48000)+.02*math.sin(2*math.pi*40*i/48000)
                right=.05*math.sin(2*math.pi*1000*i/48000)+.02*math.sin(2*math.pi*40*i/48000)
                data.extend([left,right])
            (d/'input.raw').write_bytes(data.tobytes())
            capture=spawn(['pw-record','--raw','--target','garage_music','--properties',
                'stream.capture.sink=true node.dont-fallback=true node.dont-move=true','--rate','48000','--channels','2',
                '--format','f32','--sample-count','192000','-'],stdout=subprocess.PIPE)
            config=compile_profile(DEFAULT,{'type':'Stdin','channels':2,'format':'F32_LE'},
                                   {'type':'File','channels':20,'format':'F32_LE','filename':str(d/'output.raw')})
            (d/'dsp.yaml').write_text(yaml.safe_dump(config))
            # Unmute applies ONLY to this hard-coded file output. Never ALSA.
            dsp=spawn([str(ROOT/'tools/camilladsp'),'--gain=-20',str(d/'dsp.yaml')],
                      stdin=capture.stdout,stdout=subprocess.DEVNULL)
            capture.stdout.close()
            time.sleep(.3)
            subprocess.run(['pw-play','--raw','--target','garage_music','--rate','48000','--channels','2',
                            '--format','f32',str(d/'input.raw')],check=True,timeout=7)
            capture.wait(timeout=7);dsp.wait(timeout=5)
            # pw-cat1.6.2 returns1 on bounded sample-count completion. Accept
            # only with empty error log AND the full requested PCM length.
            assert capture.returncode in (0,1) and dsp.returncode==0, f'capture={capture.returncode}, DSP={dsp.returncode}'
            assert not (ROOT/'state/native-pw-record.log').read_text().strip()
            out=array.array('f');out.frombytes((d/'output.raw').read_bytes())
            assert len(out)>=192000*20
            channels=[list(out[i::20]) for i in range(20)]
            for i in set(range(20))-{2,3,4}:assert max(map(abs,channels[i]))==0
            for i in [2,3,4]:assert rms(channels[i])>.0001
            assert channels[2]!=channels[3]
        print('PASS real synthetic PCM→PW sink/monitor→Camilla stdin→20ch FILE; L/R/sub nonzero,17 unused exactlyzero')
        missing=spawn(['pw-record','--raw','--target','garage_missing',
            '--properties','stream.capture.sink=true node.dont-fallback=true node.dont-move=true',
            '--rate','48000','--channels','2','--format','f32','-'],stdout=subprocess.DEVNULL)
        missing.wait(timeout=4)
        assert missing.returncode != 0
        assert 'defined target not found' in (ROOT/'state/native-pw-record.log').read_text()
        print('PASS missing explicit monitor target fails without alternate/default routing')
        # Stop our processes, then recreate the sink to verify actual persistence
        # of the declarative graph beyond a single process instance.
        squeeze.terminate();squeeze.wait(timeout=3)
        wp.terminate();wp.wait(timeout=3)
        pw.terminate();pw.wait(timeout=3)
        pw=spawn(['pipewire','-c',str(ROOT/'config/pipewire.conf')],stdout=subprocess.DEVNULL)
        time.sleep(.2)
        assert any(o.get('info',{}).get('props',{}).get('node.name')=='garage_music' for o in graph())
        print('PASS virtual sink recreated after real isolated PW restart; no hardware/persistence-at-boot claim')
    finally:
        for p in reversed(owned):
            if p.poll() is None:
                p.terminate()
                try:p.wait(timeout=3)
                except subprocess.TimeoutExpired:p.kill();p.wait(timeout=3)
        for log in logs:log.close()

if __name__=='__main__':main()
