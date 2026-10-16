"""Actual installed public binary/loopback socket ownership; NO receiver connection."""
import json,os,subprocess,sys
from pathlib import Path
from garage_audio.audio_receiver import ReceiverEndpoint,advertised_pairing,qualify_pairing

receipt=Path(sys.argv[1]);receipt.mkdir(mode=0o700)
pin=Path('/etc/garage-audio/soloist-sha256').read_text().strip()
if len(pin)!=64 or any(c not in '0123456789abcdef' for c in pin):raise PermissionError('exact authorized installed binary digest required')
item=json.loads(subprocess.check_output(['docker','inspect','garage-audio-music-assistant-1']))[0]
pid=item['State']['Pid'];inode=os.stat(f'/proc/{pid}/ns/net').st_ino
observer=ReceiverEndpoint(pin);value=observer.observe(pid,inode)
assert value==observer.observe(pid,inode)
ports=advertised_pairing(item['Id'],'192.168.68.74')
qualified=qualify_pairing(value,pid,inode,'192.168.68.74',ports)
assert value['port'] not in qualified and not set(qualified)&{8095,8097,1234,1235}
assert qualify_pairing(value,pid,inode,'192.168.68.74',[value['port'],8095,8097,1234,1235])==[]
(receipt/'pairing-observation.json').write_text(json.dumps({'advertised_ports':ports,'qualified_listener_ports':qualified,'legacy_namespace_only':True},indent=2))
(receipt/'observation.json').write_text(json.dumps(value,indent=2))
try:ReceiverEndpoint('0'*64).observe(pid,inode)
except PermissionError:pass
else:raise AssertionError('wrong installed binary pin admitted')
print('PASS actual kernel loopback listener UID namespace public binary pin; wrong digest denied; no socket connection/grant/argv/key access; deployed legacy namespace only')
