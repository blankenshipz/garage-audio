"""Ops MCP registry, schemas, resources, prompts, SDK isolation and CLI startup refusals. Synthetic only."""
import importlib.util
import json
import os
import re
import subprocess
import sys
import unittest
from pathlib import Path

from garage_audio.ops_mcp import content, schema, tools
from garage_audio.ops_mcp.errors import OpsError

_spec = importlib.util.spec_from_file_location('ops_mcp_fakes', Path(__file__).parent / 'fixtures/ops_mcp/fakes.py')
fakes = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fakes)

ROOT = Path(__file__).parents[1]


class Registry(unittest.TestCase):
    def test_every_tool_has_strict_schema_and_description(self):
        self.assertEqual(len(tools.TOOLS), 60)
        for tool in tools.TOOLS:
            with self.subTest(tool=tool.name):
                self.assertRegex(tool.name, r'^[a-z][a-z0-9_]{2,63}$')
                self.assertIn(tool.access, (tools.READ, tools.LOCAL, tools.WRITE, tools.PLAYBACK, tools.MEASURE,
                                            tools.STOP))
                self.assertEqual(tool.schema['type'], 'object')
                self.assertIs(tool.schema['additionalProperties'], False)
                self.assertGreater(len(tool.description), 20)
                json.dumps(tool.schema)

    def test_groups_present(self):
        names = set(tools.BY_NAME)
        for expected in ('get_status', 'diagnose', 'get_recent_logs', 'recover_audio_stack', 'ma_list_players',
                         'ma_search', 'ma_play_media', 'dsp_get_levels', 'dsp_update_peq', 'dsp_restore_backup',
                         'room_import_measurement', 'room_propose_peq', 'room_apply_correction'):
            self.assertIn(expected, names)

    def test_describe_flags_access(self):
        h = fakes.Harness(self, {'allow_write': True})
        described = {d['name']: d for d in tools.describe(h.config)}
        self.assertTrue(described['dsp_update_peq']['enabled'])
        self.assertTrue(described['dsp_update_peq']['description'].startswith('[WRITE]'))
        self.assertFalse(described['ma_play_media']['enabled'])            # playback still needs its own flag
        self.assertIn('allow_playback', described['ma_play_media']['description'])
        self.assertTrue(described['get_status']['annotations']['readOnlyHint'])
        self.assertTrue(described['recover_audio_stack']['annotations']['destructiveHint'])

    def test_unknown_tool_and_bad_arguments(self):
        h = fakes.Harness(self)
        with self.assertRaises(OpsError) as caught:
            tools.call(h.ctx, 'docker_stop', {})
        self.assertEqual(caught.exception.code, 'unknown_tool')
        for args in ({'unexpected': 1}, {'lines': 0}, {'lines': 'ten'}, {'errors_only': 'yes'}, [1]):
            with self.subTest(args=args), self.assertRaises(OpsError) as caught:
                tools.call(h.ctx, 'get_recent_logs', args)
            self.assertEqual(caught.exception.code, 'invalid_argument')

    def test_internal_errors_are_masked(self):
        h = fakes.Harness(self)

        def explode():
            raise RuntimeError('secret detail /home/someone/token=abc')
        h.ma.info = explode
        with self.assertLogs('garage_audio.ops_mcp', 'ERROR'), self.assertRaises(OpsError) as caught:
            tools.call(h.ctx, 'check_music_assistant', {})
        self.assertEqual(caught.exception.code, 'internal')
        self.assertNotIn('secret detail', caught.exception.message)
        self.assertIn('RuntimeError', caught.exception.message)


class SchemaValidator(unittest.TestCase):
    def test_subset(self):
        s = {'type': 'object', 'properties': {
            'n': {'type': 'number', 'minimum': 0, 'maximum': 10}, 'i': {'type': 'integer'},
            'e': {'type': 'string', 'enum': ['a', 'b']}, 'p': {'type': 'string', 'pattern': '^[a-z]+$', 'maxLength': 4},
            'l': {'type': 'array', 'items': {'type': 'string'}, 'minItems': 1, 'maxItems': 2, 'uniqueItems': True},
            'o': {'type': 'object', 'properties': {}, 'additionalProperties': False, 'minProperties': 0}},
             'required': ['n'], 'additionalProperties': False}
        schema.validate(s, {'n': 5, 'i': 3, 'e': 'a', 'p': 'abc', 'l': ['x'], 'o': {}})
        bad = [{}, {'n': 11}, {'n': True}, {'n': float('nan')}, {'n': float('inf')}, {'n': 1, 'i': 1.5},
               {'n': 1, 'e': 'c'}, {'n': 1, 'p': 'ABC'}, {'n': 1, 'p': 'abcde'}, {'n': 1, 'p': 'a\x00'},
               {'n': 1, 'l': []}, {'n': 1, 'l': ['x', 'x']}, {'n': 1, 'l': ['a', 'b', 'c']},
               {'n': 1, 'o': {'z': 1}}, {'n': 1, 'zz': 1}, 'string']
        for value in bad:
            with self.subTest(value=value), self.assertRaises(OpsError):
                schema.validate(s, value)


class Content(unittest.TestCase):
    def test_resources(self):
        h = fakes.Harness(self)
        h.recovery_state('ready')
        uris = [r['uri'] for r in content.list_resources()]
        self.assertEqual(uris, ['garage-audio://status', 'garage-audio://dsp/config', 'garage-audio://measurements',
                                'garage-audio://room/profile', 'garage-audio://measure/plan',
                                'garage-audio://measure/session'])
        self.assertEqual(content.read_resource(h.ctx, 'garage-audio://status')['recovery']['state'], 'ready')
        self.assertIn('config', content.read_resource(h.ctx, 'garage-audio://dsp/config'))
        self.assertEqual(content.read_resource(h.ctx, 'garage-audio://measurements')['measurements'], [])
        h.camilla.reachable = False
        self.assertEqual(content.read_resource(h.ctx, 'garage-audio://dsp/config')['error']['code'],
                         'camilladsp_unreachable')
        with self.assertRaises(OpsError):
            content.read_resource(h.ctx, 'file:///etc/passwd')

    def test_prompts(self):
        names = [p['name'] for p in content.list_prompts()]
        self.assertEqual(names, ['diagnose-garage-audio', 'room-correction-walkthrough', 'listening-session',
                                 'guided-room-measurement'])
        text = content.get_prompt('diagnose-garage-audio', {'symptom': 'no sound'})['text']
        self.assertIn('no sound', text)
        self.assertIn('backoff', text)
        self.assertIn('not specified', content.get_prompt('listening-session')['text'])
        with self.assertRaises(OpsError):
            content.get_prompt('listening-session', {'evil': 'x'})
        with self.assertRaises(OpsError):
            content.get_prompt('nope')
        # Every tool a prompt names exists.
        known = set(tools.BY_NAME)
        for prompt in content.PROMPTS.values():
            for word in re.findall(r'\b(?:ma|dsp|room|get|recover|plan|measure)_[a-z_]+\b', prompt['text']):
                self.assertIn(word, known)


def run_python(code_or_args, env_extra=None, module=False):
    env = {k: v for k, v in os.environ.items() if not k.startswith('GARAGE_OPS_MCP_')}
    env.update(env_extra or {})
    argv = [sys.executable, '-B'] + (code_or_args if module else ['-c', code_or_args])
    return subprocess.run(argv, cwd=ROOT, env=env, capture_output=True, text=True, timeout=60)


class Isolation(unittest.TestCase):
    def test_core_does_not_import_the_sdk(self):
        code = ('import sys\n'
                'from garage_audio.ops_mcp import tools, content, room, dsp, health, recovery, music, config\n'
                'bad = [m for m in sys.modules if m == "mcp" or m.startswith(("mcp.", "starlette", "uvicorn"))]\n'
                'print(bad)\n')
        result = run_python(code)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), '[]')

    def test_missing_sdk_is_a_clear_error(self):
        code = ('import sys\nsys.modules["mcp"] = None\n'
                'from garage_audio.ops_mcp import transport\n'
                'from garage_audio.ops_mcp.errors import OpsError\n'
                'try:\n    transport._sdk()\nexcept OpsError as e:\n    print(e.code, "|", e.message)\n')
        result = run_python(code)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('dependency_missing', result.stdout)
        self.assertIn('mcp==1.30.0', result.stdout)


class Cli(unittest.TestCase):
    def setUp(self):
        import tempfile
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.dir = Path(temp.name)

    def test_http_without_token_refuses_to_start(self):
        result = run_python(['-m', 'garage_audio.ops_mcp', '--transport', 'http'],
                            {'GARAGE_OPS_MCP_STATE_DIR': str(self.dir / 's')}, module=True)
        self.assertEqual(result.returncode, 2)
        self.assertIn('refuses to start without a bearer token', result.stderr)
        self.assertEqual(result.stdout, '')

    def test_http_with_loose_token_file_refuses(self):
        token = fakes.write_private(self.dir / 'token', fakes.TOKEN, mode=0o644)
        result = run_python(['-m', 'garage_audio.ops_mcp', '--transport', 'http', '--check-config'],
                            {'GARAGE_OPS_MCP_TOKEN_FILE': token}, module=True)
        self.assertEqual(result.returncode, 2)
        self.assertIn('chmod 600', result.stderr)
        self.assertNotIn(fakes.TOKEN, result.stderr + result.stdout)

    def test_check_config_prints_public_projection(self):
        token = fakes.write_private(self.dir / 'token', fakes.TOKEN)
        result = run_python(['-m', 'garage_audio.ops_mcp', '--transport', 'http', '--check-config',
                             '--config', str(ROOT / 'config/ops-mcp.example.json')],
                            {'GARAGE_OPS_MCP_TOKEN_FILE': token}, module=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        public = json.loads(result.stdout)
        self.assertFalse(public['allow_write'])
        self.assertTrue(public['server']['token_file_configured'])
        self.assertNotIn(fakes.TOKEN, result.stdout + result.stderr)

    def test_bad_config_exits_2(self):
        bad = self.dir / 'bad.json'
        bad.write_text(json.dumps({'limits': {'max_boost_db': 12}}))
        result = run_python(['-m', 'garage_audio.ops_mcp', '--check-config', '--config', str(bad)], module=True)
        self.assertEqual(result.returncode, 2)
        self.assertIn('loosen the hard limit', result.stderr)


if __name__ == '__main__':
    unittest.main()
