"""Narrow root Unix installer; fixed targets, exact admitted process, no web/Docker RPC."""
import argparse
import fcntl
import hashlib
import json
import os
import re
import socket
import stat
import struct
from .ma_enrollment import MA,verify,token_valid,EnrollmentFailure

ROOT='/home/zach/garage-audio/state/ma-enrollment'
SOCKET='/run/garage-ma-enrollment/install.sock'
TARGETS=(('app',1000),('observer',1001))
ID=re.compile(r'[a-zA-Z0-9_-]{16,64}')

def anchored(path,create=False):
    fd=os.open('/',os.O_RDONLY|os.O_DIRECTORY)
    try:
        for part in path.split('/')[1:]:
            if not part or part in ('.','..'):raise ValueError('unsafe root')
            if create:
                try:os.mkdir(part,0o711,dir_fd=fd);os.fsync(fd)
                except FileExistsError:pass
            child=os.open(part,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=fd)
            info=os.fstat(child)
            if info.st_uid not in (0,1000) or info.st_mode&0o022:
                os.close(child);raise ValueError('unsafe ancestor')
            os.close(fd);fd=child
        if os.fstat(fd).st_uid!=0:raise ValueError('root-owned publication directory required')
        return fd
    except BaseException:os.close(fd);raise

def read(fd,name,uid=0,limit=16384):
    handle=os.open(name,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=fd)
    with os.fdopen(handle,'rb') as stream:
        info=os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid!=uid or stat.S_IMODE(info.st_mode)!=0o600 or info.st_nlink!=1:
            raise ValueError('unsafe private file')
        data=stream.read(limit+1)
    if len(data)>limit:raise ValueError('private file too large')
    return data

def write(fd,name,data,uid=0):
    try:read(fd,name,uid)
    except FileNotFoundError:pass
    temp='.new-'+os.urandom(16).hex()
    handle=os.open(temp,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=fd)
    try:
        with os.fdopen(handle,'wb') as stream:
            os.fchown(stream.fileno(),uid,uid);stream.write(data);stream.flush();os.fsync(stream.fileno())
        os.replace(temp,name,src_dir_fd=fd,dst_dir_fd=fd);os.fsync(fd)
    finally:
        try:os.unlink(temp,dir_fd=fd)
        except FileNotFoundError:pass

def process_identity(pid,admission):
    base='/proc/'+str(pid)
    with open(base+'/stat') as stream:fields=stream.read().rsplit(')',1)[1].split()
    start=fields[19]
    exe=os.stat(base+'/exe')
    with open(base+'/exe','rb') as stream:exe_hash=hashlib.file_digest(stream,'sha256').hexdigest()
    with open(base+'/cgroup','rb') as stream:cgroup=stream.read()
    with open(base+'/cmdline','rb') as stream:cmdline=stream.read()
    observed={'pid':pid,'start_time':start,'exe_dev':exe.st_dev,'exe_ino':exe.st_ino,
              'exe_sha256':exe_hash,'mnt_ns':os.readlink(base+'/ns/mnt'),
              'pid_ns':os.readlink(base+'/ns/pid'),'cgroup_sha256':hashlib.sha256(cgroup).hexdigest(),
              'cmdline_sha256':hashlib.sha256(cmdline).hexdigest()}
    if any(admission.get(k)!=value for k,value in observed.items()):raise PermissionError('process identity mismatch')
    container=admission.get('container_id','')
    if not re.fullmatch('[0-9a-f]{64}',container) or container.encode() not in cgroup:
        raise PermissionError('container identity mismatch')
    if not re.fullmatch(r'sha256:[0-9a-f]{64}',admission.get('image_id','')):
        raise PermissionError('image admission missing')
    manifest=admission.get('source_manifest',{})
    if not isinstance(manifest,dict) or not 1<=len(manifest)<=256:raise PermissionError('source admission missing')
    required={'/app/garage_audio/webserver.py','/app/garage_audio/setup.py',
              '/app/garage_audio/ma_enrollment.py','/app/garage_audio/ma_enrollment_installer.py'}
    if not required.issubset(manifest):raise PermissionError('source admission incomplete')
    for path,digest in manifest.items():
        if not re.fullmatch(r'/app/(?:garage_audio|web)/[a-zA-Z0-9_./-]+',path) or '..' in path.split('/'):
            raise PermissionError('source path invalid')
        if not re.fullmatch('[0-9a-f]{64}',digest):raise PermissionError('source digest invalid')
        with open(base+'/root'+path,'rb') as stream:
            if hashlib.file_digest(stream,'sha256').hexdigest()!=digest:raise PermissionError('source mismatch')
    # Recheck start time after hashing to fail a process exit/PID reuse race.
    with open(base+'/stat') as stream:
        if stream.read().rsplit(')',1)[1].split()[19]!=start:raise PermissionError('process changed')

def authorize(connection,admission):
    pid,uid,gid=struct.unpack('3i',connection.getsockopt(socket.SOL_SOCKET,socket.SO_PEERCRED,12))
    if uid!=1000 or uid!=admission.get('uid'):raise PermissionError('peer denied')
    process_identity(pid,admission)

class Installer:
    def __init__(self,root=ROOT,ma=None):
        self.root=anchored(root,create=True)
        self.targets={}
        for name,uid in TARGETS:
            created=False
            try:os.mkdir(name,0o700,dir_fd=self.root);os.fsync(self.root);created=True
            except FileExistsError:pass
            fd=os.open(name,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=self.root)
            if created and os.fstat(fd).st_uid==0:
                os.fchmod(fd,0o711);os.fsync(fd)
            s=os.fstat(fd)
            if s.st_uid!=0 or stat.S_IMODE(s.st_mode)!=0o711:os.close(fd);raise ValueError('publication anchor invalid')
            self.targets[name]=fd
        self.ma=ma or MA()
    def close(self):
        for fd in self.targets.values():os.close(fd)
        os.close(self.root)
    def transaction(self):
        try:return json.loads(read(self.root,'current.json'))
        except FileNotFoundError:return None
    def save(self,value):write(self.root,'current.json',json.dumps(value).encode())
    def recover(self,transaction,validated=False):
        if not validated:verify(self.ma,transaction['bearer'])
        for name,uid in TARGETS:
            expected=(transaction['bearer']+'\n').encode()
            try:existing=read(self.targets[name],'token',uid,4097)
            except FileNotFoundError:existing=None
            if existing!=expected:write(self.targets[name],'token',expected,uid)
        transaction['phase']='committed';self.save(transaction)
    def status(self):
        transaction=self.transaction()
        if not transaction:return {'state':'setup_required','code':'ma_credential_required','scope_verified':False}
        try:
            if transaction['phase']!='committed':self.recover(transaction)
            verify(self.ma,transaction['bearer'])
            for name,uid in TARGETS:
                if read(self.targets[name],'token',uid,4097)!=(transaction['bearer']+'\n').encode():
                    raise EnrollmentFailure('ma_reference_mismatch')
            return {'state':'ready','code':None,'enrollment_id':transaction['enrollment_id'],'scope_verified':True}
        except EnrollmentFailure as error:
            return {'state':'setup_required','code':error.code,'scope_verified':False}
    def handle(self,payload):
        if payload=={'op':'status'}:return self.status()
        if (not isinstance(payload,dict) or set(payload)!={'op','enrollment_id','bearer'}
            or payload['op']!='install' or not isinstance(payload['enrollment_id'],str)
            or not ID.fullmatch(payload['enrollment_id']) or not token_valid(payload['bearer'])):
            raise EnrollmentFailure('invalid_installer_request')
        previous=self.transaction()
        if previous and previous['enrollment_id']==payload['enrollment_id']:
            if previous['bearer']!=payload['bearer']:raise EnrollmentFailure('ma_enrollment_conflict')
            return self.status()
        if previous and previous['phase']!='committed':raise EnrollmentFailure('ma_enrollment_interrupted')
        verify(self.ma,payload['bearer'])
        if previous:
            # Retain the previous approved transaction privately; never dump to receipts.
            write(self.root,previous['enrollment_id']+'.json',json.dumps(previous).encode())
        transaction={'enrollment_id':payload['enrollment_id'],'bearer':payload['bearer'],'phase':'prepared'}
        self.save(transaction)  # durable BEFORE either reader file changes
        self.recover(transaction,validated=True)
        # Both files just read/written under the same pinned root descriptors.
        return {'state':'ready','code':None,'enrollment_id':transaction['enrollment_id'],'scope_verified':True}

def serve(config):
    if os.geteuid()!=0:raise PermissionError('root installer required')
    # Endpoint is root-owned deployment input, NOT caller input, tied to current MA lease.
    endpoint=config['ma_endpoint']
    if not re.fullmatch(r'http://(?:music-assistant|192\.168\.68\.[0-9]{1,3}):8095',endpoint):
        raise ValueError('fixed MA endpoint required')
    admission=config['admission']
    os.umask(0o077)
    directory=anchored('/run/garage-ma-enrollment',create=True)
    os.fchown(directory,0,1000);os.fchmod(directory,0o710)
    # Single daemon owner across crash/restart. Never replace a live official owner.
    lock=os.open('owner.lock',os.O_WRONLY|os.O_CREAT|os.O_NOFOLLOW,0o600,dir_fd=directory)
    info=os.fstat(lock)
    if info.st_uid!=0 or stat.S_IMODE(info.st_mode)!=0o600 or info.st_nlink!=1 or not stat.S_ISREG(info.st_mode):
        raise ValueError('unsafe daemon lock')
    fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    try:
        old=os.stat('install.sock',dir_fd=directory,follow_symlinks=False)
        if not stat.S_ISSOCK(old.st_mode) or old.st_uid!=0 or old.st_gid!=1000 or stat.S_IMODE(old.st_mode)!=0o660:
            raise ValueError('unsafe stale socket')
        os.unlink('install.sock',dir_fd=directory);os.fsync(directory)
    except FileNotFoundError:pass
    server=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM)
    try:
        server.bind(SOCKET);os.chown(SOCKET,0,1000);os.chmod(SOCKET,0o660);server.listen(8)
        worker=Installer(ma=MA(endpoint))
        try:
            while True:
                connection,_=server.accept()
                with connection:
                    connection.settimeout(45)
                    try:
                        authorize(connection,admission)
                        raw=b''
                        while len(raw)<=8192:
                            chunk=connection.recv(8193-len(raw))
                            if not chunk:break
                            raw+=chunk
                        if len(raw)>8192:raise EnrollmentFailure('invalid_installer_request')
                        result=worker.handle(json.loads(raw))
                    except Exception as error:
                        result={'state':'unavailable','code':error.code if isinstance(error,EnrollmentFailure) else 'ma_installer_rejected','scope_verified':False}
                    try:connection.sendall(json.dumps(result).encode())
                    except OSError:pass  # A denied/disconnected peer cannot stop admission.
        finally:worker.close()
    finally:
        server.close()
        try:os.unlink('install.sock',dir_fd=directory);os.fsync(directory)
        finally:os.close(lock);os.close(directory)

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--config',required=True);args=parser.parse_args()
    parent=anchored(os.path.dirname(os.path.abspath(args.config)))
    try:config=json.loads(read(parent,os.path.basename(args.config),limit=131072))
    finally:os.close(parent)
    serve(config)

if __name__=='__main__':main()
