"""Focused configuration tests; container test uses actual pinned Camilla validator."""
import copy
import tempfile
import unittest
from pathlib import Path
from garage_audio.audio_config import DEFAULT, compile_graph, validate_spec
from garage_audio.audio_container import Worker


class ConfigurationTests(unittest.TestCase):
    def test_routes(self):
        graph = compile_graph(DEFAULT, '/unused.f32')
        self.assertEqual([r['dest'] for r in graph['mixers']['route']['mapping']], [2, 3])
        self.assertEqual(graph['filters']['headroom']['parameters']['gain'], -6)
        invalid = dict(DEFAULT, right=3)
        with self.assertRaises(ValueError): validate_spec(invalid)

    @unittest.skipUnless(Path('/opt/garage-audio/dsp/camilladsp').exists(), 'pinned Linux container required')
    def test_real_validator_apply_rollback_reconnect(self):
        with tempfile.TemporaryDirectory() as root:
            worker = Worker(root, Path(root)/'evidence')
            try:
                store = worker.configurations
                spec = dict(DEFAULT, layout='powered-sub-2.1', sub=5)
                proposal = store.execute('audio.config.propose', {'configuration': spec})
                args = {'proposal_id': proposal['proposal_id']}
                with self.assertRaises(ValueError): store.execute('audio.config.apply', args)
                self.assertTrue(store.execute('audio.config.validate', args)['validated'])
                result = store.execute('audio.config.apply', args)
                self.assertEqual(result['revision'], 1)
                self.assertIsNone(result['loaded'])
                self.assertEqual(result['runtime_state'], 'not_running')
                with self.assertRaises(ValueError): store.execute('audio.config.apply', args)
                rollback = store.execute('audio.config.propose', {'configuration': result['previous']})
                args = {'proposal_id': rollback['proposal_id']}
                store.execute('audio.config.validate', args)
                self.assertEqual(store.execute('audio.config.rollback', args)['configuration'], DEFAULT)
                from garage_audio.audio_config import Configurations
                self.assertEqual(Configurations(worker).get()['revision'], 2)
                self.assertEqual(worker.children, [])
            finally:
                worker.closed.set()
                worker.halt('test_exit')


if __name__ == '__main__': unittest.main()
