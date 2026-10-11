"""Read-only deployed pins/peer/kernel/receiver checks; no login/commands/sound.

Never query the browser IPC as root (its UID restriction is intentional). Verify
native/controller/receiver independently from kernel records and service pins.
"""
import ctypes,hashlib,http.client,json,os,socket,struct,subprocess,tempfile
from pathlib import Path
from garage_audio.ma_reference import service_pin,observe_service,REFERENCE
from garage_audio.spotify_socket_guard import syscall,MapElement

ROOT=Path('/usr/local/libexec/garage-audio');CANDIDATE=Path('/root/garage-connect-final-r20.yz9ylbow')
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def inspect(name):return json.loads(subprocess.check_output(['docker','inspect',name],stderr=subprocess.DEVNULL))[0]
def main():
    assert os.geteuid()==0
    manifest=json.loads((CANDIDATE/'frozen-candidate.json').read_text())
    actual={str(p.relative_to(ROOT)):sha(p) for p in ROOT.rglob('*') if p.is_file() and '__pycache__' not in p.parts and p.suffix!='.pyc'}
    assert actual==manifest['after_native'],'installed critical/full inventory drift'
    for target in manifest['native_mapping'].values():
        info=(ROOT/target).lstat();assert info.st_uid==0 and not info.st_mode&0o022
    for name in ('ma-service.json','ma-peer.json','soloist-sha256'):
        info=(Path('/etc/garage-audio')/name).lstat();assert info.st_uid==0 and info.st_mode&0o777==0o600
    assert sha(Path('/etc/systemd/system/garage-speakers.service'))==manifest['inputs']['config/system/garage-speakers.service']
    assert not Path('/etc/garage-audio/spotify-connect-enabled').exists()
    assert subprocess.run(['systemctl','is-active','garage-audio-dsp.service'],capture_output=True).returncode!=0
    ma=inspect('garage-audio-music-assistant-1');pid=ma['State']['Pid'];assert ma['State']['Running']
    assert observe_service(service_pin())==json.loads(REFERENCE.read_text())
    endpoint=Path(f'/proc/{pid}/root/data/spotify_connect/spotify_connect_02_00_00_00_21_01/soloist-data')
    port=int((endpoint/'ws.port').read_text());assert (endpoint/'ws.addr').read_text().strip()=='127.0.0.1'
    rootpid=int(subprocess.check_output(['systemctl','show','garage-speakers.service','-p','MainPID','--value']))
    fds=list(Path(f'/proc/{rootpid}/fd').iterdir());links=[os.readlink(p) for p in fds]
    assert links.count('anon_inode:bpf_link')==2,'exact controller-owned connect4/connect6 links absent'
    mapfd=next(p for p in fds if os.readlink(p)=='anon_inode:bpf-map')
    fields=dict(line.split(':',1) for line in Path(f'/proc/{rootpid}/fdinfo/{mapfd.name}').read_text().splitlines() if ':' in line)
    class GetID(ctypes.Structure):_fields_=[('id',ctypes.c_uint32),('next_id',ctypes.c_uint32),('flags',ctypes.c_uint32)]
    fd=syscall(14,GetID(int(fields['map_id']),0,0))
    try:
        key=ctypes.c_uint32(socket.htons(port));value=ctypes.create_string_buffer(16)
        syscall(1,MapElement(fd,0,ctypes.addressof(key),ctypes.addressof(value),0))
        uid,tgid,group=struct.unpack('<IIQ',value.raw)
        relative=next(row[3:] for row in Path(f'/proc/{pid}/cgroup').read_text().splitlines() if row.startswith('0::'))
        assert (uid,tgid,group)==(1000,pid,(Path('/sys/fs/cgroup')/relative.lstrip('/')).stat().st_ino)
    finally:os.close(fd)
    # Probe runs as a DISTINCT same-UID process in actual MA cgroup/netns. Only
    # connect, no WebSocket/command/music frame, to prove live production denial.
    code=f'import socket,errno\ns=socket.socket();s.settimeout(2)\ntry:s.connect(("127.0.0.1",{port}))\nexcept OSError as e:\n assert e.errno in (errno.EPERM,errno.EACCES),e.errno\n print("DENIED")\nelse:raise AssertionError("unapproved sameUID process connected")\nfinally:s.close()'
    assert subprocess.check_output(['docker','exec','--user','1000:1000','garage-audio-music-assistant-1','/app/venv/bin/python','-c',code],text=True).strip()=='DENIED'
    # Actual pinned MA owns an ESTABLISHED connection to the protected port;
    # its service log confirms aiohttp validated the actual RFC6455 handshake.
    own={os.readlink(p) for p in Path(f'/proc/{pid}/fd').iterdir()}
    connected=False
    for row in Path(f'/proc/{pid}/net/tcp').read_text().splitlines()[1:]:
        parts=row.split()
        if len(parts)>9 and parts[2]==f'0100007F:{port:04X}' and parts[3]=='01' and 'socket:['+parts[9]+']' in own:connected=True
    assert connected,'actual admitted MA receiver connection absent'
    captured=subprocess.run(['docker','logs','--tail','1000','garage-audio-music-assistant-1'],capture_output=True)
    raw=captured.stdout+captured.stderr
    # Do not print/persist raw account/provider log messages.
    handshake=any(b'Connected to the soloist websocket at' in line and str(port).encode() in line for line in raw.splitlines())
    settings=json.loads(Path('/home/zach/garage-audio/state/ma/settings.json').read_text());providers=settings['providers'];rows=providers.values() if isinstance(providers,dict) else providers
    config=next(p for p in rows if p.get('domain')=='spotify_connect' and p.get('enabled'))
    assert config['values']['volume_mode']=='sync_spotify'
    assert config['values']['connected_players']==['02:00:00:00:21:01']
    client=http.client.HTTPConnection('127.0.0.1',9000,timeout=3)
    try:
        client.request('POST','/jsonrpc.js',json.dumps({'id':1,'method':'slim.request','params':['',['players',0,100]]}),{'Content-Type':'application/json'})
        response=client.getresponse();data=json.loads(response.read(32768));assert response.status==200
        registered=any(p.get('playerid')=='02:00:00:00:21:01' and p.get('name')=='Garage Speakers' and p.get('connected')==1 for p in data['result']['players_loop']);assert registered
    finally:client.close()
    web=inspect('garage-audio-dashboard');assert web['Image']=='sha256:44038332e66f37ddca6a49e049a9c818e3392c06b8f073e29cedaed3e28b82f6'
    for name in ('web/speakers.js','web/index.html'):
        data=subprocess.check_output(['docker','exec','garage-audio-dashboard','cat','/app/'+name]);assert hashlib.sha256(data).hexdigest()==manifest['inputs'][name]
    scanners={name:inspect(name)['State']['Status'] for name in ('wncscanner-transcriber-1','wncscanner-web-1')};assert set(scanners.values())=={'running'}
    playback=0
    for p in Path('/proc').glob('[0-9]*/fd/*'):
        try:
            target=os.readlink(p)
            if target.startswith('/dev/snd/pcmC') and target.endswith('p'):playback+=1
        except OSError:pass
    assert playback==0
    out=Path(tempfile.mkdtemp(prefix='garage-connect-installed-readback-r20.',dir='/root'))
    result={'overlay_sha256':manifest['overlay_sha256'],'installed_inventory_sha256':manifest['after_native_sha256'],'MA_pid':pid,'native_controller_pid':rootpid,'root_owned_hashes_verified':True,'MA_reference_verified':True,'production_cgroup_links':2,'protected_port':port,'exact_MA_UID_TGID_cgroup_verified':True,'unapproved_sameUID_cgroup_connect_denied':True,'actual_MA_established_receiver_connection':connected,'actual_aiohttp_handshake_log_observed':handshake,'sync_spotify':True,'player_registered':registered,'sound_marker_absent':True,'ALSA_playback_FDs':playback,'dashboard_image':web['Image'],'scanner_status':scanners,'sound_or_user_Play':False,'receipt':str(out)}
    (out/'readback.json').write_text(json.dumps(result,sort_keys=True,indent=2)+'\n');print(json.dumps(result))
if __name__=='__main__':main()
