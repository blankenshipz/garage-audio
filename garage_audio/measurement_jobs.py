"""Persisted one-use, exact-manifest offline jobs. Hardware execution is HARD disabled."""
from contextlib import contextmanager
from dataclasses import asdict
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import threading
import time
import uuid
import ctypes
from .human_authority import HumanIdentity
from .measurement_signal import excitation, sha
from .profile import DEFAULT
from . import measurement_route
from . import measurement_artifacts as artifacts

HARDWARE_ENABLED = False
MAX_JOBS = 24
MAX_JOB_BYTES = 262144
MAX_FILES_BYTES = 32*1024*1024
TERMINAL = {'completed','failed','cancelled','revoked','expired','invalidated'}

def canonical(value): return json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()
def digest(value): return hashlib.sha256(canonical(value)).hexdigest()

class JobFailure(Exception):
    def __init__(self,code,message,requirements=()):
        self.code,self.message,self.requirements = code,message,list(requirements)

class Jobs:
    def __init__(self, app, verifier, *, test_behavior='normal', test_deadline=None):
        self.app,self.verifier = app,verifier
        self.root = app.root/'measurement-jobs'; self.root.mkdir(mode=0o700,exist_ok=True)
        self.epoch = uuid.uuid4().hex; self.lock = threading.RLock(); self.local = threading.local(); self.children = {}; self.revoked = set(); self.control_writers = {}
        self.behavior,self.test_deadline = test_behavior,test_deadline
        with self.transaction():
            for job in self._records():
                if job['state'] not in TERMINAL:
                    self._transition(job,'invalidated','server_restart; no authority or execution resumes')

    @contextmanager
    def transaction(self):
        with self.lock:
            if getattr(self.local,'depth',0):
                yield
                return
            with (self.root/'jobs.lock').open('a') as lock:
                fcntl.flock(lock,fcntl.LOCK_EX)
                self.local.depth = 1
                try: yield
                finally: self.local.depth = 0

    def directory(self, identifier):
        if not isinstance(identifier,str) or not re.fullmatch('[a-f0-9]{32}',identifier): raise ValueError('generated ID required')
        directory = self.root/identifier
        if directory.is_symlink(): raise ValueError('isolated job directory required')
        return directory

    def _read(self, identifier):
        path = self.directory(identifier)/'job.json'
        if path.is_symlink() or not path.is_file(): raise JobFailure('not_found','Job not found')
        if path.stat().st_size > MAX_JOB_BYTES: raise JobFailure('storage_limit','Job record exceeds bounded limit')
        return json.loads(path.read_text())

    def _records(self):
        paths = sorted(self.root.glob('*/job.json'))
        if len(paths) > MAX_JOBS: raise JobFailure('storage_limit','Job history limit exceeded',['Operator archive required'])
        return [self._read(p.parent.name) for p in paths]

    def _save(self, job):
        data = canonical(job)
        if len(data) > MAX_JOB_BYTES: raise JobFailure('storage_limit','Job record limit reached')
        directory = self.directory(job['id'])
        size = sum(p.stat().st_size for p in self.root.glob('*/*') if p.is_file() and not p.is_symlink())
        existing = (directory/'job.json').stat().st_size if (directory/'job.json').exists() else 0
        if size-existing+len(data) > MAX_FILES_BYTES: raise JobFailure('storage_limit','Job artifact limit reached',['Operator archive required'])
        self.app.write_atomic(directory/'job.json',data)
        return job

    def _transition(self, job, state, reason):
        job['state'] = state; job['updated_at'] = time.time()
        job['history'].append({'state':state,'at':job['updated_at'],'reason':reason})
        job['_grant'] = None
        job['approval'] = {'approved':False,'reason':reason}
        self._save(job)

    def _fresh(self, job):
        if job['epoch'] != self.epoch: return False
        if digest(self.app.read('sessions',job['session_id'])) != job['manifest']['session_revision']: return False
        directory = self.directory(job['id'])
        try:
            return (hashlib.sha256(artifacts.read(directory,'excitation.wav',400000)).hexdigest() == job['manifest']['signal']['excitation_sha256'] and
                    hashlib.sha256(artifacts.read(directory,'manifest.json',MAX_JOB_BYTES)).hexdigest() == job['manifest_file_sha256'] and
                    hashlib.sha256(artifacts.read(directory,'native-config.json',65536)).hexdigest() == job['manifest']['native_file_route']['config_sha256'])
        except (OSError,ValueError): return False

    def _grant_current(self, job):
        if not self.app.human_approval_available: return False
        if job['id'] in self.revoked: return False
        grant = job.get('_grant')
        if not grant or grant['consumed']: return False
        identity = HumanIdentity(**grant['identity'])
        return (time.time() < grant['expires_at'] and time.monotonic() < grant['expires_monotonic'] and self.verifier.current(identity) and
                grant['manifest_sha256'] == digest(job['manifest']) == job['manifest_sha256'] and self._fresh(job))

    def _public(self, job):
        value = {k:v for k,v in job.items() if not k.startswith('_') and k not in ('manifest_file_sha256','epoch')}
        if job['id'] in self.revoked and job['state'] not in TERMINAL:
            value['state'] = 'revoked'; value['approval'] = {'approved':False,'reason':'in-memory fail-close revocation; journal retry required'}
            value['journal_repair_required'] = True
        value['next_action'] = {'awaiting_human':'Human reviews exact manifest through separate fresh human login',
            'approved':'measurement_execute', 'running':'measurement_read or measurement_cancel',
            'completed':'Inspect explicitly synthetic evidence; hardware capture remains unavailable'}.get(value['state'],'Review history; request a fresh job if needed')
        if value.get('journal_repair_required'): value['next_action'] = 'Restore journal availability and retry measurement_cancel; old authority remains revoked'
        value['progress'] = {'stage':value['state'],'terminal':value['state'] in TERMINAL}
        try: provisioned = bool(self.verifier.load()[0])
        except (OSError,ValueError,TypeError): provisioned = False
        value['human_authority'] = {'provisioned':provisioned,'enrollment_available':False,'bootstrap_role':'automation',
                                    'fresh_auth_seconds':60,'session_seconds':300,'origin_required':'standalone native exact127.0.0.1 listener/local/peer',
                                    'human_approval_available':self.app.human_approval_available,
                                    'unavailable_reason':None if self.app.human_approval_available else self.app.human_approval_unavailable_reason}
        value['human_approval_available'] = self.app.human_approval_available
        value['human_approval_unavailable_reason'] = None if self.app.human_approval_available else self.app.human_approval_unavailable_reason
        value.update(measurement_route.readiness())
        return value

    def request(self, session_id, position, channel, adapter='file-null'):
        if adapter != 'file-null': raise JobFailure('hardware_disabled','Hardware adapter is unconditionally disabled',['No audio device opens or live signal execution are authorized'])
        with self.transaction():
            if len(self._records()) >= MAX_JOBS: raise JobFailure('storage_limit','Job limit reached',['Operator archive required'])
            session = self.app.read('sessions',session_id)
            plan = session.get('plan',{})
            if session.get('state') == 'cancelled' or position not in plan.get('positions',[]) or channel not in plan.get('channels',[]):
                raise JobFailure('plan_required','Select one active planned position and channel')
            identifier = uuid.uuid4().hex; directory = self.directory(identifier); directory.mkdir(mode=0o700)
            try:
                signal_spec = excitation(directory/'excitation.wav',plan['sample_rate'],channel)
                try: native_route = measurement_route.prepare(directory,channel,signal_spec)
                except (OSError,ValueError,subprocess.SubprocessError):
                    raise JobFailure('camilla_file_unavailable','Pinned CamillaDSP FILE backend unavailable or incompatible plan',
                        ['Provision fixed root-owned4.1.3 binary/library with documented hashes; use48000Hz plan; no synthetic-only fallback'])
                try: realm = self.verifier.load()[0]['realm']
                except (OSError,ValueError,TypeError): realm = 'unprovisioned-human-realm'
                manifest = {'version':2,'realm':realm,'server_epoch':self.epoch,'adapter':'file-null','native_file_route':native_route,
                    'session_id':session_id,'session_revision':digest(session),'position':position,'channel':channel,
                    'source':'synthetic','hardware_enabled':False,'sound_disabled':True,
                    'devices':{'microphone':'synthetic-file-null','output':'synthetic-file-null'},
                    'calibration':{'orientation':plan['orientation'],'sha256':digest({'kind':'synthetic-no-calibration'}),'applied':False},
                    'channel_map':{channel:0},'timing':{'reference':'synthetic-shared-file-reference','drift_ppm':0,'verified':False},
                    'signal':signal_spec,'budgets':{'setup_seconds':3,'analysis_seconds':10,'shutdown_seconds':3,'total_seconds':20},
                    'policy':{'volume_ceiling_db':-20,'startup_master_db':-60,'muted':True,
                              'profile_sha256':digest(DEFAULT),'dsp_generation':'unverified-file-null-not-native-DSP',
                              'receipt_sha256':digest({'file-null':True,'native_dsp_exercised':False})}}
                self.app.write_atomic(directory/'manifest.json',canonical(manifest))
                now = time.time()
                job = {'id':identifier,'session_id':session_id,'epoch':self.epoch,'state':'awaiting_human',
                    'source':'synthetic','hardware_enabled':False,'sound_disabled':True,'adapter':'file-null',
                    'manifest':manifest,'manifest_sha256':digest(manifest),'manifest_file_sha256':sha(directory/'manifest.json'),
                    'created_at':now,'updated_at':now,'approval':{'approved':False,'reason':'separate human fresh authentication required'},
                    '_grant':None,'authority_consumed':False,'history':[{'state':'awaiting_human','at':now,'reason':'offline excitation generated; no devices opened'}]}
                self._save(job)
                return self._public(job)
            except BaseException:
                for path in directory.iterdir(): path.unlink()
                directory.rmdir(); raise

    def approve(self, identifier, manifest_hash, identity):
        if not self.app.human_approval_available: raise JobFailure('human_channel_unavailable','Shared/container listener cannot accept human approval')
        if not self.verifier.current(identity,fresh=True): raise JobFailure('human_fresh_auth_required','Separate fresh human authentication required')
        with self.transaction():
            job = self._read(identifier)
            if identifier in self.revoked: raise JobFailure('job_revoked','Revoked job cannot be reapproved; request a new job')
            if job['state'] != 'awaiting_human': raise JobFailure('job_state','Only an awaiting-human job can be approved')
            if identity.realm != job['manifest']['realm'] or manifest_hash != job['manifest_sha256'] or not self._fresh(job):
                raise JobFailure('manifest_changed','Exact manifest/session/realm no longer matches',['Request a fresh job; do not approve changed inputs'])
            expires = min(identity.expires_at,time.time()+60)
            job['_grant'] = {'identity':asdict(identity),'manifest_sha256':manifest_hash,'expires_at':expires,
                             'expires_monotonic':min(identity.authenticated_monotonic+300,time.monotonic()+60),'consumed':False}
            job['state'] = 'approved'; job['approval'] = {'approved':True,'user':identity.user,'credential_version':identity.version,'expires_at':expires,'manifest_sha256':manifest_hash}
            job['history'].append({'state':'approved','at':time.time(),'reason':'separate fresh human approved exact file/null manifest'})
            return self._public(self._save(job))

    def get(self, identifier):
        with self.transaction():
            job = self._read(identifier)
            if job['state'] == 'approved' and not self._grant_current(job): self._transition(job,'expired','authority expired, changed or revoked')
            return self._public(job)

    def list(self):
        with self.transaction(): return [self.get(j['id']) for j in self._records()]

    def cancel(self, identifier, reason='automation_cancelled'):
        # Stop and poison authority FIRST, independent of lock-file/journal availability.
        self.directory(identifier)
        with self.lock:
            self.revoked.add(identifier)
            writer = self.control_writers.pop(identifier,None)
            if writer is not None: os.close(writer)
            child = self.children.get(identifier)
            if child is not None and child.poll() is None: child.terminate()
        with self.transaction():
            job = self._read(identifier)
            if job['state'] not in TERMINAL: self._transition(job,'cancelled' if reason == 'automation_cancelled' else 'revoked',reason)
            return self._public(job)

    def invalidate_session(self, session_id):
        with self.transaction():
            for job in self._records():
                if job['session_id'] == session_id and job['state'] not in TERMINAL: self.cancel(job['id'],'session_mutated')

    def invalidate_all(self, reason):
        # Stop any active owned children before trying to enumerate/write history.
        for identifier in list(self.children):
            try: self.cancel(identifier,reason)
            except (OSError,JobFailure): pass
        with self.transaction():
            failure = None
            for job in self._records():
                if job['state'] not in TERMINAL:
                    try: self.cancel(job['id'],reason)
                    except (OSError,JobFailure) as error: failure = error
            if failure is not None: raise failure

    def revoke_identity(self, session):
        self.verifier.revoke(session)
        with self.transaction():
            for job in self._records():
                identity = (job.get('_grant') or {}).get('identity',{})
                if identity.get('session') == session and job['state'] not in TERMINAL: self.cancel(job['id'],'human_logout_or_revocation')

    def execute(self, identifier):
        with self.transaction():
            job = self._read(identifier)
            if job['state'] == 'approved' and not self._grant_current(job):
                self._transition(job,'expired','authority expired, changed or revoked before execute')
            if job['state'] != 'approved':
                raise JobFailure('human_approval_required','Current one-use exact-manifest human approval required',
                    ['Separate human must review current manifest through fresh independent human login; agent/bootstrap strings cannot grant approval'])
            if job['manifest'].get('version') != 2:
                raise JobFailure('manifest_superseded','Legacy file-only manifest cannot execute; request a version2 native FILE job')
            directory = self.directory(identifier)
            artifacts.admit(directory,measurement_route.RENDER_FRESH+('child.stdout','child.stderr','guardian-exit.json','parent-reap.json'),measurement_route.RENDER_EXISTING)
            # Rescue reaper remains outside the owned process group. Never wait on
            # unrelated children: the monitor uses waitpid(-owned_pgid) only.
            if sys.platform.startswith('linux'):
                if ctypes.CDLL(None,use_errno=True).prctl(36,1,0,0,0): raise RuntimeError('Parent rescue subreaper unavailable')
            else: raise JobFailure('executor_unavailable','Linux owned-descendant reaping required')
            stored_bytes = sum(p.stat().st_size for p in self.root.glob('*/*') if p.is_file() and not p.is_symlink())
            if stored_bytes+9*1024*1024 > MAX_FILES_BYTES:
                raise JobFailure('storage_limit','Insufficient reserved native render space',['Operator archive required;32MiB total artifacts; originals are never evicted'])
            lease = (self.root/'execution.lock').open('a')
            try: fcntl.flock(lease,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:
                lease.close(); raise JobFailure('execution_busy','Another owned measurement child is still active')
            job['_grant']['consumed'] = True  # Durable before any spawn; never restored on failure.
            job['authority_consumed'] = True
            job['state'] = 'running'; job['history'].append({'state':'running','at':time.time(),'reason':'one-use authority durably consumed before spawn'})
            try: self._save(job)
            except BaseException:
                self.revoked.add(identifier); lease.close(); raise
            directory = self.directory(identifier)
            deadline = self.test_deadline or job['manifest']['budgets']['total_seconds']
            hard_end = time.monotonic()+deadline
            work_end = hard_end-min(3,deadline*.2)  # Reserve shutdown within exact inclusive budget.
            env = {k:os.environ[k] for k in ('PATH','PYTHONPATH','LANG','LC_ALL','TMPDIR') if k in os.environ}
            read_fd,write_fd = os.pipe()
            self.control_writers[identifier] = write_fd
            try:
                output = artifacts.exclusive(directory,'child.stdout'); errors = artifacts.exclusive(directory,'child.stderr')
                child = subprocess.Popen([sys.executable,'-m','garage_audio.measurement_runner','guardian',str(directory),str(work_end),self.behavior,str(os.getpid()),str(read_fd),self.epoch],
                    stdin=subprocess.DEVNULL,stdout=output,stderr=errors,env=env,start_new_session=True,close_fds=True,
                    pass_fds=(lease.fileno(),read_fd))  # Guardian holds lease and lifetime channel, never writer end.
                os.close(read_fd)
                output.close(); errors.close(); self.children[identifier] = child
                threading.Thread(target=self._monitor,args=(identifier,child,lease,hard_end),daemon=True).start()
            except BaseException:
                self.revoked.add(identifier)
                try: os.close(read_fd)
                except OSError: pass
                writer = self.control_writers.pop(identifier,None)
                if writer is not None: os.close(writer)
                lease.close(); self._transition(job,'failed','spawn failed; one-use authority remains consumed'); raise
            return self._public(job)

    def _monitor(self, identifier, child, lease, deadline):
        end = deadline
        reaped = []
        cleanup_code = None
        def stop_and_reap():
            nonlocal cleanup_code
            if cleanup_code is not None: return cleanup_code
            # Guardian may already be dead. Its group still belongs to this job
            # until all adopted descendants are reaped; parent cannot be killed
            # by this signal and retains the lease throughout emergency cleanup.
            if child.poll() is None:
                try: os.killpg(child.pid,signal.SIGKILL)
                except ProcessLookupError: pass
            code = child.wait()
            while True:
                try: pid,status = os.waitpid(-child.pid,os.WNOHANG)
                except ChildProcessError: break
                except InterruptedError: continue
                if not pid:
                    # waitpid proved a still-owned live child in this group;
                    # don't signal an old PGID after all owned children vanished.
                    try: os.killpg(child.pid,signal.SIGKILL)
                    except ProcessLookupError: pass
                    try: pid,status = os.waitpid(-child.pid,0)
                    except InterruptedError: continue
                reaped.append({'pid':pid,'exit_status':os.waitstatus_to_exitcode(status)})
            cleanup_code = code
            return code
        try:
            while child.poll() is None:
                with self.transaction():
                    job = self._read(identifier)
                    grant = job.get('_grant')
                    identity = HumanIdentity(**grant['identity']) if grant else None
                    if (not self.app.human_approval_available or identifier in self.revoked or job['state'] != 'running' or job['epoch'] != self.epoch or not self._fresh(job)
                            or not self.verifier.current(identity) or (grant and (time.time() >= grant['expires_at'] or time.monotonic() >= grant['expires_monotonic']))):
                        if job['state'] == 'running': self._transition(job,'revoked','input/credential/control context lost')
                        child.terminate()
                if time.monotonic() >= end:
                    # Only the still-owned, live subprocess group; never stored PID/name enumeration.
                    os.killpg(child.pid,signal.SIGKILL)
                    break
                time.sleep(.03)
            code = stop_and_reap()
            artifacts.write(self.directory(identifier),'parent-reap.json',canonical({'guardian_pid':child.pid,'guardian_exit_status':code,
                'owned_pgid':child.pid,'reaped_descendants':reaped,'owned_group_children_reaped':True}))
            with self.transaction():
                job = self._read(identifier); job['child_exit_status'] = code
                job['owned_child_exit_verified'] = True
                job['descendant_reaping'] = {'owned_pgid':child.pid,'reaped_descendants':reaped,'owned_group_children_reaped':True}
                receipt = self.directory(identifier)/'guardian-exit.json'
                if receipt.is_file() and not receipt.is_symlink() and receipt.stat().st_size <= 4096:
                    job['guardian'] = json.loads(receipt.read_text())
                path = self.directory(identifier)/'evidence.json'
                if path.is_file() and not path.is_symlink() and path.stat().st_size <= MAX_JOB_BYTES:
                    evidence = json.loads(path.read_text())
                    evidence['approved_manifest_sha256'] = job['manifest_sha256']
                    evidence['evidence_sha256'] = digest(evidence)
                    job['evidence'] = evidence
                if code == 0 and job['state'] == 'running' and path.is_file() and path.stat().st_size <= MAX_JOB_BYTES and self._fresh(job):
                    self._transition(job,'completed','owned file/null child completed; deterministic synthetic evidence')
                    self._save(job)
                elif job['state'] == 'running':
                    job['error'] = {'code':'child_or_quality_failed','message':'Owned file/null child failed quality checks or deadline','child_exit_status':code,
                                    'requirements':['Inspect bounded evidence/history; request a new job and separate approval; no hardware success is claimed']}
                    self._transition(job,'failed','child failed or guardian deadline expired; no hardware success')
                else: self._save(job)
        except BaseException:
            code = stop_and_reap()
            try:
                artifacts.write(self.directory(identifier),'parent-reap.json',canonical({'guardian_pid':child.pid,'guardian_exit_status':code,
                    'owned_pgid':child.pid,'reaped_descendants':reaped,'owned_group_children_reaped':True}))
            except (OSError,ValueError): pass
            try:
                with self.transaction():
                    job = self._read(identifier)
                    job['owned_child_exit_verified'] = True
                    if job['state'] not in TERMINAL: self._transition(job,'failed','guardian control lost; owned descendants reaped')
            except (OSError,ValueError): pass
        finally:
            stop_and_reap()  # No journal failure may bypass authoritative reap.
            lease.close(); self.children.pop(identifier,None)
            writer = self.control_writers.pop(identifier,None)
            if writer is not None: os.close(writer)
