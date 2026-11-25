"""Per-server context: configuration, injected clients, clock and the cross-process write lock."""
import contextlib
import fcntl
import os
import threading
import time

from . import backups as backup_store
from .backups import _ensure_private_dir
from .clients import default_clients
from .errors import OpsError

LOCK_FILE = '.write.lock'


class Context:
    def __init__(self, config, clients=None, clock=time.time):
        self.config = config
        self.clients = clients or default_clients(config)
        self.clock = clock
        self.backups = backup_store.backups(config, clock)
        self.history = backup_store.history(config, clock)
        self._thread_lock = threading.Lock()

    @contextlib.contextmanager
    def exclusive(self, timeout=10.0):
        """Serialize every mutating action across threads and processes (stdio and HTTP servers)."""
        if not self._thread_lock.acquire(timeout=timeout):
            raise OpsError('busy', 'another write is in progress; retry shortly')
        try:
            _ensure_private_dir(self.config.state_dir)
            fd = os.open(self.config.state_dir / LOCK_FILE, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
            try:
                deadline = time.monotonic() + timeout
                while True:
                    try:
                        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        break
                    except BlockingIOError:
                        if time.monotonic() >= deadline:
                            raise OpsError('busy', 'another ops server process is writing; retry shortly') from None
                        time.sleep(0.05)
                yield
            finally:
                os.close(fd)
        finally:
            self._thread_lock.release()
