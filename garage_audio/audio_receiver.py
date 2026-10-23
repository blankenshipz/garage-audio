"""Root projection of NONSECRET loopback endpoint after kernel listener ownership proof.

No receiver command, argv/key access, authentication or LAN port admission is performed.
"""
import hashlib,json,os,stat,subprocess,time,threading,socket,ipaddress
from pathlib import Path
from .audio_network import birth

DATA='data/spotify_connect/spotify_connect_02_00_00_00_21_01/soloist-data'

class ReceiverOwnershipError(PermissionError):
    """Only fixed nonsecret categories; retain the native fail-closed exception boundary."""
    def __init__(self,reason):
        if reason not in ('owner_absent','owner_ambiguous','owner_changed','listener_changed'):
            raise ValueError('unknown receiver ownership category')
        self.reason=reason
        super().__init__('receiver ownership observation failed: '+reason)

def owns_socket(proc,inode):
    # A receiver's unrelated transient FD may close between glob and readlink.
    # Skip that FD, NOT the whole process (which may still own the pinned listener).
    # iterdir propagates EACCES; glob may silently hide an unreadable FD table.
    for fd in (proc/'fd').iterdir():
        try:target=os.readlink(fd)
        except (FileNotFoundError,ProcessLookupError):continue
        if target=='socket:['+inode+']':return True
    return False

def listening_sockets(pid, namespace):
    """Kernel LISTEN instance identity; Linux tcp6 uses little-endian 32-bit words."""
    records=[]
    for family,name in ((socket.AF_INET,'tcp'),(socket.AF_INET6,'tcp6')):
        try:lines=Path(f'/proc/{pid}/net/{name}').read_text().splitlines()[1:]
        except FileNotFoundError:continue
        for line in lines:
            fields=line.split()
            if fields[3]!='0A':continue
            raw,port=fields[1].split(':')
            size=8 if family==socket.AF_INET6 else len(raw)
            packed=b''.join(bytes.fromhex(raw[i:i+size])[::-1] for i in range(0,len(raw),size))
            records.append({'family':'ipv6' if family==socket.AF_INET6 else 'ipv4',
                            'address':socket.inet_ntop(family,packed),'port':int(port,16),
                            'socket_inode':fields[9],'namespace_inode':namespace,'uid':int(fields[7])})
    return records

class ReceiverEndpoint:
    def __init__(self,binary_sha256):
        self.digest=binary_sha256;self.verified=None
    def observe(self,ma_pid,inode):
        root=Path(f'/proc/{ma_pid}/root')/DATA
        for name in ('ws.addr','ws.port'):
            info=(root/name).lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_size>128:raise PermissionError('bounded actual endpoint metadata required')
        if (root/'ws.addr').read_text().strip()!='127.0.0.1':raise PermissionError('actual receiver control must stay loopback')
        port=int((root/'ws.port').read_text());assert 1<=port<=65535
        listeners=[r for r in listening_sockets(ma_pid,inode)
                   if r['address']=='127.0.0.1' and r['port']==port and r['uid']==1000]
        sockets=[r['socket_inode'] for r in listeners]
        if not sockets:raise ReceiverOwnershipError('owner_absent')
        if len(sockets)!=1:raise PermissionError('one exact actual loopback listener required')
        owners=[]
        for proc in Path('/proc').glob('[0-9]*'):
            try:
                if proc.stat().st_uid!=1000:continue
                if not owns_socket(proc,sockets[0]):continue
                # Positive socket non-ownership excludes unrelated processes without
                # inspecting their namespaces. A holder's unreadable namespace is
                # still fatal: permission denial never proves non-ownership.
                if os.stat(proc/'ns/net').st_ino!=inode:continue
                executable=proc/'exe';info=executable.stat();key=(int(proc.name),birth(int(proc.name)),info.st_ino,info.st_mtime_ns,info.st_size)
                if self.verified!=key:
                    if info.st_size>100*1024*1024 or hashlib.sha256(executable.read_bytes()).hexdigest()!=self.digest:raise PermissionError('actual public receiver binary pin differs')
                    self.verified=key
                owners.append(key)
            except (FileNotFoundError,ProcessLookupError):continue
        if len(owners)!=1:raise ReceiverOwnershipError('owner_absent' if not owners else 'owner_ambiguous')
        proc=Path('/proc')/str(owners[0][0])
        if birth(owners[0][0])!=owners[0][1] or proc.stat().st_uid!=1000 or os.stat(proc/'ns/net').st_ino!=inode:
            raise ReceiverOwnershipError('owner_changed')
        if not owns_socket(proc,sockets[0]):raise ReceiverOwnershipError('owner_changed')
        current=[r for r in listening_sockets(ma_pid,inode)
                 if r['address']=='127.0.0.1' and r['port']==port and r['uid']==1000]
        if current!=listeners:raise ReceiverOwnershipError('listener_changed')
        return {'pid':owners[0][0],'birth':owners[0][1],'port':port,'binary_sha256':self.digest,'namespace_inode':inode,'listener':listeners[0]}
    def publish(self,authority,observation):
        root=Path(authority)/'receiver';root.mkdir(mode=0o755,exist_ok=True)
        for name,value in [('ws.addr','127.0.0.1'),('ws.port',str(observation['port']))]:
            temporary=root/(name+'.next');temporary.write_text(value);temporary.chmod(0o444);os.replace(temporary,root/name)

def advertised_pairing(container,address):
    """Actual public mDNS in that MA namespace; return only matching local service ports."""
    code='''import json,time
from zeroconf import Zeroconf,ServiceBrowser
ports=set()
class Listener:
 def add_service(self,z,t,n):
  v=z.get_service_info(t,n,timeout=500)
  if v and ADDRESS in v.parsed_addresses():ports.add(v.port)
 def update_service(self,z,t,n):self.add_service(z,t,n)
 def remove_service(self,z,t,n):pass
z=Zeroconf(interfaces=[ADDRESS]);browser=ServiceBrowser(z,"_spotify-connect._tcp.local.",Listener())
try:time.sleep(1);print(json.dumps(sorted(ports)))
finally:browser.cancel();z.close()
'''.replace('ADDRESS',repr(address))
    output=subprocess.check_output(['docker','exec',container,'/app/venv/bin/python','-c',code],text=True,timeout=4)
    value=json.loads(output)
    if not isinstance(value,list) or len(value)>4 or any(type(p) is not int or not 1<=p<=65535 for p in value):raise PermissionError('bounded actual pairing advertisement required')
    return value

def qualify_pairing(receiver,ma_pid,inode,address,advertised):
    return sorted({r['port'] for r in pairing_instances(receiver,ma_pid,inode,address,advertised)})

def pairing_instances(receiver,ma_pid,inode,address,advertised):
    """Only advertised TCP listeners owned by SAME exact verified receiver binary."""
    if receiver['namespace_inode']!=inode or birth(receiver['pid'])!=receiver['birth']:raise PermissionError('receiver owner displaced')
    proc=Path('/proc')/str(receiver['pid'])
    if proc.stat().st_uid!=1000 or os.stat(proc/'ns/net').st_ino!=inode:raise PermissionError('receiver identity/namespace changed')
    socket_inodes=set()
    for fd in (proc/'fd').glob('*'):
        try:value=os.readlink(fd)
        except FileNotFoundError:continue
        if value.startswith('socket:['):socket_inodes.add(value[8:-1])
    qualified=[]
    for value in listening_sockets(ma_pid,inode):
        host=ipaddress.ip_address(value['address'])
        matching=str(host)==address or host.is_unspecified or str(getattr(host,'ipv4_mapped',None))==address
        if (value['uid']==1000 and value['socket_inode'] in socket_inodes and matching
                and value['port'] in advertised and value['port'] not in (receiver['port'],8095,8097,1234,1235)):
            qualified.append(value)
    return sorted(qualified,key=lambda r:(r['port'],r['family'],r['address'],r['socket_inode']))

def verify_pairing_instances(receiver,ma_pid,inode,address,baseline):
    current=pairing_instances(receiver,ma_pid,inode,address,[r['port'] for r in baseline])
    if current!=baseline:raise PermissionError('pairing LISTEN socket instance replaced')
    return current


class AdvertisementCache:
    """Only actual SRV + matching address records, with their measured remaining TTL."""
    def __init__(self):
        self.records = {}

    def update(self, value, now=None):
        now = time.monotonic() if now is None else now
        if value['kind'] not in ('srv', 'address', 'remove'):
            raise ValueError('record kind')
        key = (value['kind'], value['id'])
        if value['kind'] == 'remove':
            self.records.pop(('srv', value['id']), None)
            return
        remaining = value['remaining_seconds']
        if type(remaining) not in (int, float) or not 0 <= remaining <= 2**32:
            raise ValueError('actual record lifetime required')
        # Emission time shares the host monotonic clock across Linux namespaces.
        expires = value['observed']+remaining
        if expires <= now:
            self.records.pop(key, None)
            return
        if len(self.records) >= 32 and key not in self.records:
            raise ValueError('bounded discovery cache')
        self.records[key] = dict(value, expires=expires)

    def ports(self, now=None):
        now = time.monotonic() if now is None else now
        self.records = {k:v for k,v in self.records.items() if v['expires']>now}
        hosts = {v['id'] for (kind,_),v in self.records.items() if kind == 'address'}
        return sorted({v['port'] for (kind,_),v in self.records.items()
                       if kind == 'srv' and v['host'] in hosts})


PAIRING_RECORD_COLLECTOR = '''
import hashlib,json,socket,time,threading,select,sys
from zeroconf import Zeroconf,ServiceBrowser,RecordUpdateListener,DNSService,DNSAddress
def identity(name):return hashlib.sha256(name.lower().encode()).hexdigest()
lock=threading.Lock()
def emit(value):
 with lock:print(json.dumps(value),flush=True)
class Records(RecordUpdateListener):
 def async_update_records(self,z,now,updates):
  for update in updates:
   r=update.new
   if isinstance(r,DNSService) and r.name.lower().endswith('._spotify-connect._tcp.local.'):
    emit({'kind':'srv','id':identity(r.name),'host':identity(r.server),'port':r.port,'remaining_seconds':max(0,(r.created+r.ttl*1000-now)/1000),'observed':time.monotonic()})
   elif isinstance(r,DNSAddress) and r.address==socket.inet_aton(ADDRESS):
    emit({'kind':'address','id':identity(r.name),'remaining_seconds':max(0,(r.created+r.ttl*1000-now)/1000),'observed':time.monotonic()})
 def async_update_records_complete(self):pass
class Services:
 def add_service(self,z,t,n):z.get_service_info(t,n,timeout=500)
 def update_service(self,z,t,n):self.add_service(z,t,n)
 def remove_service(self,z,t,n):emit({'kind':'remove','id':identity(n)})
z=Zeroconf(interfaces=[ADDRESS]);records=Records();z.add_listener(records,None)
browser=ServiceBrowser(z,'_spotify-connect._tcp.local.',Services())
try:
 # The operator owns stdin. EOF closes discovery; never orphan an exec on CLI exit.
 while True:
  if select.select([sys.stdin],[],[],.2)[0] and not sys.stdin.buffer.read(1):break
finally:browser.cancel();z.remove_listener(records);z.close()
'''


class PairingAdvertisements:
    """Persistent actual discovery; callers MUST continuously qualify every socket owner."""
    def __init__(self, container, address):
        self.cache, self.lock = AdvertisementCache(), threading.Lock()
        self.failed = False
        code = PAIRING_RECORD_COLLECTOR.replace('ADDRESS', repr(address))
        self.process = subprocess.Popen(['docker','exec','-i',container,'/app/venv/bin/python','-c',code],
                                        stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL)
        self.thread = threading.Thread(target=self.read, daemon=True)
        self.thread.start()

    def read(self):
        try:
            while True:
                line = self.process.stdout.readline(2049)
                if not line or len(line)>2048:
                    raise RuntimeError('bounded discovery stream lost')
                value = json.loads(line)
                if value.get('kind') == 'srv' and (type(value.get('port')) is not int or not 1<=value['port']<=65535):
                    raise ValueError('advertised port')
                with self.lock:self.cache.update(value)
        except Exception:
            self.failed = True

    def ports(self):
        with self.lock:
            return [] if self.failed or self.process.poll() is not None else self.cache.ports()

    def close(self):
        if self.process.poll() is None:
            self.process.stdin.close()
            try:self.process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.process.terminate()
                try:self.process.wait(timeout=2)
                except subprocess.TimeoutExpired:self.process.kill();self.process.wait(timeout=2)
        self.process.stdout.close()
        self.thread.join(timeout=2)
