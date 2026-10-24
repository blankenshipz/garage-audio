"""Isolated metadata/cleanup regressions; no Docker, devices, credentials or sound."""
import ast
import json
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from garage_audio import audio_network as network, audio_recovery as recovery


class CleanupTests(unittest.TestCase):
    def carrier(self):
        c = network.Carrier.__new__(network.Carrier)
        c.closed = False
        c.lock = 98765  # os.close is mocked; never a real descriptor
        c.id, c.peer, c.generation = 'owned-carrier', 'owned-peer', 'generation'
        c.record = {'generation': c.generation, 'carrier':c.id, 'dependents':[]}
        c.dependents, c.receipt = [], Path('/synthetic-unused')
        c.client, c.guard, c.capture = [Mock() for _ in range(3)]
        for p in (c.client, c.guard, c.capture):
            p.poll.return_value = None
        return c

    def test_each_helper_timeout_still_revokes_and_closes_peer_and_lock(self):
        for role in ('client', 'guard', 'capture'):
            with self.subTest(role=role):
                c = self.carrier()
                process = getattr(c, role)
                timeout = subprocess.TimeoutExpired('owned-helper', 3)
                process.wait.side_effect = [timeout, 0]
                links = json.dumps([{'ifname': c.peer, 'ifalias': c.generation}])
                with patch.object(network, 'revoke', return_value={'containers_stopped':{c.id:True},'software_children_closed':None}) as revoke, patch.object(network, 'run', return_value=links) as run, patch.object(network.os, 'close') as close:
                    with self.assertRaises(subprocess.TimeoutExpired) as error:
                        c.close()
                    self.assertIs(error.exception, timeout)
                    process.kill.assert_called_once()
                    revoke.assert_called_once_with(c.record, 'operator_stop')
                    run.assert_any_call('ip', 'link', 'del', c.peer)
                    close.assert_called_once_with(98765)
                    # Successful waits reap these owned children.
                    for child in (c.client, c.guard, c.capture):
                        child.poll.return_value = 0
                    # An unsuccessful close remains retryable without closing a reused FD.
                    c.close()
                    self.assertTrue(c._cleanup_complete)
                    close.assert_called_once()
                    c.close()
                    self.assertEqual(revoke.call_count, 2)
                for p in (c.client, c.guard, c.capture):
                    self.assertGreaterEqual(p.send_signal.call_count, 1)

    def test_revocation_failure_keeps_original_error_but_releases_lock(self):
        c = self.carrier()
        error = PermissionError('synthetic ownership denial')
        with patch.object(network, 'revoke', side_effect=error), patch.object(network, 'run', return_value='[]'), patch.object(network.os, 'close') as close:
            with self.assertRaises(PermissionError) as caught:
                c.close()
            self.assertIs(caught.exception, error)
            close.assert_called_once_with(98765)
            self.assertFalse(getattr(c, '_cleanup_complete', False))

    def test_logged_revocation_failure_is_not_completed_cleanup(self):
        for stopped,ack in [({ 'owned-carrier':False},True),({},True),({'owned-carrier':True},False)]:
            c=self.carrier();c.record['worker_ack']='/synthetic-ack'
            result={'containers_stopped':stopped,'software_children_closed':ack}
            with patch.object(network,'revoke',return_value=result),patch.object(network,'run',return_value='[]'),patch.object(network.os,'close') as close:
                with self.assertRaisesRegex(RuntimeError,'cleanup incomplete'):c.close()
                self.assertFalse(getattr(c,'_cleanup_complete',False));close.assert_called_once()

    def test_permission_failure_preserved_and_foreign_peer_never_deleted(self):
        c = self.carrier()
        error = PermissionError('synthetic helper permission denial')
        c.client.send_signal.side_effect = error
        links = json.dumps([{'ifname':c.peer, 'ifalias':'other-generation'}])
        with patch.object(network, 'revoke') as revoke, patch.object(network, 'run', return_value=links) as run, patch.object(network.os, 'close'):
            with self.assertRaises(PermissionError) as caught:
                c.close()
            self.assertIs(caught.exception, error)
            revoke.assert_called_once_with(c.record, 'operator_stop')
            c.guard.send_signal.assert_called_once()
            c.capture.send_signal.assert_called_once()
            run.assert_called_once_with('ip', '-j', 'link')


class TelemetryTests(unittest.TestCase):
    def test_backoff_ready_and_exception_keep_invocation_and_retry_count(self):
        with tempfile.TemporaryDirectory() as tmp:
            rows = []
            stop = SimpleNamespace(is_set=lambda: False, wait=lambda _: False)
            supervisor = recovery.Supervisor(tmp, stop, lambda: 0)
            calls = 0
            def generation():
                nonlocal calls
                calls += 1
                if calls == 1:
                    return 'recoverable'
                supervisor.ready('new-generation')
                self.assertEqual(json.loads((Path(tmp)/'recovery-state.json').read_text())['state'], 'ready')
                raise PermissionError('synthetic private detail must not appear')
            original = recovery.atomic
            def write(path, value):
                rows.append(value)
                original(path, value)
            with patch.object(recovery, 'atomic', side_effect=write):
                with self.assertRaises(PermissionError):
                    supervisor.run(generation)
            self.assertEqual([r['state'] for r in rows], ['starting','backoff','starting','ready','terminal'])
            self.assertEqual([r['retries'] for r in rows], [0,1,1,1,1])
            self.assertEqual(len({r['invocation'] for r in rows}), 1)
            self.assertEqual(rows[-1]['generation'], 'new-generation')
            self.assertNotIn('private detail', json.dumps(rows))

    def test_exhaustion_and_stop_receipts_keep_counts(self):
        with tempfile.TemporaryDirectory() as tmp:
            stop = SimpleNamespace(is_set=lambda: False, wait=lambda _: False)
            supervisor = recovery.Supervisor(tmp, stop, lambda: 0)
            self.assertEqual(supervisor.run(lambda: 'recoverable'), 'exhausted')
            value = json.loads((Path(tmp)/'recovery-state.json').read_text())
            self.assertEqual((value['state'], value['retries']), ('exhausted', 3))
            other = recovery.Supervisor(tmp, SimpleNamespace(is_set=lambda: True))
            self.assertEqual(other.run(lambda: self.fail('no start after stop')), 'stopped')
            value = json.loads((Path(tmp)/'recovery-state.json').read_text())
            self.assertEqual((value['state'], value['retries']), ('stopped', 0))
            self.assertNotEqual(value['invocation'], supervisor.invocation)


class HeartbeatBudgetTests(unittest.TestCase):
    def test_successful_slow_inspection_can_outlive_guardian_freshness_budget(self):
        # Execute the actual nested watchdog with virtual time. No real delay or
        # Docker call, and no authority interval is widened by this characterization.
        tree = ast.parse(Path(network.__file__).read_text())
        start = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == 'guard_guard')
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root/'guard-heartbeat.json').write_text(json.dumps({'time':0}))
            (root/'registry.json').write_text('{}')
            owner = SimpleNamespace(closed=False, receipt=root, registry=root/'registry.json', guard=SimpleNamespace(poll=lambda:None))
            revoked = []
            virtual_time = SimpleNamespace(monotonic=lambda:2.1, sleep=lambda _:self.fail('must revoke at stale budget'))
            scope = dict(self=owner, json=json, time=virtual_time, revoke=lambda record, reason:revoked.append(reason))
            exec(compile(ast.Module(body=[start], type_ignores=[]), '<isolated-watchdog>', 'exec'), scope)
            # Docker's four-second timeout allows success at 2.1 seconds, but the
            # independent watchdog correctly refuses the now-stale observation.
            scope['guard_guard']()
            self.assertEqual(revoked, ['independent_guard_lost'])
