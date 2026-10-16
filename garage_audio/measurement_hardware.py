"""Operator-pinned microphone capture; never opens devices during admission.

Only the job guardian calls capture after exact-manifest human authority is consumed.
No public paths, executables, sound authority or automatic mic/output fallback.
"""
import array
import ctypes
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import signal
import stat
import subprocess
import sys
import time
from . import audio_device, calibration, measurement_artifacts as artifacts
from .measurement_signal import read_wav
from .measurement_acoustics import analyze
from . import measurement_correction

POLICY = Path('/etc/garage-audio/measurement.json')
CALIBRATION = Path('/etc/garage-audio/microphone-calibration.txt')
FRESH = ('microphone.raw', 'output.raw', 'record.stderr', 'play.stderr', 'hardware-exit.json', 'evidence.json')+measurement_correction.FRESH
EXISTING = ('excitation.wav', 'hardware-admission.json', 'microphone-calibration.txt')+measurement_correction.EXISTING


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def immutable(path, limit):
    for parent in Path(path).parents:
        info = parent.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
            raise PermissionError('root-owned immutable policy ancestry required')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022 or info.st_size > limit:
            raise PermissionError('bounded root-owned immutable microphone policy/calibration required')
        return os.read(fd, limit+1)
    finally: os.close(fd)


def admission():
    policy_raw = immutable(POLICY, 8192); policy = json.loads(policy_raw)
    if set(policy) != {'version', 'microphone', 'calibration_sha256', 'outputs', 'peak_dbfs'} or policy['version'] != 1:
        raise ValueError('exact commissioned measurement policy required')
    if os.getuid() != 1002:
        raise PermissionError('hardware capture requires isolated device-owner UID1002, never dashboard privilege escalation')
    mic = policy['microphone']
    if set(mic) != {'vendor', 'product', 'serial', 'alsa_card', 'pcm', 'channels', 'channel', 'orientation'}:
        raise ValueError('exact calibrated microphone identity required')
    for key in ('vendor', 'product'):
        if not isinstance(mic[key], str) or not re.fullmatch('[a-f0-9]{4}', mic[key]): raise ValueError('USB identity required')
    for key in ('serial', 'alsa_card'):
        if not isinstance(mic[key], str) or not re.fullmatch('[A-Za-z0-9_.:-]{1,80}', mic[key]): raise ValueError('stable mic identity required')
    if mic['orientation'] not in ('0', '90') or type(mic['pcm']) is not int or mic['pcm'] != 0:
        raise ValueError('reviewed mic PCM0/orientation required')
    if type(mic['channels']) is not int or mic['channels'] not in (1, 2) or type(mic['channel']) is not int or not 0 <= mic['channel'] < mic['channels']:
        raise ValueError('reviewed mono/stereo mic channel required')
    peak = policy['peak_dbfs']
    if type(peak) not in (int, float) or not math.isfinite(peak) or not -90 <= peak <= -60:
        raise ValueError('commissioned digital peak -90..-60dBFS required; not acoustic SPL')
    device_policy = audio_device.load_policy('/device-policy.json')
    outputs = policy['outputs']
    if not isinstance(outputs, dict) or not outputs or set(outputs)-{'left', 'right', 'sub'}:
        raise ValueError('explicit commissioned logical outputs required')
    if any(type(v) is not int or v not in device_policy['outputs'] for v in outputs.values()) or len(set(outputs.values())) != len(outputs):
        raise PermissionError('measurement routes exceed commissioned device outputs')
    output = audio_device.validate_inventory(audio_device.inventory())
    cards = [p for p in Path('/sys/class/sound').glob('card[0-9]*')
             if (p/'id').is_file() and (p/'id').read_text().strip() == mic['alsa_card']]
    if len(cards) != 1: raise ValueError('calibrated microphone missing or ambiguous')
    card = cards[0]; device = (card/'device').resolve(strict=True)
    ancestors = [p for p in [device, *device.parents][:8] if all((p/k).is_file() for k in ('idVendor', 'idProduct', 'serial'))]
    if not ancestors: raise ValueError('USB microphone ancestry absent')
    usb = ancestors[0]
    if any((usb/k).read_text().strip() != mic[v] for k,v in [('idVendor','vendor'),('idProduct','product'),('serial','serial')]):
        raise ValueError('microphone identity changed')
    stream = (Path('/host-asound')/card.name/'stream0').read_text().split('Capture:')
    if len(stream) != 2: raise ValueError('microphone capture descriptor required')
    for pattern, expected in [(r'^\s*Format: (\S+)\s*$', ['S32_LE']), (r'^\s*Channels: (\d+)\s*$', [str(mic['channels'])])]:
        if re.findall(pattern, stream[1], re.M) != expected: raise ValueError('mic capture format/channels changed')
    rates = re.findall(r'^\s*Rates: ([^\n]+)$', stream[1], re.M)
    if len(rates) != 1 or '48000' not in rates[0].strip().split(', '): raise ValueError('mic48k unavailable')
    node = Path('/dev/snd')/f'pcmC{card.name[4:]}D0c'; info = node.stat()
    if not stat.S_ISCHR(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600:
        raise PermissionError('exclusive microphone node ownership required')
    raw = immutable(CALIBRATION, calibration.MAX_TEXT)
    if hashlib.sha256(raw).hexdigest() != policy['calibration_sha256']: raise ValueError('microphone calibration changed')
    points = calibration.parse(raw.decode(), 'mic-calibration')['points']
    if points[0]['frequency_hz'] > 20 or points[-1]['frequency_hz'] < 1000: raise ValueError('mic calibration band insufficient')
    for binary in ('/usr/bin/arecord', '/usr/bin/aplay'):
        info_binary = Path(binary).stat()
        if info_binary.st_uid != 0 or info_binary.st_mode & 0o022 or not os.access(binary, os.X_OK): raise PermissionError('trusted ALSA utilities required')
    return {'policy': policy, 'policy_sha256': hashlib.sha256(policy_raw).hexdigest(),
            'microphone_connection': {'usb_path': str(usb), 'usb_inode': usb.stat().st_ino,
                'node_rdev': info.st_rdev, 'node_inode': info.st_ino}, 'output': output}


def readiness():
    try:
        value = admission()
        correction_binding = measurement_correction.snapshot()
        return {'hardware_capture_available': True, 'microphone': value['policy']['microphone'],
                'commissioned_channels': sorted(value['policy']['outputs']), 'requires_independent_attended_approval': True,
                'correction_revision':correction_binding['revision'],'correction_generation':correction_binding['generation']}
    except (OSError, ValueError, PermissionError, KeyError, TypeError, measurement_correction.audio_public.AudioFailure):
        return {'hardware_capture_available': False, 'requires_independent_attended_approval': True,
                'requirements': ['Root-pinned calibrated USB microphone/48k S32_LE capture and exclusive node access',
                    'Commissioned UMC output policy and private device lease', 'Trusted ALSA utilities and standalone human authority channel',
                    'Readable current hardware correction revision/generation and pinned Camilla FILE renderer']}


def prepare(directory, channel, spec, orientation):
    bound = admission()
    if spec['sample_rate'] != 48000 or orientation != bound['policy']['microphone']['orientation'] or channel not in bound['policy']['outputs']:
        raise ValueError('measurement plan differs from calibrated microphone or commissioned route')
    artifacts.admit(directory, FRESH+('hardware-admission.json', 'microphone-calibration.txt'), ('excitation.wav',))
    artifacts.write(directory, 'hardware-admission.json', json.dumps(bound, sort_keys=True).encode())
    artifacts.write(directory, 'microphone-calibration.txt', immutable(CALIBRATION, calibration.MAX_TEXT))
    return bound


def capture(directory, manifest, guard):
    """Called only by the approved owned worker; guard revokes all children."""
    bound = manifest['hardware_admission']; spec = manifest['signal']; children = []
    if admission() != bound: raise ValueError('hardware binding changed before capture')
    artifacts.admit(directory, FRESH, EXISTING)
    mic = bound['policy']['microphone']; frames = spec['sample_frames']; rate = spec['sample_rate']
    if manifest.get('version') != 4: raise ValueError('correction-bound version4 manifest required')
    samples,output,render_evidence = measurement_correction.render(directory,manifest,guard)
    artifacts.write(directory, 'output.raw', output)
    record_frames = frames+rate//2; expected_bytes = record_frames*mic['channels']*4
    owner = os.getpid()
    def child_guard():
        if ctypes.CDLL(None).prctl(1, signal.SIGKILL, 0, 0, 0) or os.getppid() != owner: os._exit(125)
    lease = audio_device.DeviceLease()
    try:
        guard()
        with artifacts.exclusive(directory, 'microphone.raw') as recording, artifacts.exclusive(directory, 'record.stderr') as recerr, artifacts.exclusive(directory, 'play.stderr') as playerr:
            recorder = subprocess.Popen(['/usr/bin/arecord', '-q', '-D', f"hw:CARD={mic['alsa_card']},DEV=0", '-t', 'raw', '-f', 'S32_LE', '-r', '48000', '-c', str(mic['channels']), '-s', str(record_frames)],
                stdin=subprocess.DEVNULL, stdout=recording, stderr=recerr, preexec_fn=child_guard)
            children.append(recorder)
            # Require actual microphone data BEFORE permitting output; acquisition
            # pre-roll starts from capture data, not a blind device-open delay.
            end = time.monotonic()+2
            while os.fstat(recording.fileno()).st_size < rate*mic['channels']*4//10:
                guard(); lease.verify()
                if recorder.poll() is not None or time.monotonic() >= end: raise RuntimeError('microphone did not become ready; no output started')
                time.sleep(.01)
            if admission() != bound: raise ValueError('microphone changed during pre-roll')
            if recorder.poll() is not None: raise RuntimeError('microphone stopped before output; no output started')
            with artifacts.read_handle(directory, 'output.raw') as pcm:
                player = subprocess.Popen(['/usr/bin/aplay', '-q', '-D', lease.verify(), '-t', 'raw', '-f', 'S24_3LE', '-r', '48000', '-c', '12'],
                    stdin=pcm, stdout=subprocess.DEVNULL, stderr=playerr, preexec_fn=child_guard, pass_fds=(lease.fd,))
                children.append(player)
                end = time.monotonic()+4
                while any(p.poll() is None for p in children):
                    guard(); lease.verify()
                    if admission() != bound: raise ValueError('hardware identity changed during capture')
                    if os.fstat(recording.fileno()).st_size > expected_bytes or time.monotonic() >= end: raise RuntimeError('capture size/deadline exceeded')
                    time.sleep(.02)
            if any(p.returncode != 0 for p in children): raise RuntimeError('owned ALSA child failed')
        raw = artifacts.read(directory, 'microphone.raw', expected_bytes)
        if len(raw) != expected_bytes: raise ValueError('microphone capture truncated')
        values = array.array('i'); values.frombytes(raw)
        if sys.byteorder != 'little': values.byteswap()
        response = [v/2147483648 for v in values[mic['channel']::mic['channels']]]
        calraw = artifacts.read(directory, 'microphone-calibration.txt', calibration.MAX_TEXT)
        result = analyze(samples, response, rate, spec['band_hz'], calibration.parse(calraw.decode(), 'mic-calibration')['points'], guard)
        result.update(adapter='hardware', hardware_enabled=True, microphone_serial=mic['serial'],
                      calibration_sha256=bound['policy']['calibration_sha256'], orientation=mic['orientation'],
                      capture_sha256=hashlib.sha256(raw).hexdigest(), approved_hardware_sha256=digest(bound),
                      output_peak_dbfs=bound['policy']['peak_dbfs'], raw_artifact='microphone.raw',
                      correction_render=render_evidence)
        artifacts.write(directory, 'evidence.json', json.dumps(result, allow_nan=False).encode())
        return 0 if result['supported_magnitude'] else 18
    finally:
        for child in children:
            if child.poll() is None: child.kill()
        for child in children: child.wait(timeout=2)
        lease.close()
        artifacts.write(directory, 'hardware-exit.json', json.dumps({'owned_children_reaped': True, 'exit_statuses': [p.returncode for p in children]}).encode())
