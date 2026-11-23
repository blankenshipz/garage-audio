"""Measurement audio I/O backends (stdlib only).

``ProcessCapture`` runs the operator-configured player and recorder argv (no shell) as
child processes of this server, e.g. ``pw-play``/``pw-record`` or ``aplay``/``arecord``.
The player must target the stack's sink that feeds CamillaDSP (config validation refuses
raw ALSA/DAC devices), so the supervisor keeps DAC ownership and the DSP limits,
crossovers and volume ceiling apply to every measurement signal.

Abort and timeouts only ever signal the ``Popen`` objects this backend started (never a
process group, a name match or a pid read from elsewhere).

``PhoneCaptureBackend`` is a design stub for a phone-browser capture page (see
docs/ops-mcp-measurement.md); it refuses with ``not_implemented``.
"""
import os
import signal
import subprocess
import threading
import time

from .clients import redact, run_argv
from .errors import OpsError

POLL_S = 0.02
STOP_GRACE_S = 2.0
KILL_GRACE_S = 1.0
STDERR_TAIL = 2048
_ENV_KEYS = ('HOME', 'XDG_RUNTIME_DIR', 'DBUS_SESSION_BUS_ADDRESS', 'PIPEWIRE_REMOTE', 'PULSE_SERVER')


def child_env():
    env = {'PATH': os.environ.get('PATH', '/usr/bin:/bin'), 'LC_ALL': 'C'}
    for key in _ENV_KEYS:
        if key in os.environ:
            env[key] = os.environ[key]
    return env


def fill(argv, values):
    """Substitute {file}/{rate}/{channels}/{seconds} placeholders in each argument (no shell)."""
    out = []
    for item in argv:
        for key, value in values.items():
            item = item.replace('{' + key + '}', str(value))
        out.append(item)
    return out


class Stopped(Exception):
    """Raised inside a run when abort or the watchdog deadline stopped the children."""

    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


class CaptureBackend:
    """Interface: play a WAV through the stack sink while recording the measurement microphone."""

    name = 'base'
    provides_timing = True

    def play_and_record(self, *, play_file, record_file, rate, out_channels, capture_channels, play_seconds,
                        preroll, tail, should_stop, deadline):
        raise NotImplementedError

    def record(self, *, record_file, rate, capture_channels, seconds, should_stop, deadline):
        raise NotImplementedError

    def stop(self):
        """Stop only the children this backend started. Returns the number signalled."""
        return 0


class PhoneCaptureBackend(CaptureBackend):
    """Design stub: a server-hosted page records with MediaRecorder and uploads the file.

    Lower accuracy (uncalibrated, AGC/noise suppression in the browser pipeline), no
    sample-accurate timing (no loopback or shared clock), magnitude only.
    """

    name = 'phone'
    provides_timing = False

    def _refuse(self):
        raise OpsError('not_implemented', 'phone-microphone capture is a documented design stub and is not '
                       'implemented; use a USB or interface measurement microphone (measurement.capture="process")')

    def play_and_record(self, **_kwargs):
        self._refuse()

    def record(self, **_kwargs):
        self._refuse()


class ProcessCapture(CaptureBackend):
    name = 'process'

    def __init__(self, play_argv, record_argv, workdir_stderr=None):
        self.play_argv = play_argv
        self.record_argv = record_argv
        self._lock = threading.Lock()
        self._children = []
        self._stderr_dir = workdir_stderr

    # -- process handling ------------------------------------------------------------------
    def _spawn(self, argv, label, values, stderr_path):
        command = fill(argv, values)
        fd = os.open(stderr_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
        try:
            proc = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=fd,
                                    env=child_env(), start_new_session=True, close_fds=True)
        except FileNotFoundError:
            raise OpsError('command_missing', f'{label} command {os.path.basename(command[0])} is not installed '
                           'or not on PATH') from None
        except OSError:
            raise OpsError('command_failed', f'{label} command could not be started') from None
        finally:
            os.close(fd)
        with self._lock:
            self._children.append(proc)
        return proc

    @staticmethod
    def _tail(path):
        try:
            with open(path, 'rb') as handle:
                handle.seek(0, os.SEEK_END)
                size = handle.tell()
                handle.seek(max(0, size - STDERR_TAIL))
                text = handle.read().decode('utf-8', 'replace')
        except OSError:
            return ''
        return ' | '.join(redact(text)[-5:])[-600:]

    @staticmethod
    def _signal(proc, sig):
        if proc.poll() is None:
            try:
                proc.send_signal(sig)  # Popen guards against pid reuse until it is reaped
            except ProcessLookupError:
                pass

    def _terminate(self, proc, graceful):
        """Stop one child we started: SIGINT (recorder finalises its file) -> SIGTERM -> SIGKILL."""
        sequence = ([signal.SIGINT, signal.SIGTERM] if graceful else [signal.SIGTERM]) + [signal.SIGKILL]
        for sig in sequence:
            if proc.poll() is not None:
                break
            self._signal(proc, sig)
            try:
                proc.wait(timeout=STOP_GRACE_S if sig == signal.SIGINT else KILL_GRACE_S)
            except subprocess.TimeoutExpired:
                continue
        if proc.poll() is None:
            proc.wait(timeout=KILL_GRACE_S)

    def stop(self):
        with self._lock:
            children = [p for p in self._children if p.poll() is None]
        for proc in children:
            self._signal(proc, signal.SIGTERM)
        return len(children)

    def _reap_all(self):
        with self._lock:
            children, self._children = self._children, []
        for proc in children:
            if proc.poll() is None:
                self._terminate(proc, graceful=False)

    @staticmethod
    def _wait(seconds, should_stop, deadline, watch=None, until_exit=None):
        """Sleep up to ``seconds`` while polling abort/deadline. Returns early when ``until_exit`` exits."""
        end = time.monotonic() + seconds
        while True:
            if should_stop():
                raise Stopped('aborted')
            now = time.monotonic()
            if now >= deadline:
                raise Stopped('timeout')
            if until_exit is not None and until_exit.poll() is not None:
                return 'exited'
            if watch is not None and watch.poll() is not None:
                return 'watch_exited'
            if now >= end:
                return 'elapsed'
            time.sleep(min(POLL_S, max(0.0, end - now)))

    # -- runs ----------------------------------------------------------------------------
    def play_and_record(self, *, play_file, record_file, rate, out_channels, capture_channels, play_seconds,
                        preroll, tail, should_stop, deadline):
        base = os.path.dirname(play_file)
        rec_err, play_err = os.path.join(base, 'recorder.err'), os.path.join(base, 'player.err')
        total = preroll + play_seconds + tail
        try:
            recorder = self._spawn(self.record_argv, 'recorder', {'file': record_file, 'rate': rate,
                                   'channels': capture_channels, 'seconds': int(total + 1.999)}, rec_err)
            if self._wait(preroll, should_stop, deadline, watch=recorder) == 'watch_exited':
                raise OpsError('capture_failed', 'the recorder exited before playback started: '
                               + (self._tail(rec_err) or f'exit status {recorder.returncode}'))
            player = self._spawn(self.play_argv, 'player', {'file': play_file, 'rate': rate,
                                 'channels': out_channels, 'seconds': int(play_seconds + 1.999)}, play_err)
            self._wait(play_seconds + 30.0, should_stop, deadline, until_exit=player)
            if player.returncode != 0:
                raise OpsError('playback_failed', 'the player failed (is the stack sink available?): '
                               + (self._tail(play_err) or f'exit status {player.returncode}'))
            self._wait(tail, should_stop, deadline)
            self._terminate(recorder, graceful=True)
            return {'status': 'ok', 'recorder_exit': recorder.returncode}
        except Stopped as stopped:
            self._reap_all()
            return {'status': stopped.reason}
        finally:
            self._reap_all()

    def record(self, *, record_file, rate, capture_channels, seconds, should_stop, deadline):
        base = os.path.dirname(record_file)
        rec_err = os.path.join(base, 'recorder.err')
        try:
            recorder = self._spawn(self.record_argv, 'recorder', {'file': record_file, 'rate': rate,
                                   'channels': capture_channels, 'seconds': int(seconds + 1.999)}, rec_err)
            if self._wait(seconds, should_stop, deadline, watch=recorder) == 'watch_exited' \
                    and recorder.returncode not in (0, None):
                raise OpsError('capture_failed', 'the recorder failed: '
                               + (self._tail(rec_err) or f'exit status {recorder.returncode}'))
            self._terminate(recorder, graceful=True)
            return {'status': 'ok', 'recorder_exit': recorder.returncode}
        except Stopped as stopped:
            self._reap_all()
            return {'status': stopped.reason}
        finally:
            self._reap_all()


def list_capture_devices(argvs, runner=run_argv):
    """Run the configured read-only device listing commands (bounded, redacted output)."""
    out = []
    for argv in argvs:
        try:
            code, stdout, stderr = runner(argv, 5)
        except OpsError as error:
            out.append({'command': argv, 'error': error.payload()})
            continue
        lines = [line for line in redact(stdout or stderr) if line.strip()][:80]
        out.append({'command': argv, 'exit_status': code, 'lines': lines})
    return out
