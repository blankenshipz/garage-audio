"""Actual Camilla FILE electrical proof only; never opens ALSA or emits sound."""
import array, hashlib, json, math, os, subprocess, uuid
from pathlib import Path
from garage_audio.profile import compile_profile
from garage_audio.playback_lease import normal_line_graph, stereo_profile

ROOT=Path(__file__).resolve().parents[1]
receipt=ROOT/'state/native-playback-receipts'/('line-render-'+uuid.uuid4().hex)
receipt.mkdir(parents=True,mode=0o700)
frequencies=(20,40,80,1000,10000,20000)
frames=24000
signal=array.array('f')
for frequency in frequencies:
    for i in range(frames):
        value=math.sin(2*math.pi*frequency*i/48000)
        signal.extend([value,-value*.5])
source=receipt/'input.f32';source.write_bytes(signal.tobytes());output=receipt/'output.s24'
graph=normal_line_graph(compile_profile(stereo_profile(),
    {'type':'RawFile','channels':2,'format':'F32_LE','filename':str(source)},
    {'type':'File','channels':12,'format':'S24_3_LE','filename':str(output)}))
config=receipt/'graph.json';config.write_text(json.dumps(graph,indent=2))
env={**os.environ,'LD_LIBRARY_PATH':str(ROOT/'tools/libs/usr/lib/x86_64-linux-gnu')}
with (receipt/'stdout.log').open('wb') as stdout,(receipt/'stderr.log').open('wb') as stderr:
    run=subprocess.run([str(ROOT/'tools/camilladsp'),'--gain=0',str(config)],stdout=stdout,stderr=stderr,env=env,timeout=20)
(receipt/'exit').write_text(str(run.returncode))
assert run.returncode==0,('render failed; retained receipt',str(receipt))
raw=output.read_bytes();assert len(raw)%(12*3)==0
peaks=[0]*12;left=[];right=[]
for start in range(0,len(raw),36):
    row=[int.from_bytes(raw[start+c*3:start+c*3+3],'little',signed=True)/(2**23) for c in range(12)]
    for c,value in enumerate(row):peaks[c]=max(peaks[c],abs(value))
    left.append(row[2]);right.append(row[3])
assert all(peaks[c]==0 for c in set(range(12))-{2,3})
assert max(peaks)<=10**(-6/20)+1e-6
results=[]
for n,frequency in enumerate(frequencies):
    start=n*frames+4800;end=(n+1)*frames-100
    expected=[math.sin(2*math.pi*frequency*(i-n*frames)/48000) for i in range(start,end)]
    energy=sum(v*v for v in expected)
    lg=sum(a*b for a,b in zip(left[start:end],expected))/energy
    rg=sum(a*b for a,b in zip(right[start:end],expected))/energy
    assert abs(20*math.log10(lg)+6)<.001,(frequency,lg)
    assert abs(rg/lg+.5)<1e-6,(frequency,rg,lg)
    results.append({'hz':frequency,'left_gain_db':20*math.log10(lg),'right_relative_signed':rg/lg})
result={'hardware':False,'source':'synthetic-full-scale-tones/actual-Camilla-FILE',
        'graph_sha256':hashlib.sha256(config.read_bytes()).hexdigest(),'peaks':peaks,
        'response':results,'unused_outputs_zero':True,'master_db':0,'headroom_db':-6,
        'old_minus20_to_new_change_db':20,'physical_wiring_acoustics_verified':False}
(receipt/'readback.json').write_text(json.dumps(result,indent=2))
print('PASS actual Camilla FILE: 20–20000Hz flat −6dB, distinct signed L/R OUT3/4, ten unused outputs zero, full-scale peak bounded; no hardware',str(receipt))
