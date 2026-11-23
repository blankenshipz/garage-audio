"""Ops MCP recovery: sanctioned service-manager path only, backoff/transition refusal, rate limit. Synthetic."""
import importlib.util
import unittest
from pathlib import Path

from garage_audio.ops_mcp import recovery, tools
from garage_audio.ops_mcp.errors import OpsError

_spec = importlib.util.spec_from_file_location('ops_mcp_fakes', Path(__file__).parent / 'fixtures/ops_mcp/fakes.py')
fakes = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fakes)


class Recovery(unittest.TestCase):
    def harness(self, state=None, active='active', **extra):
        h = fakes.Harness(self, {'allow_write': True}, system=fakes.FakeSystem(active=active))
        if state:
            h.recovery_state(state, **extra)
        return h

    def test_auto_picks_start_or_restart(self):
        self.assertEqual(recovery.plan(self.harness('ready').ctx)['action'], 'restart')
        self.assertEqual(recovery.plan(self.harness('stopped', active='inactive').ctx)['action'], 'start')

    def test_plan_is_read_only_and_available_without_write(self):
        h = fakes.Harness(self)
        h.recovery_state('ready')
        plan = tools.call(h.ctx, 'plan_recovery', {})
        self.assertTrue(plan['allowed'])
        self.assertEqual(plan['command'], ['systemctl', 'restart', '--no-block', 'garage-audio-network.service'])
        self.assertEqual(h.system.actions, [])
        self.assertFalse((h.state_dir / recovery.ACTIONS_FILE).exists())

    def test_backoff_refused_unless_forced(self):
        h = self.harness('backoff', active='active', retries=1, delay=30)
        with self.assertRaises(OpsError) as caught:
            tools.call(h.ctx, 'recover_audio_stack', {'action': 'restart'})
        self.assertEqual(caught.exception.code, 'recovery_refused')
        self.assertIn('backoff', caught.exception.message)
        self.assertEqual(h.system.actions, [])
        result = tools.call(h.ctx, 'recover_audio_stack', {'action': 'restart', 'force': True})
        self.assertTrue(result['ok'])
        self.assertEqual(h.system.actions, ['restart'])
        self.assertTrue(any('backoff' in n for n in result['plan']['notes']))

    def test_starting_and_busy_unit_refused(self):
        for state, active in (('starting', 'active'), ('ready', 'activating'), ('ready', 'deactivating')):
            h = self.harness(state, active=active)
            with self.subTest(state=state, active=active), self.assertRaises(OpsError):
                tools.call(h.ctx, 'recover_audio_stack', {})
            self.assertEqual(h.system.actions, [])

    def test_terminal_and_exhausted_explained(self):
        for state in ('terminal', 'exhausted'):
            h = self.harness(state, active='inactive')
            result = tools.call(h.ctx, 'recover_audio_stack', {'action': 'start'})
            self.assertEqual(h.system.actions, ['start'])
            self.assertTrue(any(f"State '{state}'" in n and 'fix the cause' in n.lower()
                                or 'Fix the underlying cause' in n for n in result['plan']['notes']))

    def test_rate_limit_is_not_bypassed_by_force(self):
        h = self.harness('stopped', active='inactive')
        tools.call(h.ctx, 'recover_audio_stack', {'action': 'start'})
        for force in (False, True):
            with self.subTest(force=force), self.assertRaises(OpsError) as caught:
                tools.call(h.ctx, 'recover_audio_stack', {'action': 'start', 'force': force})
            self.assertIn('min_action_interval_seconds', caught.exception.message)
        h.clock.advance(121)
        tools.call(h.ctx, 'recover_audio_stack', {'action': 'start'})
        self.assertEqual(h.system.actions, ['start', 'start'])

    def test_service_manager_refusal_reported(self):
        h = fakes.Harness(self, {'allow_write': True}, system=fakes.FakeSystem(active='inactive', action_exit=4))
        h.recovery_state('stopped')
        result = tools.call(h.ctx, 'recover_audio_stack', {'action': 'start'})
        self.assertFalse(result['ok'])
        self.assertIn('permission', result['message'])

    def test_no_container_or_stop_path_exists(self):
        self.assertEqual(tools.BY_NAME['recover_audio_stack'].schema['properties']['action']['enum'],
                         ['auto', 'start', 'restart'])
        h = self.harness('ready')
        with self.assertRaises(OpsError):
            tools.call(h.ctx, 'recover_audio_stack', {'action': 'stop'})
        self.assertEqual(h.system.actions, [])


if __name__ == '__main__':
    unittest.main()
