"""Trusted bootstrap: root-private snapshot, pinned bundle, strict extraction.

Administrator must first copy this file to a root-private directory and verify
its independently delivered SHA256 BEFORE executing it. Never sudo user code.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import tarfile
import tempfile

CAM_SHA='55f5ec2ed80fcc79a543672f9f89ace4557d290d80584ef31ee0442111bd0b11'
ALSA_SHA='96e20cf5c9095ef54ba46fccffea5c01cf2de17a1012c5683a67e43504a6d726'
CODE={'garage_audio/'+name+'.py' for name in ['__init__','broker','control','daemon','hardware','ipc','producer','profile','human_authority','human_playback','playback_lease','speaker_realm','speaker_controller','ma_bridge','ma_reference','connect_port','spotify_socket_guard']}
ASSETS=CODE|{'scripts/dsp-daemon','scripts/policy-entry.py','scripts/admin-dac-rollback','scripts/human-playback','scripts/human-playback-entry.py','scripts/speaker-entry.py','scripts/prepare-spotify-connect.py','docs/spotify-only-quickstart.md','config/system/garage-audio-dsp.service','config/system/garage-speakers.service','camilla.tar.gz','libasound.deb'}

def digest(path):
    with open(path,'rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()

def trusted_path(path):
    path=Path(path)
    for item in [path,*path.parents]:
        if item.exists() or item.is_symlink():
            info=item.lstat()
            if item.is_symlink() or info.st_uid!=0 or info.st_mode&0o022:
                raise PermissionError('root install/bootstrap path is mutable or symbolic')

def snapshot(source,dest,expected):
    if not re.fullmatch('[0-9a-f]{64}',expected):raise ValueError('independent SHA256 required')
    fd=os.open(source,os.O_RDONLY|os.O_NOFOLLOW)
    try:
        info=os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size>100_000_000:raise ValueError('regular bounded release file required')
        with os.fdopen(fd,'rb',closefd=False) as src,open(dest,'xb') as target:
            shutil.copyfileobj(src,target);target.flush();os.fsync(target.fileno())
    finally:os.close(fd)
    if digest(dest)!=expected:raise ValueError('private snapshot digest mismatch')

def extract_release(archive,dest):
    with tarfile.open(archive,'r:gz') as t:
        members=t.getmembers()
        if len(members)>100:raise ValueError('too many release assets')
        names=[m.name for m in members]
        if len(names)!=len(set(names)):raise ValueError('duplicate archive member')
        allowed=lambda n:n in ASSETS|{'manifest.json'} or (n.startswith('wheels/') and re.fullmatch(r'wheels/[A-Za-z0-9_.+-]+\.whl',n))
        for m in members:
            if not m.isfile() or not allowed(m.name) or m.size>20_000_000:raise ValueError('symlink/traversal/untrusted archive asset')
            out=dest/m.name;out.parent.mkdir(parents=True,exist_ok=True)
            with t.extractfile(m) as src,out.open('xb') as target:shutil.copyfileobj(src,target)
        manifest=json.loads((dest/'manifest.json').read_text())
        if set(manifest)!=set(names)-{'manifest.json'} or not ASSETS<=set(manifest):raise ValueError('manifest asset-set mismatch')
        for name,expected in manifest.items():
            if digest(dest/name)!=expected:raise ValueError('manifest content mismatch')
        if digest(dest/'camilla.tar.gz')!=CAM_SHA:raise ValueError('official Camilla digest mismatch')
        if digest(dest/'libasound.deb')!=ALSA_SHA:raise ValueError('pinned ALSA library package digest mismatch')
        wheels=[n for n in names if n.startswith('wheels/')]
        if len(wheels)!=3:raise ValueError('exactly3 pinned official-client dependency wheels required')
        prefixes=['camilladsp-4.0.0-','pyyaml-6.0.3-','websocket_client-1.9.0-']
        if not all(sum(Path(n).name.lower().startswith(p) for n in wheels)==1 for p in prefixes):raise ValueError('unverified wheel/version')
    with tarfile.open(dest/'camilla.tar.gz','r:gz') as t:
        members=t.getmembers()
        if len(members)!=1 or members[0].name!='camilladsp' or not members[0].isfile():raise ValueError('unexpected official executable archive layout')
        with t.extractfile(members[0]) as src,(dest/'camilladsp').open('xb') as target:shutil.copyfileobj(src,target)

def run(args):
    if os.geteuid()!=0:raise PermissionError('administrator execution required')
    # The bootstrap itself must already be root-owned, nonsymlink and private.
    self_path=Path(__file__)
    trusted_path(self_path)
    info=self_path.stat()
    if info.st_uid!=0 or info.st_mode&0o022 or self_path.is_symlink():raise PermissionError('bootstrap is not trusted root-owned snapshot')
    if not re.fullmatch('[0-9a-f]{4}',args.vendor) or not re.fullmatch('[0-9a-f]{4}',args.product):raise ValueError('verified lowercase USB identifiers required')
    if not re.fullmatch('[A-Za-z0-9_.:-]+',args.serial) or not re.fullmatch('[A-Za-z0-9_]+',args.card) or not 0<=args.pcm<=15:raise ValueError('verified serial/ALSA identity required')
    if args.pcm!=0 or args.playback_channels not in (12,16,20) or args.playback_format not in ('S24_3LE','S32_LE'):
        raise ValueError('reviewed PCM0 playback capability policy required')
    os.umask(0o077)
    with tempfile.TemporaryDirectory(prefix='garage-audio-',dir='/root') as tmp:
        private=Path(tmp);archive=private/'release.tar.gz';snapshot(args.bundle,archive,args.sha256)
        assets=private/'verified';assets.mkdir();extract_release(archive,assets)
        trusted_path('/usr/local/libexec/garage-audio')
        trusted_path('/etc/garage-audio')
        for destination in ['/etc/garage-audio/identity.json','/etc/garage-audio/playback-capabilities.json','/etc/systemd/system/garage-audio-dsp.service','/etc/udev/rules.d/99-garage-audio-dac.rules']:
            trusted_path(destination)
        if Path('/usr/local/libexec/garage-audio').exists():
            for item in Path('/usr/local/libexec/garage-audio').rglob('*'):
                info=item.lstat()
                if info.st_uid!=0 or (not item.is_symlink() and info.st_mode&0o022) or (item.is_symlink() and not item.resolve().is_relative_to('/usr/local/libexec/garage-audio')):
                    raise PermissionError('existing privileged code tree is not immutable')
        os.umask(0o022) # deployment readable by DSP UID; private snapshot stays0700
        # All privileged actions below consume ONLY this immutable private copy.
        command=lambda *argv:subprocess.run(argv,check=True)
        for group in ['garage-stream','garage-dac','garage-control']:command('groupadd','-f',group)
        if subprocess.run(['id','garage-dsp'],capture_output=True).returncode:
            command('useradd','--system','--no-create-home','--home-dir','/nonexistent','--shell','/usr/sbin/nologin','--gid','garage-stream','garage-dsp')
        command('usermod','-aG','garage-control','garage-dsp');command('usermod','-aG','garage-stream,garage-control','zach')
        if 'audio' in subprocess.check_output(['id','-nG','zach'],text=True).split():raise ValueError('review existing application audio group before provisioning')
        allowed={'garage-stream','garage-control'}
        if not set(subprocess.check_output(['id','-nG','garage-dsp'],text=True).split())<=allowed:raise ValueError('unexpected DSP groups')
        target=Path('/usr/local/libexec/garage-audio');target.mkdir(parents=True,exist_ok=True)
        if subprocess.run(['systemctl','is-active','--quiet','garage-audio-dsp.service']).returncode==0:
            raise ValueError('stop the owned DSP through reviewed operator workflow before installation')
        for name in CODE:
            out=target/name;out.parent.mkdir(exist_ok=True)
            shutil.copyfile(assets/name,out);os.chmod(out,0o644)
        for source,name in [('camilladsp','camilladsp'),('scripts/dsp-daemon','dsp-daemon'),('scripts/policy-entry.py','entry.py'),('scripts/admin-dac-rollback','admin-rollback'),('scripts/human-playback','human-playback'),('scripts/human-playback-entry.py','human-entry.py'),('scripts/speaker-entry.py','speaker-entry.py')]:
            shutil.copyfile(assets/source,target/name);os.chmod(target/name,0o755 if name!='entry.py' else 0o644)
        command('/usr/bin/dpkg-deb','-x',str(assets/'libasound.deb'),str(target/'libs'))
        command('/usr/bin/python3','-m','venv','--copies',str(target/'venv'))
        command(str(target/'venv/bin/pip'),'install','--no-index','--find-links',str(assets/'wheels'),
                'camilladsp==4.0.0','PyYAML==6.0.3','websocket-client==1.9.0')
        config=Path('/etc/garage-audio');config.mkdir(exist_ok=True)
        identity={'model':'UMC1820','vendor':args.vendor,'product':args.product,'serial':args.serial,'alsa_card':args.card,'pcm':args.pcm,
                  'admin_uid':int(subprocess.check_output(['id','-u','zach'],text=True))}
        (config/'identity.json').write_text(json.dumps(identity));os.chmod(config/'identity.json',0o644)
        capabilities={'identity':{k:v for k,v in identity.items() if k!='admin_uid'},
                      'channels':args.playback_channels,'format':args.playback_format,'samplerate':48000}
        policy=config/'playback-capabilities.json'
        backup=config/'playback-capabilities.json.pre-install'
        if policy.exists() and not backup.exists():shutil.copy2(policy,backup)
        policy.write_text(json.dumps(capabilities));os.chmod(policy,0o644)
        unit=Path('/etc/systemd/system/garage-audio-dsp.service');rule=Path('/etc/udev/rules.d/99-garage-audio-dac.rules')
        for item in [unit,rule]:
            backup=config/(item.name+'.pre-install')
            if item.exists() and not backup.exists():shutil.copy2(item,backup)
        shutil.copyfile(assets/'config/system/garage-audio-dsp.service',unit);os.chmod(unit,0o644)
        phone_unit=Path('/etc/systemd/system/garage-speakers.service');trusted_path(phone_unit)
        shutil.copyfile(assets/'config/system/garage-speakers.service',phone_unit);os.chmod(phone_unit,0o644)
        # Separate admission identity must be explicitly deployed from observed
        # dashboard Unix UID. No permissive default and no inherited agent role.
        lines=[f'SUBSYSTEM=="sound", KERNEL=="{kernel}", ATTRS{{idVendor}}=="{args.vendor}", ATTRS{{idProduct}}=="{args.product}", ATTRS{{serial}}=="{args.serial}", OWNER="garage-dsp", GROUP="garage-dac", MODE="0600", TAG-="uaccess"' for kernel in ['pcmC*D*p','controlC*']]
        rule.write_text('\n'.join(lines)+'\n');os.chmod(rule,0o644)
        command('systemctl','daemon-reload');command('udevadm','control','--reload-rules')
        command('systemctl','disable','garage-audio-dsp.service')
        command('systemctl','disable','garage-speakers.service')
        print('Verified private assets installed. NOT enabled/started; no device trigger. Attended identity/ACL and namespace validation still required.')

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--bundle',required=True);p.add_argument('--sha256',required=True)
    for field in ['vendor','product','serial','card']:p.add_argument('--'+field,required=True)
    p.add_argument('--pcm',type=int,required=True)
    p.add_argument('--playback-channels',type=int,choices=(12,16,20),default=20)
    p.add_argument('--playback-format',choices=('S24_3LE','S32_LE'),default='S32_LE')
    run(p.parse_args())
