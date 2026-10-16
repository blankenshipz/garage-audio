"""Owned Linux exec wrapper: parent-death race check without threaded preexec_fn."""
import ctypes
import os
import signal
import sys


def main():
    expected = int(os.environ['GARAGE_AUDIO_PARENT_PID'])
    if ctypes.CDLL(None).prctl(1, signal.SIGKILL, 0, 0, 0) or os.getppid() != expected:
        raise SystemExit(125)
    # Only worker-created fixed argv reaches this private package executable.
    if sys.argv[1] == 'capture':
        command = ['pw-record', '--target', 'garage_music',
            '--properties=stream.capture.sink=true node.dont-fallback=true node.dont-move=true',
            '--rate', '48000', '--channels', '2', '--format', 'f32', '-']
    elif sys.argv[1] == 'dsp':
        command = ['/opt/garage-audio/dsp/camilladsp', *sys.argv[2:]]
    elif sys.argv[1] == 'scalar-input' and len(sys.argv) == 3:
        command = [sys.executable, '-m', 'garage_audio.audio_scalar', 'input', sys.argv[2]]
    else:
        raise SystemExit(64)
    os.execvp(command[0], command)


if __name__ == '__main__':
    main()
