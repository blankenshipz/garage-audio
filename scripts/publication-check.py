#!/usr/bin/env python3
"""Software-only, retained packaging gate. No application imports or device access."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
TOP = {'LICENSE', 'THIRD_PARTY_NOTICES.md', 'README.md', 'pyproject.toml',
       'MANIFEST.in', 'requirements.txt', 'requirements.lock', '.env.example',
       '.dockerignore', 'API_CONTRACT.md', 'API_CONTRACT_ROOM.md'}
TREES = {'garage_audio', 'ma_provider', 'profiles', 'web', 'docs', 'config',
         'scripts', 'vendor', 'tests', 'research', '.github'}
FORBIDDEN = {'state', 'secrets', 'tools', 'venv', '.venv', '__pycache__',
             'node_modules', '.git', '.pytest_cache'}
REQUIRED = {'LICENSE', 'THIRD_PARTY_NOTICES.md', 'README.md', '.env.example',
            'pyproject.toml', 'garage_audio/__init__.py', 'web/index.html',
            'profiles/uncalibrated.json', 'vendor/dsp/PROVENANCE.md',
            'vendor/dsp/SHA256SUMS', 'vendor/dsp/licenses/ALSA-LGPL-COPYING',
            'vendor/dsp/licenses/LICENSE_GLPv3.txt',
            'vendor/dsp/licenses/LICENSE_MPL2.0.txt'}


def safe(name):
    parts = Path(name).parts
    return (not Path(name).is_absolute() and '..' not in parts and
            not FORBIDDEN.intersection(parts) and
            Path(name).name != 'opencode.json' and
            (not Path(name).name.startswith('.env') or Path(name).name == '.env.example') and
            Path(name).suffix not in {'.pyc', '.pyo', '.raw', '.wav', '.key', '.pem'} and
            Path(name).name not in {'auth.json', '.DS_Store'})


def sources():
    result = {}
    for path in sorted(ROOT.rglob('*')):
        relative = path.relative_to(ROOT)
        # Never read private/unselected bytes, even while computing a digest.
        if any(p.endswith('.egg-info') for p in relative.parts) or not safe(str(relative)) or not (relative.parts[0] in TREES or
                str(relative) in TOP or (len(relative.parts) == 1 and
                (path.name.startswith('Dockerfile') or path.name.startswith('compose') and path.suffix == '.yaml'))):
            continue
        if path.is_symlink():
            raise ValueError(f'symlink not permitted in publication inputs: {relative}')
        if path.is_file():
            result[str(relative)] = hashlib.sha256(path.read_bytes()).hexdigest()
    if not REQUIRED <= result.keys():
        raise ValueError(f'missing source assets: {sorted(REQUIRED-result.keys())}')
    return result


def archive_inventory(archive):
    with tarfile.open(archive) as bundle:
        result = {}
        for member in bundle.getmembers():
            relative = '/'.join(Path(member.name).parts[1:])
            if not relative:
                continue
            if not safe(relative) or member.issym() or member.islnk():
                raise ValueError(f'unsafe packaged path: {relative}')
            if member.isfile():
                result[relative] = hashlib.sha256(bundle.extractfile(member).read()).hexdigest()
        if not REQUIRED <= result.keys():
            raise ValueError(f'missing packaged assets: {sorted(REQUIRED-result.keys())}')
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    out = args.output.resolve()
    if out == ROOT or ROOT in out.parents:
        parser.error('receipt/output must be outside repository inputs')
    out.mkdir(mode=0o700, parents=True, exist_ok=False)
    if shutil.disk_usage(out).free < 2 * 1024**3 + 128 * 1024**2:
        raise RuntimeError('need 2GiB reserve plus 128MiB packaging headroom')
    inventory = sources()
    snapshot = out/'source'
    snapshot.mkdir()
    for name in inventory:
        destination = snapshot/name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT/name, destination)
    if inventory != sources():
        raise RuntimeError('source inputs changed while snapshotting; pause owners and retry in a new receipt')
    (out/'input-inventory.json').write_text(json.dumps(inventory, indent=2)+'\n')
    revision = hashlib.sha256(json.dumps(inventory, sort_keys=True).encode()).hexdigest()
    (out/'input-digest.txt').write_text(revision+'\n')
    env = dict(os.environ, SOURCE_DATE_EPOCH='1767225600', PYTHONDONTWRITEBYTECODE='1')
    def run(argv, cwd, label):
        with (out/(label+'.stdout')).open('wb') as stdout, (out/(label+'.stderr')).open('wb') as stderr:
            completed = subprocess.run(argv, cwd=cwd, env=env, stdout=stdout, stderr=stderr)
        (out/(label+'.exit')).write_text(str(completed.returncode)+'\n')
        completed.check_returncode()
    wheels, inventories = [], []
    for number in (1, 2):
        target = out/f'build-{number}'
        target.mkdir()
        run([os.sys.executable, '-m', 'build', '--sdist', '--outdir', str(target)], snapshot, f'sdist-{number}')
        archive, = target.glob('*.tar.gz')
        inventories.append(archive_inventory(archive))
        missing = inventory.keys() - inventories[-1].keys()
        if missing:
            raise ValueError(f'snapshot inputs missing from sdist: {sorted(missing)}')
        extracted = out/f'extracted-{number}'
        extracted.mkdir()
        with tarfile.open(archive) as bundle:
            # archive_inventory already rejects traversal, links and forbidden paths.
            bundle.extractall(extracted)
        source, = extracted.iterdir()
        run([os.sys.executable, '-m', 'build', '--wheel', '--outdir', str(target)], source, f'wheel-{number}')
        wheel, = target.glob('*.whl')
        with zipfile.ZipFile(wheel) as bundle:
            names = bundle.namelist()
            if any(not safe(name) for name in names):
                raise ValueError('private/cache path in wheel')
            if not any(name.endswith('/share/garage-audio/web/index.html') for name in names):
                raise ValueError('wheel web asset missing')
            if not any(name.endswith('/LICENSE') for name in names):
                raise ValueError('wheel license missing')
        wheels.append(hashlib.sha256(wheel.read_bytes()).hexdigest())
    if inventories[0] != inventories[1] or wheels[0] != wheels[1]:
        raise ValueError('nonreproducible normalized source inventory or wheel bytes')
    report = {'input_digest':revision, 'wheel_sha256':wheels[0],
              'source_files':len(inventory), 'sdist_files':len(inventories[0]),
              'normalized_sdist_reproducible':True, 'wheel_reproducible':True,
              'live_hardware_verified':False}
    (out/'result.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report))


if __name__ == '__main__':
    main()
