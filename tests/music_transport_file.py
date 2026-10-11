"""Real FILE DAC process + independent synthetic transport; not MA/hardware proof."""
import subprocess,unittest
from unittest.mock import patch
from tests.speaker_guardian import fixtures

class FileTransportTests(unittest.TestCase):
    def fixture(self):
        f=fixtures.NativeFileTests();f.setUp();self.addCleanup(f.doCleanups);f.peer.realm='phone'
        generation=f.policy.generation()
        f.policy.execute({'cmd':'phone-start','generation':generation},f.peer)
        status=f.policy.execute({'cmd':'initialize','generation':generation})
        self.transport=subprocess.Popen(['sleep','30'])
        self.addCleanup(self.close_transport)
        return f,status['generation']
    def close_transport(self):
        if self.transport.poll() is None:self.transport.terminate()
        self.transport.wait(timeout=3)
    def test_real_file_deadline_closes_output_not_independent_transport(self):
        f,g=self.fixture()
        with patch('garage_audio.broker.SECONDS',2):
            f.policy.execute({'cmd':'phone-prepare','generation':g},f.peer)
            f.policy.execute({'cmd':'phone-open','generation':g},f.peer)
            f.process.wait(timeout=3)
        self.assertIsNone(self.transport.poll())
    def test_real_file_stop_closes_output_not_independent_transport(self):
        f,g=self.fixture();f.policy.execute({'cmd':'phone-prepare','generation':g},f.peer)
        f.policy.execute({'cmd':'speaker-stop'});f.process.wait(timeout=3)
        self.assertIsNone(self.transport.poll())

if __name__=='__main__':unittest.main()
