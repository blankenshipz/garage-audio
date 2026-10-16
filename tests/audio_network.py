"""Mac-side real LAN negative probes while target fixture remains live; no production port."""
import json
from pathlib import Path
import socket
import subprocess
import sys

if sys.argv[1:2] == ['--file']:
    import os
    import time
    import array
    import urllib.request
    from unittest.mock import patch
    import garage_audio.audio_network as net
    source = Path(__file__).resolve().parents[1]
    root = Path(sys.argv[2]).resolve()
    root.mkdir(mode=0o700, parents=True, exist_ok=False)
    ma_image = 'sha256:28023f8c0d96ca2496391f3218a6d70d7ef3ba84db846b3a3f3cd9ce7ef8aedc'
    def fixture(case, recovery=False):
        c = net.Carrier(root/case)
        c.start(source/'scripts/bootstrap-audio-network.py')
        c.ns('nft','add','element','inet','garage_audio','self_stream','{','18088','}')
        # Test-only WAV origin: same interface/address/reply policy as production self stream.
        ipc = c.receipt/'ipc';ipc.mkdir(mode=0o711)
        evidence = c.receipt/'pcm';evidence.mkdir(mode=0o750);os.chown(evidence,1002,1003)
        for name, uid, gid, mode in [('pipewire',1001,1003,0o770),('events',1002,1001,0o710),('public',1002,1003,0o710)]:
            p=ipc/name;p.mkdir(mode=mode);os.chown(p,uid,gid);p.chmod(mode)
        binds=('-v',str(ipc)+':/run/garage-audio','-v',str(evidence)+':/evidence')
        for module in ('audio_container','audio_child'):
            binds+=('-v',str(source/f'garage_audio/{module}.py')+f':/app/garage_audio/{module}.py:ro')
        worker=c.worker(binds,evidence)
        try:
            for _ in range(60):
                if (ipc/'public/control.sock').exists():break
                time.sleep(.1)
            def status():
                return json.loads(net.docker('exec','--user','1000:1003',worker,'python','-m','garage_audio.audio_container','--status'))
            assert status()['network_admitted'] and status()['state']=='off'
            ma_volumes=('-v',str(source/'tests/audio_ma_fixture.py')+':/fixtures/audio_ma_fixture.py:ro')
            if os.environ.get('GARAGE_TEST_MA_OVERLAY'):
                overlay=Path(os.environ['GARAGE_TEST_MA_OVERLAY'])
                manifest=json.loads((source/'vendor/ma-volume/manifest.json').read_text())
                import hashlib
                for name,hashes in manifest['runtime_files'].items():
                    file=overlay/name
                    assert hashlib.sha256(file.read_bytes()).hexdigest()==hashes['result']
                    ma_volumes+=('-v',str(file)+':/app/venv/lib/python3.14/site-packages/'+name+':ro')
            ma=c.add('music-assistant',['/app/venv/bin/python','/fixtures/audio_ma_fixture.py'],
                image=ma_image,volumes=ma_volumes,
                extra=('--user','1000:1000','--init','--tmpfs','/test-state:rw,uid=1000,gid=1000,size=96m',
                       '--tmpfs','/tmp:rw,uid=1000,gid=1000,size=64m','-e','GARAGE_FIXTURE_IP='+c.address))
            transport=c.add('transport',['sh','/app/audio/container-transport'],volumes=binds,
                extra=('--user','1001:1001','--group-add','1003','--init','--tmpfs','/tmp:rw,uid=1001,gid=1001,size=32m',
                       '-e','MA_SLIMPROTO_HOST=127.0.0.1'))
            network=c.management_network()
            app=c.app(network)
            def api(path, payload=None):
                command="import urllib.request,json; u='http://127.0.0.1:18088'+"+repr(path)+"; p="+repr(payload)
                command+="; r=urllib.request.urlopen(urllib.request.Request(u,data=json.dumps(p).encode() if p else None,headers={'Content-Type':'application/json'}),timeout=5); print(r.read().decode())"
                return json.loads(c.ns('python3','-c',command))
            for _ in range(100):
                try:
                    if api('/fixture/state')['available']:break
                except Exception:pass
                time.sleep(.2)
            else:raise AssertionError('actual MA/player not registered')
            if os.environ.get('GARAGE_TEST_MA_OVERLAY'):
                policy=api('/fixture/policy')
                assert 'spotify_authoritative' in policy['options'],policy
                assert policy['provider_created'] is False and policy['consent_supplied'] is False
                print('PASS patched_MA_modules_runtime_enum_public_unenrolled_entries_NO_PUBLIC_ENUM_CLAIM',policy,flush=True)
            alias=net.docker('exec',app,'python','-c',"import os,socket,urllib.request; assert os.environ['MA_URL']=='http://music-assistant:8095'; print(socket.gethostbyname('music-assistant')); assert urllib.request.urlopen('http://music-assistant:8095/info',timeout=2).status==200").strip()
            assert alias==c.address
            print('PASS fixed_music_assistant_alias_private_route_actual_MA_info',flush=True)
            assert c.ns('python3','-c',"import urllib.request; r=urllib.request.urlopen('http://"+c.address+":18088/synthetic.wav',timeout=3); assert r.read(4)==b'RIFF'; print(r.status)")=='200'
            seq=0;epoch='fresh-synthetic-'+c.generation
            receiver={'state':'playing','active':True,'logged_in':True,'volume':100}
            def event(kind,ticket=None):
                nonlocal seq
                seq+=1
                value=dict(receiver,kind=kind,epoch=epoch,sequence=seq)
                if ticket:value['ticket']=ticket
                return json.loads(net.docker('exec','--user','1001:1001',transport,'python','-c',
                    "import json;from pathlib import Path;from garage_audio.audio_container import request;print(json.dumps(request(Path('/run/garage-audio/events/event.sock'),"+repr(value)+")))"))
            event('baseline');event('heartbeat')
            assert status()['state']=='off' and not list(evidence.glob('*.f32'))
            if recovery:
                print('PASS new_generation_recovery_snapshot_OFF_no_autoplay',flush=True)
            api('/fixture/command',{'op':'play'})
            api('/fixture/command',{'op':'volume','value':100})  # isolated synthetic FILE only
            ticket=event('prepare')['pending_ticket'];event('delta',ticket)
            for _ in range(8):
                event('heartbeat');time.sleep(.35)
            live=status();assert live['state']=='playing' and len(live['owned_children'])==2
            files=list(evidence.glob('*.f32'));assert len(files)==1
            samples=array.array('f',files[0].read_bytes());assert len(samples)>48000*12
            peaks=[max(abs(x) for x in samples[i::12]) for i in range(12)]
            assert all(peaks[i]==0 for i in range(12) if i not in (2,3))
            assert abs(peaks[2]-.15295)<.012 and abs(peaks[3]-.076475)<.012, peaks
            print('PASS actual_MA_PipeWire_Camilla_FILE_network_generation_PCM',peaks,flush=True)
            if case=='stop-failure':
                real_run=net.subprocess.run
                def failed_stop(args,*a,**kw):
                    if args[:2]==['docker','stop']:
                        raise subprocess.CalledProcessError(1,args) # synthetic failure, NOT native denial bypass
                    return real_run(args,*a,**kw)
                with patch.object(net.subprocess,'run',failed_stop):
                    c.guard.kill();c.guard.wait(timeout=2)
                    for _ in range(80):
                        if (c.receipt/'guard-revoked.json').exists():break
                        time.sleep(.1)
                    receipt=json.loads((c.receipt/'guard-revoked.json').read_text())
                    assert receipt['software_children_closed'] is True and receipt['physical_silence_verified'] is False
                    assert not all(receipt['containers_stopped'].values())
                    off=status();assert off['state']=='off' and not off['owned_children'] and not off['network_admitted']
                    size=files[0].stat().st_size;time.sleep(.4);assert files[0].stat().st_size==size
                    print('PASS independent_worker_guard_closes_PCM_despite_synthetic_failed_container_stop_NO_PHYSICAL_CLAIM',flush=True)
            else:
                c.client.terminate();c.client.wait(timeout=3)
                for _ in range(80):
                    if (c.receipt/'guard-revoked.json').exists():break
                    time.sleep(.1)
                receipt=json.loads((c.receipt/'guard-revoked.json').read_text())
                assert receipt['software_children_closed'] is True
                assert list(receipt['containers_stopped'])[0]==worker
                assert all(receipt['containers_stopped'].values()) and receipt['physical_silence_verified'] is False
                ack=json.loads((evidence/'network-closed.json').read_text())
                assert ack['generation']==c.generation and ack['state']=='off' and not ack['owned_children']
                size=files[0].stat().st_size;time.sleep(.4);assert files[0].stat().st_size==size
                print('PASS real_network_loss_worker_first_OFF_children_closed_PCM_stable',flush=True)
        finally:
            for identity in c.dependents:
                try:(c.receipt/(identity[:12]+'.log')).write_text(net.docker('logs',identity))
                except Exception:pass
            c.close()
    for case in (sys.argv[3:] or ('lease-loss','recovery','stop-failure')):
        fixture(case,recovery=case=='recovery')
    print('COVERAGE actual_MA_Squeezelite_PipeWire_Camilla_FILE_DHCP; synthetic_WAV_receiver_stopfailure; no_Spotify_provider_PCM_DAC_sound')
    raise SystemExit(0)

root, receipt = sys.argv[1:]
ssh = ['ssh', '-o', 'BatchMode=yes', 'zach@192.168.68.74']
ready = subprocess.check_output(ssh+[f'for i in $(seq 1 80); do if sudo -n test -f {root}/lan-ready.json; '
    f'then sudo -n cat {root}/lan-ready.json; exit 0; fi; sleep .5; done; exit 1'],
    timeout=45, text=True)
value = json.loads(ready)
observed = []
for family, address in [(socket.AF_INET, value['address']), (socket.AF_INET6, '::ffff:'+value['address'])]:
    for port in value['ports']:
        sock = socket.socket(family);sock.settimeout(.8)
        try:
            sock.connect((address, port));reachable=True
        except OSError as error:
            reachable=False
        finally:
            sock.close()
        observed.append({'address': address, 'port': port, 'reachable': reachable})
assert not any(x['reachable'] for x in observed), observed
command = f'pid=$(docker inspect --format "{{{{.State.Pid}}}}" {value["carrier"]}); '
command += 'test "$pid" -gt 0; sudo -n nsenter -t "$pid" -n nft list chain inet garage_audio input'
rules = subprocess.check_output(ssh+[command], timeout=8, text=True)
import re
counters = re.findall(r'counter packets (\d+) bytes', rules)
assert any(int(x) >= 6 for x in counters), rules  # actual packets reached live namespace drop rule
Path(receipt).mkdir(mode=0o700, parents=True, exist_ok=False)
Path(receipt, 'lan-results.json').write_text(json.dumps({'ready': value, 'observed': observed}, indent=2))
Path(receipt, 'live-firewall.txt').write_text(rules)
subprocess.run(ssh+[f'sudo -n touch {root}/lan-checked'], timeout=8, check=True)
print('PASS real_Mac_LAN_and_IPv6_mapped_denied; live_nft_drop_counter', counters)
