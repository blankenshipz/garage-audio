"""Freeze nonsensitive review inputs, NOT production installation/sound approval.

Live Compose, MA settings/auth/data, private receipts, caches and hardware binding
are deliberately absent. Existing target-owned native dependencies are separately
pinned. Root must review and close integration gaps before any deployment gate.
"""
import argparse,hashlib,io,json,tarfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
PROVIDER_FILES=('__init__.py','manifest.json','client.py','connect_authority.py','connect_adapter.py','connect_runtime.py')
def freeze(output):
    output.mkdir(mode=0o700,parents=True,exist_ok=False)
    paths=set(ROOT.glob('garage_audio/*.py'))|set(ROOT.glob('web/*'))
    paths={p for p in paths if p.is_file()}
    for pattern in ('config/system/*.service','docs/*.md'):
        paths.update(ROOT.glob(pattern))
    names=('scripts/build-admin-bundle.py','scripts/provision.py','scripts/prepare-spotify-connect.py','scripts/freeze-spotify-review.py','scripts/speaker-entry.py','scripts/policy-entry.py','scripts/dsp-daemon','scripts/human-playback','scripts/human-playback-entry.py','scripts/admin-dac-rollback')
    names+=tuple('ma_provider/garage_speaker_control/'+p for p in PROVIDER_FILES)
    names+=tuple('tests/'+p for p in ('test_connect_adapter.py','test_connect_authority.py','test_connect_lease.py','test_connect_runtime.py','connect_native_file.py','connect_daemon_observation.py','connect_private_volume.py','connect_status_browser.cjs','spotify_socket_guard.py','spotify_socket_soloist.py','normal_transport_file.py','normal_ma_worker.py','native_playback.py','ma_bridge_file.py'))
    paths.update(ROOT/p for p in names)
    contents={}
    for p in sorted(paths):
        if not p.is_file() or p.is_symlink():raise PermissionError('regular explicit package input required: '+str(p.relative_to(ROOT)))
        contents[str(p.relative_to(ROOT))]=p.read_bytes()
    inventory={n:hashlib.sha256(raw).hexdigest() for n,raw in contents.items()}
    revision=hashlib.sha256(json.dumps(inventory,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    service={'image':'sha256:28023f8c0d96ca2496391f3218a6d70d7ef3ba84db846b3a3f3cd9ce7ef8aedc','exe':'/usr/local/bin/python3.14','exe_sha256':'30a7def0b3e8d24a36d3829202717542bef124c0a40608b2232b7c837b271bc9','argv_sha256':'fa6071aa099d403f5f9fbef1d14ac4c94282e682326755b7579765e067053a4d','assets':{p:inventory['ma_provider/garage_speaker_control/'+p] for p in PROVIDER_FILES}}
    (output/'ma-service-candidate.json').write_text(json.dumps(service,sort_keys=True,indent=2)+'\n')
    metadata={'intentRevision':20,'inputRevision':revision,'inventory':inventory,'soundArmed':False,'deployableGatePassed':False,'liveComposeExcluded':True,'soloistSha256':'4ced7515a932e19473ae5296192d5953f110318ac403926c782c33193b89b3f1','externalNativeDependencies':{'camilla.tar.gz':'55f5ec2ed80fcc79a543672f9f89ace4557d290d80584ef31ee0442111bd0b11','libasound.deb':'96e20cf5c9095ef54ba46fccffea5c01cf2de17a1012c5683a67e43504a6d726'}}
    (output/'input-inventory.json').write_text(json.dumps(metadata,sort_keys=True,indent=2)+'\n')
    archive=output/'review-candidate.tar.gz'
    with tarfile.open(archive,'w:gz') as target:
        for name,raw in contents.items():
            info=tarfile.TarInfo(name);info.mode=0o644;info.size=len(raw);target.addfile(info,io.BytesIO(raw))
    digest=hashlib.sha256(archive.read_bytes()).hexdigest()
    (output/'candidate-sha256').write_text(digest+'\n')
    print(json.dumps({'output':str(output),'inputRevision':revision,'archiveSha256':digest,'files':len(inventory),'deploymentGatePassed':False,'soundArmed':False}))

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,required=True);freeze(parser.parse_args().output)
