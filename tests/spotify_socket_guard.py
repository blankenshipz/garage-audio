"""Actual isolated Linux cgroup connection enforcement, synthetic TCP server.

NOT real Soloist/Spotify media evidence. Production processes are never moved.
"""
import json,os,socket,subprocess,sys,tempfile,threading,time,uuid
from pathlib import Path
from garage_audio.spotify_socket_guard import SocketGuard

assert os.geteuid()==0
receipt=Path(tempfile.mkdtemp(prefix='garage-socket-guard-test.',dir='/root'))
group=Path('/sys/fs/cgroup')/('garage-spotify-isolated-'+uuid.uuid4().hex)
group.mkdir();(group/'cgroup.procs').write_text(str(os.getpid()))
guard=None;children=[]
server=socket.socket();server.bind(('127.0.0.1',0));server.listen(8);port=server.getsockname()[1]
other=socket.socket();other.bind(('127.0.0.1',0));other.listen(8)
def accept(listener):
    try:
        while True:
            connection,_=listener.accept();connection.sendall(b'ISOLATED_TCP_BODY');connection.close()
    except OSError:pass
threading.Thread(target=accept,args=(server,),daemon=True).start()
threading.Thread(target=accept,args=(other,),daemon=True).start()
def client(ipv6=False,port_override=None):
    code="""import os,socket,sys
os.setgroups([]);os.setgid(1000);os.setuid(1000)
sys.stdin.readline()
s=socket.socket(socket.AF_INET6 if sys.argv[2]=='6' else socket.AF_INET)
try:
 s.connect(('::ffff:127.0.0.1' if sys.argv[2]=='6' else '127.0.0.1',int(sys.argv[1])))
 print('ALLOWED '+s.recv(128).decode())
except PermissionError:print('DENIED')
finally:s.close()
"""
    p=subprocess.Popen([sys.executable,'-c',code,str(port_override or port),'6' if ipv6 else '4'],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True);children.append(p);return p
def go(p):
    out,err=p.communicate('GO\n',timeout=5)
    (receipt/(str(p.pid)+'.stdout')).write_text(out);(receipt/(str(p.pid)+'.stderr')).write_text(err)
    assert p.returncode==0,(p.returncode,str(receipt))
    return out.strip()
try:
    guard=SocketGuard(str(group),receipt)
    legitimate=client();guard.protect(port,pid=legitimate.pid,uid=1000,cgroup_id=group.stat().st_ino)
    assert go(client())=='DENIED','Unrelated same UID/cgroup process reached protected port'
    assert go(client(True))=='DENIED','IPv4-mapped IPv6 bypass reached protected port'
    assert go(legitimate)=='ALLOWED ISOLATED_TCP_BODY'
    deadline=time.monotonic()+2
    while guard.owners[port][0] is not None and time.monotonic()<deadline:time.sleep(.02)
    assert guard.owners[port][0] is None,'pidfd death did not tombstone protected endpoint'
    assert go(client())=='DENIED','Owner death exposed surviving receiver port'
    assert go(client(port_override=other.getsockname()[1]))=='ALLOWED ISOLATED_TCP_BODY','Unrelated socket impacted'
    guard.close();guard=None
    helper="""import os,sys,time
from pathlib import Path
from garage_audio.spotify_socket_guard import SocketGuard
g=SocketGuard(sys.argv[1],Path(sys.argv[2]))
g.protect(int(sys.argv[3]),pid=os.getpid(),uid=1000,cgroup_id=Path(sys.argv[1]).stat().st_ino)
print('LINK_READY',flush=True)
time.sleep(30)
"""
    owner=subprocess.Popen([sys.executable,'-c',helper,str(group),str(receipt),str(port)],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True);children.append(owner)
    assert owner.stdout.readline().strip()=='LINK_READY','isolated kernel link owner failed'
    assert go(client())=='DENIED','live link owner failed to protect endpoint'
    owner.kill();owner.wait(timeout=3)
    assert go(client())=='ALLOWED ISOLATED_TCP_BODY','dead owner left unreachable cgroup rules'
    print(json.dumps({'receipt':str(receipt),'actual_kernel_cgroup_connect4_connect6':True,'unrelated_same_UID_same_cgroup_denied':True,'mapped_IPv6_bypass_denied':True,'exact_registered_client_body_allowed':True,'server':'synthetic TCP, NOT Soloist','production_attachment':False,'native_or_music_control':False}))
finally:
    if guard:guard.close()
    for child in children:
        if child.poll() is None:child.kill();child.wait()
    server.close();other.close();Path('/sys/fs/cgroup/cgroup.procs').write_text(str(os.getpid()));group.rmdir()
