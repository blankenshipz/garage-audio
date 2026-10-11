"""Stage a muted candidate from non-stream metadata; never install/start audio.

Run as the ordinary project owner on Linux. Output is NOT daemon provisioning.
"""
import json
from pathlib import Path
import sys
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from garage_audio.hardware import validate_identity, playback_capabilities
from garage_audio.profile import DEFAULT, hardware_config, validate


def prepare():
    cards = [p for p in Path('/sys/class/sound').glob('card[0-9]*')
             if (p / 'id').exists() and (p / 'id').read_text().strip() == 'UMC1820']
    if len(cards) != 1:
        raise ValueError('UMC1820 missing/ambiguous; no fallback')
    card = cards[0]
    ancestors = [(card / 'device').resolve(), *(card / 'device').resolve().parents]
    usb = [p for p in ancestors if (p / 'idVendor').exists()
           and (p / 'idVendor').read_text().strip() == '1397'
           and (p / 'idProduct').read_text().strip() == '0514']
    if len(usb) != 1:
        raise ValueError('verified UMC USB ancestry required')
    identity = validate_identity(dict(model='UMC1820', vendor='1397', product='0514',
                                     serial=(usb[0] / 'serial').read_text().strip(),
                                     alsa_card='UMC1820', pcm=0))
    proc = Path('/proc/asound') / card.name
    for direction in ('p', 'c'):
        if (proc / f'pcm0{direction}/sub0/status').read_text().strip() != 'closed':
            raise ValueError('device stream is open; leave existing state alone')
    stream = (proc / 'stream0').read_text()
    capabilities = playback_capabilities(identity)
    profile = validate(DEFAULT)
    for branch in profile['channels'].values():
        branch['mute'] = True
    alsa = 'hw:CARD=UMC1820,DEV=0'
    config = hardware_config(profile, alsa, capabilities)
    destination = ROOT / 'state' / 'hardware-preparation' / datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')
    destination.mkdir(parents=True, mode=0o700, exist_ok=False)
    documents = {'identity.json': identity, 'profile.json': profile,
                 'playback-capabilities.json': capabilities,
                 'camilla-muted-candidate.json': config,
                 'preparation.json': dict(status='staged-not-active', autostart=False,
                     physical_map_verified=False, master_mute_verified=False,
                     required_startup=dict(mute=True, gain_db=-60),
                     playback_channels=capabilities['channels'], alsa_format=capabilities['format'],
                     samplerate=48000, alsa_device=alsa,
                     planned_analog_outputs=dict(left=3, right=4, sub=5),
                     prerequisites=['review/install freshly pinned privileged source bundle and capability policy',
                                    'reviewed administrator DAC isolation provisioning',
                                    'attended physical mapping approval'])}
    for name, document in documents.items():
        with (destination / name).open('x') as target:
            json.dump(document, target, indent=2)
            target.write('\n')
    (destination / 'usb-stream-metadata.txt').write_text(stream)
    print(destination)


if __name__ == '__main__':
    prepare()
