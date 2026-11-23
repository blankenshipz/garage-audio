"""Ops MCP CamillaDSP tools over a fake CamillaDSP and a fake websocket (no network)."""
import copy
import importlib.util
import json
import unittest
from pathlib import Path

from garage_audio.ops_mcp import clients, dsp, tools
from garage_audio.ops_mcp.errors import OpsError

_spec = importlib.util.spec_from_file_location('ops_mcp_fakes', Path(__file__).parent / 'fixtures/ops_mcp/fakes.py')
fakes = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fakes)

WRITE = {'allow_write': True}


class ReadTools(unittest.TestCase):
    def test_levels_and_partial_failure(self):
        h = fakes.Harness(self)
        levels = tools.call(h.ctx, 'dsp_get_levels', {})
        self.assertEqual(levels['state'], 'Running')
        self.assertEqual(levels['clipped_samples'], 0)
        self.assertEqual(levels['volume_db'], -20.0)
        self.assertEqual(levels['volume_ceiling_db'], -10.0)

        def broken():
            raise OpsError('camilladsp_unsupported', 'CamillaDSP does not support GetClippedSamples')
        h.camilla.get_clipped_samples = broken
        levels = tools.call(h.ctx, 'dsp_get_levels', {})
        self.assertEqual(levels['clipped_samples']['error']['code'], 'camilladsp_unsupported')
        self.assertEqual(levels['state'], 'Running')

    def test_filters_summary_and_config(self):
        h = fakes.Harness(self)
        summary = tools.call(h.ctx, 'dsp_get_filters', {})
        self.assertEqual(summary['playback_channels'], 4)
        self.assertEqual(summary['channel_map'], {'left': 0, 'right': 1, 'sub': 2})
        self.assertEqual(summary['filters']['peq_left_1']['parameters']['gain'], -3.0)
        self.assertEqual(summary['limit_violations'], [])
        active = tools.call(h.ctx, 'dsp_get_config', {})
        self.assertEqual(active['config'], fakes.base_config())

    def test_unreachable_is_structured(self):
        h = fakes.Harness(self, camilla=fakes.FakeCamilla(reachable=False))
        with self.assertRaises(OpsError) as caught:
            tools.call(h.ctx, 'dsp_get_config', {})
        self.assertEqual(caught.exception.code, 'camilladsp_unreachable')


class Pipeline(unittest.TestCase):
    def test_ensure_in_pipeline_modern_and_legacy(self):
        config = fakes.base_config()
        dsp.ensure_in_pipeline(config, 'peq_left_1', 0)          # already present: untouched
        self.assertEqual(config['pipeline'], fakes.base_config()['pipeline'])
        dsp.ensure_in_pipeline(config, 'ops_sub_delay', 2)
        self.assertEqual(config['pipeline'][-1], {'type': 'Filter', 'names': ['ops_sub_delay'], 'channels': [2]})
        dsp.ensure_in_pipeline(config, 'ops_sub_gain', 2)        # joins the ops-owned step
        self.assertEqual(config['pipeline'][-1]['names'], ['ops_sub_delay', 'ops_sub_gain'])
        legacy = {'pipeline': [{'type': 'Filter', 'channel': 0, 'names': ['x']}]}
        dsp.ensure_in_pipeline(legacy, 'ops_left_gain', 0)
        self.assertEqual(legacy['pipeline'][-1], {'type': 'Filter', 'names': ['ops_left_gain'], 'channel': 0})

    def test_remove_filters_drops_empty_steps(self):
        config = fakes.base_config()
        dsp.remove_filters(config, ['sub_lowpass'])
        self.assertNotIn('sub_lowpass', config['filters'])
        self.assertEqual(len(config['pipeline']), 2)


class Edits(unittest.TestCase):
    def test_update_existing_peq(self):
        h = fakes.Harness(self, WRITE)
        result = tools.call(h.ctx, 'dsp_update_peq', {'filter': 'peq_left_1', 'gain': -5, 'reason': 'tame 120 Hz'})
        self.assertTrue(result['readback_verified'])
        self.assertEqual(h.camilla.config['filters']['peq_left_1']['parameters'],
                         {'type': 'Peaking', 'freq': 120.0, 'q': 2.0, 'gain': -5.0})
        # read, validate, (backup on disk), apply, read back
        self.assertEqual(h.camilla.events, ['get_config', 'validate', 'set_config', 'get_config'])
        backups = tools.call(h.ctx, 'dsp_list_backups', {})['backups']
        self.assertEqual(backups[0]['id'], result['backup_id'])
        self.assertEqual(backups[0]['reason'], 'tame 120 Hz')

    def test_create_peq_needs_channel_and_all_params(self):
        h = fakes.Harness(self, WRITE)
        with self.assertRaises(OpsError) as caught:
            tools.call(h.ctx, 'dsp_update_peq', {'filter': 'new_peq', 'gain': -2})
        self.assertEqual(caught.exception.code, 'not_found')
        with self.assertRaises(OpsError) as caught:
            tools.call(h.ctx, 'dsp_update_peq', {'filter': 'new_peq', 'channel': 'right', 'gain': -2})
        self.assertEqual(caught.exception.code, 'invalid_argument')
        result = tools.call(h.ctx, 'dsp_update_peq', {'filter': 'new_peq', 'channel': 'right', 'freq': 60,
                                                      'q': 4, 'gain': -6})
        self.assertEqual(result['changed_filters'], ['new_peq'])
        step = h.camilla.config['pipeline'][-1]
        self.assertEqual(step, {'type': 'Filter', 'names': ['new_peq'], 'channels': [1]})

    def test_wrong_type_and_unmapped_channel(self):
        h = fakes.Harness(self, {'allow_write': True, 'camilladsp': {'channels': {'left': 0, 'right': 1}}})
        with self.assertRaises(OpsError) as caught:
            tools.call(h.ctx, 'dsp_update_peq', {'filter': 'sub_lowpass', 'gain': -1})
        self.assertEqual(caught.exception.code, 'wrong_filter_type')
        with self.assertRaises(OpsError) as caught:
            tools.call(h.ctx, 'dsp_set_channel_delay', {'channel': 'sub', 'delay_ms': 1})
        self.assertEqual(caught.exception.code, 'channel_not_mapped')
        self.assertNotIn('set_config', h.camilla.events)
        self.assertEqual(tools.call(h.ctx, 'dsp_list_backups', {})['backups'], [])

    def test_delay_and_gain_filters(self):
        h = fakes.Harness(self, WRITE)
        tools.call(h.ctx, 'dsp_set_channel_delay', {'channel': 'sub', 'delay_ms': 3.5})
        tools.call(h.ctx, 'dsp_set_channel_gain', {'channel': 'sub', 'gain_db': -2.5})
        filters = h.camilla.config['filters']
        self.assertEqual(filters['ops_sub_delay'], {'type': 'Delay', 'parameters': {'delay': 3.5, 'unit': 'ms',
                                                                                    'subsample': False}})
        self.assertEqual(filters['ops_sub_gain']['parameters']['gain'], -2.5)
        self.assertEqual(h.camilla.config['pipeline'][-1], {'type': 'Filter', 'names': ['ops_sub_delay', 'ops_sub_gain'],
                                                            'channels': [2]})
        # Updating again edits in place; the pipeline is not duplicated.
        tools.call(h.ctx, 'dsp_set_channel_delay', {'channel': 'sub', 'delay_ms': 4})
        self.assertEqual(sum('ops_sub_delay' in (s.get('names') or []) for s in h.camilla.config['pipeline']), 1)
        self.assertEqual(len(tools.call(h.ctx, 'dsp_list_backups', {})['backups']), 3)

    def test_volume_and_mute_are_backed_up(self):
        h = fakes.Harness(self, WRITE)
        result = tools.call(h.ctx, 'dsp_set_main_volume', {'volume_db': -25})
        self.assertEqual(result['readback_db'], -25.0)
        self.assertEqual(h.camilla.volume, -25.0)
        record = h.ctx.backups.get(result['backup_id'])
        self.assertEqual(record['volume_db'], -20.0)   # pre-change volume is what the backup keeps
        with self.assertRaises(OpsError):
            tools.call(h.ctx, 'dsp_set_main_volume', {'volume_db': -5})   # above the -10 dB default ceiling
        self.assertEqual(h.camilla.volume, -25.0)
        muted = tools.call(h.ctx, 'dsp_set_mute', {'muted': True})
        self.assertTrue(muted['readback'])
        self.assertFalse(h.ctx.backups.get(muted['backup_id'])['mute'])

    def test_restore_with_volume(self):
        h = fakes.Harness(self, WRITE)
        first = tools.call(h.ctx, 'dsp_set_channel_gain', {'channel': 'left', 'gain_db': -3})
        tools.call(h.ctx, 'dsp_set_main_volume', {'volume_db': -30})
        restored = tools.call(h.ctx, 'dsp_restore_backup', {'backup_id': first['backup_id'], 'restore_volume': True})
        self.assertNotIn('ops_left_gain', h.camilla.config['filters'])
        self.assertEqual(h.camilla.volume, -20.0)
        self.assertEqual(restored['restored_from'], first['backup_id'])
        self.assertEqual(h.ctx.backups.get(restored['backup_id'])['volume_db'], -30.0)


class FakeSocket:
    def __init__(self, replies, sent):
        self.replies, self.sent, self.closed = replies, sent, False

    def send_text(self, text):
        self.sent.append(json.loads(text))

    def recv_text(self):
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply

    def close(self):
        self.closed = True


class CamillaClientWire(unittest.TestCase):
    def client(self, *replies):
        self.sent = []
        self.sockets = []
        queue = list(replies)

        def connect():
            sock = FakeSocket([queue.pop(0)], self.sent)
            self.sockets.append(sock)
            return sock
        h = fakes.Harness(self)
        return clients.CamillaClient(h.config, connect=connect)

    def test_commands_and_values(self):
        config = fakes.base_config()
        cam = self.client(json.dumps({'GetConfigJson': {'result': 'Ok', 'value': json.dumps(config)}}),
                          json.dumps({'SetVolume': {'result': 'Ok'}}),
                          json.dumps({'GetVolume': {'result': 'Ok', 'value': -12.5}}))
        self.assertEqual(cam.get_config(), config)
        cam.set_volume(-15)
        self.assertEqual(cam.get_volume(), -12.5)
        self.assertEqual(self.sent, ['GetConfigJson', {'SetVolume': -15.0}, 'GetVolume'])
        self.assertTrue(all(s.closed for s in self.sockets))

    def test_errors(self):
        cases = [
            (json.dumps({'SetConfigJson': {'result': {'Error': 'bad filter'}}}), 'camilladsp_rejected'),
            (json.dumps({'SetConfigJson': {'result': 'Error'}}), 'camilladsp_rejected'),
            (json.dumps({'Invalid': {'error': 'unknown command'}}), 'camilladsp_error'),
            (json.dumps({'SetConfigJson': {'error': 'unknown command'}}), 'camilladsp_unsupported'),
            ('not json', 'camilladsp_error'),
            (OSError('reset'), 'camilladsp_unreachable'),
        ]
        for reply, code in cases:
            cam = self.client(reply)
            with self.subTest(code=code), self.assertRaises(OpsError) as caught:
                cam.set_config({'a': 1})
            self.assertEqual(caught.exception.code, code)
            self.assertTrue(self.sockets[0].closed)

    def test_connect_failure(self):
        def refuse():
            raise ConnectionRefusedError()
        cam = clients.CamillaClient(fakes.Harness(self).config, connect=refuse)
        with self.assertRaises(OpsError) as caught:
            cam.get_state()
        self.assertEqual(caught.exception.code, 'camilladsp_unreachable')

    def test_rejection_reason_is_bounded(self):
        cam = self.client(json.dumps({'ValidateConfigJson': {'result': {'Error': 'x' * 5000}}}))
        with self.assertRaises(OpsError) as caught:
            cam.validate_config(copy.deepcopy(fakes.base_config()))
        self.assertLessEqual(len(caught.exception.details['reason']), 500)


if __name__ == '__main__':
    unittest.main()
