"""UMC identity/descriptor/node admission without opening any sound device."""
import fcntl
import json
import os
from pathlib import Path
import stat

IDENTITY = {'vendor': '1397', 'product': '0514', 'serial': 'D72E24B7',
            'alsa_card': 'UMC1820', 'pcm': 0}
CAPABILITIES = {'channels': 12, 'format': 'S24_3LE', 'samplerate': 48000}


def load_policy(path):
    path = Path(path)
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022 or info.st_size > 4096:
        raise PermissionError('immutable root-owned device policy required')
    value = json.loads(path.read_text())
    if value != {'version': 1, 'identity': IDENTITY, 'capabilities': CAPABILITIES,
                  'device_uid': 1002, 'device_gid': 1003, 'outputs': [3, 4]}:
        raise ValueError('exact reviewed UMC policy required')
    return value


def inventory(sound=Path('/sys/class/sound'), proc=Path('/host-asound'), nodes=Path('/host-snd')):
    cards = [p for p in sound.glob('card[0-9]*') if (p/'id').is_file()
             and (p/'id').read_text().strip() == 'UMC1820']
    if len(cards) != 1:
        raise ValueError('UMC card missing or ambiguous; no fallback')
    card = cards[0]
    device = (card/'device').resolve(strict=True)
    matches = [p for p in [device, *device.parents][:8]
               if all((p/k).is_file() for k in ('idVendor', 'idProduct', 'serial', 'devnum', 'busnum'))]
    if not matches:
        raise ValueError('USB ancestry required')
    # The root hub also has these descriptors. Bind the nearest USB device,
    # never search past a mismatched device for a convenient ancestor identity.
    usb = matches[0]
    usb_info = usb.stat()
    stream = (proc/card.name/'stream0').read_text()
    import re
    sections = stream.split('Playback:')
    if len(sections) != 2 or 'Capture:' not in sections[1]:
        raise ValueError('unrecognized USB descriptor')
    playback = sections[1].split('Capture:', 1)[0]
    channels = re.findall(r'^\s*Channels: (\d+)\s*$', playback, re.M)
    formats = re.findall(r'^\s*Format: (\S+)\s*$', playback, re.M)
    rates = re.findall(r'^\s*Rates: ([^\n]+)$', playback, re.M)
    if len(channels) != 1 or len(formats) != 1 or len(rates) != 1:
        raise ValueError('ambiguous USB alternate setting')
    number = int(card.name[4:])
    names = [f'pcmC{number}D0p', f'controlC{number}']
    node_values = {}
    for name in names:
        info = (nodes/name).lstat()
        if not stat.S_ISCHR(info.st_mode):
            raise ValueError('exact character node required')
        node_values[name] = {'dev': info.st_dev, 'inode': info.st_ino, 'rdev': info.st_rdev,
            'uid': info.st_uid, 'gid': info.st_gid, 'mode': stat.S_IMODE(info.st_mode)}
    return {'identity': dict(IDENTITY, vendor=(usb/'idVendor').read_text().strip().lower(),
                product=(usb/'idProduct').read_text().strip().lower(),
                serial=(usb/'serial').read_text().strip()),
            'capabilities': {'channels': int(channels[0]), 'format': formats[0],
                             'samplerate': 48000 if '48000' in rates[0].split(', ') else None},
            'connection': {'path': str(usb), 'inode': usb_info.st_ino, 'dev': usb_info.st_dev,
                           'busnum': (usb/'busnum').read_text().strip(),
                           'devnum': (usb/'devnum').read_text().strip()},
            'card': number, 'nodes': node_values}


def validate_inventory(value, container_nodes=Path('/dev/snd')):
    if value['identity'] != IDENTITY or value['capabilities'] != CAPABILITIES:
        raise ValueError('USB identity/capabilities changed; no remap or fallback')
    for name, node in value['nodes'].items():
        if node['uid'] != 1002 or node['gid'] != 1003 or node['mode'] != 0o600:
            raise PermissionError('exclusive numeric UID1002/GID1003 mode0600 required')
        try:
            mapped = (container_nodes/name).lstat()
        except FileNotFoundError:
            raise PermissionError('verified UMC node missing from container; recreate identity-bound mapping') from None
        if not stat.S_ISCHR(mapped.st_mode) or mapped.st_rdev != node['rdev'] \
                or mapped.st_uid != 1002 or mapped.st_gid != 1003 or stat.S_IMODE(mapped.st_mode) != 0o600:
            raise PermissionError('exact mapped node identity/permissions required')
    return value


def device_environment():
    """Root boot preflight: derive numeric paths from USB identity, never saved card index.

    Metadata only. Does not change permissions, acquire ownership, or open a PCM.
    Existing running leases still revoke on connection changes; no automatic resume.
    """
    value = inventory(proc=Path('/proc/asound'), nodes=Path('/dev/snd'))
    validate_inventory(value, container_nodes=Path('/dev/snd'))
    return {'GARAGE_AUDIO_PCM_NODE': f'/dev/snd/pcmC{value["card"]}D0p',
            'GARAGE_AUDIO_CONTROL_NODE': f'/dev/snd/controlC{value["card"]}'}


class DeviceLease:
    def __init__(self, policy='/device-policy.json', lock='/device-lock/owner.lock'):
        self.policy = Path(policy)
        load_policy(self.policy)
        self.binding = validate_inventory(inventory())
        self.fd = os.open(lock, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
        info = os.fstat(self.fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != 1002 or stat.S_IMODE(info.st_mode) != 0o600:
            self.close()
            raise PermissionError('private device ownership lock required')
        try:
            fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.verify()
        except BaseException:
            self.close()
            raise

    def verify(self):
        load_policy(self.policy)
        if validate_inventory(inventory()) != self.binding:
            raise ValueError('USB connection or node identity changed; revoke current generation')
        return f"hw:CARD=UMC1820,DEV=0"

    def close(self):
        if getattr(self, 'fd', None) is not None:
            os.close(self.fd)
            self.fd = None


def hardware_graph(graph, alsa):
    if alsa != 'hw:CARD=UMC1820,DEV=0':
        raise ValueError('no default DAC or alternate target')
    # Declarative state is not commissioning authority. Until the immutable
    # reviewed policy changes, only the existing OUT3/4 wiring is admissible.
    mapping = graph['mixers']['route']['mapping']
    if [row['dest'] for row in mapping] != [2, 3]:
        raise PermissionError('output map exceeds commissioned OUT3/4 policy; sub wiring authority required')
    gain = graph['filters']['headroom']['parameters']['gain']
    if type(gain) not in (int, float) or not -60 <= gain <= -6:
        raise ValueError('hardware headroom must remain at least 6dB')
    graph['title'] = 'Garage identity-bound UMC declarative OUT3/4'
    graph['devices']['playback'] = {'type': 'Alsa', 'channels': 12, 'device': alsa, 'format': 'S24_3_LE'}
    graph['devices'].update(enable_rate_adjust=True, capture_samplerate=48000,
        resampler={'type': 'AsyncSinc', 'profile': 'Balanced'}, target_level=2048,
        adjust_period=1, volume_limit=0, volume_ramp_time=400)
    return graph
