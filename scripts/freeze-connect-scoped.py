"""Host-side dry packaging of an explicit delta against installed baseline.

No installation, services, settings/auth reads, arm or sound. Input archive is
reviewed nonsensitive source; outputs/pins/diff are external private artifacts.
"""
import argparse,difflib,hashlib,io,json,os,shutil,subprocess,tarfile,tempfile
from pathlib import Path

ROOT=Path('/usr/local/libexec/garage-audio');HOST=Path('/home/zach/garage-audio')
CODE=('broker','playback_lease','speaker_controller','ma_bridge','ma_reference','connect_port','spotify_socket_guard')
PROVIDER=('__init__.py','client.py','manifest.json','connect_authority.py','connect_adapter.py','connect_runtime.py')
def sha(raw):return hashlib.sha256(raw).hexdigest()
def inventory():return {str(p.relative_to(ROOT)):sha(p.read_bytes()) for p in ROOT.rglob('*') if p.is_file() and '__pycache__' not in p.parts and p.suffix!='.pyc'}
def main():
    parser=argparse.ArgumentParser();parser.add_argument('--archive',type=Path,required=True);parser.add_argument('--sha256',required=True);parser.add_argument('--prior-candidate',type=Path);args=parser.parse_args()
    assert os.geteuid()==0 and sha(args.archive.read_bytes())==args.sha256
    os.umask(0o077);output=Path(tempfile.mkdtemp(prefix='garage-connect-final-r20.',dir='/root'))
    assert shutil.disk_usage(output).free>2*1024**3,'2GiB reserve for ~650MiB deployment backup/image growth'
    mapping={f'garage_audio/{name}.py':f'garage_audio/{name}.py' for name in CODE}
    mapping.update({'ma_provider/garage_speaker_control/'+name:'ma-provider/garage_speaker_control/'+name for name in PROVIDER})
    mapping.update({'config/system/garage-speakers.service':'config/system/garage-speakers.service','scripts/prepare-spotify-connect.py':'prepare-spotify-connect.py','docs/spotify-only-quickstart.md':'docs/spotify-only-quickstart.md'})
    required=set(mapping)|{'web/speakers.js','web/index.html','scripts/deploy-connect-muted.py'}
    with tarfile.open(args.archive) as archive:
        assert set(archive.getnames())==required,'explicit scoped input inventory required'
        contents={}
        for name in sorted(required):
            member=archive.getmember(name);assert member.isfile() and member.size<1024*1024
            contents[name]=archive.extractfile(member).read()
    before=inventory();after=dict(before)
    for name,target in mapping.items():after[target]=sha(contents[name])
    service=json.loads(Path('/etc/garage-audio/ma-service.json').read_text())
    assert service['image']=='sha256:28023f8c0d96ca2496391f3218a6d70d7ef3ba84db846b3a3f3cd9ce7ef8aedc'
    service['assets']={name:sha(contents['ma_provider/garage_speaker_control/'+name]) for name in PROVIDER}
    contents['install/ma-service.json']=(json.dumps(service,sort_keys=True,indent=2)+'\n').encode()
    binary=HOST/'state/ma/soloist/soloist';digest=sha(binary.read_bytes())
    assert digest=='4ced7515a932e19473ae5296192d5953f110318ac403926c782c33193b89b3f1','Soloist drift'
    contents['install/soloist-sha256']=(digest+'\n').encode()
    dashboard=json.loads(subprocess.check_output(['docker','inspect','garage-audio-dashboard']))[0]
    image=dashboard['Image'];dashboard_compose=Path(dashboard['Config']['Labels']['com.docker.compose.project.config_files'].split(',')[0])
    assert dashboard_compose.is_relative_to(HOST/'dashboard-releases') and dashboard_compose.name=='compose.dashboard.yaml'
    base_tag='garage-audio-dashboard:connect-base-'+image.removeprefix('sha256:')[:12]
    contents['Dockerfile']=f'FROM {base_tag}\nCOPY --chmod=0644 web/speakers.js web/index.html /app/web/\n'.encode()
    # External immutable gate inventory includes unchanged installed inputs. No
    # whole dirty workspace rebuild; preserve all unrelated installed files.
    manifest={'intentRevision':20,'inputs':{n:sha(v) for n,v in contents.items()},'native_mapping':mapping,'before_native':before,'after_native':after,'before_native_sha256':sha(json.dumps(before,sort_keys=True).encode()),'after_native_sha256':sha(json.dumps(after,sort_keys=True).encode()),'compose_sha256':sha((HOST/'compose.yaml').read_bytes()),'system_unit_before_sha256':sha(Path('/etc/systemd/system/garage-speakers.service').read_bytes()),'service_pin_before_sha256':sha(Path('/etc/garage-audio/ma-service.json').read_bytes()),'dashboard_base_image':image,'MA_image':service['image'],'soloist_sha256':digest,'sound_armed':False}
    archive_path=output/'connect-scoped-overlay.tar'
    with tarfile.open(archive_path,'w') as archive:
        for name,raw in sorted(contents.items()):
            member=tarfile.TarInfo(name);member.size=len(raw);member.mode=0o644;archive.addfile(member,io.BytesIO(raw))
    manifest['overlay_sha256']=sha(archive_path.read_bytes())
    manifest.update(dashboard_compose=str(dashboard_compose),dashboard_compose_sha256=sha(dashboard_compose.read_bytes()),dashboard_base_tag=base_tag)
    (output/'frozen-candidate.json').write_text(json.dumps(manifest,sort_keys=True,indent=2)+'\n')
    diff=[]
    for name,target in sorted(mapping.items()):
        path=ROOT/target;old=path.read_text() if path.is_file() else ''
        diff.extend(difflib.unified_diff(old.splitlines(True),contents[name].decode().splitlines(True),fromfile='installed/'+target,tofile='candidate/'+target))
    old=Path('/etc/systemd/system/garage-speakers.service').read_text()
    diff.extend(difflib.unified_diff(old.splitlines(True),contents['config/system/garage-speakers.service'].decode().splitlines(True),fromfile='installed/systemd/garage-speakers.service',tofile='candidate/systemd/garage-speakers.service'))
    for name in ('web/speakers.js','web/index.html'):
        if args.prior_candidate:
            prior=json.loads((args.prior_candidate/'frozen-candidate.json').read_text())
            labels=json.loads(subprocess.check_output(['docker','image','inspect',image]))[0]['Config']['Labels']
            assert labels['garage.connect.overlay']==prior['overlay_sha256']
            prior_archive=args.prior_candidate/'connect-scoped-overlay.tar';assert sha(prior_archive.read_bytes())==prior['overlay_sha256']
            with tarfile.open(prior_archive) as t:raw=t.extractfile(name).read()
            assert sha(raw)==prior['inputs'][name];old=raw.decode() # reused immutable source evidence, NOT a live denied file read
        else:old=subprocess.check_output(['docker','exec','garage-audio-dashboard','cat','/app/'+name],text=True)
        diff.extend(difflib.unified_diff(old.splitlines(True),contents[name].decode().splitlines(True),fromfile='installed/dashboard/'+name,tofile='candidate/dashboard/'+name))
    old=Path('/etc/garage-audio/ma-service.json').read_text()
    diff.extend(difflib.unified_diff(old.splitlines(True),contents['install/ma-service.json'].decode().splitlines(True),fromfile='installed/ma-service.json',tofile='candidate/ma-service.json'))
    diff.extend(difflib.unified_diff([],contents['scripts/deploy-connect-muted.py'].decode().splitlines(True),fromfile='/dev/null',tofile='candidate/deploy-connect-muted.py'))
    (output/'focused-critical.diff').write_text(''.join(diff))
    (output/'deploy-connect-muted.py').write_bytes(contents['scripts/deploy-connect-muted.py'])
    for name in ('web/speakers.js','web/index.html','Dockerfile'):
        path=output/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(contents[name])
    print(json.dumps({'candidate_directory':str(output),'overlay_sha256':manifest['overlay_sha256'],'native_before':manifest['before_native_sha256'],'native_after':manifest['after_native_sha256'],'files':len(contents),'sound_armed':False,'installed':False}))

if __name__=='__main__':main()
