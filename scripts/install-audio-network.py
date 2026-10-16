#!/usr/bin/env python3
"""Install only the frozen NETWORK helper. Does not start/enable/cut over anything."""
import argparse,hashlib,json,os,pathlib,shutil,subprocess
p=argparse.ArgumentParser();p.add_argument('release');p.add_argument('manifest_sha256');args=p.parse_args()
if os.geteuid()!=0:raise PermissionError('host operator root installation required')
root=pathlib.Path(args.release).resolve();manifest=root/'config/audio-boot-inputs.json'
if hashlib.sha256(manifest.read_bytes()).hexdigest()!=args.manifest_sha256:raise PermissionError('frozen input manifest differs')
inventory=json.loads(manifest.read_text())['files']
for name,digest in inventory.items():
 target=root/name
 if not target.resolve().is_relative_to(root) or target.is_symlink() or hashlib.sha256(target.read_bytes()).hexdigest()!=digest:
  raise PermissionError('frozen package input differs')
for binary in ('docker','ip','nsenter','nft','busybox','tcpdump'):
 if not shutil.which(binary):raise RuntimeError('required NETWORK executable absent: '+binary)
destination=pathlib.Path('/usr/local/libexec/garage-audio-network')
destination.mkdir(mode=0o755,exist_ok=True);(destination/'garage_audio').mkdir(mode=0o755,exist_ok=True)
mapping={'garage_audio/audio_boot.py':destination/'garage_audio/audio_boot.py',
 'garage_audio/audio_receiver.py':destination/'garage_audio/audio_receiver.py',
 'garage_audio/audio_network.py':destination/'garage_audio/audio_network.py',
 'scripts/network-entry.py':destination/'network-entry.py',
 'scripts/bootstrap-audio-network.py':destination/'dhcp-hook',
 'config/system/garage-audio-network.service':pathlib.Path('/etc/systemd/system/garage-audio-network.service')}
for name,target in mapping.items():
 if name not in inventory:raise PermissionError('helper input missing from inventory')
 if target.exists() or target.is_symlink():raise PermissionError('existing helper requires separately retained rollback/update transaction')
for name,target in mapping.items():
 with target.open('xb') as f:f.write((root/name).read_bytes());f.flush();os.fsync(f.fileno())
 os.chown(target,0,0);os.chmod(target,0o755 if target.name=='dhcp-hook' else 0o644)
init=destination/'garage_audio/__init__.py'
if not init.exists():init.write_text('');os.chmod(init,0o644)
subprocess.run(['systemctl','daemon-reload'],check=True)
print('Installed frozen NETWORK helper only; service remains unstarted/unenabled. Root boot configuration still required.')
