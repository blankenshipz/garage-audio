"""Generate vendor assets from approved immutable public patch and cached official bytes.

Run by release operator only; no inference, network, credentials or upstream checkout edits.
"""
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import re


def compile_positions(base, lines):
    # Upstream dev line numbers differ from stable. Compile deterministic stable positions
    # from UNIQUE complete exact old-context blocks, then pin both bytes and positions.
    # No missing context, whitespace tolerance, approximate matching or runtime offset search.
    blocks=[]
    for line in lines:
        if line.startswith('@@ '):
            blocks.append([])
        elif blocks and line[:1] in (' ', '-'):
            blocks[-1].append(line[1:])
    source=base.decode().splitlines(keepends=True)
    positions=[];delta=0
    headers=[re.match(r'@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@', x)
             for x in lines if x.startswith('@@ ')]
    for old, header in zip(blocks,headers):
        matches=[i for i in range(len(source)-len(old)+1) if source[i:i+len(old)]==old]
        if len(matches)!=1:
            raise ValueError('complete exact hunk context is not unique')
        positions.append({'old':matches[0],'new':matches[0]+delta})
        delta+=int(header[4] or 1)-int(header[2] or 1)
    return positions

SHA = '2c3530213db3fa11d84ed7240bed5c5ca4e39ea579e52e28004cc5408384d252'
COMMIT = 'e531b4ffcb9ebabd94e41b5611b816424fe03723'
BASE = 'ghcr.io/music-assistant/server@sha256:28023f8c0d96ca2496391f3218a6d70d7ef3ba84db846b3a3f3cd9ce7ef8aedc'


def main():
    base, approved_patch, destination = map(Path, sys.argv[1:])
    spec = importlib.util.spec_from_file_location('apply_patch', Path(__file__).with_name('apply-ma-volume-patch.py'))
    module = importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    data = approved_patch.read_bytes()
    if hashlib.sha256(data).hexdigest() != SHA:
        raise ValueError('approved patch bytes changed')
    parts = module.sections(data)
    if len(parts) != 8:
        raise ValueError('eight public upstream files required')
    hashes = {}
    for name in parts:
        if name.startswith('tests/'):
            continue
        relative = name.removeprefix('music_assistant/')
        path = base/('spotify_connect/'+relative.removeprefix('providers/spotify_connect/')
                     if relative.startswith('providers/spotify_connect/') else 'en.json')
        content = path.read_bytes()
        try:
            positions=compile_positions(content, parts[name])
            result = module.apply_exact(content, parts[name], positions)
        except ValueError as error:
            raise ValueError(name+': '+str(error)) from error
        hashes[name] = {'base': module.digest(content), 'result': module.digest(result), 'hunk_positions':positions}
    if len(hashes) != 6:
        raise ValueError('six runtime files required')
    manifest = {'base_image': BASE, 'upstream_commit': COMMIT, 'patch_sha256': SHA,
                'public_diff_files': sorted(parts), 'runtime_files': hashes,
                'mode': 'volume_mode=spotify_authoritative', 'temporary_pending_release': True,
                'pr_url': None, 'runtime_qualified': False}
    destination.mkdir(parents=True, exist_ok=False)
    (destination/'spotify-authoritative.patch').write_bytes(data)
    (destination/'manifest.json').write_text(json.dumps(manifest, sort_keys=True, indent=2)+'\n')
    print(json.dumps(manifest, sort_keys=True))


if __name__ == '__main__':
    main()
