"""Actual Camilla FILE render only, never DAC; synthetic phone authority fixture."""
import array
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from garage_audio.playback_lease import preparation_graph,stereo_graph,stereo_profile
from garage_audio.profile import compile_profile

PROJECT=Path(os.environ.get('GARAGE_FILE_TEST_PROJECT',Path(__file__).resolve().parents[1]))

class FileTests(unittest.TestCase):
    def test_zero_preparation_and_stereo_packed24(self):
        with tempfile.TemporaryDirectory(dir=PROJECT/'state') as directory:
            root=Path(directory);source=root/'input.raw'
            source.write_bytes(array.array('f',[.25,.125]*24000).tobytes())
            capture={'type':'RawFile','channels':2,'format':'F32_LE','filename':str(source)}
            for prepare in (True,False):
                output=root/('zero.raw' if prepare else 'stereo.raw')
                graph=compile_profile(stereo_profile(),capture,{'type':'File','channels':12,'format':'S24_3_LE','filename':str(output)})
                graph=(preparation_graph if prepare else stereo_graph)(graph)
                config=root/'config.json';config.write_text(json.dumps(graph))
                result=subprocess.run([str(PROJECT/'tools/camilladsp'),'--gain=-60',str(config)],capture_output=True,timeout=15,
                    env={**os.environ,'LD_LIBRARY_PATH':str(PROJECT/'tools/libs/usr/lib/x86_64-linux-gnu')})
                # Persist full render diagnostics before any assertion/fixture cleanup.
                logs=Path(os.environ['GARAGE_FILE_TEST_RECEIPT']);logs.mkdir(parents=True,exist_ok=True)
                label='zero' if prepare else 'stereo'
                (logs/(label+'.stdout')).write_bytes(result.stdout);(logs/(label+'.stderr')).write_bytes(result.stderr)
                (logs/(label+'.exit')).write_text(str(result.returncode));(logs/(label+'.config.json')).write_text(json.dumps(graph))
                self.assertEqual(result.returncode,0,result.stderr.decode())
                raw=output.read_bytes();self.assertGreater(len(raw),0);self.assertEqual(len(raw)%36,0)
                peaks=[0]*12
                for i in range(0,len(raw),3):
                    sample=int.from_bytes(raw[i:i+3],'little',signed=True);channel=(i//3)%12
                    peaks[channel]=max(peaks[channel],abs(sample))
                if prepare:self.assertEqual(peaks,[0]*12)
                else:
                    self.assertGreater(peaks[2],0);self.assertGreater(peaks[3],0)
                    self.assertTrue(all(peaks[i]==0 for i in set(range(12))-{2,3}))
                    self.assertLessEqual(max(peaks),int(2**23*10**(-60/20))+2)
                (logs/(label+'.readback.json')).write_text(json.dumps({'peaks':peaks,'hardware':False}))
                print('FILE ONLY',label,'peaks',peaks)

if __name__=='__main__':unittest.main()
