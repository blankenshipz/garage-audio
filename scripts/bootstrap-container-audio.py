#!/usr/bin/env python3
"""Narrow root udev/identity/permissions bootstrap; no playback or application services.

Default inspect-only. --apply is for the root-owned frozen-release cutover, not setup UI.
It must be run only after release gates and stopping old owners; never creates sound grants.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from garage_audio.audio_device import inventory, IDENTITY, CAPABILITIES

RULE = '''# Garage Docker candidate; exact UMC playback/control only, never capture/default DAC.
SUBSYSTEM=="sound", KERNEL=="pcmC*D0p", ATTRS{idVendor}=="1397", ATTRS{idProduct}=="0514", ATTRS{serial}=="D72E24B7", OWNER:="1002", GROUP:="1003", MODE:="0600", TAG-="uaccess"
SUBSYSTEM=="sound", KERNEL=="controlC*", ATTRS{idVendor}=="1397", ATTRS{idProduct}=="0514", ATTRS{serial}=="D72E24B7", OWNER:="1002", GROUP:="1003", MODE:="0600", TAG-="uaccess"
'''


def reconciled_legacy_rules(text):
    """Retire only the two known old native-owner rules; reject unknown host inputs."""
    prefix = 'SUBSYSTEM=="sound", KERNEL=="'
    suffix = '", ATTRS{idVendor}=="1397", ATTRS{idProduct}=="0514", ATTRS{serial}=="D72E24B7", OWNER="garage-dsp", GROUP="garage-dac", MODE="0600", TAG-="uaccess"'
    expected = {prefix + kernel + suffix for kernel in ('pcmC*D*p', 'controlC*')}
    actual = {line.strip() for line in text.splitlines() if line.strip() and not line.lstrip().startswith('#')}
    retired = '# Native UMC ownership superseded by 99-garage-audio-docker.rules.\n'
    if text == retired:
        return retired
    if actual != expected:
        raise RuntimeError('unrecognized legacy DAC rules; reconcile explicitly before cutover')
    return retired


def exclusive(nodes):
    result = subprocess.run(['fuser', *map(str, nodes)], capture_output=True, timeout=3)
    if result.returncode != 1 or result.stdout.strip():
        raise RuntimeError('DAC/control owner exists or owner scan unavailable; no cutover')


def durable(path, data, mode):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(path)+'.candidate', os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
    with os.fdopen(fd, 'wb') as out:
        out.write(data)
        out.flush()
        os.fsync(out.fileno())
    os.chmod(str(path)+'.candidate', mode)
    os.replace(str(path)+'.candidate', path)
    parent = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(parent)
    finally:
        os.close(parent)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--frozen-inventory')
    parser.add_argument('--candidate-sha256')
    parser.add_argument('--receipt')
    args = parser.parse_args()
    value = inventory(proc=Path('/proc/asound'), nodes=Path('/dev/snd'))
    if value['identity'] != IDENTITY or value['capabilities'] != CAPABILITIES:
        raise RuntimeError('exact UMC descriptors required')
    nodes = [Path('/dev/snd')/name for name in value['nodes']]
    result = {'inspect_only': not args.apply, 'identity': IDENTITY, 'capabilities': CAPABILITIES,
              'pcm_node': str(nodes[0]), 'control_node': str(nodes[1]),
              'device_verified': False, 'physical_verified': False}
    if not args.apply:
        print(json.dumps(result))
        return
    if os.geteuid() != 0 or not args.receipt or not args.frozen_inventory \
            or not re.fullmatch('[a-f0-9]{64}', args.candidate_sha256 or ''):
        raise RuntimeError('root-owned frozen candidate and private receipt required')
    frozen = Path(args.frozen_inventory)
    if hashlib.sha256(frozen.read_bytes()).hexdigest() != args.candidate_sha256:
        raise RuntimeError('frozen inventory digest mismatch')
    receipt = Path(args.receipt)
    receipt.mkdir(mode=0o700, parents=True, exist_ok=False)
    exclusive(nodes)
    paths = [Path('/etc/udev/rules.d/99-garage-audio-docker.rules'),
             Path('/etc/garage-audio/docker-device.json'),
             Path('/etc/udev/rules.d/99-garage-audio-dac.rules')]
    # Retain previous host bytes/metadata for exact quiet rollback; no accounts/state access.
    previous = {}
    for index, path in enumerate(paths):
        if path.exists():
            info = path.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
                raise RuntimeError('existing bootstrap input is not root-owned immutable regular file')
            backup = receipt/f'previous-{index}'
            backup.write_bytes(path.read_bytes())
            os.chmod(backup, 0o600)
            previous[str(path)] = {'backup': str(backup), 'mode': stat.S_IMODE(info.st_mode)}
        else:
            previous[str(path)] = None
    legacy = reconciled_legacy_rules(paths[2].read_text()) if paths[2].exists() else None
    (receipt/'before.json').write_text(json.dumps({'inventory': value, 'previous': previous,
        'candidate_sha256': args.candidate_sha256}, indent=2))
    lockdir = Path('/var/lib/garage-audio/device-lock')
    lockdir.mkdir(mode=0o755, parents=True, exist_ok=True)
    os.chown(lockdir, 0, 0)
    os.chmod(lockdir, 0o755)
    lock = lockdir/'owner.lock'
    if not lock.exists():
        fd = os.open(lock, os.O_RDONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        os.close(fd)
    if not stat.S_ISREG(lock.lstat().st_mode):
        raise RuntimeError('ownership lock must be regular, never a symlink')
    os.chown(lock, 1002, 1003)
    os.chmod(lock, 0o600)
    import fcntl
    lock_fd = os.open(lock, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        exclusive(nodes)
        for node in nodes:
            os.chown(node, 1002, 1003)
            os.chmod(node, 0o600)
        try:
            exclusive(nodes)  # eliminate an old-UID open racing the initial admission scan
            after = inventory(proc=Path('/proc/asound'), nodes=Path('/dev/snd'))
            if after['connection'] != value['connection'] or any(
                    after['nodes'][name]['inode'] != meta['inode'] for name, meta in value['nodes'].items()):
                raise RuntimeError('USB/node generation changed during bootstrap')
            policy = {'version': 1, 'identity': IDENTITY, 'capabilities': CAPABILITIES,
                      'device_uid': 1002, 'device_gid': 1003, 'outputs': [3, 4]}
            durable(paths[0], RULE.encode(), 0o644)
            durable(paths[1], json.dumps(policy).encode(), 0o444)
            if legacy is not None:
                durable(paths[2], legacy.encode(), 0o644)
            subprocess.run(['udevadm', 'control', '--reload-rules'], check=True, timeout=3)
        except BaseException:
            for name, meta in value['nodes'].items():
                node = Path('/dev/snd')/name
                if node.lstat().st_ino == meta['inode']:
                    os.chown(node, meta['uid'], meta['gid'])
                    os.chmod(node, meta['mode'])
            (receipt/'failure.json').write_text(json.dumps({'restore_nodes_attempted': True,
                'host_files_may_require_exact_quiet_rollback': True}))
            raise
    finally:
        os.close(lock_fd)
    result.update(inspect_only=False, device_verified=True,
                  candidate_sha256=args.candidate_sha256, receipt=str(receipt))
    (receipt/'result.json').write_text(json.dumps(result))
    print(json.dumps(result))


if __name__ == '__main__':
    main()
