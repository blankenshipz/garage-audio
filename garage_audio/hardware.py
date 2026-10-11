"""Metadata/permission admission only; never opens an audio device."""
import json
from pathlib import Path
import pwd
import re
import stat

SOUND_ROOT = Path('/sys/class/sound')
PROC_ROOT = Path('/proc/asound')
FORMATS = {'S24_3LE': 'S24_3_LE', 'S32_LE': 'S32_LE'}

def validate_identity(value):
    if set(value) != {'model','vendor','product','serial','alsa_card','pcm'} or value['model']!='UMC1820':
        raise ValueError('explicit UMC1820 identity fields required')
    for key in ('vendor','product'):
        if not isinstance(value[key],str) or not re.fullmatch('[0-9a-f]{4}',value[key]):raise ValueError('verified lowercase USB VID/PID required')
    if not isinstance(value['serial'],str) or not re.fullmatch('[A-Za-z0-9_.:-]+',value['serial']):raise ValueError('unique verified USB serial required')
    if not isinstance(value['alsa_card'],str) or not re.fullmatch('[A-Za-z0-9_]+',value['alsa_card']):raise ValueError('actual stable ALSA card ID required')
    if type(value['pcm']) is not int or not 0<=value['pcm']<=15:raise ValueError('actual PCM device number required')
    return value

def verified_card(identity):
    validate_identity(identity)
    cards=[p for p in SOUND_ROOT.glob('card[0-9]*')
           if (p/'id').exists() and (p/'id').read_text().strip()==identity['alsa_card']]
    if len(cards)!=1:raise ValueError('bound UMC ALSA card missing/ambiguous; no fallback')
    card=cards[0];device=(card/'device').resolve()
    ancestors=[device,*device.parents][:8]
    matches=[p for p in ancestors if all((p/k).is_file() for k in ('idVendor','idProduct','serial'))
             and (p/'idVendor').read_text().strip().lower()==identity['vendor']
             and (p/'idProduct').read_text().strip().lower()==identity['product']
             and (p/'serial').read_text().strip()==identity['serial']]
    if len(matches)!=1:raise ValueError('USB ancestry differs from verified UMC serial identity')
    return card

def bound_device(state):
    identity=validate_identity(json.loads((state/'hardware-identity.json').read_text()))
    expected=f"hw:CARD={identity['alsa_card']},DEV={identity['pcm']}"
    if (state/'alsa-device').read_text().strip()!=expected:raise ValueError('ALSA string does not match verified hardware identity')
    card=verified_card(identity)
    node=Path(f"/dev/snd/pcmC{card.name[4:]}D{identity['pcm']}p")
    info=node.stat()
    if not stat.S_ISCHR(info.st_mode) or info.st_uid!=pwd.getpwnam('garage-dsp').pw_uid or stat.S_IMODE(info.st_mode)!=0o600:
        raise ValueError('DAC node is not isolated owner garage-dsp/mode0600; refuse start')
    return expected

def validate_capabilities(value, identity=None):
    if not isinstance(value,dict) or set(value)!={'identity','channels','format','samplerate'}:
        raise ValueError('explicit pinned playback capability fields required')
    validate_identity(value['identity'])
    if value['identity']['pcm']!=0:
        raise ValueError('reviewed UMC endpoint metadata describes PCM0 only')
    if identity is not None and value['identity']!=identity:
        raise ValueError('capabilities belong to a different USB/ALSA identity')
    if type(value['channels']) is not int or value['channels'] not in (12,16,20):
        raise ValueError('unreviewed UMC playback channel count')
    if not isinstance(value['format'],str) or value['format'] not in FORMATS or type(value['samplerate']) is not int or value['samplerate']!=48000:
        raise ValueError('unreviewed playback format/rate')
    return value

def playback_capabilities(identity):
    """Read descriptors only, never hw_params or PCM; verify exact USB ancestry."""
    card=verified_card(identity)
    stream=(PROC_ROOT/card.name/'stream0').read_text()
    sections=stream.split('Playback:')
    if len(sections)!=2 or 'Capture:' not in sections[1]:
        raise ValueError('unrecognized USB stream metadata')
    playback=sections[1].split('Capture:',1)[0]
    channels=re.findall(r'^\s*Channels: (\d+)\s*$',playback,re.M)
    formats=re.findall(r'^\s*Format: (\S+)\s*$',playback,re.M)
    rates=re.findall(r'^\s*Rates: ([^\n]+)$',playback,re.M)
    # Multiple alternate settings are not implicitly negotiated or downmixed.
    if len(channels)!=1 or len(formats)!=1 or len(rates)!=1 or '48000' not in rates[0].strip().split(', '):
        raise ValueError('ambiguous endpoint or 48k not advertised')
    return validate_capabilities(dict(identity=dict(identity),channels=int(channels[0]),
                                      format=formats[0],samplerate=48000))

def admit_capabilities(identity, expected):
    validate_capabilities(expected,identity)
    if playback_capabilities(identity)!=expected:
        raise ValueError('UMC playback capabilities changed; reprepare/review, no automatic remap')
    return expected

def load_pinned_capabilities(path, identity):
    """Root-owned policy only. Missing file retains legacy20/S32 expectation."""
    if not path.exists() and not path.is_symlink():
        return validate_capabilities(dict(identity=dict(identity),channels=20,
                                          format='S32_LE',samplerate=48000),identity)
    for item in [path,*path.parents]:
        info=item.lstat()
        if item.is_symlink() or info.st_uid!=0 or info.st_mode&0o022:
            raise PermissionError('playback capability policy is not immutable root-owned metadata')
    if not stat.S_ISREG(path.stat().st_mode):
        raise PermissionError('regular capability policy file required')
    return validate_capabilities(json.loads(path.read_text()),identity)

def admit_map_capabilities(state, expected):
    """Never reuse a physical-map receipt across a capability/identity change."""
    if (state/'physical-map-verified').exists():
        previous=state/'playback-capabilities.json'
        if not previous.is_file() or json.loads(previous.read_text())!=expected:
            raise ValueError('physical map predates/differs from pinned capabilities; administrator revalidation required')
