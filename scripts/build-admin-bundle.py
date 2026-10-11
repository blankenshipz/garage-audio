"""User-only bundle builder. Its digest must be delivered out-of-band."""
import hashlib
import io
import json
from pathlib import Path
import tarfile
import argparse
from datetime import datetime, timezone

ROOT=Path(__file__).resolve().parents[1]
code=['__init__','broker','control','daemon','hardware','ipc','producer','profile','human_authority','human_playback','playback_lease','speaker_realm','speaker_controller','ma_bridge','ma_reference','connect_port','spotify_socket_guard']
assets={f'garage_audio/{n}.py':ROOT/f'garage_audio/{n}.py' for n in code}
assets.update({p:ROOT/p for p in ['scripts/dsp-daemon','scripts/policy-entry.py','scripts/admin-dac-rollback','scripts/human-playback','scripts/human-playback-entry.py','scripts/speaker-entry.py','scripts/prepare-spotify-connect.py','docs/spotify-only-quickstart.md','config/system/garage-audio-dsp.service','config/system/garage-speakers.service']})
assets['camilla.tar.gz']=ROOT/'tools/camilla.tar.gz'
assets['libasound.deb']=ROOT/'tools/libasound2t64_1.2.15.3-1ubuntu1.1_amd64.deb'
for path in sorted((ROOT/'tools/policy-wheels').glob('*.whl')):assets['wheels/'+path.name]=path
manifest={name:hashlib.sha256(path.read_bytes()).hexdigest() for name,path in assets.items()}
parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path)
args=parser.parse_args()
output=args.output or ROOT/'state/native-playback-receipts'/datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')/'admin-release.tar.gz'
output.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
with output.open('xb') as target,tarfile.open(fileobj=target,mode='w:gz') as t:
    for name,path in assets.items():
        if path.is_symlink():raise ValueError('builder refuses symbolic links')
        data=path.read_bytes();info=tarfile.TarInfo(name);info.size=len(data);info.mode=0o644
        t.addfile(info,io.BytesIO(data))
    data=json.dumps(manifest,sort_keys=True).encode();info=tarfile.TarInfo('manifest.json');info.size=len(data);t.addfile(info,io.BytesIO(data))
print('bundle SHA256',hashlib.sha256(output.read_bytes()).hexdigest())
print('bootstrap SHA256',hashlib.sha256((ROOT/'scripts/provision.py').read_bytes()).hexdigest())
print('bundle path',output)
