"""Isolated transaction/graph safety tests; mocks never establish hardware proof."""
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from garage_audio.audio_config import DEFAULT, Configurations, compile_graph, validate_spec
from garage_audio.audio_container import Worker
from garage_audio.audio_device import hardware_graph


def corrected():
    return dict(DEFAULT, layout='powered-sub-2.1', sub=5,
                correction={'left': [{'freq': 80, 'q': 2, 'gain': -4}],
                            'right': [], 'sub': [{'freq': 60, 'q': 1, 'gain': -6}]})


class TransactionTests(unittest.TestCase):
    def test_invalid_correction_rejected(self):
        for field, value in [('gain', .1), ('gain', -7), ('gain', True),
                             ('freq', float('nan')), ('freq', 1001), ('q', 11)]:
            spec = corrected(); spec['correction']['left'][0][field] = value
            with self.subTest(field=field, value=value), self.assertRaises(ValueError): validate_spec(spec)
        for change in ({'correction': {}}, {'correction': {'left': [], 'right': [], 'sub': []}, 'approval': True}):
            with self.assertRaises(ValueError): validate_spec(dict(DEFAULT, **change))
        spec = corrected(); spec['correction']['sub'][0]['freq'] = 301
        with self.assertRaises(ValueError): validate_spec(spec)
        spec = corrected(); spec['layout'] = 'stereo'; spec['sub'] = None
        with self.assertRaises(ValueError): validate_spec(spec)

    def test_persistent_correction_removal_and_rollback(self):
        with tempfile.TemporaryDirectory() as root:
            worker = Worker(root, Path(root)/'evidence')
            try:
                store = worker.configurations
                def accept(spec, operation='apply'):
                    proposal = store.execute('audio.config.propose', {'configuration': spec})
                    args = {'proposal_id': proposal['proposal_id']}
                    with patch('garage_audio.audio_config.subprocess.run', return_value=subprocess.CompletedProcess([], 0, b'', b'')):
                        store.execute('audio.config.validate', args)
                    return store.execute('audio.config.'+operation, args), args
                result, stale = accept(corrected())
                self.assertIsNone(result['loaded'])
                self.assertEqual(Configurations(worker).state['configuration'], corrected())
                result['configuration']['correction']['left'].clear()
                self.assertEqual(store.state['configuration'], corrected())
                with self.assertRaisesRegex(ValueError, 'stale'): store.execute('audio.config.apply', stale)
                proposal = store.execute('audio.config.propose', {'configuration': DEFAULT})
                self.assertIsNone(proposal['diff']['correction']['after'])
                result, _ = accept(DEFAULT, 'rollback')
                self.assertEqual(result['configuration'], DEFAULT)
                self.assertEqual(Configurations(worker).state['revision'], 2)
                self.assertEqual(worker.children, [])
            finally:
                worker.closed.set(); worker.halt('test_exit')

    def test_hardware_policy_and_graph_consumption_without_devices(self):
        spec = dict(DEFAULT, headroom_db=-9, correction={'left': [{'freq': 80, 'q': 2, 'gain': -4}], 'right': [], 'sub': []})
        graph = hardware_graph(compile_graph(spec, '/unused'), 'hw:CARD=UMC1820,DEV=0')
        self.assertEqual(graph['filters']['left_correction_0']['parameters']['gain'], -4)
        self.assertEqual(graph['filters']['headroom']['parameters']['gain'], -9)
        with self.assertRaises(PermissionError): hardware_graph(compile_graph(corrected(), '/unused'), 'hw:CARD=UMC1820,DEV=0')
        with tempfile.TemporaryDirectory() as root:
            worker = Worker(root, Path(root)/'evidence')
            worker.mode = 'hardware'  # Isolated startup seam; no device admission.
            worker.configurations.state['configuration'] = corrected()
            try:
                with patch.object(worker, 'network_valid', return_value=True), patch('garage_audio.audio_device.DeviceLease') as lease:
                    with self.assertRaisesRegex(PermissionError, 'OUT3/4'): worker.start_owned(None)
                    lease.assert_not_called()
                # Hardware mutation remains denied even with a matching-looking proposal.
                args = {'proposal_id': worker.configurations.propose(spec)['proposal_id']}
                with self.assertRaises(PermissionError): worker.configurations.execute('audio.config.validate', args)
                self.assertEqual(worker.children, [])
            finally:
                worker.closed.set(); worker.halt('test_exit')


if __name__ == '__main__': unittest.main()
