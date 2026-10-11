"""Root-owned Soloist port reservation and kernel-observed daemon attestation.

No caller PID/path/namespace/port fields. Each scope belongs to one admitted MA
connection. Reservations protect BEFORE publication; root/admin are trusted.
"""
import hashlib
import os
from pathlib import Path
import select
import socket
import stat
import threading
import time

from .spotify_socket_guard import SocketGuard

DATA = 'data/spotify_connect/spotify_connect_02_00_00_00_21_01/soloist-data'
BINARY = 'data/soloist/soloist'
SOLOIST_PIN = '/etc/garage-audio/soloist-sha256'
VOLUME_PATH = Path('/var/lib/garage-speakers/spotify-volume.json')

def saved_volume():
    import json
    try:info=VOLUME_PATH.lstat()
    except FileNotFoundError:return None
    if not stat.S_ISREG(info.st_mode) or info.st_uid!=0 or info.st_mode&0o077 or info.st_size>128:
        raise PermissionError('private root-owned receiver volume metadata required')
    value=json.loads(VOLUME_PATH.read_text())
    if set(value)!={'volume'} or type(value['volume']) is not int or not 0<=value['volume']<=100:
        raise PermissionError('bounded receiver volume metadata required')
    return value['volume']

def save_volume(value):
    import json
    if type(value) is not int or not 0<=value<=100:raise PermissionError('observed receiver volume required')
    VOLUME_PATH.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
    parent=VOLUME_PATH.parent.lstat()
    if not stat.S_ISDIR(parent.st_mode) or parent.st_uid!=0 or parent.st_mode&0o077:
        raise PermissionError('private root-owned receiver state required')
    temporary=VOLUME_PATH.with_suffix('.new')
    fd=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_TRUNC|os.O_NOFOLLOW,0o600)
    os.fchmod(fd,0o600);os.fchown(fd,0,0)
    with os.fdopen(fd,'w') as handle:
        json.dump({'volume':value},handle);handle.flush();os.fsync(handle.fileno())
    os.replace(temporary,VOLUME_PATH)


def proc_stat(pid):
    fields = Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()
    return int(fields[1]), fields[19]


class PortScope:
    def __init__(self, peer, guard):
        self.peer = peer
        self.guard = guard
        self.lock = threading.Lock()
        self.daemon_fd = None
        self.daemon = None
        self.closed = False
        peer.verify()
        pid = peer.pid
        # The live Compose contract is host networking. Never infer this from
        # endpoint text or accept a caller-provided namespace.
        self.netns = os.readlink(f'/proc/{pid}/ns/net')
        if self.netns != os.readlink('/proc/1/ns/net'):
            raise PermissionError('exact host-network MA namespace required')
        entries = Path(f'/proc/{pid}/cgroup').read_text().splitlines()
        paths = [row[3:] for row in entries if row.startswith('0::')]
        if len(paths) != 1 or '..' in Path(paths[0]).parts:
            raise PermissionError('observed unified MA cgroup required')
        group = Path('/sys/fs/cgroup') / paths[0].lstrip('/')
        self.socket = socket.socket()
        self.socket.bind(('127.0.0.1', 0))
        self.port = self.socket.getsockname()[1]
        try:
            guard.protect(self.port, pid=pid, uid=1000, cgroup_id=group.stat().st_ino)
        except BaseException:
            self.socket.close()
            raise
        # A root-protected port is returned before Soloist starts. Closing this
        # reservation allows bind, not connect: no untrusted control client can
        # connect during the bind/attest interval. A competing listener cannot
        # pass the subsequent exact-child/executable/socket-owner attestation.
        self.socket.close()
        self.ma_fd = os.pidfd_open(pid)

    def attest(self):
        with self.lock:
            if self.closed:raise PermissionError('receiver port scope closed')
            self.peer.verify()
            if not self.peer.alive():raise PermissionError('MA process exited')
            root = Path(f'/proc/{self.peer.pid}/root')
            directory = root / DATA
            for name, value in (('ws.addr', '127.0.0.1'), ('ws.port', str(self.port))):
                path = directory / name
                info = path.lstat()
                if not stat.S_ISREG(info.st_mode) or info.st_uid != 1000 or info.st_size > 128:
                    raise PermissionError('exact receiver endpoint metadata required')
                if path.read_text().strip() != value:
                    raise PermissionError('receiver endpoint differs from reserved port')
            # LISTEN inode must be the reserved IPv4 loopback endpoint in this
            # pinned netns. Port-file contents alone never attest the receiver.
            match = f'0100007F:{self.port:04X}'
            inodes = set()
            for line in Path(f'/proc/{self.peer.pid}/net/tcp').read_text().splitlines()[1:]:
                fields = line.split()
                if len(fields) > 9 and fields[1] == match and fields[3] == '0A':
                    inodes.add(fields[9])
            if len(inodes) != 1:raise PermissionError('one exact reserved receiver listener required')
            expected_inode = 'socket:[' + next(iter(inodes)) + ']'
            children = Path(f'/proc/{self.peer.pid}/task/{self.peer.pid}/children').read_text().split()
            expected_exe = root / BINARY
            expected_hash = hashlib.sha256(expected_exe.read_bytes()).hexdigest()
            pin = Path(SOLOIST_PIN)
            pin_info = pin.lstat()
            if not stat.S_ISREG(pin_info.st_mode) or pin_info.st_uid != 0 or pin_info.st_mode & 0o077:
                raise PermissionError('private root-owned approved Soloist digest required')
            if pin.read_text().strip() != expected_hash:
                raise PermissionError('Soloist binary differs from reviewed immutable digest')
            matches = []
            for item in children:
                child = int(item)
                try:
                    base = Path(f'/proc/{child}')
                    parent, start = proc_stat(child)
                    if parent != self.peer.pid or base.stat().st_uid != 1000:continue
                    if os.readlink(base / 'ns/net') != self.netns:continue
                    if os.readlink(base / 'ns/mnt') != self.peer.reference['process']['mount_namespace']:continue
                    if not os.path.samefile(base / 'exe', expected_exe):continue
                    if not any(os.readlink(fd) == expected_inode for fd in (base / 'fd').iterdir()):continue
                    matches.append((child, start, expected_hash))
                except (OSError, ValueError):continue
            if len(matches) != 1:raise PermissionError('exact MA child Soloist listener owner required')
            observed = matches[0]
            if self.daemon is not None and self.daemon != observed:
                raise PermissionError('daemon changed; fresh guarded reservation required')
            if self.daemon_fd is None:
                candidate=os.pidfd_open(observed[0])
                try:
                    if proc_stat(observed[0])!=(self.peer.pid,observed[1]) or select.select([candidate],[],[],0)[0]:
                        raise PermissionError('daemon identity changed during pidfd binding')
                except BaseException:os.close(candidate);raise
                self.daemon_fd=candidate
            self.daemon = observed
            if not self.alive():raise PermissionError('daemon exited during attestation')
            return {'daemon': list(observed), 'port_protected': True}

    def alive(self):
        return (not self.closed and self.peer.alive() and self.daemon_fd is not None
                and not select.select([self.daemon_fd], [], [], 0)[0])

    def close(self):
        # Never unprotect a still-running daemon. Its existing connections must
        # first end via supported provider stop. Retain deny-all until it exits.
        self.closed = True
        def retire():
            if self.daemon_fd is not None:
                select.select([self.daemon_fd], [], [])
                os.close(self.daemon_fd)
                self.daemon_fd = None
            elif not select.select([self.ma_fd], [], [], 0)[0]:
                # Spawn may not yet have been attested. Keep protection until
                # MA exits, rather than exposing an unobserved orphan listener.
                select.select([self.ma_fd], [], [])
            os.close(self.ma_fd)
            self.guard.release(self.port)
        threading.Thread(target=retire, daemon=True).start()


def new_guard():
    logs = Path('/run/garage-speakers/connect-verifier')
    logs.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = logs.lstat()
    if info.st_uid != 0 or info.st_mode & 0o077:
        raise PermissionError('private root-owned verifier directory required')
    return SocketGuard('/sys/fs/cgroup', logs)
