"""Private worker-owned finite qualification ledger; only host root issues attempts.

Policy is a separate root-owned READ-ONLY bind. Missing state is never a new budget.
This ledger is not a playback grant: existing fresh receiver admission still applies.
"""
import fcntl
import json
import os
from pathlib import Path
import re
import stat
import time
import uuid


def boot_id():
    return Path('/proc/sys/kernel/random/boot_id').read_text().strip()


def private_json(path, uid, mode):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != uid or stat.S_IMODE(info.st_mode) != mode or info.st_size > 4096 or info.st_nlink != 1:
            raise PermissionError('exact private qualification record required')
        return json.loads(os.read(fd, 4097))
    finally:
        os.close(fd)


def atomic(path, value, uid):
    data = json.dumps(value, separators=(',', ':')).encode()
    if len(data) > 4096:
        raise ValueError('bounded qualification record required')
    temp = path.with_name(path.name+'.next')
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        if os.geteuid() != uid:
            os.fchown(fd, uid, uid)  # root issuer; worker keeps its existing primary GID
        os.write(fd, data)
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(temp, path)
    directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:os.fsync(directory)
    finally:os.close(directory)


class Qualification:
    def __init__(self, policy, store, worker_uid=1002):
        self.store = Path(store)
        info = self.store.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != worker_uid or stat.S_IMODE(info.st_mode) != 0o700:
            raise PermissionError('private worker-owned qualification store required')
        self.uid = worker_uid
        p = private_json(policy, 0, 0o444)
        if set(p) != {'attempt', 'mode'} or p['mode'] != 'scalar' or not re.fullmatch('[a-f0-9]{32}', p['attempt']):
            raise PermissionError('root-issued scalar attempt required')
        self.attempt = p['attempt']
        self.path = self.store/(self.attempt+'.json')
        # Read at construction: missing/corrupt/old-boot state fails closed, never arms.
        self.read()

    def read(self):
        v = private_json(self.path, self.uid, 0o600)
        if set(v) != {'attempt', 'boot', 'started', 'segments', 'terminal'} or v['attempt'] != self.attempt or type(v['segments']) is not int or not 0 <= v['segments'] <= 8 or type(v['terminal']) is not bool:
            raise PermissionError('exact qualification state required')
        if v['started'] is not None and (type(v['started']) not in (int, float) or not 0 <= v['started'] < float('inf')):
            raise PermissionError('finite qualification start required')
        if (v['segments'] > 0) != (v['started'] is not None) or not isinstance(v['boot'], str):
            raise PermissionError('consistent qualification state required')
        if v['started'] is not None and v['boot'] == boot_id() and v['started'] > time.monotonic():
            raise PermissionError('qualification clock cannot run backwards')
        if v['boot'] != boot_id():
            v['terminal'] = True  # reboot cannot turn an old monotonic deadline into new time
        if v['started'] is not None and time.monotonic()-v['started'] >= 300:
            v['terminal'] = True
        return v

    def valid(self):
        v = self.read()
        return not v['terminal']  # eighth active segment may finish; ninth cannot start

    def lock(self):
        fd = os.open(self.store/'budget.lock', os.O_RDWR | os.O_NOFOLLOW)
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != self.uid or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1:
            os.close(fd)
            raise PermissionError('qualification lock ownership required')
        fcntl.flock(fd, fcntl.LOCK_EX)
        return fd

    def admit_segment(self):
        fd = self.lock()
        try:
            v = self.read()
            if v['terminal'] or v['segments'] >= 8:
                raise PermissionError('qualification consumed; root must issue a NEW attempt')
            if v['started'] is None:
                v['started'] = time.monotonic()  # FIRST genuine admitted Play only
            v['segments'] += 1
            atomic(self.path, v, self.uid)  # durable BEFORE any PCM child is launched
            return v
        finally:
            os.close(fd)

    def consume(self):
        # Worker is the sole authority writer; serialize with admission across processes.
        fd = self.lock()
        try:
            v = self.read()
            v['terminal'] = True
            atomic(self.path, v, self.uid)
        finally:os.close(fd)


def issue(policy, store, *, consumed=False, attempt=None):
    """Explicit host ROOT operation only, NEVER called by boot/observer/UI/MCP.

    Preserve old records; seed the already-consumed r15 attempt with consumed=True.
    A new empty attempt is issued explicitly only after witness/standby readiness.
    Mount policy as a read-only file in a root-owned directory; store only to worker.
    """
    if os.geteuid() != 0:
        raise PermissionError('host root must explicitly issue qualification')
    policy, store = Path(policy), Path(store)
    if policy.exists():
        raise FileExistsError('new policy pathname required; existing attempts never overwritten')
    store.mkdir(mode=0o700, parents=True, exist_ok=True)
    if not stat.S_ISDIR(store.lstat().st_mode):
        raise PermissionError('qualification store may not be a symlink')
    os.chown(store, 1002, 1002)
    os.chmod(store, 0o700)
    lock = store/'budget.lock'
    if not lock.exists():
        fd = os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        try:
            os.fchown(fd, 1002, 1002)
            os.fsync(fd)
        finally:os.close(fd)
    attempt = attempt or uuid.uuid4().hex
    if not re.fullmatch('[a-f0-9]{32}', attempt) or (store/(attempt+'.json')).exists():
        raise FileExistsError('new attempt identity required; existing records preserved')
    atomic(store/(attempt+'.json'), {'attempt':attempt, 'boot':boot_id(), 'started':None, 'segments':0, 'terminal':consumed}, 1002)
    policy.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
    info = policy.parent.lstat()
    if info.st_uid != 0 or info.st_mode & 0o022:
        raise PermissionError('policy directory must be root-owned/non-writable')
    fd = os.open(policy, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o444)
    try:
        os.write(fd, json.dumps({'attempt':attempt, 'mode':'scalar'}).encode())
        os.fsync(fd)
    finally:os.close(fd)
    return attempt
