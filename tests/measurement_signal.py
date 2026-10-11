"""Synthetic PCM, quality and FFT checks. No audio devices or calibration claims."""
import math
import random
from pathlib import Path
import tempfile
import unittest
from garage_audio.measurement_signal import capture_evidence, excitation, fft, read_wav, sha, write_wav

class SignalTest(unittest.TestCase):
    def test_fft_roundtrip(self):
        values = [math.sin(i*.21) for i in range(128)]
        actual = fft(fft(values),inverse=True)
        self.assertLess(max(abs(a-b) for a,b in zip(actual,values)),1e-10)
    def test_band_limited_known_transfer_and_quality_rejections(self):
        with tempfile.TemporaryDirectory() as tmp:
            x,y = Path(tmp)/'x.wav',Path(tmp)/'y.wav'
            # Impulse fixture validates the supported transfer estimator, not room acoustics.
            reference = [0.0]*4096; reference[1000] = .01
            write_wav(x,reference,48000)
            spec = {'sample_rate':48000,'sample_frames':4096,'pre_seconds':.01,
                    'excitation_sha256':sha(x),'band_hz':[100,1000]}
            write_wav(y,[.8*v for v in reference],48000)
            result = capture_evidence(x,y,spec)
            self.assertTrue(result['supported_ir'])
            self.assertFalse(result['checks']['calibration_verified'])
            # PCM16 quantization: within .05 dB of the known 0.8 transfer.
            self.assertTrue(all(abs(p['magnitude_db']-20*math.log10(.8)) < .05 for p in result['transfer_points']))
            for bad in ([0.0]*4096,[v*.001 for v in reference]):
                write_wav(y,bad,48000)
                self.assertFalse(capture_evidence(x,y,spec)['supported_ir'])
            rng = random.Random(1)
            noise_only = [0.0]*480+[rng.uniform(-.001,.001) for _ in range(4096-480)]
            write_wav(y,noise_only,48000)
            self.assertFalse(capture_evidence(x,y,spec)['supported_ir'])
            clipped = list(reference); clipped[1000] = 1
            write_wav(y,clipped,48000)
            self.assertFalse(capture_evidence(x,y,spec)['supported_ir'])
            noisy = list(reference); noisy[:480] = [.01]*480
            write_wav(y,noisy,48000)
            self.assertFalse(capture_evidence(x,y,spec)['checks']['pre_noise_below_dbfs_minus_50'])
            write_wav(y,reference[:-1],48000)
            with self.assertRaises(ValueError): capture_evidence(x,y,spec)
    def test_sub_sweep_defaults_bounds_and_original_hash(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'excitation.wav'
            spec = excitation(path,48000,'sub')
            samples = read_wav(path,48000,spec['sample_frames'])
            self.assertEqual(spec['band_hz'],[20,200])
            self.assertLessEqual(spec['peak_dbfs'],-40)
            self.assertEqual(spec['excitation_sha256'],sha(path))
            self.assertTrue(all(v == 0 for v in samples[:12000]))
            self.assertEqual(len(samples),84000)

if __name__ == '__main__': unittest.main()
