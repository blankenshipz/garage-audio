"""Private lifecycle recovery: classified workload exits only, never authority renewal."""
import fcntl,json,os,stat,time,uuid
from pathlib import Path
from .audio_network import atomic,docker

class WorkloadCrash(RuntimeError):
    pass

def check_dependent(value,identity,expected,generation):
    """Called by the guardian before teardown; no post-stop exit-code inference."""
    if value['Id']!=identity or value['Config']['Labels'].get('garage.audio.generation')!=generation:
        raise PermissionError('dependent ownership mismatch')
    state=value['State']
    role=value['Config']['Labels'].get('com.docker.compose.service')
    # Receiver/MA, hardware worker and carrier loss remain security-terminal.
    # 0 and signal/kill exits are not classified as recoverable application crashes.
    if (not state['Running'] and state['Status']=='exited' and not state.get('OOMKilled')
            and role in ('dashboard','audio-observer','audio-transport')
            and 1<=state['ExitCode']<=125):
        raise WorkloadCrash('owned_workload_crash')

def recoverable(error,receipt):
    # Never let a nested native permission/ownership error be hidden by a wrapper.
    seen=set();current=error
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current,(PermissionError,OSError,ValueError)):return False
        current=current.__cause__ or current.__context__
    if str(error)!='network generation permanently revoked':return False
    try:value=json.loads((receipt/'guard-failure.json').read_text())
    except (OSError,ValueError):return False
    return value.get('kind')=='owned_workload_crash'

def verify_closed(carrier):
    """Cleanup must have completed before releasing a generation for replacement."""
    for identity in [*carrier.dependents,carrier.id]:
        if not identity:continue
        item=json.loads(docker('inspect',identity))[0]
        if (item['Id']!=identity or item['Config']['Labels'].get('garage.audio.generation')!=carrier.generation
                or item['State']['Running']):
            raise PermissionError('prior generation not conclusively stopped')
    if carrier.worker_ack:
        ack=json.loads(Path(carrier.worker_ack).read_text())
        initiating=json.loads((carrier.receipt/'guard-failure.json').read_text())['time']
        if (ack['generation']!=carrier.generation or ack['state']!='off' or ack['owned_children']
                or ack.get('observed',0)<initiating):
            raise PermissionError('prior hardware worker closure unproved')

class Supervisor:
    delays=(5,30,120)
    window=600
    def __init__(self,root,stop,clock=time.monotonic):
        self.root=Path(root);self.stop=stop;self.clock=clock
        self.invocation=uuid.uuid4().hex;self.retries=0;self.generation=None
    def publish(self,state,**fields):
        atomic(self.root/'recovery-state.json',{'schema':1,'invocation':self.invocation,
            'state':state,'retries':self.retries,'generation':self.generation,
            'observed':time.monotonic(),**fields})
    def ready(self,generation):
        self.generation=generation
        self.publish('ready')  # diagnostic only; never consumed as sound authority
    def run(self,generation):
        attempts=[];total=0
        try:
            while not self.stop.is_set():
                self.generation=None
                self.publish('starting')
                result=generation()
                if self.stop.is_set() or result=='stopped':
                    self.publish('stopped');return 'stopped'
                if result!='recoverable':
                    self.publish('terminal');return 'terminal'
                now=self.clock();attempts=[x for x in attempts if now-x<self.window]
                # The lifetime cap never resets after a quiet interval.
                if total>=len(self.delays) or len(attempts)>=3:
                    self.publish('exhausted');return 'exhausted'
                delay=self.delays[total];total+=1;attempts.append(now)
                self.retries=total
                self.publish('backoff',delay=delay)
                if self.stop.wait(delay):
                    self.publish('stopped');return 'stopped'
            self.publish('stopped');return 'stopped'
        except Exception:
            # Do not leak exception messages or mask the original cleanup failure.
            try:self.publish('terminal',reason='generation_failed')
            except Exception:pass
            raise

def owner_lock(path=Path('/run/garage-audio-lifecycle.lock')):
    fd=os.open(path,os.O_CREAT|os.O_RDWR|os.O_CLOEXEC|os.O_NOFOLLOW,0o600)
    try:
        info=os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid!=os.geteuid() or stat.S_IMODE(info.st_mode)!=0o600:
            raise PermissionError('private lifecycle lock required')
        fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
    except BaseException:
        os.close(fd);raise
    return fd
