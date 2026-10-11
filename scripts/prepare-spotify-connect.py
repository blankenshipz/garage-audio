"""Reviewed root-only, stopped-MA configuration migration; no login/play/arm.

Run only after candidate review. Stores a private byte-for-byte settings backup;
changes ONLY official volume_mode. Does not write the sound-enablement marker.
"""
import copy,json,os,stat,subprocess,tempfile
from pathlib import Path

TARGET='02:00:00:00:21:01'
def migrate(settings):
    value=copy.deepcopy(settings)
    providers=value['providers'];rows=providers.values() if isinstance(providers,dict) else providers
    matches=[p for p in rows if p.get('domain')=='spotify_connect' and p.get('enabled') is True]
    if len(matches)!=1:raise PermissionError('one enabled official Connect provider required')
    provider=matches[0];values=provider['values']
    if values.get('connected_players')!=[TARGET]:raise PermissionError('fixed Garage Speakers receiver target required')
    if values.get('volume_mode') not in ('player_only','sync_spotify'):raise PermissionError('reviewed supported volume migration required')
    values['volume_mode']='sync_spotify'
    return value

def main():
    import argparse,hashlib
    parser=argparse.ArgumentParser();parser.add_argument('--soloist-sha256',required=True);args=parser.parse_args()
    if os.geteuid()!=0:raise PermissionError('root administration required')
    description=json.loads(subprocess.check_output(['docker','inspect','garage-audio-music-assistant-1']))[0]
    if description['State']['Running']:raise PermissionError('supported MA-only stop required before settings-owner update')
    if description['Config']['User']!='1000:1000' or description['HostConfig']['NetworkMode']!='host':raise PermissionError('exact existing MA service boundary required')
    digest=hashlib.sha256(Path('/home/zach/garage-audio/state/ma/soloist/soloist').read_bytes()).hexdigest()
    if args.soloist_sha256!=digest:raise PermissionError('binary differs from reviewed candidate digest')
    settings=Path('/home/zach/garage-audio/state/ma/settings.json')
    info=settings.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid!=1000 or info.st_size>2*1024*1024:raise PermissionError('exact bounded MA settings required')
    raw=settings.read_bytes();value=migrate(json.loads(raw))
    backup=Path(tempfile.mkdtemp(prefix='garage-connect-config-r20.',dir='/root'))
    (backup/'settings.json').write_bytes(raw);(backup/'settings.json').chmod(0o600)
    assert (backup/'settings.json').read_bytes()==raw
    fd,name=tempfile.mkstemp(prefix='.garage-connect-',dir=settings.parent)
    try:
        os.fchmod(fd,stat.S_IMODE(info.st_mode));os.fchown(fd,info.st_uid,info.st_gid)
        with os.fdopen(fd,'w') as handle:
            json.dump(value,handle);handle.flush();os.fsync(handle.fileno())
        os.replace(name,settings)
    finally:
        Path(name).unlink(missing_ok=True)
    # The binary's version/hash is nonsensitive; keep its approval root-private.
    pin=Path('/etc/garage-audio/soloist-sha256')
    fd=os.open(pin,os.O_WRONLY|os.O_CREAT|os.O_TRUNC|os.O_NOFOLLOW,0o600)
    os.fchmod(fd,0o600);os.fchown(fd,0,0)
    with os.fdopen(fd,'w') as handle:handle.write(digest+'\n');handle.flush();os.fsync(handle.fileno())
    print(json.dumps({'backup_reference':str(backup),'volume_mode':'sync_spotify','sound_armed':False,'login_or_play':False}))

if __name__=='__main__':main()
