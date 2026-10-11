#!/usr/bin/env python3
"""Retain local room integration gate receipts outside package inputs.

This is local executable HTTP/MCP coverage, NOT deployed/browser/LLM acceptance.
Run from the frozen extracted source with its pinned environment before release.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--candidate',type=Path,help='Frozen receipt containing candidate.tar and inventory.json')
    parser.add_argument('--receipt-root',type=Path,default=Path('/private/tmp/opencode'))
    args = parser.parse_args()
    os.umask(0o077)
    root = Path(__file__).resolve().parent.parent
    receipt = Path(tempfile.mkdtemp(prefix='gate-room-local-',dir=args.receipt_root))
    candidate = None
    if args.candidate:
        candidate = digest(args.candidate/'candidate.tar')
        if candidate != (args.candidate/'candidate.sha256').read_text().strip():
            raise SystemExit('Candidate archive digest mismatch')
        inventory = json.loads((args.candidate/'inventory.json').read_text())
        for item in inventory:
            path = root/item['path']
            if not path.is_relative_to(root) or path.is_symlink() or digest(path) != item['sha256']:
                raise SystemExit('Frozen source mismatch; retain receipt and rebuild candidate')
    else:
        inventory = [{'path':str(p.relative_to(root)),'sha256':digest(p)}
            for folder in ('garage_audio','tests','web','docs') for p in sorted((root/folder).rglob('*'))
            if p.is_file() and not p.is_symlink() and '__pycache__' not in p.parts and p.suffix != '.pyc']
        inventory += [{'path':p,'sha256':digest(root/p)} for p in ('requirements.txt','requirements.lock','API_CONTRACT.md','API_CONTRACT_ROOM.md')]
    (receipt/'source.json').write_text(json.dumps({'candidate':candidate,'inventory':inventory},indent=2)+'\n')
    source_revision = hashlib.sha256(json.dumps(inventory,sort_keys=True).encode()).hexdigest()
    env = {**os.environ,'PYTHONPATH':str(root),'TMPDIR':str(args.receipt_root.resolve())}
    results = []
    for test in ('room_integration.py','application.py','measurement_workflow.py','measurement_mcp.py'):
        name = Path(test).stem
        started = time.time()
        with (receipt/(name+'.stdout')).open('wb') as out, (receipt/(name+'.stderr')).open('wb') as err:
            result = subprocess.run([sys.executable,str(root/'tests'/test)],cwd=root,env=env,stdout=out,stderr=err)
        (receipt/(name+'.exit')).write_text(str(result.returncode)+'\n')
        results.append({'test':test,'exit':result.returncode,'started_at':started,'finished_at':time.time()})
    unchanged = all(digest(root/i['path']) == i['sha256'] for i in inventory)
    summary = {'mode':'local executable','source_revision':source_revision,'candidate':candidate,
        'results':results,'source_unchanged':unchanged,'receipt':str(receipt),
        'not_covered':['LAN deployment','desktop/mobile browser','physical phone','live LLM/vision','hardware/sound']}
    (receipt/'result.json').write_text(json.dumps(summary,indent=2)+'\n')
    print(json.dumps(summary))
    return 0 if unchanged and all(r['exit'] == 0 for r in results) else 1


if __name__ == '__main__': sys.exit(main())
