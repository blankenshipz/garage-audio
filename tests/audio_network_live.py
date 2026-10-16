"""Host root executable fixture. No production state/devices; retains own stopped objects."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

# Listener role must work in the cached unprivileged image without host bootstrap imports.
if '--listener' in sys.argv:
    import socket
    import threading
    def serve(port, family):
        sock = socket.socket(family)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        if family == socket.AF_INET6:
            sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
        sock.bind(('::' if family == socket.AF_INET6 else '0.0.0.0', port))
        sock.listen()
        while True:
            client, _ = sock.accept()
            client.sendall(b'owned-network-fixture')
            client.close()
    for port, family in [(8095, socket.AF_INET), (1234, socket.AF_INET), (1235, socket.AF_INET6)]:
        threading.Thread(target=serve, args=(port, family), daemon=True).start()
    while True:
        time.sleep(1)

from garage_audio.audio_network import Carrier, atomic, docker, run, birth

base = Path(sys.argv[1]).resolve()
source = Path(__file__).resolve().parents[1]

if '--victim' in sys.argv:
    c = Carrier(base/'victim')
    c.start(source/'scripts/bootstrap-audio-network.py')
    dependent = c.add('dependent', ['sleep', 'infinity'])
    atomic(base/'victim-ready.json', {'registry': c.record, 'dependent': dependent})
    while True:
        signal.pause()

base.mkdir(mode=0o700, parents=True, exist_ok=False)
if '--owner' in sys.argv:
    victim = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), str(base), '--victim'])
    try:
        for _ in range(100):
            if (base/'victim-ready.json').exists():
                break
            if victim.poll() is not None:
                raise RuntimeError('owned victim exited before admission')
            time.sleep(.2)
        else:
            raise RuntimeError('owned victim admission timeout')
        ready = json.loads((base/'victim-ready.json').read_text())
        record = ready['registry']
        victim.kill();victim.wait(timeout=2)
        for _ in range(80):
            ended = all(not json.loads(docker('inspect', identity))[0]['State']['Running']
                        for identity in (record['carrier'], ready['dependent']))
            if ended and (base/'victim/guard-revoked.json').exists():
                break
            time.sleep(.2)
        else:
            raise AssertionError('independent guard did not revoke ownerless namespace')
        for key in ('dhcp', 'capture'):
            for _ in range(20):
                try:
                    same = birth(record[key]['pid']) == record[key]['birth']
                except (FileNotFoundError, ProcessLookupError):
                    same = False
                if not same:
                    break
                time.sleep(.1)
            assert not same, key+' orphaned'
        print('PASS actual_owner_SIGKILL_independent_stop_and_helper_retirement', flush=True)
        time.sleep(.2)
        fresh = Carrier(base/'replacement')
        try:
            fresh.start(source/'scripts/bootstrap-audio-network.py')
            # Linux can reuse retired namespace inodes; identity includes ID, generation and PID birth.
            assert fresh.id != record['carrier'] and fresh.generation != record['generation']
            assert os.stat(f'/proc/{fresh.pid}/ns/net').st_ino == fresh.inode
            dependent = fresh.add('fresh-join', ['sleep', 'infinity'])
            info = json.loads(docker('inspect', dependent))[0]
            assert os.stat(f"/proc/{info['State']['Pid']}/ns/net").st_ino == fresh.inode
            print('PASS new_registered_generation_fresh_rejoin', flush=True)
        finally:
            fresh.close()
    finally:
        if victim.poll() is None:
            victim.terminate();victim.wait(timeout=3)
    raise SystemExit(0)
before = run('ip', '-j', 'route')
probe_script = '''import socket,sys
s=socket.socket(socket.AF_INET6 if ':' in sys.argv[1] else socket.AF_INET);s.settimeout(.8)
try:
 s.connect((sys.argv[1],int(sys.argv[2])));data=s.recv(64);success=data==b'owned-network-fixture'
except OSError: success=False
assert success==(sys.argv[3]=='allow'),(sys.argv[1:3],success)
'''


def connect(args, address, port, allow):
    run(*args, 'python3', '-c', probe_script, address, str(port), 'allow' if allow else 'deny')


def stopped(identity):
    return not json.loads(docker('inspect', identity))[0]['State']['Running']


def wait_stop(c, probe):
    for _ in range(60):
        if stopped(probe) and stopped(c.id):
            return
        time.sleep(.2)
    raise AssertionError('dependent/carrier not stopped after revoke')


def one(case):
    c = Carrier(base/case)
    network = None
    try:
        c.start(source/'scripts/bootstrap-audio-network.py')
        network = c.management_network()
        info = json.loads(docker('network', 'inspect', network))[0]
        client = c.own('client', ['sleep', 'infinity'], network=network)
        c.dependents.append(client);c.write_registry();docker('start', client)
        client_info = json.loads(docker('inspect', client))[0]
        peer = client_info['NetworkSettings']['Networks'][c.name+'-management']['IPAddress']
        client_pid = client_info['State']['Pid']
        bridge = info['Options'].get('com.docker.network.bridge.name', 'br-'+network[:12])
        c.management(network, client)
        run('nsenter', '-t', str(client_pid), '-n', 'ip', 'route', 'add', c.address+'/32', 'dev', 'eth0')
        probe = c.add('wildcard', ['python', '/probe.py', '--listener'],
                      volumes=('-v', str(source/'tests/audio_network_live.py')+':/probe.py:ro'))
        time.sleep(.5)
        ns = ['nsenter', '-t', str(c.pid), '-n']
        connect(ns, '127.0.0.1', 1234, True)
        connect(ns, '127.0.0.2', 1234, False)
        connect(ns, '::ffff:127.0.0.2', 1234, False)
        connect(['docker', 'exec', client], c.address, 8095, True)
        connect(['docker', 'exec', client], c.address, 1234, False)
        connect(['docker', 'exec', client], '::ffff:'+c.address, 1235, False)
        # Only the LAN interface owns an address; private management veth stays unnumbered.
        assert json.loads(c.ns('ip', '-j', 'addr', 'show', 'dev', 'ga-mgmt'))[0]['addr_info'] == []
        (c.receipt/'links.json').write_text(c.ns('ip', '-j', '-d', 'link'))
        (c.receipt/'routes.json').write_text(c.ns('ip', '-j', 'route'))
        (c.receipt/'firewall.txt').write_text(c.ns('nft', 'list', 'ruleset'))
        (c.receipt/'listeners.txt').write_text(c.ns('ss', '-lntp'))
        # Real DHCP renewal, not synthetic fixture heartbeat.
        old = json.loads((c.receipt/'lease.json').read_text())
        os.kill(c.client.pid, signal.SIGUSR1)
        for _ in range(70):
            renewed = json.loads((c.receipt/'lease.json').read_text())
            if renewed['event'] == 'renew' and renewed['received'] > old['received']:
                break
            time.sleep(.2)
        else:
            raise AssertionError('actual same-address renewal not observed')
        assert renewed['ip'] == c.address and not stopped(probe) and c.guard.poll() is None
        connect(['docker', 'exec', client], c.address, 8095, True)
        print('PASS real_DHCP_same_address_renew_preserves_running_namespace', case, flush=True)
        if case == 'expiry':
            atomic(base/'lan-ready.json', {'address': c.address, 'ports': [8095, 1234, 1235],
                   'carrier': c.id, 'inode': c.inode, 'receipt': str(c.receipt)})
            for _ in range(120):
                if (base/'lan-checked').exists():
                    break
                time.sleep(.5)
            else:
                raise AssertionError('external physical LAN check not supplied')
            changed = dict(renewed, received=time.monotonic()-renewed['seconds']-1)
            atomic(c.receipt/'lease.json', changed)  # labeled synthetic expiry, real guard action
        elif case == 'helper-death':
            c.client.terminate();c.client.wait(timeout=3)
        elif case == 'guard-death':
            c.guard.kill();c.guard.wait(timeout=3)
        elif case == 'address-loss':
            c.ns('ip', '-4', 'addr', 'flush', 'dev', 'ga-lan')
        elif case == 'namespace-restart':
            docker('restart', '-t', '0', c.id)
        wait_stop(c, probe)
        revocations = (c.receipt/'revocations.jsonl').read_text()
        print('PASS real_dependent_stop_on_'+case, revocations.splitlines()[0], flush=True)
        try:
            c.add('stale-rejoin', ['sleep', 'infinity'])
        except RuntimeError:
            pass
        else:
            raise AssertionError('stale carrier admitted rejoin')
        print('PASS old_generation_rejoin_denied', case, flush=True)
    finally:
        c.close()
        if network:
            # Retain stopped containers/network as evidence; only own veth/DHCP effects removed.
            (c.receipt/'network-id.txt').write_text(network+'\n')


try:
    for case in (sys.argv[2:] or ['expiry', 'helper-death', 'guard-death', 'address-loss', 'namespace-restart']):
        one(case)
finally:
    (base/'host-route-before.json').write_text(before)
    (base/'host-route-after.json').write_text(run('ip', '-j', 'route'))
print('COVERAGE actual_Linux_Docker_DHCP_nft_routing_guard; synthetic_wildcard_apps_expiry; no_MA_Soloist_device_or_sound')
