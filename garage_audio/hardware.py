"""Metadata/permission admission only; never opens an audio device."""
import json
from pathlib import Path
import pwd
import re
import stat

def validate_identity(value):
    if set(value) != {'model','vendor','product','serial','alsa_card','pcm'} or value['model']!='UMC1820':
        raise ValueError('explicit UMC1820 identity fields required')
    for key in ('vendor','product'):
        if not isinstance(value[key],str) or not re.fullmatch('[0-9a-f]{4}',value[key]):raise ValueError('verified lowercase USB VID/PID required')
    if not isinstance(value['serial'],str) or not re.fullmatch('[A-Za-z0-9_.:-]+',value['serial']):raise ValueError('unique verified USB serial required')
    if not isinstance(value['alsa_card'],str) or not re.fullmatch('[A-Za-z0-9_]+',value['alsa_card']):raise ValueError('actual stable ALSA card ID required')
    if type(value['pcm']) is not int or not 0<=value['pcm']<=15:raise ValueError('actual PCM device number required')
    return value

def bound_device(state):
    identity=validate_identity(json.loads((state/'hardware-identity.json').read_text()))
    expected=f"hw:CARD={identity['alsa_card']},DEV={identity['pcm']}"
    if (state/'alsa-device').read_text().strip()!=expected:raise ValueError('ALSA string does not match verified hardware identity')
    cards=[p for p in Path('/sys/class/sound').glob('card[0-9]*')
           if (p/'id').exists() and (p/'id').read_text().strip()==identity['alsa_card']]
    if len(cards)!=1:raise ValueError('bound UMC ALSA card missing/ambiguous; no fallback')
    card=cards[0];device=(card/'device').resolve()
    ancestors=[device,*device.parents][:8]
    matches=[p for p in ancestors if all((p/k).is_file() for k in ('idVendor','idProduct','serial'))
             and (p/'idVendor').read_text().strip().lower()==identity['vendor']
             and (p/'idProduct').read_text().strip().lower()==identity['product']
             and (p/'serial').read_text().strip()==identity['serial']]
    if len(matches)!=1:raise ValueError('USB ancestry differs from verified UMC serial identity')
    node=Path(f"/dev/snd/pcmC{card.name[4:]}D{identity['pcm']}p")
    info=node.stat()
    if not stat.S_ISCHR(info.st_mode) or info.st_uid!=pwd.getpwnam('garage-dsp').pw_uid or stat.S_IMODE(info.st_mode)!=0o600:
        raise ValueError('DAC node is not isolated owner garage-dsp/mode0600; refuse start')
    return expected
