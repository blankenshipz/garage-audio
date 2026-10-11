"""Offline bounded PCM and band-limited regularized IR estimate; no sound APIs."""
import array
import cmath
import hashlib
import math
import os
import sys
import wave

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def write_wav(path, samples, rate):
    if len(samples) > 192000: raise ValueError('bounded mono file required')
    data = array.array('h',(int(max(-1,min(1,x))*32767) for x in samples))
    if sys.byteorder != 'little': data.byteswap()
    with wave.open(str(path),'wb') as wav:
        wav.setnchannels(1); wav.setsampwidth(2); wav.setframerate(rate); wav.writeframes(data.tobytes())
    os.chmod(path,0o600)

def read_wav(path, expected_rate, expected_frames):
    if path.is_symlink() or path.stat().st_size > 400000: raise ValueError('invalid capture file')
    with wave.open(str(path),'rb') as wav:
        if (wav.getnchannels(),wav.getsampwidth(),wav.getframerate(),wav.getnframes()) != (1,2,expected_rate,expected_frames):
            raise ValueError('capture format/length mismatch')
        data = array.array('h'); data.frombytes(wav.readframes(expected_frames))
        if sys.byteorder != 'little': data.byteswap()
    return [x/32767 for x in data]

def excitation(path, rate, channel):
    seconds, pre, post, fade = 1,.25,.5,.05
    low,high = (20,200) if channel == 'sub' else (20,1000)
    count = int(rate*seconds); ratio = math.log(high/low)
    values = [0.0]*int(pre*rate)
    for i in range(count):
        t = i/rate
        envelope = min(1,t/fade,(seconds-t)/fade)
        phase = 2*math.pi*low*seconds/ratio*(math.exp(t*ratio/seconds)-1)
        values.append(.01*max(0,envelope)*math.sin(phase))
    values += [0.0]*int(post*rate)
    write_wav(path,values,rate)
    return {'sample_rate':rate,'sample_frames':len(values),'channels':1,'sample_width_bytes':2,
            'encoding':'PCM16LE','excitation_sha256':sha(path),
            'peak_dbfs':20*math.log10(max(abs(int(v*32767))/32767 for v in values)), 'maximum_peak_dbfs':-40,
            'band_hz':[low,high],'fade_seconds':fade,'pre_seconds':pre,'post_seconds':post,
            'sweep_seconds':seconds,'signal_seconds':len(values)/rate}

def fft(values, inverse=False, guard=lambda:None):
    n = len(values); out = list(map(complex,values)); j = 0
    for i in range(1,n):
        bit = n>>1
        while j & bit: j ^= bit; bit >>= 1
        j ^= bit
        if i < j: out[i],out[j] = out[j],out[i]
    length = 2
    while length <= n:
        guard()
        root = cmath.exp((2j if inverse else -2j)*math.pi/length)
        for start in range(0,n,length):
            w = 1
            for k in range(length//2):
                a,b = out[start+k],out[start+k+length//2]*w
                out[start+k],out[start+k+length//2] = a+b,a-b
                w *= root
        length *= 2
    return [x/n for x in out] if inverse else out

def capture_evidence(excitation_path, capture_path, spec, guard=lambda:None):
    rate,frames = spec['sample_rate'],spec['sample_frames']
    x = read_wav(excitation_path,rate,frames); y = read_wav(capture_path,rate,frames)
    guard()
    peak = max(abs(v) for v in y)
    pre = int(spec['pre_seconds']*rate)
    noise = math.sqrt(sum(v*v for v in y[:pre])/max(1,pre))
    x_energy = sum(v*v for v in x); y_energy = sum(v*v for v in y)
    x_rms = math.sqrt(x_energy/frames); y_rms = math.sqrt(y_energy/frames)
    gain_ratio = y_rms/max(x_rms,1e-15)
    correlation = abs(sum(a*b for a,b in zip(x,y)))/max(math.sqrt(x_energy*y_energy),1e-15)
    snr_db = 20*math.log10(max(y_rms,1e-15)/max(noise,1e-8))
    reference_ok = sha(excitation_path) == spec['excitation_sha256']
    checks = {'length_format_valid':True,'reference_hash_valid':reference_ok,
              'clipping_free':peak < .98,'pre_noise_below_dbfs_minus_50':noise <= 10**(-50/20),
              'usable_excitation_energy':1e-6 <= x_rms <= .02,
              'usable_response_energy':y_rms >= 1e-5 and .02 <= gain_ratio <= 4,
              'response_snr_at_least_20_db':snr_db >= 20,
              'shared_reference_correlation_supported':correlation >= .5,
              'clock_drift_verified':False,'calibration_verified':False,'timing_verified':False}
    quality_keys = ('reference_hash_valid','clipping_free','pre_noise_below_dbfs_minus_50','usable_excitation_energy',
                    'usable_response_energy','response_snr_at_least_20_db','shared_reference_correlation_supported')
    quality = {'excitation_rms':x_rms,'response_rms':y_rms,'response_gain_ratio':gain_ratio,
               'response_snr_db':snr_db,'shared_reference_correlation':correlation}
    if not all(checks[k] for k in quality_keys):
        return {'source':'synthetic','checks':checks,'supported_ir':False,'peak_dbfs':20*math.log10(max(peak,1e-15)),
                'pre_noise_dbfs':20*math.log10(max(noise,1e-15)),'capture_sha256':sha(capture_path),
                'excitation_sha256':sha(excitation_path),'quality':quality,'warnings':['Strict capture quality rejected; no supported IR, calibration or timing claim']}
    n = 1
    while n < 2*frames: n *= 2
    X = fft(x+[0.]*(n-frames),guard=guard); Y = fft(y+[0.]*(n-frames),guard=guard)
    power = max(abs(v)**2 for v in X); regularization = power*1e-6
    low,high = spec['band_hz']
    spectrum = []
    for i,(a,b) in enumerate(zip(X,Y)):
        frequency = min(i,n-i)*rate/n
        spectrum.append(b*a.conjugate()/(abs(a)**2+regularization) if low <= frequency <= high else 0j)
    impulse = fft(spectrum,inverse=True,guard=guard)
    return {'source':'synthetic','checks':checks,'quality':quality,'supported_ir':True,
            'ir_kind':'regularized band-limited excitation/capture transfer estimate; not calibrated absolute IR',
            'ir_sample_rate':rate,'ir_first_samples':[v.real for v in impulse[:1024]],
            'regularization_relative':1e-6,'analysis_band_hz':[low,high],
            'transfer_points':[{'frequency_hz':index*rate/n,'magnitude_db':20*math.log10(max(abs(spectrum[index]),1e-15))}
                               for index in [min(math.floor(high*n/rate),max(math.ceil(low*n/rate),round(low*(high/low)**(i/60)*n/rate))) for i in range(61)]],
            'peak_dbfs':20*math.log10(max(peak,1e-15)), 'pre_noise_dbfs':20*math.log10(max(noise,1e-15)),
            'capture_sha256':sha(capture_path),'excitation_sha256':sha(excitation_path),
            'warnings':['Synthetic file/null capture, not microphone or garage evidence',
                        'No calibration correction, clock compensation, absolute latency or acoustic timing claim']}
