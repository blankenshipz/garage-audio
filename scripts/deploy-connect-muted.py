"""Root-reviewed exact frozen Connect delta. NEVER arm/select/Play/open DAC.

Run with explicit candidate manifest and overlay digest after critical review.
Preserve settings/auth/queues, live Compose and dashboard mounts/ports. Leave
root sound marker absent until parent's pending physical-Fosi confirmation.
"""
import argparse,hashlib,json,os,shutil,subprocess,tarfile,tempfile,time
from pathlib import Path

ROOT=Path('/usr/local/libexec/garage-audio');HOST=Path('/home/zach/garage-audio')
def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def inventory():return {str(p.relative_to(ROOT)):sha(p) for p in ROOT.rglob('*') if p.is_file() and '__pycache__' not in p.parts and p.suffix!='.pyc'}
def main():
    parser=argparse.ArgumentParser();parser.add_argument('--candidate',type=Path,required=True);parser.add_argument('--sha256',required=True);parser.add_argument('--dashboard-image',required=True);args=parser.parse_args()
    assert os.geteuid()==0
    directory=args.candidate;manifest=json.loads((directory/'frozen-candidate.json').read_text());archive=directory/'connect-scoped-overlay.tar'
    assert args.sha256==manifest['overlay_sha256']==sha(archive)
    assert inventory()==manifest['before_native'],'installed baseline drift'
    assert sha(HOST/'compose.yaml')==manifest['compose_sha256']
    assert sha(Path('/etc/systemd/system/garage-speakers.service'))==manifest['system_unit_before_sha256']
    assert sha(Path('/etc/garage-audio/ma-service.json'))==manifest['service_pin_before_sha256']
    assert not Path('/etc/garage-audio/spotify-connect-enabled').exists(),'do not silently overwrite sound authority'
    assert shutil.disk_usage('/root').free>2*1024**3,'reserve for private backup and image growth'
    os.umask(0o077);backup=Path(tempfile.mkdtemp(prefix='garage-connect-deploy-r20.',dir='/root'));logs=backup/'logs';logs.mkdir();step=0
    def run(argv,timeout=120):
        nonlocal step
        step+=1
        with (logs/f'{step:03}.stdout').open('xb') as out,(logs/f'{step:03}.stderr').open('xb') as err:
            result=subprocess.run(argv,stdout=out,stderr=err,timeout=timeout,env={**os.environ,'PYTHONPATH':str(ROOT),'DASHBOARD_IMAGE':args.dashboard_image})
        (logs/f'{step:03}.exit').write_text(str(result.returncode));result.check_returncode();return (logs/f'{step:03}.stdout').read_bytes()
    def inspect(name):return json.loads(subprocess.check_output(['docker','inspect',name],timeout=10,stderr=subprocess.DEVNULL))[0] # never log environment/credentials
    ma=inspect('garage-audio-music-assistant-1');dashboard=inspect('garage-audio-dashboard')
    assert ma['Image']==manifest['MA_image'] and ma['Config']['User']=='1000:1000' and ma['HostConfig']['NetworkMode']=='host'
    assert dashboard['Image']==manifest['dashboard_base_image']
    dashboard_compose=Path(manifest['dashboard_compose']);assert sha(dashboard_compose)==manifest['dashboard_compose_sha256']
    assert args.dashboard_image.startswith('sha256:') and len(args.dashboard_image)==71
    candidate_image=json.loads(subprocess.check_output(['docker','image','inspect',args.dashboard_image],timeout=10))[0]
    assert candidate_image['Config']['Labels']['garage.connect.overlay']==manifest['overlay_sha256']
    shutil.copytree(ROOT,backup/'native',symlinks=True);shutil.copytree('/etc/garage-audio',backup/'config',symlinks=True)
    shutil.copy2('/etc/systemd/system/garage-speakers.service',backup/'garage-speakers.service');shutil.copy2(HOST/'compose.yaml',backup/'compose.yaml')
    run([str(ROOT/'venv/bin/python'),'-c','from garage_audio.speaker_controller import NativeChain; NativeChain().stop()'])
    run(['systemctl','stop','garage-speakers.service'])
    assert subprocess.run(['systemctl','is-active','garage-audio-dsp.service'],capture_output=True).returncode!=0
    run(['docker','compose','-p','garage-audio','-f',str(HOST/'compose.yaml'),'stop','music-assistant'])
    assert not inspect('garage-audio-music-assistant-1')['State']['Running']
    shutil.copytree(HOST/'state/ma',backup/'ma-data',symlinks=True)
    with tarfile.open(archive) as source:
        assert set(source.getnames())==set(manifest['inputs'])
        for name,expected in manifest['inputs'].items():
            member=source.getmember(name);assert member.isfile();raw=source.extractfile(member).read();assert hashlib.sha256(raw).hexdigest()==expected
            targets=[]
            if name in manifest['native_mapping']:targets.append(ROOT/manifest['native_mapping'][name])
            if name=='config/system/garage-speakers.service':targets.append(Path('/etc/systemd/system/garage-speakers.service'))
            if name.startswith('install/'):targets.append(Path('/etc/garage-audio')/Path(name).name)
            for target in targets:
                target.parent.mkdir(parents=True,exist_ok=True)
                temporary=target.with_name(target.name+'.connect-r20-new');fd=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
                with os.fdopen(fd,'wb') as handle:handle.write(raw);handle.flush();os.fsync(handle.fileno())
                os.chown(temporary,0,0);os.chmod(temporary,0o600 if name.startswith('install/') else 0o644);os.replace(temporary,target)
    assert inventory()==manifest['after_native']
    run([str(ROOT/'venv/bin/python'),str(ROOT/'prepare-spotify-connect.py'),'--soloist-sha256',manifest['soloist_sha256']])
    run(['systemctl','daemon-reload'])
    run(['docker','compose','-p','garage-audio','-f',str(HOST/'compose.yaml'),'up','-d','--no-deps','music-assistant'])
    run([str(ROOT/'venv/bin/python'),'-c','from garage_audio.ma_reference import service_pin,observe_service,publish; publish(observe_service(service_pin()))'])
    run(['systemctl','start','garage-speakers.service']);run(['systemctl','is-active','garage-speakers.service'])
    override=directory/'compose.dashboard.connect.json'
    override.write_text(json.dumps({'services':{'dashboard':{'image':args.dashboard_image}}}))
    run(['docker','compose','-p','garage-audio-dashboard','-f',str(dashboard_compose),'-f',str(override),'up','-d','--no-deps','dashboard'])
    web_now=inspect('garage-audio-dashboard')
    assert web_now['Image']==args.dashboard_image and web_now['Mounts']==dashboard['Mounts']
    assert web_now['HostConfig']['PortBindings']==dashboard['HostConfig']['PortBindings'] and web_now['Config']['Env']==dashboard['Config']['Env']
    now=inspect('garage-audio-music-assistant-1');assert now['Image']==ma['Image'] and now['Mounts']==ma['Mounts']
    assert sha(HOST/'compose.yaml')==manifest['compose_sha256'],'live Compose must remain unchanged'
    assert not Path('/etc/garage-audio/spotify-connect-enabled').exists()
    assert subprocess.run(['systemctl','is-active','garage-audio-dsp.service'],capture_output=True).returncode!=0
    (backup/'candidate.json').write_text(json.dumps(manifest,sort_keys=True,indent=2))
    print(json.dumps({'rollback_reference':str(backup),'installed_native_sha256':manifest['after_native_sha256'],'sound_armed':False,'dashboard_image':args.dashboard_image,'login_play_device_open':False}))

if __name__=='__main__':main()
