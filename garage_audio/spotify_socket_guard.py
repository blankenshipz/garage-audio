"""Linux cgroup connect gate for scoped Soloist loopback control ports.

Only the exact observed MA TGID, UID and cgroup can create a control connection.
The native gateway's independent pidfd/starttime/image/namespace checks remain
mandatory for sound authority; this network gate is NOT human attestation.
Ports must be reserved before starting a receiver and retained until it exits.
Production attachment/activation is an installer decision, never automatic here.
"""
import ctypes,errno,os,select,socket,struct,threading

LIBC=ctypes.CDLL(None,use_errno=True)
SYS_BPF=321 # reviewed Linux amd64 target only

def syscall(command,buffer):
    result=LIBC.syscall(SYS_BPF,command,ctypes.byref(buffer),ctypes.sizeof(buffer))
    if result<0:raise OSError(ctypes.get_errno(),os.strerror(ctypes.get_errno()))
    return result

class MapCreate(ctypes.Structure):
    _fields_=[('map_type',ctypes.c_uint32),('key_size',ctypes.c_uint32),('value_size',ctypes.c_uint32),('max_entries',ctypes.c_uint32),('map_flags',ctypes.c_uint32)]
class MapElement(ctypes.Structure):
    _fields_=[('map_fd',ctypes.c_uint32),('padding',ctypes.c_uint32),('key',ctypes.c_uint64),('value',ctypes.c_uint64),('flags',ctypes.c_uint64)]
class Load(ctypes.Structure):
    _fields_=[('prog_type',ctypes.c_uint32),('insn_cnt',ctypes.c_uint32),('insns',ctypes.c_uint64),('license',ctypes.c_uint64),('log_level',ctypes.c_uint32),('log_size',ctypes.c_uint32),('log_buf',ctypes.c_uint64),('kern_version',ctypes.c_uint32),('prog_flags',ctypes.c_uint32),('prog_name',ctypes.c_char*16),('prog_ifindex',ctypes.c_uint32),('expected_attach_type',ctypes.c_uint32)]
class Attach(ctypes.Structure):
    _fields_=[('target_fd',ctypes.c_uint32),('attach_bpf_fd',ctypes.c_uint32),('attach_type',ctypes.c_uint32),('attach_flags',ctypes.c_uint32)]
class LinkCreate(ctypes.Structure):
    _fields_=[('prog_fd',ctypes.c_uint32),('target_fd',ctypes.c_uint32),('attach_type',ctypes.c_uint32),('flags',ctypes.c_uint32)]

class Assembly:
    def __init__(self):self.instructions=[];self.labels={};self.fixups=[]
    def emit(self,op,d=0,s=0,offset=0,imm=0):self.instructions.append([op,d|(s<<4),offset,imm])
    def label(self,name):self.labels[name]=len(self.instructions)
    def branch(self,op,d=0,s=0,imm=0,target='allow'):
        self.fixups.append((len(self.instructions),target));self.emit(op,d,s,imm=imm)
    def bytes(self):
        for index,target in self.fixups:self.instructions[index][2]=self.labels[target]-index-1
        return b''.join(struct.pack('<BBhi',*i) for i in self.instructions)

def program(map_fd,ipv6=False):
    a=Assembly();e=a.emit;b=a.branch
    e(0xbf,6,1) # preserve sockaddr context
    if ipv6:
        # Only IPv4-mapped ::ffff:127.0.0.1; receiver binds IPv4 loopback.
        for offset,value in ((8,0),(12,0),(16,-65536),(20,0x0100007f)):
            # Word compare: LDX_W zero-extends while a JMP64 negative immediate
            # sign-extends (the ::ffff word is 0xffff0000). Do not allow that
            # signedness mismatch to skip enforcement for IPv4-mapped IPv6.
            e(0x61,0,6,offset);b(0x56,0,imm=value)
    else:e(0x61,0,6,4);b(0x55,0,imm=0x0100007f)
    e(0x61,0,6,24);e(0x63,10,0,-4) # user_port network byte order
    e(0x18,1,1,imm=map_fd);e(0x00)
    e(0xbf,2,10);e(0x07,2,imm=-4);e(0x85,imm=1) # map lookup
    b(0x15,0,imm=0);e(0xbf,7,0)
    e(0x85,imm=14);e(0x77,0,imm=32);e(0x61,1,7,4);b(0x5d,0,1,target='deny') # TGID
    e(0x85,imm=15);e(0x67,0,imm=32);e(0x77,0,imm=32);e(0x61,1,7,0);b(0x5d,0,1,target='deny') # UID
    e(0x85,imm=80);e(0x79,1,7,8);b(0x5d,0,1,target='deny') # cgroup id
    a.label('allow');e(0xb7,0,imm=1);e(0x95)
    a.label('deny');e(0xb7,0,imm=0);e(0x95)
    return a.bytes()

class SocketGuard:
    def __init__(self,cgroup,verifier_directory):
        if os.geteuid()!=0:raise PermissionError('root-owned guarded socket installation required')
        if os.uname().machine!='x86_64':raise PermissionError('fixed Linux amd64 implementation required')
        self.cgroup=os.open(cgroup,os.O_RDONLY|os.O_DIRECTORY);self.programs=[]
        self.owners={};self.owner_lock=threading.Lock();self.closed=threading.Event()
        self.map=syscall(0,MapCreate(1,4,16,16,0))
        try:
            for attach_type,ipv6 in ((10,False),(11,True)):
                code=ctypes.create_string_buffer(program(self.map,ipv6));license=ctypes.create_string_buffer(b'GPL');log=ctypes.create_string_buffer(65536)
                attr=Load(prog_type=18,insn_cnt=(len(code.raw)-1)//8,insns=ctypes.addressof(code),license=ctypes.addressof(license),log_level=1,log_size=len(log),log_buf=ctypes.addressof(log),expected_attach_type=attach_type,prog_name=b'garage_spotify')
                try:fd=syscall(5,attr)
                finally:(verifier_directory/('connect'+str(attach_type)+'.log')).write_bytes(log.value)
                try:link=syscall(28,LinkCreate(fd,self.cgroup,attach_type,0))
                except BaseException:os.close(fd);raise
                # Kernel link lifetime follows this root process/fd. An abrupt
                # service exit cannot leave unreachable global cgroup rules.
                self.programs.append((fd,link))
        except BaseException:self.close();raise
        self.watcher=threading.Thread(target=self._watch_owners,daemon=True);self.watcher.start()
    def protect(self,port,*,pid,uid,cgroup_id):
        if type(port) is not int or not 1<=port<=65535 or min(pid,uid,cgroup_id)<=0:raise ValueError('exact nonroot observed process binding required')
        owner=os.pidfd_open(pid)
        try:
            with self.owner_lock:
                if self.closed.is_set() or port in self.owners:raise PermissionError('fresh protected port ownership required')
                if select.select([owner],[],[],0)[0]:raise PermissionError('registered owner already exited')
                self._write(port,uid,pid,cgroup_id)
                self.owners[port]=(owner,uid,cgroup_id);owner=None
        finally:
            if owner is not None:os.close(owner)
    def _write(self,port,uid,pid,cgroup_id):
        key=ctypes.c_uint32(socket.htons(port));value=ctypes.create_string_buffer(struct.pack('<IIQ',uid,pid,cgroup_id))
        syscall(2,MapElement(self.map,0,ctypes.addressof(key),ctypes.addressof(value),0))
    def _watch_owners(self):
        while not self.closed.wait(.02):
            with self.owner_lock:
                for port,(owner,uid,cgroup) in list(self.owners.items()):
                    if owner is not None and select.select([owner],[],[],0)[0]:
                        # Keep the port DENY-ALL after owner death. Removing the
                        # entry would expose a surviving receiver to any client.
                        # pidfd cannot silently admit a recycled numeric PID.
                        self._write(port,uid,0,cgroup)
                        os.close(owner);self.owners[port]=(None,uid,cgroup)
    def release(self,port):
        # Installer must prove receiver/listener exit before releasing a port.
        with self.owner_lock:
            key=ctypes.c_uint32(socket.htons(port));syscall(3,MapElement(self.map,0,ctypes.addressof(key),0,0))
            owner,_,_=self.owners.pop(port)
            if owner is not None:os.close(owner)
    def close(self):
        self.closed.set()
        if getattr(self,'watcher',None):self.watcher.join(timeout=2)
        for owner,_,_ in self.owners.values():
            if owner is not None:os.close(owner)
        self.owners.clear()
        for fd,link in reversed(self.programs):os.close(link);os.close(fd)
        self.programs=[]
        if getattr(self,'map',None) is not None:os.close(self.map);self.map=None
        if getattr(self,'cgroup',None) is not None:os.close(self.cgroup);self.cgroup=None
