"""User-only bundle builder. Its digest must be delivered out-of-band."""
import hashlib
import io
import json
from pathlib import Path
import tarfile

ROOT=Path(__file__).resolve().parents[1]
code=['__init__','broker','control','daemon','hardware','ipc','profile']
assets={f'garage_audio/{n}.py':ROOT/f'garage_audio/{n}.py' for n in code}
assets.update({p:ROOT/p for p in ['scripts/dsp-daemon','scripts/policy-entry.py','scripts/admin-dac-rollback','config/system/garage-audio-dsp.service']})
assets['camilla.tar.gz']=ROOT/'tools/camilla.tar.gz'
for path in sorted((ROOT/'tools/policy-wheels').glob('*.whl')):assets['wheels/'+path.name]=path
manifest={name:hashlib.sha256(path.read_bytes()).hexdigest() for name,path in assets.items()}
output=ROOT/'state/admin-release.tar.gz'
with tarfile.open(output,'w:gz') as t:
    for name,path in assets.items():
        if path.is_symlink():raise ValueError('builder refuses symbolic links')
        data=path.read_bytes();info=tarfile.TarInfo(name);info.size=len(data);info.mode=0o644
        t.addfile(info,io.BytesIO(data))
    data=json.dumps(manifest,sort_keys=True).encode();info=tarfile.TarInfo('manifest.json');info.size=len(data);t.addfile(info,io.BytesIO(data))
print('bundle SHA256',hashlib.sha256(output.read_bytes()).hexdigest())
print('bootstrap SHA256',hashlib.sha256((ROOT/'scripts/provision.py').read_bytes()).hexdigest())
