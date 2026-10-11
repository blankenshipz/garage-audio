"""Actual Soloist WS protocol response behind isolated client-cgroup enforcement.

Production receiver and canonical MA client never move or restart. No music,
pairing, volume, activation, status commands or inference. No account data read.
"""
import json,os,subprocess,sys,tempfile,uuid
from pathlib import Path
from garage_audio.spotify_socket_guard import SocketGuard

assert os.geteuid()==0
description=json.loads(subprocess.check_output(['docker','inspect','garage-audio-music-assistant-1']))[0]
pid=description['State']['Pid'];assert description['Config']['User']=='1000:1000'
directory=Path('/home/zach/garage-audio/state/ma/spotify_connect/spotify_connect_02_00_00_00_21_01/soloist-data')
assert (directory/'ws.addr').read_text().strip()=='127.0.0.1';port=int((directory/'ws.port').read_text())
receipt=Path(tempfile.mkdtemp(prefix='garage-socket-soloist-test.',dir='/root'))
group=Path('/sys/fs/cgroup')/('garage-spotify-status-isolated-'+uuid.uuid4().hex)
group.mkdir();(group/'cgroup.procs').write_text(str(os.getpid()));guard=None;children=[]
def client():
    # Direct wire observation instead of CLI exit codes: Soloist ctl status can
    # successfully report offline/unreachable, so exit0 is not command admission.
    helper=r'''import base64,hashlib,os,socket,sys
os.setgroups([]);os.setgid(1000);os.setuid(1000);sys.stdin.readline()
s=socket.socket();s.settimeout(3)
try:s.connect(('127.0.0.1',int(sys.argv[1])))
except PermissionError:print('DENIED');raise SystemExit(0)
key=base64.b64encode(os.urandom(16)).decode()
request=f'GET / HTTP/1.1\r\nHost: 127.0.0.1:{sys.argv[1]}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Version: 13\r\nSec-WebSocket-Key: {key}\r\n\r\n'
s.sendall(request.encode());data=b''
while b'\r\n\r\n' not in data:
 block=s.recv(4096)
 if not block:break
 data+=block
 if len(data)>8192:raise RuntimeError('bounded handshake required')
head,buffer=data.split(b'\r\n\r\n',1);head=head.decode('ascii')
accept=base64.b64encode(hashlib.sha1((key+'258EAFA5-E914-47DA-95CA-C5AB0DC85B11').encode()).digest()).decode()
assert head.startswith('HTTP/1.1 101') and accept in head
mask=os.urandom(4);s.sendall(bytes([0x89,0x81])+mask+bytes([ord('G')^mask[0]]))
def read(size):
 global buffer
 while len(buffer)<size:
  block=s.recv(65536)
  if not block:raise RuntimeError('receiver closed before Pong')
  buffer+=block
 value,buffer=buffer[:size],buffer[size:];return value
for _ in range(32):
 first,second=read(2);length=second&127
 if length==126:length=int.from_bytes(read(2),'big')
 elif length==127:length=int.from_bytes(read(8),'big')
 if length>65536 or second&128:raise RuntimeError('bounded server frame required')
 body=read(length)
 # Drop initial account/state frames without parsing, logging or storing them.
 if first&15==10 and body==b'G':break
else:raise RuntimeError('actual receiver did not prove responsive Pong')
print('ACTUAL_SOLOIST_RFC6455_101');s.close()
'''
    compile(helper,'wire-client','exec')
    child=subprocess.Popen([sys.executable,'-c',helper,str(port)],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    children.append(child);return child
try:
    guard=SocketGuard(str(group),receipt);legitimate=client()
    guard.protect(port,pid=legitimate.pid,uid=1000,cgroup_id=group.stat().st_ino)
    unauthorized=client();out,err=unauthorized.communicate('GO\n',timeout=8)
    (receipt/'negative.stderr').write_text(err)
    assert unauthorized.returncode==0 and out.strip()=='DENIED','Unrelated process reached actual Soloist control socket'
    out,err=legitimate.communicate('GO\n',timeout=8)
    (receipt/'positive.stderr').write_text(err)
    assert legitimate.returncode==0 and out.strip()=='ACTUAL_SOLOIST_RFC6455_101','Exact registered client failed actual Soloist protocol response'
    assert int((directory/'ws.port').read_text())==port,'Receiver port changed; test identity not stable'
    print(json.dumps({'receipt':str(receipt),'actual_Soloist_WS_101_response_verified':True,'actual_receiver_Ping_Pong_verified':True,
                      'unrelated_same_UID_same_cgroup_denied':True,'negative_exit':unauthorized.returncode,
                      'positive_exit':legitimate.returncode,'native_kernel_EPERRM_negative':True,
                      'raw_account_status_body_retained':False,'production_processes_moved_or_restarted':False,
                      'target':'existing Garage Speakers Soloist control socket','Spotify_or_native_play':False}))
finally:
    if guard:guard.close()
    for child in children:
        if child.poll() is None:child.kill();child.wait()
    Path('/sys/fs/cgroup/cgroup.procs').write_text(str(os.getpid()));group.rmdir()
