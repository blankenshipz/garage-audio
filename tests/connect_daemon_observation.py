"""Actual Soloist inode/process observation, synthetic admission/private pin.

No production guard install, config writes, receiver commands or sound. This
proves the attester's actual procfs positive/negative logic, NOT MA socket auth.
"""
import hashlib,json,os,select,subprocess,tempfile,threading
from pathlib import Path
from garage_audio import connect_port
from garage_audio.ma_bridge import process_reference

assert os.geteuid()==0
description=json.loads(subprocess.check_output(['docker','inspect','garage-audio-music-assistant-1']))[0]
pid=description['State']['Pid'];reference=process_reference(pid)
directory=Path('/proc')/str(pid)/'root'/connect_port.DATA
port=int((directory/'ws.port').read_text());assert (directory/'ws.addr').read_text().strip()=='127.0.0.1'
private=Path(tempfile.mkdtemp(prefix='garage-connect-attest-fixture.',dir='/root'))
pin=private/'soloist-sha256';pin.write_text(hashlib.sha256((Path('/proc')/str(pid)/'root'/connect_port.BINARY).read_bytes()).hexdigest()+'\n');pin.chmod(0o600)
connect_port.SOLOIST_PIN=str(pin) # isolated fixture config, not production /etc
class ObservedPeer:
    def __init__(self):self.pid=pid;self.reference={'process':reference};self.fd=os.pidfd_open(pid)
    def verify(self):assert process_reference(pid)==reference
    def alive(self):return not select.select([self.fd],[],[],0)[0]
peer=ObservedPeer()
scope=connect_port.PortScope.__new__(connect_port.PortScope)
scope.peer=peer;scope.port=port;scope.netns=os.readlink(f'/proc/{pid}/ns/net')
scope.lock=threading.Lock();scope.closed=False;scope.daemon=None;scope.daemon_fd=None
try:
    value=scope.attest()
    assert scope.alive() and value['daemon'][0]!=pid
    pin.write_text('0'*64+'\n')
    try:scope.attest()
    except PermissionError:denied=True
    else:raise AssertionError('wrong reviewed binary digest accepted')
    print(json.dumps({'actual_live_Soloist_process_inode_starttime_namespace_match':True,'wrong_private_fixture_binary_pin_denied':True,'production_config_or_receiver_changed':False,'MA_gateway_admission':'synthetic fixture, not kernel-auth proof','guard_attached':False,'sound_or_commands':False,'receipt':str(private)}))
finally:
    if scope.daemon_fd is not None:os.close(scope.daemon_fd)
    os.close(peer.fd)
