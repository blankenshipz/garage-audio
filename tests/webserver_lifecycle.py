"""Lifecycle tests: real SIGTERM, admission stop, drain then owned-store close.

No provider, MA, credentials or sound. Target container journey supplies live proof.
"""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from types import SimpleNamespace

from garage_audio.webserver import Server


class Lifecycle(unittest.TestCase):
    def test_close_invalidates_grants_and_releases_store(self):
        events=[]
        app=SimpleNamespace(jobs=SimpleNamespace(invalidate_all=lambda reason:events.append(reason)),
                            agent_harness=SimpleNamespace(close=lambda:events.append('store_closed')))
        server=Server(('127.0.0.1',0),app,'unused','web')
        server.server_close()
        self.assertEqual(events,['application_shutdown','store_closed'])

    def test_failed_job_teardown_still_releases_store(self):
        events=[]
        def fail(reason): raise OSError('synthetic storage failure')
        app=SimpleNamespace(jobs=SimpleNamespace(invalidate_all=fail),
                            agent_harness=SimpleNamespace(close=lambda:events.append('store_closed')))
        server=Server(('127.0.0.1',0),app,'unused','web')
        with self.assertRaises(OSError): server.server_close()
        self.assertEqual(events,['store_closed'])

    def test_handler_threads_drain_and_socket_is_bounded(self):
        self.assertFalse(Server.daemon_threads)
        self.assertTrue(Server.block_on_close)

    def test_sigterm_closes_owned_resources_after_loop_exit(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            child=subprocess.Popen([sys.executable,'-c',r'''
import json,sys
from pathlib import Path
from garage_audio.webserver import serve_until_signal
root=Path(sys.argv[1])
class Probe:
 def serve_forever(self,poll_interval):
  import threading
  self.event=threading.Event();(root/'ready').touch();self.event.wait(5)
  (root/'loop-ended').touch()
 def shutdown(self): self.event.set()
 def server_close(self):
  assert (root/'loop-ended').exists()
  (root/'closed').touch()
serve_until_signal(Probe())
''',directory],stdout=subprocess.PIPE,stderr=subprocess.PIPE)
            try:
                deadline=time.monotonic()+5
                while not (root/'ready').exists():
                    if time.monotonic()>deadline: self.fail('child readiness timeout')
                    time.sleep(.01)
                child.send_signal(signal.SIGTERM)
                stdout,stderr=child.communicate(timeout=5)
                self.assertEqual(child.returncode,0,stderr.decode())
                self.assertTrue((root/'closed').exists())
            finally:
                if child.poll() is None: child.kill();child.communicate()


if __name__=='__main__': unittest.main()
