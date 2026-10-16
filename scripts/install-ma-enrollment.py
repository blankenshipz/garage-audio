#!/usr/bin/env python3
"""Root-only frozen bootstrap installation; never starts/enables/admit/enrolls."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat

FILES={'garage_audio/'+n for n in ('__init__.py','private_records.py','ma_enrollment.py',
    'ma_enrollment_installer.py','ma_enrollment_admission.py')}
UNIT='config/system/garage-ma-enrollment.service'
TMPFILES='config/system/garage-ma-enrollment.tmpfiles'

def directory(path,mode,*,create=False,gid=0):
    fd=os.open('/',os.O_RDONLY|os.O_DIRECTORY)
    try:
        parts=Path(path).parts[1:]
        for index,part in enumerate(parts):
            if part in ('.','..'):raise PermissionError('unsafe directory')
            final=index==len(parts)-1;created=False
            if create:
                try:os.mkdir(part,mode if final else 0o755,dir_fd=fd);created=True
                except FileExistsError:pass
            child=os.open(part,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=fd)
            info=os.fstat(child)
            if created:
                os.fchown(child,0,gid if final else 0)
                os.fchmod(child,mode if final else 0o755);os.fsync(child);os.fsync(fd)
                info=os.fstat(child)
            if info.st_uid not in (0,1000) or info.st_mode&0o022:
                os.close(child);raise PermissionError('unsafe ancestor')
            if final and (info.st_uid!=0 or info.st_gid!=gid or stat.S_IMODE(info.st_mode)!=mode):
                os.close(child);raise PermissionError('existing directory mismatch; no automatic repair')
            os.close(fd);fd=child
        return fd
    except BaseException:os.close(fd);raise

def read_root(path,private=False):
    # Source inputs must be root-immutable all the way to /, not merely chowned files.
    fd=os.open('/',os.O_RDONLY|os.O_DIRECTORY)
    try:
        for part in path.parts[1:-1]:
            child=os.open(part,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=fd)
            info=os.fstat(child)
            if info.st_uid!=0 or info.st_mode&0o022:os.close(child);raise PermissionError('source ancestor not immutable')
            os.close(fd);fd=child
        handle=os.open(path.name,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=fd)
        with os.fdopen(handle,'rb') as stream:
            info=os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_uid!=0 or info.st_nlink!=1 or info.st_mode&0o022:
                raise PermissionError('source not immutable regular file')
            if private and stat.S_IMODE(info.st_mode)!=0o600:raise PermissionError('inventory must be root0600')
            data=stream.read(131073)
            if len(data)>131072:raise PermissionError('bootstrap input exceeds bound')
            return data
    finally:os.close(fd)

def write_new(fd,name,data,mode=0o444):
    handle=os.open(name,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,mode,dir_fd=fd)
    with os.fdopen(handle,'wb') as stream:
        os.fchmod(stream.fileno(),mode);stream.write(data);stream.flush();os.fsync(stream.fileno())
    os.fsync(fd)

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--release',type=Path,required=True)
    p.add_argument('--inventory',type=Path,required=True)
    p.add_argument('--expected-digest',required=True)
    args=p.parse_args()
    if os.geteuid()!=0:raise PermissionError('root installation required')
    os.umask(0o077)
    if not args.release.is_absolute() or not args.inventory.is_absolute():raise PermissionError('absolute root-owned inputs required')
    raw=read_root(args.inventory,private=True);digest=hashlib.sha256(raw).hexdigest()
    if not re.fullmatch('[0-9a-f]{64}',args.expected_digest) or digest!=args.expected_digest:
        raise PermissionError('bootstrap inventory digest mismatch')
    inventory=json.loads(raw)
    if set(inventory)!=FILES|{UNIT,TMPFILES}:raise PermissionError('exact stdlib bootstrap input set required')
    data={n:read_root(args.release/n) for n in inventory}
    for name,value in data.items():
        if hashlib.sha256(value).hexdigest()!=inventory[name]:raise PermissionError('bootstrap input hash mismatch')
    base=Path('/usr/local/libexec/garage-ma-enrollment')/digest
    if base.exists() or base.is_symlink():raise PermissionError('retain existing bootstrap; no overwrite/retry')
    if Path('/etc/systemd/system/garage-ma-enrollment.service').exists():raise PermissionError('existing service requires explicit retained update transaction')
    if Path('/etc/tmpfiles.d/garage-ma-enrollment.conf').exists():raise PermissionError('existing tmpfiles input requires explicit retained update transaction')
    # Empty root-owned directory anchors must precede Docker create_host_path:false binds.
    for path,mode,gid in [('/etc/garage-audio/ma-enrollment',0o700,0),
        ('/home/zach/garage-audio/state/ma-enrollment',0o711,0),
        ('/home/zach/garage-audio/state/ma-enrollment/app',0o711,0),
        ('/home/zach/garage-audio/state/ma-enrollment/observer',0o711,0),
        ('/run/garage-ma-enrollment',0o710,1000)]:
        fd=directory(path,mode,create=True,gid=gid);os.close(fd)
    fd=directory(str(base),0o755,create=True);os.close(fd)
    fd=directory(str(base/'garage_audio'),0o755,create=True)
    try:
        for name in sorted(FILES):write_new(fd,Path(name).name,data[name])
        os.fchmod(fd,0o555);os.fsync(fd)
    finally:os.close(fd)
    fd=directory(str(base),0o755)
    try:write_new(fd,'bootstrap-inventory.json',raw);os.fchmod(fd,0o555);os.fsync(fd)
    finally:os.close(fd)
    unit=data[UNIT].replace(b'@BOOTSTRAP@',str(base).encode())
    if b'@BOOTSTRAP@' in unit:raise PermissionError('unit template incomplete')
    fd=directory('/etc/systemd/system',0o755)
    try:write_new(fd,'garage-ma-enrollment.service',unit,0o644)
    finally:os.close(fd)
    fd=directory('/etc/tmpfiles.d',0o755)
    try:write_new(fd,'garage-ma-enrollment.conf',data[TMPFILES],0o644)
    finally:os.close(fd)
    print(json.dumps({'bootstrap':str(base),'inventory_sha256':digest,'installed_only':True,
        'service_started_or_enabled':False,'admission_or_credentials_created':False}))

if __name__=='__main__':main()
