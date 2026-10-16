"""Anchored generated job artifacts: no follow, no overwrite, no special files."""
from contextlib import contextmanager
import os
from pathlib import Path
import re
import stat
import uuid

@contextmanager
def directory_fd(directory):
    directory = Path(directory)
    if not re.fullmatch('[a-f0-9]{32}',directory.name) or directory.parent.name != 'measurement-jobs':
        raise ValueError('Generated measurement job directory required')
    directory = directory.absolute()
    if '..' in directory.parts: raise ValueError('Literal generated job path required')
    # Walk from an anchored root: intermediate-component symlinks cannot redirect
    # an absolute open between validation and use.
    fd = os.open('/',os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:
        for index,name in enumerate(directory.parts[1:]):
            opened = os.open(name,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=fd)
            os.close(fd); fd = opened
            if index >= len(directory.parts)-3:
                info = os.fstat(fd)
                if info.st_uid != os.getuid() or info.st_mode & 0o077: raise ValueError('Private owned job parent required')
        yield fd
    finally: os.close(fd)

def regular(info):
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1 or info.st_mode & 0o077:
        raise ValueError('Private owned single-link regular artifact required')

def admit(directory, fresh, existing=()):
    with directory_fd(directory) as fd:
        for name in fresh:
            try: os.stat(name,dir_fd=fd,follow_symlinks=False)
            except FileNotFoundError: continue
            raise ValueError('Execution artifacts must be fresh; no overwrite/resume')
        for name in existing:
            regular(os.stat(name,dir_fd=fd,follow_symlinks=False))

def exclusive(directory, name, mode='wb'):
    if Path(name).name != name: raise ValueError('Literal generated artifact name required')
    with directory_fd(directory) as fd:
        opened = os.open(name,os.O_RDWR|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=fd)
        regular(os.fstat(opened))
    return os.fdopen(opened,mode)

def write(directory, name, data):
    with exclusive(directory,name) as file: file.write(data)

def read(directory, name, limit):
    with directory_fd(directory) as fd:
        regular(os.stat(name,dir_fd=fd,follow_symlinks=False))
        opened = os.open(name,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=fd)
        try:
            info = os.fstat(opened); regular(info)
            if info.st_size > limit: raise ValueError('Bounded artifact required')
            return os.read(opened,limit+1)
        finally: os.close(opened)

@contextmanager
def read_handle(directory, name):
    with directory_fd(directory) as fd:
        opened = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
        try: regular(os.fstat(opened))
        except BaseException:
            os.close(opened); raise
    with os.fdopen(opened, 'rb') as file: yield file

def replace_json(directory, name, data):
    """Private anchored journal replacement, separate from top-level app records."""
    if name != 'job.json': raise ValueError('only job journal replacement allowed')
    temporary = 'journal-'+uuid.uuid4().hex+'.tmp'
    with exclusive(directory, temporary) as file:
        file.write(data); file.flush(); os.fsync(file.fileno())
    with directory_fd(directory) as fd:
        try: regular(os.stat(name, dir_fd=fd, follow_symlinks=False))
        except FileNotFoundError: pass
        os.rename(temporary, name, src_dir_fd=fd, dst_dir_fd=fd)
        os.fsync(fd)
