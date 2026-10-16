"""Root-only deployment admission builder, never callable over HTTP/IPC/MCP.

Docker is inspected by this one-time administrator helper, not by the installer.
The expected image and source inventory come from the frozen candidate, not the app.
"""
import argparse
import hashlib
import json
import os
import re
import subprocess
from .ma_enrollment_installer import anchored,read,write,process_identity

def build(container,image_id,manifest):
    if not re.fullmatch('[a-zA-Z0-9_.-]{1,100}',container) or not re.fullmatch(r'sha256:[0-9a-f]{64}',image_id):
        raise ValueError('invalid pinned container/image')
    required={'/app/garage_audio/webserver.py','/app/garage_audio/ma_enrollment.py',
              '/app/garage_audio/ma_enrollment_installer.py','/app/garage_audio/setup.py'}
    if not required.issubset(manifest):raise ValueError('frozen source inventory incomplete')
    data=json.loads(subprocess.check_output(['docker','inspect',container],text=True,timeout=10))[0]
    if data['Image']!=image_id or not data['State']['Running'] or data['Config']['User'] not in ('1000','1000:1000'):
        raise ValueError('candidate process/image does not match admission')
    # init:true makes State.Pid the init/reaper, NOT the HTTP socket caller.
    # Admit the unique canonical webserver descendant; never a UID-only process.
    pending=[data['State']['Pid']];matches=[];visited=set()
    while pending:
        candidate=pending.pop()
        if candidate in visited or len(visited)>=64:raise ValueError('candidate process tree exceeds admission bound')
        visited.add(candidate);base='/proc/'+str(candidate)
        try:
            with open(base+'/cmdline','rb') as stream:argv=stream.read().split(b'\0')
            with open(base+'/task/'+str(candidate)+'/children') as stream:pending.extend(int(x) for x in stream.read().split())
        except FileNotFoundError:continue
        if argv[1:3]==[b'-m',b'garage_audio.webserver']:matches.append(candidate)
    if len(matches)!=1:raise ValueError('exact unique app HTTP process not available')
    pid=matches[0];base='/proc/'+str(pid)
    with open(base+'/stat') as stream:start=stream.read().rsplit(')',1)[1].split()[19]
    exe=os.stat(base+'/exe')
    with open(base+'/exe','rb') as stream:exe_hash=hashlib.file_digest(stream,'sha256').hexdigest()
    with open(base+'/cgroup','rb') as stream:cgroup_hash=hashlib.sha256(stream.read()).hexdigest()
    with open(base+'/cmdline','rb') as stream:cmdline_hash=hashlib.sha256(stream.read()).hexdigest()
    admission={'uid':1000,'pid':pid,'start_time':start,'exe_dev':exe.st_dev,'exe_ino':exe.st_ino,
               'exe_sha256':exe_hash,'mnt_ns':os.readlink(base+'/ns/mnt'),
               'pid_ns':os.readlink(base+'/ns/pid'),'cgroup_sha256':cgroup_hash,'cmdline_sha256':cmdline_hash,
               'container_id':data['Id'],'image_id':image_id,'source_manifest':manifest}
    process_identity(pid,admission)
    return admission

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--container',required=True);parser.add_argument('--image-id',required=True)
    parser.add_argument('--manifest',required=True);parser.add_argument('--ma-endpoint',required=True)
    parser.add_argument('--output',required=True);args=parser.parse_args()
    if os.geteuid()!=0:raise PermissionError('root deployment admission required')
    parent=anchored(os.path.dirname(os.path.abspath(args.manifest)))
    try:manifest=json.loads(read(parent,os.path.basename(args.manifest),limit=131072))
    finally:os.close(parent)
    admission=build(args.container,args.image_id,manifest)
    if not re.fullmatch(r'http://(?:music-assistant|192\.168\.68\.[0-9]{1,3}):8095',args.ma_endpoint):
        raise ValueError('fixed current-lease MA endpoint required')
    parent=anchored(os.path.dirname(os.path.abspath(args.output)))
    try:write(parent,os.path.basename(args.output),json.dumps({'ma_endpoint':args.ma_endpoint,'admission':admission}).encode())
    finally:os.close(parent)
    print('Root process/image/source admission written; no credentials returned or services changed')

if __name__=='__main__':main()
