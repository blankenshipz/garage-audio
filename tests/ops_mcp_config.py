"""Ops MCP configuration: file + env overrides, strict keys, credential files. Synthetic, no network."""
import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path

from garage_audio.ops_mcp import config as cfg
from garage_audio.ops_mcp.errors import ConfigError

_spec = importlib.util.spec_from_file_location('ops_mcp_fakes', Path(__file__).parent / 'fixtures/ops_mcp/fakes.py')
fakes = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fakes)

EXAMPLE = Path(__file__).parents[1] / 'config/ops-mcp.example.json'


class ConfigLoading(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.dir = Path(temp.name)

    def write(self, value, name='ops.json'):
        path = self.dir / name
        path.write_text(json.dumps(value))
        return str(path)

    def test_defaults_are_read_only_loopback(self):
        config = cfg.load_config(None, environ={})
        self.assertFalse(config.allow_write)
        self.assertFalse(config.allow_playback)
        self.assertEqual(config.host, '127.0.0.1')
        self.assertTrue(config.loopback_only())
        self.assertEqual(config.unit, 'garage-audio-network.service')
        self.assertEqual(config.policy.volume_ceiling_db, -10.0)
        self.assertEqual(config.room['max_boost_db'], 0.0)

    def test_file_then_env_overrides(self):
        path = self.write({'_comment': 'ignored', 'allow_write': False,
                           'server': {'port': 9000}, 'music_assistant': {'url': 'http://192.0.2.10:8095/'}})
        env = {'GARAGE_OPS_MCP_ALLOW_WRITE': 'true', 'GARAGE_OPS_MCP_PORT': '9100',
               'GARAGE_OPS_MCP_CAMILLA_HOST': 'dsp.example.invalid', 'GARAGE_OPS_MCP_STATE_DIR': str(self.dir / 's'),
               'GARAGE_OPS_MCP_SUPERVISOR_UNIT': 'other-owner.service'}
        config = cfg.load_config(path, environ=env)
        self.assertTrue(config.allow_write)
        self.assertEqual(config.port, 9100)
        self.assertEqual(config.ma_url, 'http://192.0.2.10:8095')
        self.assertEqual(config.camilla_host, 'dsp.example.invalid')
        self.assertEqual(config.unit, 'other-owner.service')
        self.assertEqual(config.state_dir, self.dir / 's')

    def test_config_path_from_env(self):
        path = self.write({'allow_playback': True})
        config = cfg.load_config(None, environ={'GARAGE_OPS_MCP_CONFIG': path})
        self.assertTrue(config.allow_playback)
        self.assertEqual(config.source, path)

    def test_rejects_unknown_and_malformed(self):
        cases = [
            {'allow_writes': True},                                  # typo must not silently drop a setting
            {'server': {'hots': '0.0.0.0'}},
            {'allow_write': 'yes'},                                  # strings are not booleans in the file
            {'limits': {'max_boost': 1}},
            {'supervisor': {'unit': 'bad unit; rm'}},
            {'supervisor': {'systemctl': 'systemctl restart x'}},   # must be argv, never a shell string
            {'music_assistant': {'url': 'http://user:pw@192.0.2.1:8095'}},
            {'music_assistant': {'url': 'ftp://192.0.2.1'}},
            {'music_assistant': {'url': 'http://192.0.2.1/api?x=1'}},
            {'camilladsp': {'channels': {'left': 0, 'right': 0}}},
            {'camilladsp': {'channels': {'centre': 3}}},
            {'camilladsp': {'channels': 3}},
            {'room_correction': {'max_boost_db': 4}},                # above default limits.max_boost_db=3
            {'room_correction': {'max_filters': 9}},                 # above default max_filters_per_channel=8
            {'room_correction': {'freq_min_hz': 300, 'freq_max_hz': 100}},
            {'containers': {'names': ['ok', '../bad']}},
            ['not', 'an', 'object'],
        ]
        for value in cases:
            with self.subTest(value=value), self.assertRaises(ConfigError):
                cfg.load_config(self.write(value), environ={})

    def test_env_errors(self):
        for env in ({'GARAGE_OPS_MCP_ALLOW_WRITE': 'maybe'}, {'GARAGE_OPS_MCP_PORT': 'eighty'},
                    {'GARAGE_OPS_MCP_PORT': '70000'}):
            with self.subTest(env=env), self.assertRaises(ConfigError):
                cfg.load_config(None, environ=env)

    def test_unreadable_and_invalid_json(self):
        with self.assertRaises(ConfigError):
            cfg.load_config(str(self.dir / 'missing.json'), environ={})
        bad = self.dir / 'bad.json'
        bad.write_text('{not json')
        with self.assertRaises(ConfigError):
            cfg.load_config(str(bad), environ={})

    def test_public_projection_has_no_credentials(self):
        token_file = fakes.write_private(self.dir / 'token', fakes.TOKEN)
        ma_file = fakes.write_private(self.dir / 'ma', 'ma-secret-value-1234567890')
        path = self.write({'server': {'token_file': token_file}, 'music_assistant': {'token_file': ma_file}})
        text = json.dumps(cfg.load_config(path, environ={}).public())
        self.assertNotIn(fakes.TOKEN, text)
        self.assertNotIn('ma-secret-value', text)
        self.assertNotIn(token_file, text)
        self.assertIn('"token_file_configured": true', text)

    def test_example_config_is_valid_and_placeholder_only(self):
        self.assertTrue(EXAMPLE.is_file(), 'config/ops-mcp.example.json must ship')
        config = cfg.load_config(str(EXAMPLE), environ={})
        self.assertFalse(config.allow_write)
        self.assertFalse(config.allow_playback)
        self.assertTrue(config.loopback_only())


class Credentials(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.dir = Path(temp.name)

    def config(self, token_file=None):
        data = {'server': {'token_file': token_file}} if token_file else {}
        path = self.dir / 'c.json'
        path.write_text(json.dumps(data))
        return cfg.load_config(str(path), environ={})

    def test_token_file_preferred_over_env(self):
        path = fakes.write_private(self.dir / 'token', fakes.TOKEN + '\n')
        token = cfg.load_bearer_token(self.config(path), environ={'GARAGE_OPS_MCP_TOKEN': 'e' * 40})
        self.assertEqual(token, fakes.TOKEN)

    def test_env_token_and_absent(self):
        self.assertEqual(cfg.load_bearer_token(self.config(), environ={'GARAGE_OPS_MCP_TOKEN': 'e' * 40}), 'e' * 40)
        self.assertIsNone(cfg.load_bearer_token(self.config(), environ={}))

    def test_loose_permissions_rejected_without_echo(self):
        for mode in (0o644, 0o640, 0o604):
            path = fakes.write_private(self.dir / f'token{mode:o}', fakes.TOKEN, mode)
            with self.subTest(mode=oct(mode)), self.assertRaises(ConfigError) as caught:
                cfg.load_bearer_token(self.config(path), environ={})
            self.assertIn('chmod 600', caught.exception.message)
            self.assertNotIn(fakes.TOKEN, json.dumps(caught.exception.payload()))

    def test_symlink_and_missing_rejected(self):
        real = fakes.write_private(self.dir / 'real', fakes.TOKEN)
        link = self.dir / 'link'
        os.symlink(real, link)
        for path in (str(link), str(self.dir / 'absent')):
            with self.subTest(path=path), self.assertRaises(ConfigError):
                cfg.load_bearer_token(self.config(path), environ={})

    def test_weak_tokens_rejected(self):
        for value in ('short', 'has space ' + 'x' * 40, 'ünicode' + 'x' * 40):
            path = fakes.write_private(self.dir / 'weak', value)
            with self.subTest(value=value[:8]), self.assertRaises(ConfigError) as caught:
                cfg.load_bearer_token(self.config(path), environ={})
            self.assertNotIn(value, caught.exception.message)
        with self.assertRaises(ConfigError):
            cfg.load_bearer_token(self.config(), environ={'GARAGE_OPS_MCP_TOKEN': 'too-short'})


if __name__ == '__main__':
    unittest.main()
