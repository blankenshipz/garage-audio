"""Ops MCP health tools over synthetic owner receipts and fake clients (no systemd/Docker/network)."""
import importlib.util
import json
import unittest
from pathlib import Path

from garage_audio.ops_mcp import clients, health, tools
from garage_audio.ops_mcp.errors import OpsError

_spec = importlib.util.spec_from_file_location('ops_mcp_fakes', Path(__file__).parent / 'fixtures/ops_mcp/fakes.py')
fakes = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fakes)


class Status(unittest.TestCase):
    def test_overall_status_ready(self):
        h = fakes.Harness(self)
        h.recovery_state('ready', generation='g1', retries=0)
        status = tools.call(h.ctx, 'get_status', {})
        self.assertEqual(status['recovery']['state'], 'ready')
        self.assertEqual(status['owner_unit']['ActiveState'], 'active')
        self.assertEqual(len(status['containers']['containers']), 2)
        self.assertTrue(status['music_assistant']['reachable'])
        self.assertEqual(status['camilladsp']['processing_state'], 'Running')
        self.assertFalse(status['write_enabled'])
        diagnosis = tools.call(h.ctx, 'diagnose', {})
        self.assertTrue(diagnosis['healthy'])
        self.assertIn('No fault detected', ' '.join(diagnosis['next_steps']))

    def test_missing_status_root_is_reported_not_raised(self):
        h = fakes.Harness(self, {'supervisor': {'status_root': str(Path('/nonexistent/ops-mcp-test'))}})
        self.assertEqual(tools.call(h.ctx, 'get_supervisor_state', {})['recovery']['state'], 'unavailable')

    def test_unit_name_substituted_in_guidance(self):
        h = fakes.Harness(self, {'supervisor': {'unit': 'site-owner.service'}})
        h.recovery_state('exhausted', retries=3)
        report = health.recovery_status(h.ctx)
        self.assertIn('site-owner.service', report['restart_guidance'])
        self.assertNotIn('garage-audio-network.service', report['restart_guidance'])

    def test_diagnose_degraded(self):
        system = fakes.FakeSystem(active='failed', sub='failed')
        system.container_list = [{'name': 'ma', 'running': False, 'status': 'exited', 'oom_killed': True},
                                 {'name': 'connect', 'running': True, 'health': 'unhealthy'}]
        h = fakes.Harness(self, system=system, ma=fakes.FakeMA(reachable=False),
                          camilla=fakes.FakeCamilla(reachable=False))
        h.recovery_state('backoff', retries=1, delay=30)
        result = tools.call(h.ctx, 'diagnose', {})
        self.assertFalse(result['healthy'])
        text = result['summary']
        for needle in ("'backoff'", 'not running', 'ma', 'Unhealthy containers: connect', 'killed for memory',
                       'Music Assistant is not reachable', 'private by design', 'owner exited during a retry wait'):
            self.assertIn(needle, text)
        self.assertIn('Investigate host memory pressure before restarting anything.', result['next_steps'])

    def test_heartbeat_reads_newest_receipt(self):
        h = fakes.Harness(self)
        h.recovery_state('ready', generation='g1')
        beat = health.heartbeat(h.ctx)
        self.assertEqual(beat['state'], 'ready')
        self.assertIn('tolerance', beat)

    def test_probe_errors_are_structured(self):
        class Broken(fakes.FakeSystem):
            def unit_state(self, unit=None):
                raise OpsError('unit_state_unavailable', 'the service manager did not report the unit state')

            def containers(self):
                raise OpsError('docker_unavailable', 'docker ps failed')
        h = fakes.Harness(self, system=Broken())
        status = tools.call(h.ctx, 'get_status', {})
        self.assertEqual(status['owner_unit']['error']['code'], 'unit_state_unavailable')
        self.assertIsNone(status['containers']['containers'])
        self.assertIn('could not be read', tools.call(h.ctx, 'diagnose', {})['summary'])


class Logs(unittest.TestCase):
    def test_owner_default_and_bounds(self):
        h = fakes.Harness(self, {'logs': {'max_lines': 2}})
        result = tools.call(h.ctx, 'get_recent_logs', {'lines': 50})
        self.assertEqual(result['source'], 'garage-audio-network.service')
        self.assertEqual(result['count'], 2)
        self.assertEqual(h.system.log_calls[-1][2], 2)

    def test_errors_only_and_configured_sources(self):
        h = fakes.Harness(self, {'logs': {'units': ['pipewire.service'], 'containers': ['camilladsp']}})
        self.assertEqual(tools.call(h.ctx, 'get_recent_logs', {'errors_only': True})['lines'],
                         ['2026-11-22T10:00:01 ERROR guard failed'])
        tools.call(h.ctx, 'get_recent_logs', {'source': 'camilladsp', 'since': '10 min ago'})
        self.assertEqual(h.system.log_calls[-1][:2], ('container', 'camilladsp'))
        tools.call(h.ctx, 'get_recent_logs', {'source': 'pipewire.service'})
        self.assertEqual(h.system.log_calls[-1][:2], ('unit', 'pipewire.service'))

    def test_rejects_unconfigured_source_and_bad_since(self):
        h = fakes.Harness(self)
        for args in ({'source': 'sshd.service'}, {'since': '--output=json'}, {'since': 'now; rm'}):
            with self.subTest(args=args), self.assertRaises(OpsError) as caught:
                tools.call(h.ctx, 'get_recent_logs', args)
            self.assertEqual(caught.exception.code, 'invalid_argument')
        self.assertEqual(h.system.log_calls, [])


class SystemClientArgv(unittest.TestCase):
    """The real SystemClient only ever runs bounded argv lists; checked with a recording runner."""

    def setUp(self):
        self.calls = []
        self.h = fakes.Harness(self, {'containers': {'names': ['extra']}})

    def runner(self, outputs):
        def run(argv, timeout):
            self.calls.append(argv)
            return outputs.pop(0)
        return run

    def test_unit_state_and_logs(self):
        out = 'ActiveState=active\nSubState=running\nNRestarts=2\nIgnored=x\n'
        client = clients.SystemClient(self.h.config, self.runner([(0, out, ''), (0, 'line token=abc123 ok\n', '')]))
        state = client.unit_state()
        self.assertEqual((state['ActiveState'], state['NRestarts']), ('active', '2'))
        self.assertNotIn('Ignored', state)
        self.assertEqual(self.calls[0][:3], ['systemctl', 'show', 'garage-audio-network.service'])
        self.assertEqual(client.unit_logs('garage-audio-network.service', 5), ['line token=[REDACTED] ok'])
        self.assertEqual(self.calls[1][:3], ['journalctl', '-u', 'garage-audio-network.service'])

    def test_containers_read_only_projection(self):
        inspect = [{'Name': '/ma', 'State': {'Status': 'running', 'Running': True, 'Health': {'Status': 'healthy'}},
                    'Config': {'Labels': {'garage.audio.generation': 'g1', 'com.docker.compose.service': 'ma'},
                               'Env': ['SECRET=1']}, 'RestartCount': 0}]
        client = clients.SystemClient(self.h.config, self.runner([(0, 'abc\n', ''), (1, json.dumps(inspect), 'gone')]))
        found = client.containers()
        self.assertEqual(self.calls[0][:2], ['docker', 'ps'])
        self.assertEqual(self.calls[1][:4], ['docker', 'inspect', '--type', 'container'])
        self.assertNotIn('SECRET', json.dumps(found))
        self.assertEqual(found[0]['generation_label'], 'g1')
        self.assertEqual(found[1], {'name': 'extra', 'status': 'missing', 'running': False})
        for argv in self.calls:
            self.assertFalse({'stop', 'kill', 'rm', 'start', 'restart'} & set(argv))

    def test_unit_action_only_start_restart(self):
        client = clients.SystemClient(self.h.config, self.runner([(0, '', '')]))
        self.assertEqual(client.unit_action('restart')['command'],
                         ['systemctl', 'restart', '--no-block', 'garage-audio-network.service'])
        for action in ('stop', 'kill', 'disable'):
            with self.assertRaises(OpsError):
                client.unit_action(action)
        self.assertEqual(len(self.calls), 1)

    def test_run_argv_never_uses_a_shell(self):
        code, out, _err = clients.run_argv(['printf', '%s', '$HOME;echo'], 5)
        self.assertEqual((code, out), (0, '$HOME;echo'))
        with self.assertRaises(OpsError) as caught:
            clients.run_argv(['/nonexistent/ops-mcp-binary'], 5)
        self.assertEqual(caught.exception.code, 'command_missing')


class Redaction(unittest.TestCase):
    def test_redacts_credentials(self):
        lines = clients.redact('Authorization: Bearer abcdefghijklmnop\napi_key=hunter2hunter2 next\n'
                               'jwt eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.c2lnbmF0dXJlLXZhbHVl\n'
                               'hex 0123456789abcdef0123456789abcdef\x1b[31m\n' + 'x' * 600)
        text = '\n'.join(lines)
        for secret in ('abcdefghijklmnop', 'hunter2', 'eyJhbGci', '0123456789abcdef0123456789abcdef', '\x1b'):
            self.assertNotIn(secret, text)
        self.assertLessEqual(len(lines[-1]), 401)


if __name__ == '__main__':
    unittest.main()
