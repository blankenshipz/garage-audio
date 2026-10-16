"""Hold app entry until the root operator installed the current private route."""
import json,os,stat,time
from pathlib import Path

def network_valid(path,generation):
    path=Path(path)
    try:
        info=path.lstat()
        if (path.parent/'revoked.json').exists() or not stat.S_ISREG(info.st_mode) or info.st_uid!=0 or info.st_mode&0o022 or info.st_size>4096:return False
        value=json.loads(path.read_text());now=time.monotonic()
        return value['generation']==generation and value['healthy'] is True and 0<=now-value['tick']<=2 and now<value['expires']
    except (OSError,ValueError,KeyError,TypeError):return False

def wait():
    path=Path(os.environ['GARAGE_AUDIO_NETWORK_LEASE']);generation=os.environ['GARAGE_AUDIO_NETWORK_GENERATION']
    ready=Path('/network/app-ready.json');deadline=time.monotonic()+30
    while time.monotonic()<deadline:
        if not network_valid(path,generation):raise PermissionError('network generation unavailable')
        if ready.exists():
            info=ready.lstat();value=json.loads(ready.read_text())
            if info.st_uid!=0 or info.st_mode&0o022 or value!={'generation':generation,'route_ready':True}:raise PermissionError('exact immutable route readiness required')
            os.execvp('python',['python','-m','garage_audio.webserver'])
        time.sleep(.1)
    raise TimeoutError('private route was not installed')

if __name__=='__main__':wait()
