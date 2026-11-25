"""Synthetic: a capture child that fails at segment start halts as capture_connect_failed."""
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from garage_audio import audio_container
from garage_audio.audio_container import Worker
try:
    from garage_audio.audio_observer import DecisionTrace
except ImportError:  # aiohttp (observer dependency) not installed
    DecisionTrace = None

REAL_POPEN = subprocess.Popen


def fake_popen(capture_code):
    def popen(argv, **kwargs):
        role = argv[-1] if argv[-1] == 'capture' else 'dsp'
        code = 'import sys;sys.exit(%d)' % capture_code if role == 'capture' else 'import time;time.sleep(30)'
        kwargs.pop('pass_fds', None)
        return REAL_POPEN([sys.executable, '-c', code], **{k: v for k, v in kwargs.items() if k in ('stdout', 'stderr', 'stdin')})
    return popen


class CaptureFailure(unittest.TestCase):
    def worker(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        worker = Worker(temp.name, Path(temp.name)/'evidence')
        self.addCleanup(lambda: (worker.closed.set(), worker.halt('test_exit')))
        worker.baseline = True
        worker.last_observation = time.monotonic()
        return worker

    def start(self, worker, capture_code):
        with patch.object(worker, 'network_valid', return_value=True), \
             patch.object(audio_container.subprocess, 'Popen', fake_popen(capture_code)), \
             patch.object(audio_container, 'CAPTURE_STARTUP_WINDOW', 3):
            worker.start_owned(None)

    def test_immediate_capture_exit_halts_with_reason_in_status_and_trace(self):
        worker = self.worker()
        with self.assertRaisesRegex(RuntimeError, 'capture_connect_failed') as caught:
            self.start(worker, 1)
        status = worker.status()
        self.assertEqual((status['state'], status['reason'], status['owned_children']), ('off', 'capture_connect_failed', []))
        if DecisionTrace is None:
            self.skipTest('observer dependency aiohttp missing; trace assertion not run')
        with tempfile.TemporaryDirectory() as root:
            trace = DecisionTrace(Path(root)/'trace.json')
            try:
                trace.ipc_failure(caught.exception, epoch=1)
                self.assertEqual(trace.rows[-1]['ipc_error'], 'capture_connect_failed')
                self.assertIs(trace.rows[-1]['ok'], False)
            finally:
                trace.close()

    def test_exit_zero_in_window_is_also_a_failure(self):
        worker = self.worker()
        with self.assertRaisesRegex(RuntimeError, 'capture_connect_failed'):
            self.start(worker, 0)
        self.assertEqual(worker.status()['reason'], 'capture_connect_failed')

    def test_healthy_capture_reaches_playing(self):
        worker = self.worker()
        with patch.object(audio_container, 'CAPTURE_STARTUP_WINDOW', .1):
            with patch.object(worker, 'network_valid', return_value=True), \
                 patch.object(audio_container.subprocess, 'Popen', lambda argv, **kw: REAL_POPEN(
                     [sys.executable, '-c', 'import time;time.sleep(30)'],
                     **{k: v for k, v in kw.items() if k in ('stdout', 'stderr', 'stdin')})):
                worker.start_owned(None)
        self.assertEqual(worker.status()['state'], 'playing')


if __name__ == '__main__':
    unittest.main()
