"""Root-only fixed MA service observation. No enrollment, grant or audio startup.

Pin file provisioned by reviewed installer, never caller/container role strings.
The observer may replace only process metadata after positive immutable service
checks; authority is terminated before reload, and failures clear readiness.
"""
import hashlib,json,os,subprocess,time
from pathlib import Path
from .ma_bridge import process_reference,MAProcessPeer,load_reference,MA_UID

PIN=Path('/etc/garage-audio/ma-service.json')
REFERENCE=Path('/etc/garage-audio/ma-peer.json')
PROVIDER='/app/venv/lib/python3.14/site-packages/music_assistant/providers/garage_speaker_control'
INSTALLED='/usr/local/libexec/garage-audio/ma-provider/garage_speaker_control'
IMAGE='sha256:28023f8c0d96ca2496391f3218a6d70d7ef3ba84db846b3a3f3cd9ce7ef8aedc'

def service_pin():
    if os.geteuid()!=0:raise PermissionError('root-only service observer')
    info=PIN.lstat()
    if not PIN.is_file() or PIN.is_symlink() or info.st_uid!=0 or info.st_mode&0o077:raise PermissionError('private immutable root service pin required')
    pin=json.loads(PIN.read_text())
    if set(pin)!={'image','exe','exe_sha256','argv_sha256','assets'} or pin['image']!=IMAGE or pin['exe']!='/usr/local/bin/python3.14' or set(pin['assets'])!={'__init__.py','client.py','manifest.json','connect_authority.py','connect_adapter.py','connect_runtime.py'}:raise PermissionError('exact approved MA service pin required')
    return pin

def verify_description(value,pin,expected=None):
    # expected is an in-process fixture dependency, NEVER read from IPC, container,
    # environment or the service pin. Production call omits it, using fixed scope.
    expected=expected or {'name':'garage-audio-music-assistant-1','network':'host','project':'garage-audio','directory':'/home/zach/garage-audio','provider':INSTALLED,'socket':'/run/garage-speakers/ma','data':'/home/zach/garage-audio/state/ma'}
    labels=value['Config']['Labels'];state=value['State']
    if value['Name']!='/'+expected['name'] or value['Image']!=pin['image'] or not state['Running'] or value['HostConfig']['NetworkMode']!=expected['network'] or value['Config'].get('User','')!='1000:1000':raise PermissionError('approved MA container identity required')
    if any(labels.get(k)!=v for k,v in {'com.docker.compose.project':expected['project'],'com.docker.compose.service':'music-assistant','com.docker.compose.container-number':'1','com.docker.compose.project.working_dir':expected['directory']}.items()):raise PermissionError('approved MA project/service required')
    mounts={m['Destination']:m for m in value['Mounts']}
    required={PROVIDER:(expected['provider'],False),'/run/garage-speakers-ma':(expected['socket'],False),'/data':(expected['data'],True)}
    for destination,(source,writable) in required.items():
        m=mounts.get(destination)
        if not m or m['Type']!='bind' or m['Source']!=source or m['RW'] is not writable:raise PermissionError('approved MA mount required')
    process=process_reference(state['Pid'])
    if process['uid']!=MA_UID or any(process[k]!=pin[k] for k in ('exe','exe_sha256','argv_sha256')):raise PermissionError('approved MA executable/argv required')
    ref={'process':process,'provider_path':PROVIDER,'assets':pin['assets']}
    peer=object.__new__(MAProcessPeer);peer.pid=process['pid'];peer.reference=ref;peer.verify()
    return ref

def observe_service(pin):
    raw=subprocess.check_output(['/usr/bin/docker','inspect','garage-audio-music-assistant-1'],timeout=3,stderr=subprocess.DEVNULL)
    items=json.loads(raw)
    if len(items)!=1:raise PermissionError('one MA service required')
    return verify_description(items[0],pin)

def publish(reference,path=REFERENCE):
    if os.geteuid()!=0:raise PermissionError('root-only reference publication')
    temp=path.with_name(path.name+'.new')
    fd=os.open(temp,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    try:
        with os.fdopen(fd,'w') as target:
            json.dump(reference,target,sort_keys=True);target.flush();os.fsync(target.fileno())
        os.replace(temp,path)
    finally:temp.unlink(missing_ok=True)

def reconcile(controller,gateway,candidate,publisher=publish):
    if os.geteuid()!=0:raise PermissionError('root-only reference reconciliation')
    if candidate!=gateway.reference:
        controller.ma_available=False;controller.normal_available=False
        controller.stop()
        worker=controller.worker
        if worker and worker.is_alive():worker.join(timeout=5)
        with controller.lock:
            if controller.state!='stopped' or (worker and worker.is_alive()):raise PermissionError('old native authority termination unverified')
            if controller.ma_owner:controller.ma_owner.closed=True
            controller.ma_owner=None
            publisher(candidate);gateway.reference=candidate
    controller.ma_available=True;controller.normal_available=True

def invalidate(controller):
    controller.ma_available=False;controller.normal_available=False
    controller.stop()

def monitor(controller,gateway):
    while True:
        try:
            candidate=observe_service(service_pin())
            reconcile(controller,gateway,candidate)
        except Exception:
            try:invalidate(controller)
            except Exception:pass
        time.sleep(1)
