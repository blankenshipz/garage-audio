"""Private lifecycle recovery: classified workload exits only, never authority renewal."""
import argparse,errno,fcntl,json,math,os,socket,stat,subprocess,sys,time,uuid
from pathlib import Path
from .audio_network import (HEARTBEAT_TOLERANCE_DEFAULT,HEARTBEAT_TOLERANCE_ENV,HEARTBEAT_TOLERANCE_MAX,
    HEARTBEAT_TOLERANCE_MIN,STALL_KIND,atomic,docker,heartbeat_tolerance)

class WorkloadCrash(RuntimeError):
    pass

# Classifications that may use the existing bounded Supervisor retry. Everything else
# (including a guard that actually exited) is terminal.
RECOVERABLE_KINDS=('owned_workload_crash',STALL_KIND)

def guard_classification(receipt):
    """Safe, message-free projection of guard-failure.json for diagnostics."""
    try:value=json.loads((Path(receipt)/'guard-failure.json').read_text())
    except (OSError,ValueError):return None
    if not isinstance(value,dict) or not isinstance(value.get('kind'),str):return None
    result={'kind':value['kind']}
    for key in ('staleness','tolerance'):
        if isinstance(value.get(key),(int,float)) and not isinstance(value.get(key),bool):result[key]=value[key]
    return result

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
    # A stalled-but-alive guard (host pressure) was stopped by the owner before the
    # classification was written; it is retried only by the bounded Supervisor.
    return value.get('kind') in RECOVERABLE_KINDS

EX_TEMPFAIL=75

# Transient resolver/connection failures while the network or Music Assistant is still starting.
# EAI_NONAME is included on purpose: with a degraded local resolver stub (e.g. systemd-resolved
# that cannot reach its upstream) lookups of the fixed internal MA hostname return EAI_AGAIN or
# EAI_NONAME interchangeably. A genuinely wrong hostname still ends terminal-by-exhaustion after
# the existing 3 bounded retries (5+30+120 s), so the cost of the wider net is bounded.
_TRANSIENT_GAI=tuple(getattr(socket,name) for name in ('EAI_AGAIN','EAI_NONAME','EAI_NODATA') if hasattr(socket,name))
_TRANSIENT_ERRNO=(errno.ECONNREFUSED,errno.ECONNRESET)
_TRANSIENT_TEXT=('temporary failure in name resolution','name or service not known',
    'no address associated with hostname','connection refused','connection reset by peer')
# Exception class names that cross the docker-exec boundary as `safe_error_class` (the MA policy
# script prints only the class name on stdout). aiohttp wraps DNS/connect failures in these.
TRANSIENT_CLASS_NAMES=frozenset(('ClientConnectorDNSError','ClientConnectorError','ClientConnectionResetError',
    'ServerDisconnectedError','ConnectionRefusedError','ConnectionResetError','gaierror','CannotConnect','ConnectionFailed'))

class TransientNetworkError(RuntimeError):
    """Startup failure positively attributed to a transient resolver/connection problem."""

def _text_transient(value):
    return isinstance(value,str) and any(item in value[:4096].lower() for item in _TRANSIENT_TEXT)

def _direct_transient(error):
    if isinstance(error,TransientNetworkError):return True
    if isinstance(error,socket.gaierror):return error.errno in _TRANSIENT_GAI
    if isinstance(error,(ConnectionRefusedError,ConnectionResetError)):return True
    if isinstance(error,OSError) and (error.errno in _TRANSIENT_GAI or error.errno in _TRANSIENT_ERRNO):return True
    # Stringified across a boundary (RuntimeError(str), CalledProcessError output, URLError.reason text).
    if _text_transient(str(error)):return True
    for name in ('stderr','output','reason'):
        value=getattr(error,name,None)
        if isinstance(value,bytes):value=value[:4096].decode('utf-8','replace')
        if _text_transient(value):return True
    return False

def _inner(error):
    """Exceptions a wrapper carries besides __cause__/__context__ (aiohttp .os_error, URLError .reason)."""
    for name in ('os_error','reason'):
        value=getattr(error,name,None)
        if isinstance(value,BaseException):yield value
    for value in getattr(error,'exceptions',()) or ():
        if isinstance(value,BaseException):yield value

def _chain(error):
    seen=set();todo=[error]
    while todo:
        current=todo.pop(0)
        if current is None or id(current) in seen:continue
        seen.add(id(current));yield current
        todo.extend([current.__cause__,current.__context__,*_inner(current)])

def transient_network(error):
    """True if the error graph shows a transient resolver/connection failure and no permission denial."""
    nodes=list(_chain(error))
    if any(isinstance(node,PermissionError) for node in nodes):return False
    return any(_direct_transient(node) for node in nodes)

def policy_failure_transient(stdout_path):
    """Whether a policy-admission attempt's stdout names a transient connection failure class."""
    try:
        with open(stdout_path,'rb') as handle:value=json.loads(handle.read(2048))
    except (OSError,ValueError):return False
    return isinstance(value,dict) and value.get('safe_error_class') in TRANSIENT_CLASS_NAMES

def preadmission(error,carrier):
    """Infrastructure failure before any dependent was registered: no worker, DAC or receiver existed."""
    if carrier.dependents or carrier.worker_ack:return False
    transient=False
    for current in _chain(error):
        # Ownership, identity and consent failures stay terminal.
        if isinstance(current,PermissionError):return False
        if _direct_transient(current) or any(_direct_transient(x) for x in _inner(current)):transient=True
        elif isinstance(current,(OSError,ValueError)):return False
    return transient or isinstance(error,(subprocess.SubprocessError,RuntimeError))

def classify_failure(error,receipt,carrier):
    """'recoverable' | 'preadmission' | 'terminal' for a generation failure.

    A guard stall is classified by the owner-side watcher only after it has stopped the guard, so
    the driver (which sees the guard gone first) waits, bounded, for that final verdict. If the
    verdict is not final in time the failure is terminal: never guess recoverable."""
    if not carrier.await_guard_verdict():return 'terminal'
    if recoverable(error,receipt):return 'recoverable'
    # A recorded guard failure of a non-recoverable kind is final: no network-looking error in the
    # chain may turn it into a retry.
    guard=_read_json(Path(receipt)/'guard-failure.json')
    if guard is not None and guard.get('kind') not in RECOVERABLE_KINDS:return 'terminal'
    # A positively identified transient resolver/connection failure is retried (bounded) even after
    # dependents registered: Music Assistant itself is a dependent, so DNS trouble at startup always
    # happens after registration. Teardown is proven by verify_closed before any retry. It keeps the
    # 'preadmission' label so exhaustion exits EX_TEMPFAIL (the unit retries under its start limit).
    if transient_network(error):return 'preadmission'
    return 'preadmission' if preadmission(error,carrier) else 'terminal'

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

def _read_json(path):
    try:value=json.loads(Path(path).read_text())
    except (OSError,ValueError):return None
    return value if isinstance(value,dict) else None

def latest_generation(root):
    """Newest generation-<ns> receipt directory under root, or None. Read-only."""
    best=None
    try:
        for item in Path(root).iterdir():
            name=item.name
            if name.startswith('generation-') and name[11:].isascii() and name[11:].isdigit() and item.is_dir():
                if best is None or int(name[11:])>int(best.name[11:]):best=item
    except OSError:return None
    return best

def latest_failure(root):
    """Name-only diagnostic of the newest generation's failure; never message text."""
    receipt=latest_generation(root)
    if receipt is None:return {}
    result={}
    guard=guard_classification(receipt)
    if guard:result['guard_failure']=guard
    driver=_read_json(receipt/'driver-failure.json') or {}
    for key in ('class','recovery','guard_exit'):
        if isinstance(driver.get(key),(str,int)) and not isinstance(driver.get(key),bool):result['driver_'+key]=driver[key]
    return result

class Supervisor:
    delays=(5,30,120)
    window=600
    def __init__(self,root,stop,clock=time.monotonic):
        self.root=Path(root);self.stop=stop;self.clock=clock
        self.invocation=uuid.uuid4().hex;self.retries=0;self.generation=None;self.last=None
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
                result=generation();self.last=result
                if self.stop.is_set() or result=='stopped':
                    self.publish('stopped');return 'stopped'
                failure=latest_failure(self.root)  # classification only, for operators
                if result not in ('recoverable','preadmission'):
                    self.publish('terminal',last_failure=failure);return 'terminal'
                now=self.clock();attempts=[x for x in attempts if now-x<self.window]
                # The lifetime cap never resets after a quiet interval.
                if total>=len(self.delays) or len(attempts)>=3:
                    self.publish('exhausted',last_failure=failure);return 'exhausted'
                delay=self.delays[total];total+=1;attempts.append(now)
                self.retries=total
                self.publish('backoff',delay=delay,last_failure=failure)
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

# ---- read-only status ------------------------------------------------------------
DEFAULT_ROOT=Path('/var/lib/garage-audio-network')
SERVICE='garage-audio-network.service'

STATE_HELP={
 'starting':('A new generation is being created or admitted. No sound authority exists yet.',
  'Wait. If this persists, check the service log and the newest generation receipt; do not start a second owner.'),
 'ready':('The generation passed the startup policy/OFF checks, stable receiver and pairing admission. '
  'Diagnostic only: it does not prove Spotify authentication, phone discovery or audibility.',
  'No action needed.'),
 'backoff':('A recoverable failure (an owned workload crash or a guard heartbeat stall) closed the previous '
  'generation; the owner is waiting before the next bounded retry (5, 30, then 120 seconds).',
  'Wait; the owner retries by itself. Do not restart it. If it keeps recurring, investigate the cause (for a stall: host memory/CPU/IO pressure).'),
 'stopped':('The owner was stopped deliberately (service stop / signal) and the generation closed.',
  f'Start the owner with the service manager when ready: systemctl start {SERVICE}.'),
 'terminal':('A security, ownership, identity, lease, permission or unclassified failure closed the generation. '
  'It is never retried automatically.',
  'Do not loop restarts. Inspect last guard failure/revocation reason and the service log, fix the cause, '
  f'confirm no generation containers are running, then start the owner once: systemctl start {SERVICE}.'),
 'exhausted':('Recoverable failures exceeded the bounded retry cap (3 retries per owner invocation; the cap never resets). '
  'The owner exited and does not retry on its own.',
  'Fix the underlying cause first (for a stall: relieve host pressure or apply resource protection), '
  f'confirm no generation containers are running, then start the owner once: systemctl start {SERVICE}.'),
}

def _age(now,value):
    if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value):return None
    return round(now-value,3)

def tolerance_report(environ=None):
    """Effective tolerance read exactly as the owner reads it, from THIS process's environment.
    An invalid value is reported as invalid and never echoed."""
    environment=os.environ if environ is None else environ
    report={'setting':HEARTBEAT_TOLERANCE_ENV,'default_seconds':HEARTBEAT_TOLERANCE_DEFAULT,
            'min_seconds':HEARTBEAT_TOLERANCE_MIN,'max_seconds':HEARTBEAT_TOLERANCE_MAX,
            'scope':'this process environment; the owner unit may set it differently (see its Environment=)'}
    try:value=heartbeat_tolerance(environment)
    except ValueError:
        return dict(report,effective_seconds=None,effective_source='invalid')
    return dict(report,effective_seconds=value,effective_source='environment' if HEARTBEAT_TOLERANCE_ENV in environment else 'default')

def collect_status(root=DEFAULT_ROOT,now=None):
    """Read-only snapshot. Takes no lock and writes nothing. Monotonic ages assume the same
    boot (CLOCK_MONOTONIC is system-wide on Linux); a negative age marks another boot."""
    root=Path(root);now=time.monotonic() if now is None else now;notes=[]
    state=_read_json(root/'recovery-state.json')
    report={'root':str(root),'state':'unknown','explanation':'No recovery-state.json: the owner has not run '
            'with this root, or the record was removed.','restart_guidance':'Check the service manager for the owner unit '
            f'({SERVICE}) before starting anything.','retries':None,'retries_remaining':None,'generation':None,
            'tolerance':tolerance_report(),
            'notes':notes}
    if state:
        name=state.get('state') if isinstance(state.get('state'),str) else 'unknown'
        explanation,guidance=STATE_HELP.get(name,('Unrecognized recovery state.','Inspect the owner log.'))
        retries=state.get('retries') if isinstance(state.get('retries'),int) else None
        report.update(state=name,explanation=explanation,restart_guidance=guidance,retries=retries,
            retries_remaining=None if retries is None else max(0,len(Supervisor.delays)-retries),
            generation=state.get('generation'),invocation=state.get('invocation'),
            state_age_seconds=_age(now,state.get('observed')))
        if 'delay' in state:report['backoff_delay_seconds']=state['delay']
        if isinstance(state.get('last_failure'),dict):report['recorded_failure']=state['last_failure']
    receipt=latest_generation(root)
    if receipt is not None:
        report['receipt']=receipt.name
        heartbeat=_read_json(receipt/'guard-heartbeat.json')
        report['heartbeat_age_seconds']=_age(now,heartbeat.get('time')) if heartbeat else None
        report['guard_failure']=None
        failure=_read_json(receipt/'guard-failure.json')
        if failure:
            report['guard_failure']={k:failure[k] for k in ('kind','staleness','tolerance') if k in failure}
            report['guard_failure']['age_seconds']=_age(now,failure.get('time'))
        revocation=_read_json(receipt/'guard-revoked.json') or _read_json(receipt/'authority/revoked.json')
        if revocation:
            stopped=revocation.get('containers_stopped')
            report['last_revocation']={'reason':revocation.get('reason'),'age_seconds':_age(now,revocation.get('time')),
                'containers_stopped':bool(stopped) and all(v is True for v in stopped.values()) if isinstance(stopped,dict) else None}
        else:report['last_revocation']=None
        driver=_read_json(receipt/'driver-failure.json')
        report['driver_failure']=None if not driver else {k:driver[k] for k in ('class','recovery','guard_exit','guard_failure') if k in driver}
    ages=[report.get(k) for k in ('state_age_seconds','heartbeat_age_seconds')]
    if any(isinstance(a,(int,float)) and a<0 for a in ages):
        notes.append('Monotonic timestamps are ahead of this boot (host restarted since they were written); ages are not meaningful.')
        for key in ('state_age_seconds','heartbeat_age_seconds'):
            if isinstance(report.get(key),(int,float)) and report[key]<0:report[key]=None
    if receipt is not None and report['state'] in ('terminal','exhausted','backoff','stopped'):
        notes.append('The heartbeat stops when a generation closes; its age is only live while state is starting/ready.')
    notes.append('Diagnostic only: this reads receipts and never proves the owner or its containers are running. '
                 f'Confirm with the service manager (systemctl status {SERVICE}).')
    return report

def format_status(report):
    lines=[f"state: {report['state']}",f"  {report['explanation']}"]
    for label,key in (('generation','generation'),('receipt','receipt'),('retries','retries'),('retries remaining','retries_remaining'),
            ('state age (s)','state_age_seconds'),('heartbeat age (s)','heartbeat_age_seconds')):
        if report.get(key) is not None:lines.append(f'{label}: {report[key]}')
    failure=report.get('guard_failure')
    if failure:
        extra=''.join(f', {k}={failure[k]}' for k in ('staleness','tolerance') if k in failure)
        lines.append(f"last guard failure: {failure.get('kind')}{extra} (age {failure.get('age_seconds')} s)")
    revocation=report.get('last_revocation')
    if revocation:lines.append(f"last revocation: {revocation.get('reason')} (containers stopped: {revocation.get('containers_stopped')})")
    driver=report.get('driver_failure')
    if driver:lines.append('driver failure: '+', '.join(f'{k}={v}' for k,v in driver.items()))
    tolerance=report['tolerance']
    if tolerance['effective_source']=='invalid':
        effective=f"INVALID in this environment (owner would refuse to start; must be {tolerance['min_seconds']:g}-{tolerance['max_seconds']:g} s)"
    else:effective=f"{tolerance['effective_seconds']:g} s ({tolerance['effective_source']})"
    lines.append(f"guard heartbeat tolerance, effective in this process's environment: {effective}; default "
                 f"{tolerance['default_seconds']:g} s, set with {tolerance['setting']} ({tolerance['min_seconds']:g}-{tolerance['max_seconds']:g}); "
                 "the owner unit's own Environment= may differ")
    lines.append('restart guidance: '+report['restart_guidance'])
    lines.extend('note: '+note for note in report['notes'])
    return '\n'.join(lines)

def main(argv=None):
    parser=argparse.ArgumentParser(prog='python -m garage_audio.audio_recovery',description='Read-only lifecycle recovery status.')
    commands=parser.add_subparsers(dest='command',required=True)
    status=commands.add_parser('status',help='show recovery state without taking locks or writing anything')
    status.add_argument('--root',type=Path,default=DEFAULT_ROOT)
    status.add_argument('--json',action='store_true')
    args=parser.parse_args(argv)
    try:
        if not args.root.is_dir():
            print(f'status root not found: {args.root}',file=sys.stderr);return 2
        if not os.access(args.root,os.R_OK|os.X_OK):raise PermissionError
        report=collect_status(args.root)
    except PermissionError:
        print('cannot read the status root; run as the account that owns it (read-only access suffices)',file=sys.stderr);return 2
    print(json.dumps(report,indent=2,sort_keys=True) if args.json else format_status(report))
    return 0

if __name__=='__main__':
    raise SystemExit(main())
