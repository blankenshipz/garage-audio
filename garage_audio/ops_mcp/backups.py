"""Private state files: atomic JSON writes, timestamped DSP backups and measurement history.

Every DSP write takes a backup of the configuration that is about to be replaced
*before* sending anything to CamillaDSP. Files are written to a temporary file in
the same directory, fsynced, renamed into place and the directory is fsynced, so a
crash leaves either the old or the new file, never a torn one. Retention is
bounded; the oldest entries are pruned after a successful write.
"""
import json
import os
import re
import threading
import time
import uuid
from pathlib import Path

from .errors import OpsError

BACKUP_DIR = 'dsp-backups'
HISTORY_DIR = 'measurements'
ID_PATTERN = re.compile(r'^[0-9]{8}T[0-9]{6}\.[0-9]{6}Z-[0-9a-f]{8}$')
MAX_FILE_BYTES = 4 * 1024 * 1024


def _ensure_private_dir(path):
    """Create ``path`` with mode 0700 if missing; refuse an existing one that is not private.

    A group/world-accessible directory is refused rather than silently tightened, so
    the operator notices a misconfigured location instead of the server changing it.
    """
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = path.lstat()
    if path.is_symlink() or not path.is_dir() or info.st_uid != os.geteuid():
        raise OpsError('state_dir_unsafe', 'state directory must be a real directory owned by this user')
    if info.st_mode & 0o077:
        raise OpsError('state_dir_unsafe', 'state directory (or a subdirectory) is group/world accessible; '
                       'run chmod 700 on it (mode 0700 is required)')
    return path


def atomic_write_json(path, value):
    """Write ``value`` as JSON to ``path`` atomically with mode 0600."""
    path = Path(path)
    data = json.dumps(value, indent=2, sort_keys=True).encode('utf-8')
    if len(data) > MAX_FILE_BYTES:
        raise OpsError('state_too_large', 'record exceeds the state file size limit')
    temp = path.with_name(f'.{path.name}.{uuid.uuid4().hex}.tmp')
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    try:
        with os.fdopen(fd, 'wb') as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    except BaseException:
        try:
            os.unlink(temp)
        except OSError:
            pass
        raise
    dir_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)
    return path


def read_json(path):
    path = Path(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    with os.fdopen(fd, 'rb') as handle:
        data = handle.read(MAX_FILE_BYTES + 1)
    if len(data) > MAX_FILE_BYTES:
        raise OpsError('state_too_large', 'record exceeds the state file size limit')
    return json.loads(data.decode('utf-8'))


def new_id(clock=time.time):
    now = clock()
    stamp = time.strftime('%Y%m%dT%H%M%S', time.gmtime(now))
    micro = int((now - int(now)) * 1_000_000)
    return f'{stamp}.{micro:06d}Z-{uuid.uuid4().hex[:8]}'


class Journal:
    """A bounded directory of timestamped JSON records (backups or measurement history)."""

    def __init__(self, root, name, retention, clock=time.time):
        self.root = Path(root)
        self.name = name
        self.retention = retention
        self.clock = clock
        self.lock = threading.Lock()

    @property
    def directory(self):
        return self.root / self.name

    def _dir(self):
        _ensure_private_dir(self.root)
        return _ensure_private_dir(self.directory)

    def save(self, record):
        with self.lock:
            directory = self._dir()
            ident = new_id(self.clock)
            record = dict(record, id=ident, saved_at=round(self.clock(), 6))
            atomic_write_json(directory / f'{ident}.json', record)
            self._prune(directory, keep=ident)
            return record

    def _prune(self, directory, keep):
        # Ids sort by timestamp; if the clock stepped back, the entry just written may sort
        # oldest. It is never pruned: it is the rollback point for the write in progress.
        ids = [i for i in self._ids(directory) if i != keep]
        excess = len(ids) + 1 - self.retention
        for ident in ids[:excess] if excess > 0 else []:
            try:
                (directory / f'{ident}.json').unlink()
            except OSError:
                pass

    @staticmethod
    def _ids(directory):
        try:
            names = [p.name[:-5] for p in directory.iterdir() if p.name.endswith('.json')]
        except FileNotFoundError:
            return []
        return sorted(n for n in names if ID_PATTERN.fullmatch(n))

    def list(self, limit=50):
        ids = self._ids(self.directory)
        return list(reversed(ids))[:limit]

    def get(self, ident):
        if not isinstance(ident, str) or not ID_PATTERN.fullmatch(ident):
            raise OpsError('invalid_id', f'{self.name} id is not valid')
        path = self.directory / f'{ident}.json'
        try:
            value = read_json(path)
        except FileNotFoundError:
            raise OpsError('not_found', f'{self.name} entry not found', id=ident) from None
        except (OSError, ValueError):
            raise OpsError('unreadable', f'{self.name} entry cannot be read', id=ident) from None
        if not isinstance(value, dict) or value.get('id') != ident:
            raise OpsError('unreadable', f'{self.name} entry is malformed', id=ident)
        return value


def backups(config, clock=time.time):
    return Journal(config.state_dir, BACKUP_DIR, config.backup_retention, clock)


def history(config, clock=time.time):
    return Journal(config.state_dir, HISTORY_DIR, config.room['history_retention'], clock)
