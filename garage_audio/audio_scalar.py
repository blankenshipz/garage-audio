"""Qualification only: bounded scalar meters; PCM exists only in RAM and pipes."""
import array
import json
import math
import os
from pathlib import Path
import select
import stat
import sys
import threading
import time

SEGMENTS = 8
BUFFER = 65536
WINDOW = 8192  # scalar-only aggregation: retain every frame, eightfold fewer rows
SECONDS = 300


class Meter:
    def __init__(self, path, channels, provenance):
        self.channels, self.frames = channels, 0
        self.pending = bytearray()
        self.log = open(path, 'x', buffering=1)
        self.provenance = provenance
        self.first_byte = self.first_nonzero = None
        self.first_nonzero_frame = None
        self.closed = False
        self.peaks = [0.0]*channels
        self.squares = [0.0]*channels
        self.nonzero = [0]*channels
        self.window_frames = 0
        self.log_bytes = 0
        self.log_limit = (8 if channels == 12 else 4)*1024*1024

    def record(self, value):
        encoded = json.dumps(value, separators=(',', ':'))+'\n'
        self.log_bytes += len(encoded.encode())
        if self.log_bytes > self.log_limit:
            raise RuntimeError('scalar receipt budget exhausted')
        self.log.write(encoded)

    def feed(self, data):
        # Four <=8KiB transient byte/array views + <one frame residual stay <64KiB.
        if len(data) > 8192:
            raise ValueError('scalar buffer bound')
        received = time.monotonic_ns()
        if data and self.first_byte is None:
            self.first_byte = received
        self.pending.extend(data)
        frame_size = self.channels*4
        size = len(self.pending)//frame_size*frame_size
        values = array.array('f')
        values.frombytes(self.pending[:size])
        del self.pending[:size]
        if sys.byteorder != 'little':
            values.byteswap()
        if values.itemsize != 4:
            raise ValueError('F32_LE required')
        for index, value in enumerate(values):
            if not math.isfinite(value):
                raise ValueError('finite F32_LE required')
            channel = index % self.channels
            self.peaks[channel] = max(self.peaks[channel], abs(value))
            self.squares[channel] += value*value
            self.nonzero[channel] += value != 0
            if value and self.first_nonzero is None:
                self.first_nonzero = received
                self.first_nonzero_frame = self.frames+self.window_frames
            if channel != self.channels-1:
                continue
            self.window_frames += 1
            if self.window_frames != WINDOW:
                continue
            record = {'stage': self.provenance, 'pid': os.getpid(),
                      'observed_ns': received, 'frame_start': self.frames,
                      'frames': WINDOW, 'channels': self.channels, 'rate': 48000,
                      'format': 'F32_LE', 'peak': self.peaks, 'sum_squares': self.squares,
                      'nonzero': self.nonzero, 'first_byte_read_ns': self.first_byte,
                      'first_nonzero_read_ns': self.first_nonzero,
                      'first_nonzero_frame': self.first_nonzero_frame,
                      'pending_bytes': len(self.pending),
                      'production_timestamp_proven': False}
            self.record(record)
            self.frames += WINDOW
            self.window_frames = 0
            self.peaks = [0.0]*self.channels
            self.squares = [0.0]*self.channels
            self.nonzero = [0]*self.channels
        if len(self.pending) >= frame_size:
            raise RuntimeError('scalar frame bound')

    def close(self):
        if not self.closed:
            self.closed = True
            try:
                self.record({'stage': self.provenance, 'frames': self.frames,
                             'partial_window_frames': self.window_frames,
                             'partial_peak': self.peaks, 'partial_sum_squares': self.squares,
                             'partial_nonzero': self.nonzero,
                             'first_nonzero_read_ns': self.first_nonzero,
                             'first_nonzero_frame': self.first_nonzero_frame,
                             'discarded_residual_bytes': len(self.pending)})
            finally:
                self.pending.clear()
                self.log.close()


class ScalarBank:
    """Private fixed FIFOs pinned by inherited descriptors, never path-opened by DSP."""
    def __init__(self, root, generation):
        self.root = Path(root)/('scalar-'+generation)
        self.root.mkdir(mode=0o700)
        directory = self.root.lstat()
        self.directory_identity = (directory.st_dev, directory.st_ino)
        self.closed, self.failed = threading.Event(), threading.Event()
        self.readers, self.fds, self.identities = [], [], []
        self.ready = []
        try:
            for segment in range(1, SEGMENTS+1):
                path = self.root/f'{segment}.fifo'
                os.mkfifo(path, 0o600)
                fd = os.open(path, os.O_RDWR | os.O_NONBLOCK | os.O_NOFOLLOW)
                info = os.fstat(fd)
                if not stat.S_ISFIFO(info.st_mode) or info.st_uid != os.getuid():
                    raise PermissionError('private FIFO required')
                self.fds.append(fd)
                self.identities.append((info.st_dev, info.st_ino))
                ready = threading.Event()
                self.ready.append(ready)
                thread = threading.Thread(target=self.drain, args=(segment, fd, ready), daemon=True)
                self.readers.append(thread)
                thread.start()
            if not all(ready.wait(2) for ready in self.ready) or not self.healthy():
                raise RuntimeError('scalar readers not ready')
        except BaseException:
            self.close()
            raise

    def healthy(self):
        if self.failed.is_set() or self.closed.is_set():
            return False
        try:
            directory = self.root.lstat()
            if not stat.S_ISDIR(directory.st_mode) or (directory.st_dev, directory.st_ino) != self.directory_identity or directory.st_uid != os.getuid() or stat.S_IMODE(directory.st_mode) != 0o700:
                self.failed.set()
                return False
            for i, fd in enumerate(self.fds):
                info = (self.root/f'{i+1}.fifo').lstat()
                actual = os.fstat(fd)
                if not stat.S_ISFIFO(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1 or (info.st_dev, info.st_ino) != self.identities[i] or (actual.st_dev, actual.st_ino) != self.identities[i]:
                    self.failed.set()
                    return False
            if not all(t.is_alive() for t in self.readers):
                self.failed.set()
                return False
            return True
        except OSError:
            self.failed.set()
            return False

    def writer(self, segment):
        if not 1 <= segment <= SEGMENTS or not self.healthy():
            self.failed.set()
            raise RuntimeError('scalar exhausted or reader/FIFO lost')
        # Reopen the PINNED FIFO object, not its replaceable directory entry.
        fd = os.open(f'/proc/self/fd/{self.fds[segment-1]}', os.O_WRONLY)
        info = os.fstat(fd)
        if not stat.S_ISFIFO(info.st_mode) or (info.st_dev, info.st_ino) != self.identities[segment-1]:
            os.close(fd)
            raise PermissionError('inherited FIFO identity differs')
        return fd

    def drain(self, segment, fd, ready):
        meter = None
        try:
            meter = Meter(self.root/f'{segment}.output.jsonl', 12, 'dsp-output-read')
            ready.set()
            while not self.closed.is_set():
                if select.select([fd], [], [], .05)[0]:
                    meter.feed(os.read(fd, 8192))
        except BaseException:
            self.failed.set()
        finally:
            if meter:
                try:meter.close()
                except Exception:self.failed.set()

    def close(self):
        self.closed.set()
        for thread in self.readers:
            thread.join(timeout=2)
        for fd in self.fds:
            os.close(fd)
        self.fds.clear()


def input_meter(path):
    """No withholding/dropping of early input: measure then forward every byte."""
    meter = Meter(path, 2, 'capture-before-dsp-read')
    forwarded = 0
    next_forward_record = 0
    try:
        while True:
            data = os.read(0, 8192)
            if not data:
                break
            meter.feed(data)
            remaining = memoryview(data)
            while remaining:
                count = os.write(1, remaining)
                remaining = remaining[count:]
                forwarded += count
            if forwarded >= next_forward_record:
                meter.record({'stage': 'capture-forward-complete', 'pid': os.getpid(),
                              'observed_ns': time.monotonic_ns(), 'bytes_forwarded': forwarded,
                              'frame_bytes': 8, 'pipe_backpressure_may_delay': True})
                next_forward_record = forwarded + WINDOW*8
    finally:
        meter.record({'stage': 'capture-forward-terminal', 'pid': os.getpid(),
                      'bytes_forwarded': forwarded, 'frame_bytes': 8})
        meter.close()


if __name__ == '__main__':
    if len(sys.argv) != 3 or sys.argv[1] != 'input':
        raise SystemExit(64)
    input_meter(sys.argv[2])
