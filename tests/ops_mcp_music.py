"""Ops MCP Music Assistant tools against a fake MA server API (no network)."""
import importlib.util
import io
import json
import tempfile
import unittest
import urllib.error
from pathlib import Path

from garage_audio.ops_mcp import clients, tools
from garage_audio.ops_mcp.errors import OpsError, SafetyError

_spec = importlib.util.spec_from_file_location('ops_mcp_fakes', Path(__file__).parent / 'fixtures/ops_mcp/fakes.py')
fakes = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fakes)

PLAY = {'allow_write': True, 'allow_playback': True}


class ReadTools(unittest.TestCase):
    def test_players_projection(self):
        h = fakes.Harness(self, {'music_assistant': {'players': ['player-a']}})
        players = tools.call(h.ctx, 'ma_list_players', {})['players']
        self.assertEqual([p['player_id'] for p in players], ['player-a', 'player-b'])
        self.assertNotIn('unexpected', players[0])
        self.assertNotIn('secret', players[0]['current_media'])
        self.assertEqual([p['controllable'] for p in players], [True, False])

    def test_queue_and_search(self):
        h = fakes.Harness(self)
        queue = tools.call(h.ctx, 'ma_get_queue', {'player_id': 'player-a', 'limit': 5})
        self.assertEqual(queue['queue']['current_item']['uri'], 'library://track/1')
        self.assertEqual(queue['items'][0]['media']['artists'], ['Artist'])
        self.assertEqual(h.ma.calls[-1], ('player_queues/items', {'queue_id': 'player-a', 'limit': 5, 'offset': 0}))
        found = tools.call(h.ctx, 'ma_search', {'query': 'song', 'media_types': ['track'], 'limit': 3})
        self.assertEqual(found['results']['tracks'][0]['item_id'], '1')
        self.assertEqual(found['results']['albums'], [])
        self.assertNotIn('unrelated', found['results'])
        self.assertEqual(h.ma.calls[-1][1]['media_types'], ['track'])

    def test_reachability(self):
        self.assertTrue(tools.call(fakes.Harness(self).ctx, 'check_music_assistant', {})['reachable'])
        down = tools.call(fakes.Harness(self, ma=fakes.FakeMA(reachable=False)).ctx, 'check_music_assistant', {})
        self.assertFalse(down['reachable'])
        self.assertEqual(down['error']['code'], 'ma_unreachable')


class Control(unittest.TestCase):
    def test_play_transport_volume_group(self):
        h = fakes.Harness(self, PLAY)
        tools.call(h.ctx, 'ma_play_media', {'player_id': 'player-a', 'uri': 'library://track/1', 'option': 'replace'})
        tools.call(h.ctx, 'ma_player_command', {'player_id': 'player-a', 'command': 'next'})
        tools.call(h.ctx, 'ma_stop_playback', {'player_id': 'player-a', 'command': 'pause'})
        tools.call(h.ctx, 'ma_set_volume', {'player_id': 'player-a', 'level': 25})
        tools.call(h.ctx, 'ma_set_mute', {'player_id': 'player-a', 'muted': True})
        tools.call(h.ctx, 'ma_group_players', {'target_player_id': 'player-a', 'member_player_ids': ['player-b']})
        tools.call(h.ctx, 'ma_ungroup_player', {'player_id': 'player-b'})
        self.assertEqual([c[0] for c in h.ma.calls], [
            'player_queues/play_media', 'players/cmd/next', 'players/cmd/pause', 'players/cmd/volume_set',
            'players/cmd/volume_mute', 'players/cmd/group_many', 'players/cmd/ungroup'])
        self.assertEqual(h.ma.calls[3][1], {'player_id': 'player-a', 'volume_level': 25})

    def test_volume_capped_and_commands_bounded(self):
        h = fakes.Harness(self, {**PLAY, 'limits': {'ma_volume_max': 40}})
        with self.assertRaises(SafetyError):
            tools.call(h.ctx, 'ma_set_volume', {'player_id': 'player-a', 'level': 41})
        for args in ({'player_id': 'player-a', 'level': 101}, {'player_id': 'player-a', 'level': 10.5},
                     {'player_id': 'player-a', 'level': True}):
            with self.subTest(args=args), self.assertRaises(OpsError):
                tools.call(h.ctx, 'ma_set_volume', args)
        with self.assertRaises(OpsError):  # pause/stop tool cannot start playback
            tools.call(h.ctx, 'ma_stop_playback', {'player_id': 'player-a', 'command': 'play'})
        with self.assertRaises(OpsError):
            tools.call(h.ctx, 'ma_player_command', {'player_id': 'player-a', 'command': 'stop'})
        self.assertEqual(h.ma.calls, [])

    def test_player_allow_list(self):
        h = fakes.Harness(self, {**PLAY, 'music_assistant': {'players': ['player-a']}})
        with self.assertRaises(OpsError) as caught:
            tools.call(h.ctx, 'ma_play_media', {'player_id': 'player-b', 'uri': 'library://track/1'})
        self.assertEqual(caught.exception.code, 'player_not_allowed')
        with self.assertRaises(OpsError):
            tools.call(h.ctx, 'ma_group_players', {'target_player_id': 'player-a', 'member_player_ids': ['player-b']})
        with self.assertRaises(OpsError):
            tools.call(h.ctx, 'ma_group_players', {'target_player_id': 'player-a', 'member_player_ids': ['player-a']})
        self.assertEqual(h.ma.calls, [])


class FakeResponse(io.BytesIO):
    def __init__(self, body, status=200):
        super().__init__(body)
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class MAClientWire(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.token_file = fakes.write_private(Path(temp.name) / 'ma-token', 'ma-token-value-0123456789\n')
        self.h = fakes.Harness(self, {'music_assistant': {'url': 'http://192.0.2.20:8095',
                                                          'token_file': self.token_file}})
        self.requests = []

    def client(self, reply):
        test = self

        class Opener:
            def open(self, request, timeout):
                test.requests.append(request)
                if isinstance(reply, Exception):
                    raise reply
                return FakeResponse(json.dumps(reply).encode())
        return clients.MAClient(self.h.config, opener=Opener())

    def test_call_posts_bearer_json_and_unwraps(self):
        result = self.client({'message_id': 'garage-ops-mcp', 'result': [1]}).call('players/all')
        self.assertEqual(result, [1])
        request = self.requests[0]
        self.assertEqual(request.full_url, 'http://192.0.2.20:8095/api')
        self.assertEqual(request.get_header('Authorization'), 'Bearer ma-token-value-0123456789')
        self.assertEqual(json.loads(request.data)['command'], 'players/all')

    def test_errors_never_echo_token_or_body(self):
        cases = [
            (urllib.error.HTTPError('http://x', 401, 'nope ma-token-value', {}, io.BytesIO(b'ma-token-value')),
             'ma_authentication_failed'),
            (OSError('refused'), 'ma_unreachable'),
            ({'error_code': 7, 'details': 'ma-token-value'}, 'ma_command_failed'),
        ]
        for reply, code in cases:
            with self.subTest(code=code), self.assertRaises(OpsError) as caught:
                self.client(reply).call('players/all')
            self.assertEqual(caught.exception.code, code)
            self.assertNotIn('ma-token-value', json.dumps(caught.exception.payload()))

    def test_missing_token_file_configuration(self):
        h = fakes.Harness(self)
        with self.assertRaises(OpsError) as caught:
            clients.MAClient(h.config, opener=object()).call('players/all')
        self.assertEqual(caught.exception.code, 'ma_not_configured')

    def test_info_needs_no_token(self):
        info = self.client({'server_version': '2.5.0', 'schema_version': 27, 'extra': 'x'}).info()
        self.assertEqual(info, {'server_version': '2.5.0', 'schema_version': 27})
        self.assertIsNone(self.requests[0].get_header('Authorization'))


if __name__ == '__main__':
    unittest.main()
