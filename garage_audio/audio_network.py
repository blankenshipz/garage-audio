"""Narrow host-operator owned Docker namespace/DHCP bootstrap (not a web helper).

This implementation exposes management only, with all dynamic pairing ports closed.
Real receiver listener ownership qualification must precede production admission.
"""
import ipaddress
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import uuid
import threading
import fcntl
import stat


def docker(*args):
    return subprocess.check_output(['docker', *args], text=True, timeout=4)


def birth(pid):
    text = Path(f'/proc/{pid}/stat').read_text()
    fields = text[text.rfind(')')+2:].split()
    if fields[0] == 'Z':
        raise ProcessLookupError('owned process exited')
    return fields[19]


def revoke(record, reason):
    revoked_at = time.monotonic()
    root = Path(record['receipt'])
    atomic(root/'authority/revoked.json', {'generation': record['generation'], 'reason': reason}, mode=0o444)
    software_closed = None
    if record.get('worker_ack'):
        software_closed = False
        for _ in range(40):
            try:
                ack = json.loads(Path(record['worker_ack']).read_text())
                if ack['generation'] == record['generation'] and ack['state'] == 'off' and not ack['owned_children'] \
                        and ack.get('observed', 0) >= revoked_at:
                    software_closed = True
                    break
            except (OSError, ValueError, KeyError):
                pass
            time.sleep(.05)
    stopped = {}
    for identity in record['dependents'] + [record['carrier']]:
        if identity == record['carrier']:
            for key, sig in [('dhcp', signal.SIGTERM), ('capture', signal.SIGINT)]:
                try:
                    if key in record and birth(record[key]['pid']) == record[key]['birth']:
                        os.kill(record[key]['pid'], sig)
                        if key == 'dhcp':
                            time.sleep(.2)
                except (ProcessLookupError, FileNotFoundError):
                    pass
        try:
            value = json.loads(docker('inspect', identity))[0]
            if value['Id'] != identity or value['Config']['Labels'].get('garage.audio.generation') != record['generation']:
                raise RuntimeError('owned container identity mismatch')
            subprocess.run(['docker', 'stop', '-t', '0', identity], check=True, timeout=5,
                           stdout=subprocess.DEVNULL)
            stopped[identity] = not json.loads(docker('inspect', identity))[0]['State']['Running']
        except Exception as error:
            reason += '; stop-error:'+type(error).__name__
            stopped[identity] = False
    value = json.dumps({'reason': reason, 'time': time.monotonic(), 'generation': record['generation'],
                        'software_children_closed': software_closed, 'containers_stopped': stopped,
                        'physical_silence_verified': False})
    with (root/'revocations.jsonl').open('a') as out:
        out.write(value+'\n')
    (root/'guard-revoked.json').write_text(value)


def guard_main(registry):
    registry = Path(registry)
    if os.geteuid() != 0 or registry.lstat().st_mode & 0o077:
        raise PermissionError('private root-operated network registry required')
    record = json.loads(registry.read_text())
    while True:
        try:
            record = json.loads(registry.read_text())
            for item in ('owner', 'dhcp'):
                if birth(record[item]['pid']) != record[item]['birth']:
                    raise RuntimeError(item+'_lost')
            if record.get('receiver') and birth(record['receiver']['pid'])!=record['receiver']['birth']:
                raise RuntimeError('receiver_listener_owner_lost')
            lease = json.loads(Path(record['receipt'], 'lease.json').read_text())
            if lease['event'] not in ('bound', 'renew') or lease['ip'] != record['address']:
                raise RuntimeError('lease_lost_or_address_changed')
            if time.monotonic() >= lease['received']+lease['seconds']:
                raise RuntimeError('lease_expired')
            carrier = json.loads(docker('inspect', record['carrier']))[0]
            pid = carrier['State']['Pid']
            if carrier['Config']['Labels'].get('garage.audio.generation') != record['generation'] \
                    or not carrier['State']['Running'] or pid != record['carrier_process']['pid'] \
                    or birth(pid) != record['carrier_process']['birth'] \
                    or os.stat(f'/proc/{pid}/ns/net').st_ino != record['inode']:
                raise RuntimeError('namespace_generation_changed')
            addresses = json.loads(subprocess.check_output(['nsenter', '-t', str(pid), '-n', 'ip', '-j',
                '-4', 'addr', 'show', 'dev', 'ga-lan'], text=True, timeout=2))
            if [a['local'] for x in addresses for a in x['addr_info']] != [record['address']]:
                raise RuntimeError('address_lost')
            workload_crash=None
            for identity, expected in record.get('dependent_processes', {}).items():
                dependent = json.loads(docker('inspect', identity))[0]
                from .audio_recovery import check_dependent,WorkloadCrash
                try:check_dependent(dependent,identity,expected,record['generation'])
                except WorkloadCrash as error:
                    workload_crash=error
                    continue
                child = dependent['State']['Pid']
                if (not dependent['State']['Running'] or child != expected['pid']
                        or birth(child) != expected['birth']
                        or os.stat(f'/proc/{child}/ns/net').st_ino != expected['inode']):
                    raise RuntimeError('dependent_generation_changed')
            if workload_crash:raise workload_crash
        except Exception as error:
            from .audio_recovery import WorkloadCrash
            # Preserve the initiating classification BEFORE revoke stops siblings.
            # Existing revocation (including deliberate stop) is never recoverable.
            kind='owned_workload_crash' if isinstance(error,WorkloadCrash) and not (Path(record['receipt'])/'authority/revoked.json').exists() else 'terminal'
            atomic(Path(record['receipt'])/'guard-failure.json',{'kind':kind,'time':time.monotonic()})
            revoke(record, str(error) if isinstance(error, RuntimeError) else type(error).__name__)
            return
        atomic(Path(record['receipt'])/'guard-heartbeat.json', {'time': time.monotonic()})
        if (Path(record['receipt'])/'authority/revoked.json').exists():
            revoke(record, 'generation_already_revoked')
            return
        atomic(Path(record['receipt'])/'authority/authority.json', {'generation': record['generation'],
               'healthy': True, 'tick': time.monotonic(), 'expires': lease['received']+lease['seconds']}, mode=0o444)
        time.sleep(.2)

IMAGE = 'sha256:ece60bd70b5dbfe21d20254988c25485d9b1229d24c44955e9ab52b663865d7a'
MAC = '02:ad:ed:29:02:01'


def run(*args):
    return subprocess.check_output(args, text=True, timeout=8).strip()


def atomic(path, value, mode=0o600):
    temporary = path.with_name(path.name+'.next-'+uuid.uuid4().hex)
    temporary.write_text(json.dumps(value))
    temporary.chmod(mode)
    os.replace(temporary, path)


class Carrier:
    def __init__(self, receipt):
        if os.geteuid() != 0:
            raise PermissionError('trusted host administrator only')
        self.lock = os.open('/run/garage-audio-carrier.lock', os.O_CREAT | os.O_RDWR | os.O_CLOEXEC | os.O_NOFOLLOW, 0o600)
        info = os.fstat(self.lock)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or stat.S_IMODE(info.st_mode) != 0o600:
            os.close(self.lock)
            raise PermissionError('root-owned private carrier lock required')
        fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        self.receipt = Path(receipt).resolve()
        self.receipt.mkdir(mode=0o700, parents=True, exist_ok=False)
        (self.receipt/'authority').mkdir(mode=0o755)
        self.generation = uuid.uuid4().hex
        self.name = 'gaudio-net-'+self.generation[:10]
        self.dependents = []
        self.guard = self.client = self.capture = None
        self.id = self.peer = None
        self.registry = self.receipt/'registry.json'
        self.closed = False
        self.worker_ack = None
        self.dependent_processes = {}

    def ns(self, *args):
        return run('nsenter', '-t', str(self.pid), '-n', *args)

    def own(self, suffix, entry, network='none', volumes=(), image=IMAGE, extra=()):
        identity = docker('create', '--name', self.name+'-'+suffix, '--label',
            'garage.audio.generation='+self.generation, '--network', network,
            '--read-only', '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges',
            '--pids-limit', '96', '--entrypoint', entry[0], *volumes, *extra, image, *entry[1:]).strip()
        return identity

    def start(self, hook):
        hook=Path(hook).resolve()
        if not hook.is_file() or not os.access(hook,os.X_OK):
            raise PermissionError('executable immutable DHCP hook required before namespace creation')
        self.id = self.own('carrier', ['sleep', 'infinity'])
        docker('start', self.id)
        value = json.loads(docker('inspect', self.id))[0]
        self.pid = value['State']['Pid']
        self.inode = os.stat(f'/proc/{self.pid}/ns/net').st_ino
        links = json.loads(run('ip', '-j', 'link'))
        if any(x.get('address') == MAC for x in links):
            raise RuntimeError('dedicated carrier MAC already present')
        # Parent links do not list interfaces inside other Docker namespaces.
        for identity in docker('ps', '-q').split():
            pid = int(docker('inspect', '--format', '{{.State.Pid}}', identity).strip())
            other = json.loads(run('nsenter', '-t', str(pid), '-n', 'ip', '-j', 'link'))
            if any(x.get('address') == MAC for x in other):
                raise RuntimeError('dedicated carrier MAC already used in a Docker namespace')
        run('ip', 'link', 'add', 'link', 'enp2s0', 'name', 'ga-lan', 'netns', str(self.pid),
            'address', MAC, 'type', 'macvlan', 'mode', 'bridge')
        self.ns('sysctl', '-q', '-w', 'net.ipv6.conf.all.disable_ipv6=1')
        self.ns('sysctl', '-q', '-w', 'net.ipv6.conf.default.disable_ipv6=1')
        # Atomic namespace-only firewall installed BEFORE DHCP or application start.
        rules = '''table inet garage_audio {
          set management { type ipv4_addr; }
          set local_address { type ipv4_addr; }
          set self_stream { type inet_service; elements = { 8097 }; }
          set pairing { type inet_service; }
          chain input { type filter hook input priority 0; policy drop;
            meta nfproto ipv6 drop
            iifname != "lo" ip saddr 127.0.0.0/8 counter drop
            iifname != "lo" ip daddr 127.0.0.0/8 counter drop
            iifname "lo" ip saddr 127.0.0.1 ip daddr 127.0.0.1 accept
            iifname "lo" ip saddr @local_address ip daddr @local_address tcp dport @self_stream accept
            iifname "lo" ip saddr @local_address ip daddr @local_address tcp sport @self_stream ct state established accept
            iifname "lo" counter drop
            iifname "ga-lan" tcp dport { 8095, 1234, 1235 } counter drop
            iifname "ga-lan" ip daddr @local_address tcp dport @pairing accept
            iifname "ga-mgmt" ip saddr @management ip daddr @local_address tcp dport 8095 accept
            iifname "ga-mgmt" counter drop
            iifname "ga-lan" ip saddr { 0.0.0.0, 224.0.0.0/4 } counter drop
            iifname "ga-lan" udp sport 67 udp dport 68 accept
            iifname "ga-lan" udp dport 5353 ip daddr 224.0.0.251 accept
            iifname "ga-lan" ip daddr @local_address ct state established,related accept
          }
          chain forward { type filter hook forward priority 0; policy drop; }
        }'''
        subprocess.run(['nsenter', '-t', str(self.pid), '-n', 'nft', '-f', '-'],
                       input=rules, text=True, check=True, timeout=3)
        self.ns('ip', 'link', 'set', 'lo', 'up')
        self.ns('ip', 'link', 'set', 'ga-lan', 'up')
        self.capture = subprocess.Popen(['nsenter', '-t', str(self.pid), '-n', 'tcpdump',
            '-i', 'ga-lan', '-n', '-e', '-vvv', '-l',
            '(udp port 67 or udp port 68) and ether host '+MAC],
            stdout=(self.receipt/'dhcp-packets.txt').open('w'),
            stderr=(self.receipt/'capture.stderr').open('w'))
        time.sleep(.2)
        env = dict(os.environ, GARAGE_LEASE_RECEIPT=str(self.receipt))
        self.client = subprocess.Popen(['nsenter', '-t', str(self.pid), '-n', 'busybox',
            'udhcpc', '-f', '-n', '-R', '-a', '-i', 'ga-lan', '-t', '3', '-T', '4',
            '-s', str(Path(hook).resolve()), '-x', 'hostname:'+self.name], env=env,
            stdout=(self.receipt/'dhcp.stdout').open('w'), stderr=(self.receipt/'dhcp.stderr').open('w'))
        for _ in range(80):
            if self.client.poll() is not None:
                raise RuntimeError('DHCP helper exited')
            path = self.receipt/'lease.json'
            if path.exists():
                lease = json.loads(path.read_text())
                if lease['event'] == 'bound':
                    break
            time.sleep(.2)
        else:
            raise RuntimeError('bounded DHCP admission timed out')
        self.address = str(ipaddress.IPv4Address(lease['ip']))
        subnet = ipaddress.IPv4Network('0.0.0.0/'+lease['mask']).prefixlen
        self.ns('ip', 'addr', 'add', self.address+'/'+str(subnet), 'dev', 'ga-lan')
        self.ns('ip', 'route', 'add', 'default', 'via', str(ipaddress.IPv4Address(lease['gateway'])), 'dev', 'ga-lan')
        # MA's supported stream URL uses the sole LAN address even for its own transport.
        # Admit ONLY that fixed MA stream service on local input, not receiver WS aliases.
        self.ns('nft', 'add', 'element', 'inet', 'garage_audio', 'local_address', '{', self.address, '}')
        self.write_registry()
        self.guard = subprocess.Popen([sys.executable, '-m', 'garage_audio.audio_network', '--guard', str(self.registry)],
            stderr=(self.receipt/'guard.stderr').open('w'), pass_fds=(self.lock,))
        def guard_guard():
            begun = time.monotonic()
            while not self.closed:
                heartbeat = self.receipt/'guard-heartbeat.json'
                try:
                    last = json.loads(heartbeat.read_text())['time'] if heartbeat.exists() else begun
                except (ValueError, OSError):
                    last = begun
                if self.guard.poll() is not None or time.monotonic()-last > 2:
                    revoke(json.loads(self.registry.read_text()), 'independent_guard_lost')
                    return
                time.sleep(.2)
        threading.Thread(target=guard_guard, daemon=True).start()
        return self

    def write_registry(self):
        self.record = {'generation': self.generation, 'carrier': self.id, 'dependents': self.dependents,
            'carrier_process': {'pid': self.pid, 'birth': birth(self.pid)},
            'inode': self.inode, 'address': self.address, 'receipt': str(self.receipt),
            'owner': {'pid': os.getpid(), 'birth': birth(os.getpid())},
            'capture': {'pid': self.capture.pid, 'birth': birth(self.capture.pid)},
            'dhcp': {'pid': self.client.pid, 'birth': birth(self.client.pid)}}
        self.record['worker_ack'] = self.worker_ack
        self.record['dependent_processes'] = self.dependent_processes
        self.record['receiver'] = getattr(self,'receiver_process',None)
        atomic(self.registry, self.record)

    def register(self, identity, worker_ack=None):
        """Admit a CREATED, stopped frozen role before its first process exists."""
        self.healthy()
        value=json.loads(docker('inspect',identity))[0]
        if value['Id']!=identity or value['State']['Running'] or value['Config']['Labels'].get('garage.audio.generation')!=self.generation:
            raise PermissionError('stopped exact generation dependent required')
        if identity in self.dependents:raise ValueError('dependent already registered')
        if worker_ack is not None:
            if self.worker_ack is not None:raise ValueError('one worker acknowledgement owner')
            self.worker_ack=str(worker_ack);self.dependents.insert(0,identity)
        else:self.dependents.append(identity)
        self.write_registry()

    def healthy(self):
        if (self.closed or self.guard is None or self.guard.poll() is not None
                or (self.receipt/'authority/revoked.json').exists()
                or (self.receipt/'guard-revoked.json').exists()):
            raise RuntimeError('network generation permanently revoked')

    def start_registered(self, identity, shared_namespace=False):
        self.healthy()
        if identity not in self.dependents:raise PermissionError('register before start')
        docker('start',identity)
        try:
            self.healthy()
            value=json.loads(docker('inspect',identity))[0];pid=value['State']['Pid']
            inode=os.stat(f'/proc/{pid}/ns/net').st_ino
            if not value['State']['Running'] or (shared_namespace and inode!=self.inode):
                raise RuntimeError('dependent namespace/start mismatch')
            self.dependent_processes[identity]={'pid':pid,'birth':birth(pid),'inode':inode}
            self.write_registry();self.healthy()
        except BaseException:
            revoke(self.record,'registered_start_failed');raise
        return identity

    def add(self, suffix, command, volumes=(), image=IMAGE, extra=()):
        if self.guard.poll() is not None or (self.receipt/'guard-revoked.json').exists():
            raise RuntimeError('lease guardian unavailable; no dependent start')
        identity = self.own(suffix, command, network='container:'+self.id, volumes=volumes, image=image, extra=extra)
        self.dependents.append(identity)
        self.write_registry()  # register BEFORE start, so guardian owns even a racing start
        docker('start', identity)
        if self.guard.poll() is not None or (self.receipt/'guard-revoked.json').exists():
            revoke(json.loads(self.registry.read_text()), 'dependent_start_revoked')
            raise RuntimeError('dependent start revoked')
        value = json.loads(docker('inspect', identity))[0]
        if os.stat(f"/proc/{value['State']['Pid']}/ns/net").st_ino != self.inode:
            revoke(self.record, 'dependent_namespace_mismatch')
            raise RuntimeError('dependent joined wrong namespace')
        return identity

    def worker(self, volumes, evidence):
        if self.guard.poll() is not None or (self.receipt/'authority/revoked.json').exists():
            raise RuntimeError('no worker on revoked generation')
        identity = self.own('audio-device', ['python', '-m', 'garage_audio.audio_container'],
            volumes=(*volumes, '-v', str(self.receipt/'authority')+':/network:ro'),
            extra=('--user', '1002:1003', '--group-add', '1001', '--tmpfs', '/tmp:rw,size=16m',
                '-e', 'GARAGE_AUDIO_NETWORK_LEASE=/network/authority.json',
                '-e', 'GARAGE_AUDIO_NETWORK_GENERATION='+self.generation,
                '-e', 'PIPEWIRE_RUNTIME_DIR=/run/garage-audio/pipewire',
                '-e', 'PIPEWIRE_REMOTE=garage-pipewire'))
        self.dependents.insert(0, identity)
        self.worker_ack = str(Path(evidence)/'network-closed.json')
        self.write_registry()
        docker('start', identity)
        if self.guard.poll() is not None or (self.receipt/'authority/revoked.json').exists():
            revoke(self.record, 'worker_start_revoked')
            raise RuntimeError('worker start revoked')
        return identity

    def app(self, network, volumes=(), image=IMAGE, entry=('sleep', 'infinity')):
        # Immutable alias injected by trusted operator, never caller URL/IP or LAN fallback.
        if self.guard.poll() is not None or (self.receipt/'authority/revoked.json').exists():
            raise RuntimeError('no app on revoked generation')
        identity = self.own('dashboard', entry, network=network, volumes=volumes, image=image,
            extra=('--add-host', 'music-assistant:'+self.address,
                   '-e', 'MA_URL=http://music-assistant:8095'))
        self.dependents.append(identity);self.write_registry();docker('start', identity)
        self.management(network, identity)
        pid = json.loads(docker('inspect', identity))[0]['State']['Pid']
        run('nsenter', '-t', str(pid), '-n', 'ip', 'route', 'add', self.address+'/32', 'dev', 'eth0')
        if self.guard.poll() is not None or (self.receipt/'authority/revoked.json').exists():
            revoke(self.record, 'app_generation_revoked')
            raise RuntimeError('app generation revoked')
        return identity

    def management_network(self):
        # Retained receipts/stopped networks can exhaust Docker's default /16 pools.
        # Choose an unused tiny private subnet, checking host routes and Docker IPAM.
        occupied=[]
        for route in json.loads(run('ip','-j','route','show','table','all')):
            dst=route.get('dst')
            if dst and dst != 'default':
                try:occupied.append(ipaddress.ip_network(dst,strict=False))
                except ValueError:pass
        identities=docker('network','ls','-q').split()
        if identities:
            for network in json.loads(docker('network','inspect',*identities)):
                for config in network.get('IPAM',{}).get('Config') or []:
                    if config.get('Subnet'):occupied.append(ipaddress.ip_network(config['Subnet']))
        for subnet in ipaddress.ip_network('10.203.0.0/16').subnets(new_prefix=29):
            if not any(subnet.version==x.version and subnet.overlaps(x) for x in occupied):
                return docker('network','create','--internal','--subnet',str(subnet),'--label',
                              'garage.audio.generation='+self.generation,self.name+'-management').strip()
        raise RuntimeError('no unused owned management subnet')

    def management(self, network_identity, client_identity):
        # Resolve only exact same-generation owned resources, never a caller's arbitrary bridge.
        network = json.loads(docker('network', 'inspect', network_identity))[0]
        client = json.loads(docker('inspect', client_identity))[0]
        if network['Id'] != network_identity or network['Labels'].get('garage.audio.generation') != self.generation \
                or client['Id'] != client_identity or client['Config']['Labels'].get('garage.audio.generation') != self.generation:
            raise PermissionError('exact owned management resources required')
        bridge = network['Options'].get('com.docker.network.bridge.name', 'br-'+network_identity[:12])
        subnet = network['IPAM']['Config'][0]['Subnet']
        client_address = client['NetworkSettings']['Networks'][network['Name']]['IPAddress']
        self.peer = 'gam'+self.generation[:10]
        run('ip', 'link', 'add', self.peer, 'type', 'veth', 'peer', 'name', 'ga-mgmt', 'netns', str(self.pid))
        run('ip', 'link', 'set', self.peer, 'alias', self.generation)
        run('ip', 'link', 'set', self.peer, 'master', bridge)
        run('ip', 'link', 'set', self.peer, 'up')
        self.ns('ip', 'link', 'set', 'ga-mgmt', 'up')
        self.ns('ip', 'route', 'add', str(ipaddress.IPv4Network(subnet)), 'dev', 'ga-mgmt')
        self.ns('sysctl', '-q', '-w', 'net.ipv4.conf.ga-mgmt.arp_ignore=0')
        self.ns('nft', 'add', 'element', 'inet', 'garage_audio', 'management',
                '{', str(ipaddress.IPv4Address(client_address)), '}')

    def close(self):
        if getattr(self, '_cleanup_complete', False):
            return
        # Stop the independent watchdog, but allow another close to retry failed
        # cleanup. No helper failure may skip revocation of the owned generation.
        self.closed = True
        errors = []
        def attempt(action):
            try:
                action()
            except Exception as error:
                errors.append(error)
        def stop_process(process, sig, timeout):
            if process and process.poll() is None:
                process.send_signal(sig)
                try:
                    process.wait(timeout=timeout)
                except subprocess.TimeoutExpired as error:
                    # Popen is an owned child, not a PID rediscovered by name.
                    try:
                        process.kill()
                        process.wait(timeout=timeout)
                    except Exception:
                        pass  # retain the initiating timeout; replacement stays forbidden
                    raise error
        attempt(lambda: stop_process(self.client, signal.SIGTERM, 4))
        attempt(lambda: stop_process(self.guard, signal.SIGTERM, 3))
        attempt(lambda: stop_process(self.capture, signal.SIGINT, 3))
        if self.id:
            record = getattr(self, 'record', {'generation': self.generation, 'carrier': self.id,
                'dependents': self.dependents, 'receipt': str(self.receipt)})
            attempt(lambda: revoke(record, 'operator_stop'))
        def close_peer():
            if self.peer:
                links = json.loads(run('ip', '-j', 'link'))
                if any(x['ifname'] == self.peer and x.get('ifalias') == self.generation for x in links):
                    run('ip', 'link', 'del', self.peer)
        attempt(close_peer)
        if self.lock is not None:
            lock, self.lock = self.lock, None
            attempt(lambda: os.close(lock))
        if errors:
            raise errors[0]  # cleanup failure remains terminal to the supervisor
        self._cleanup_complete = True


if __name__ == '__main__':
    if sys.argv[1:2] != ['--guard'] or len(sys.argv) != 3:
        raise SystemExit('Only private host --guard registry mode; no public helper/API')
    guard_main(sys.argv[2])
