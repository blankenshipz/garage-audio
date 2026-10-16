"""Anchored, bounded private JSON files; no path-following writes or shared temps."""
import json
import os
import re
import stat
import uuid
from pathlib import Path
from contextlib import contextmanager


class RecordLimit(ValueError):
    pass


def check(info, directory=False):
    if (not (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode))
            or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077
            or (not directory and info.st_nlink != 1)):
        raise ValueError('Private storage invalid')


def open_root(root, create=False):
    path = Path(root).absolute()
    fd = os.open('/',os.O_RDONLY|os.O_DIRECTORY)
    try:
        for part in path.parts[1:]:
            if part in ('.','..'): raise ValueError('Unsafe root')
            if create:
                try: os.mkdir(part,0o700,dir_fd=fd); os.fsync(fd)
                except FileExistsError: pass
            child = os.open(part,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=fd)
            os.close(fd)
            fd = child
        check(os.fstat(fd),True)
        return fd
    except Exception:
        os.close(fd)
        raise


@contextmanager
def directory(root, collection=None, *, create=False):
    fd = open_root(root,create=create)
    try:
        check(os.fstat(fd), True)
        if collection is not None:
            if not re.fullmatch(r'[a-z-]+', collection):
                raise ValueError('Invalid collection')
            try:
                os.mkdir(collection, 0o700, dir_fd=fd)
                os.fsync(fd)
            except FileExistsError:
                pass
            child = os.open(collection, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            try:
                check(os.fstat(child), True)
                yield child
            finally:
                os.close(child)
        else:
            yield fd
    finally:
        os.close(fd)


def read(fd, name, limit):
    handle = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
    with os.fdopen(handle, 'rb') as stream:
        check(os.fstat(stream.fileno()))
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise RecordLimit('Private record exceeds size limit')
    return json.loads(data)


def write(fd, name, data):
    # Refuse unsafe existing destinations, including hard links; never repair modes.
    try:
        check(os.stat(name, dir_fd=fd, follow_symlinks=False))
    except FileNotFoundError:
        pass
    temp = uuid.uuid4().hex + '.tmp'
    handle = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd)
    try:
        with os.fdopen(handle, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, name, src_dir_fd=fd, dst_dir_fd=fd)
        os.fsync(fd)
    finally:
        try:
            os.unlink(temp, dir_fd=fd)
        except FileNotFoundError:
            pass
