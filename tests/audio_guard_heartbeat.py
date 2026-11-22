"""Guard-heartbeat tolerance, stall classification, batched inspect and read-only status.

Fakes only: no Docker, hardware, network, sudo or real processes; virtual time."""
import contextlib
import io
import json
import os
import signal
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from garage_audio import audio_network as network
from garage_audio import audio_recovery as recovery

REVOKED = 'network generation permanently revoked'


class Done(BaseException):
    """Ends the guard loop after the pass under test."""


class FakeGuard:
    """Stand-in for the guard subprocess. `dies` is 'term', 'kill' or 'never'."""

    def __init__(self, returncode=None, dies='term', on_terminate=None):
        self.returncode = returncode
        self.dies = dies
        self.signals = []
        self.on_terminate = on_terminate

    def poll(self):
        return self.returncode

    def terminate(self):
        self.signals.append('term')
        if self.on_terminate:
            self.on_terminate()
        if self.dies == 'term':
            self.returncode = -signal.SIGTERM
        elif self.dies == 'self-exit':
            self.returncode = 1

    def kill(self):
        self.signals.append('kill')
        if self.dies == 'kill':
            self.returncode = -signal.SIGKILL

    def wait(self, timeout=None):
        if self.returncode is None:
            raise subprocess.TimeoutExpired('guard', timeout)
        return self.returncode


class Clock:
    """Virtual monotonic clock: yields the scripted values, then repeats the last."""

    def __init__(self, *values):
        self.values = list(values)

    def __call__(self):
        return self.values.pop(0) if len(self.values) > 1 else self.values[0]


def make_root(testcase):
    temp = tempfile.TemporaryDirectory()
    testcase.addCleanup(temp.cleanup)
    root = Path(temp.name)
    (root/'authority').mkdir()
    return root


def watcher(testcase, guard, tolerance=12.0, heartbeat=0.0):
    """Carrier shell (no constructor: no lock, docker or root needed) with a recording revoke."""
    root = make_root(testcase)
    (root/'registry.json').write_text(json.dumps({'generation': 'g', 'carrier': 'c', 'dependents': [],
                                                  'receipt': str(root)}))
    if heartbeat is not None:
        (root/'guard-heartbeat.json').write_text(json.dumps({'time': heartbeat}))
    carrier = object.__new__(network.Carrier)
    carrier.closed = False
    carrier.receipt = root
    carrier.registry = root/'registry.json'
    carrier.guard = guard
    carrier.heartbeat_tolerance = tolerance
    carrier.stall_pending = threading.Event()
    carrier.guard_verdict = threading.Event()
    carrier.watcher_failed = threading.Event()
    carrier.dependents = ['registered-dependent']  # past pre-admission: only the guard class decides
    carrier.worker_ack = None
    calls = []

    def revoke(record, reason):
        failure = root/'guard-failure.json'
        calls.append({'reason': reason, 'failure_at_revoke': json.loads(failure.read_text()) if failure.exists() else None})
        (root/'authority/revoked.json').write_text(json.dumps({'reason': reason}))
        return {'reason': reason}
    return carrier, root, calls, revoke


def run_watcher(carrier, revoke, clock, sleeps=1000, on_sleep=None):
    """Run watch_guard on virtual time; sleep() closes the carrier after `sleeps` polls."""
    count = [0]

    def sleep(_):
        count[0] += 1
        if on_sleep:
            on_sleep(count[0])
        if count[0] >= sleeps:
            carrier.closed = True
    with patch.object(network, 'revoke', side_effect=revoke), \
            patch.object(network.time, 'monotonic', side_effect=clock), \
            patch.object(network.time, 'sleep', side_effect=sleep):
        carrier.watch_guard()


def failure(root):
    return json.loads((root/'guard-failure.json').read_text())


class HeartbeatToleranceSetting(unittest.TestCase):
    def test_default_and_valid_values(self):
        self.assertEqual(network.heartbeat_tolerance({}), 12.0)
        for raw, expected in [('10', 10.0), ('300', 300.0), (' 15.5 ', 15.5), ('12', 12.0)]:
            self.assertEqual(network.heartbeat_tolerance({network.HEARTBEAT_TOLERANCE_ENV: raw}), expected)

    def test_minimum_is_not_below_a_worst_case_successful_guard_pass(self):
        # One successful pass can take the batched inspect timeout plus the namespace probe.
        worst_pass = network.GUARD_INSPECT_TIMEOUT + network.GUARD_NS_TIMEOUT
        self.assertGreater(network.HEARTBEAT_TOLERANCE_MIN, worst_pass)
        self.assertGreaterEqual(network.HEARTBEAT_TOLERANCE_DEFAULT, network.HEARTBEAT_TOLERANCE_MIN)

    def test_invalid_values_fail_closed_without_echoing_the_value(self):
        for raw in ['', ' ', 'abc', '2', '9.9', '300.1', 'nan', 'inf', '-inf', '-5', '0', '1e999', '12s']:
            with self.subTest(raw=raw), self.assertRaises(ValueError) as caught:
                network.heartbeat_tolerance({network.HEARTBEAT_TOLERANCE_ENV: raw})
            if raw in ('abc', '12s'):
                self.assertNotIn(raw, str(caught.exception))

    def test_invalid_value_stops_carrier_before_lock_or_receipt(self):
        with patch.dict(os.environ, {network.HEARTBEAT_TOLERANCE_ENV: 'abc'}), \
                patch.object(network.os, 'geteuid', return_value=0), \
                patch.object(network.os, 'open', side_effect=AssertionError('lock must not be taken')):
            with self.assertRaises(ValueError):
                network.Carrier('/nonexistent/receipt')

    def test_invalid_value_is_terminal_never_recoverable(self):
        # ValueError anywhere in the cause chain is never retried.
        outer = RuntimeError(REVOKED)
        outer.__cause__ = ValueError('tolerance')
        self.assertFalse(recovery.recoverable(outer, Path('/nonexistent')))
        self.assertFalse(recovery.preadmission(ValueError('x'), SimpleNamespace(dependents=[], worker_ack=None)))


class WatcherTolerance(unittest.TestCase):
    def test_a_alive_but_stale_within_tolerance_does_not_revoke(self):
        guard = FakeGuard()
        carrier, root, calls, revoke = watcher(self, guard)
        # begun, then staleness 3s, 11.9s: always inside the 12s tolerance.
        run_watcher(carrier, revoke, Clock(0, 3, 11.9, 11.9), sleeps=3)
        self.assertEqual(calls, [])
        self.assertEqual(guard.signals, [])
        self.assertFalse((root/'guard-failure.json').exists())

    def test_tolerance_is_configurable(self):
        guard = FakeGuard()
        carrier, root, calls, revoke = watcher(self, guard, tolerance=10.0)
        run_watcher(carrier, revoke, Clock(0, 10.5))
        self.assertEqual([c['reason'] for c in calls], [network.STALL_KIND])
        self.assertEqual(failure(root)['tolerance'], 10.0)

    def test_missing_heartbeat_counts_from_watcher_start(self):
        guard = FakeGuard()
        carrier, root, calls, revoke = watcher(self, guard, heartbeat=None)
        run_watcher(carrier, revoke, Clock(100, 105, 111.9), sleeps=3)
        self.assertEqual(calls, [])
        carrier2, root2, calls2, revoke2 = watcher(self, FakeGuard(), heartbeat=None)
        run_watcher(carrier2, revoke2, Clock(100, 113))
        self.assertEqual([c['reason'] for c in calls2], [network.STALL_KIND])

    def test_torn_heartbeat_read_never_refreshes_staleness(self):
        guard = FakeGuard()
        carrier, root, calls, revoke = watcher(self, guard)
        (root/'guard-heartbeat.json').write_text('{"tim')
        run_watcher(carrier, revoke, Clock(0, 13))
        self.assertEqual([c['reason'] for c in calls], [network.STALL_KIND])

    def test_b_stale_beyond_tolerance_with_live_guard_is_a_recoverable_stall(self):
        guard = FakeGuard()
        carrier, root, calls, revoke = watcher(self, guard)
        run_watcher(carrier, revoke, Clock(0, 13.25))
        self.assertEqual([c['reason'] for c in calls], ['guard_heartbeat_stall'])
        self.assertEqual(guard.signals, ['term'])
        recorded = failure(root)
        self.assertEqual((recorded['kind'], recorded['staleness'], recorded['tolerance']),
                         ('guard_heartbeat_stall', 13.25, 12.0))
        # The classification existed BEFORE revoke stopped anything (verify_closed 'time' semantics).
        self.assertEqual(calls[0]['failure_at_revoke']['kind'], 'guard_heartbeat_stall')
        self.assertTrue(recovery.recoverable(RuntimeError(REVOKED), root))
        self.assertEqual(recovery.guard_classification(root)['staleness'], 13.25)

    def test_b_stall_retries_use_the_existing_bounded_backoff_then_exhaust(self):
        delays, receipts = [], []

        def generation():
            carrier, root, calls, revoke = watcher(self, FakeGuard())
            receipts.append(root)
            run_watcher(carrier, revoke, Clock(0, 20))
            return 'recoverable' if recovery.recoverable(RuntimeError(REVOKED), root) else 'terminal'
        stop = SimpleNamespace(is_set=lambda: False, wait=lambda delay: delays.append(delay) or False)
        state = make_root(self)
        result = recovery.Supervisor(state, stop, lambda: 0).run(generation)
        self.assertEqual((result, len(receipts), delays), ('exhausted', 4, [5, 30, 120]))
        self.assertEqual(json.loads((state/'recovery-state.json').read_text())['state'], 'exhausted')

    def test_stubborn_guard_is_killed_after_bounded_terminate(self):
        guard = FakeGuard(dies='kill')
        carrier, root, calls, revoke = watcher(self, guard)
        with patch.object(network, 'GUARD_TERMINATE_WAIT', 0), patch.object(network, 'GUARD_KILL_WAIT', 0):
            run_watcher(carrier, revoke, Clock(0, 13))
        self.assertEqual(guard.signals, ['term', 'kill'])
        self.assertEqual([c['reason'] for c in calls], [network.STALL_KIND])

    def test_guard_that_cannot_be_stopped_is_terminal_not_a_stall(self):
        guard = FakeGuard(dies='never')
        carrier, root, calls, revoke = watcher(self, guard)
        with patch.object(network, 'GUARD_TERMINATE_WAIT', 0), patch.object(network, 'GUARD_KILL_WAIT', 0):
            run_watcher(carrier, revoke, Clock(0, 13))
        self.assertEqual([c['reason'] for c in calls], ['independent_guard_lost'])
        self.assertFalse((root/'guard-failure.json').exists())
        self.assertFalse(recovery.recoverable(RuntimeError(REVOKED), root))


class WatcherTerminal(unittest.TestCase):
    def test_c_exited_guard_revokes_immediately_and_terminally(self):
        for code in (0, 1, -signal.SIGKILL):
            with self.subTest(code=code):
                guard = FakeGuard(returncode=code)
                carrier, root, calls, revoke = watcher(self, guard, heartbeat=None)
                run_watcher(carrier, revoke, Clock(0))  # no staleness needed
                self.assertEqual([c['reason'] for c in calls], ['independent_guard_lost'])
                self.assertEqual(guard.signals, [])
                self.assertFalse((root/'guard-failure.json').exists())
                self.assertFalse(recovery.recoverable(RuntimeError(REVOKED), root))

    def test_d_existing_terminal_classification_is_never_masked_by_a_stall(self):
        for kind in ('terminal', 'owned_workload_crash'):
            with self.subTest(kind=kind):
                guard = FakeGuard()
                carrier, root, calls, revoke = watcher(self, guard)
                (root/'guard-failure.json').write_text(json.dumps({'kind': kind, 'time': 1}))
                run_watcher(carrier, revoke, Clock(0, 50))
                self.assertEqual([c['reason'] for c in calls], ['independent_guard_lost'])
                self.assertEqual(failure(root), {'kind': kind, 'time': 1})
                self.assertEqual(guard.signals, [])

    def test_d_existing_revocation_or_deliberate_stop_is_never_a_stall(self):
        for name in ('authority/revoked.json', 'guard-revoked.json'):
            with self.subTest(name=name):
                carrier, root, calls, revoke = watcher(self, FakeGuard())
                (root/name).write_text('{}')
                run_watcher(carrier, revoke, Clock(0, 50))
                self.assertEqual([c['reason'] for c in calls], ['independent_guard_lost'])
                self.assertFalse((root/'guard-failure.json').exists())
                self.assertFalse(recovery.recoverable(RuntimeError(REVOKED), root))

    def test_d_terminal_written_while_guard_is_stopping_wins(self):
        carrier, root, calls, revoke = watcher(self, None)
        carrier.guard = FakeGuard(on_terminate=lambda: (root/'guard-failure.json').write_text(
            json.dumps({'kind': 'terminal', 'time': 2})))
        run_watcher(carrier, revoke, Clock(0, 50))
        self.assertEqual([c['reason'] for c in calls], ['independent_guard_lost'])
        self.assertEqual(failure(root)['kind'], 'terminal')
        self.assertFalse(recovery.recoverable(RuntimeError(REVOKED), root))

    def test_d_guard_exiting_by_itself_while_being_stopped_is_terminal(self):
        guard = FakeGuard(dies='self-exit')  # exit status 1: not our signal
        carrier, root, calls, revoke = watcher(self, guard)
        run_watcher(carrier, revoke, Clock(0, 50))
        self.assertEqual([c['reason'] for c in calls], ['independent_guard_lost'])
        self.assertFalse((root/'guard-failure.json').exists())

    def test_d_deliberate_stop_owns_revocation(self):
        guard = FakeGuard()
        carrier, root, calls, revoke = watcher(self, guard)
        carrier.closed = True
        self.assertIsNone(carrier.revoke_stalled_guard(99))
        self.assertEqual((calls, guard.signals), ([], []))
        carrier.closed = False
        guard.on_terminate = lambda: setattr(carrier, 'closed', True)
        with patch.object(network, 'revoke', side_effect=revoke):
            self.assertIsNone(carrier.revoke_stalled_guard(99))
        self.assertEqual(calls, [])
        self.assertFalse((root/'guard-failure.json').exists())

    def test_d_classification_precedence_in_writer(self):
        root = make_root(self)
        with patch.object(network.time, 'monotonic', return_value=5):
            self.assertTrue(network.write_guard_failure(root, 'owned_workload_crash'))
            # the guard's own terminal replaces its earlier eligible crash (existing behaviour)
            self.assertTrue(network.write_guard_failure(root, 'terminal'))
            self.assertEqual(failure(root)['kind'], 'terminal')
            # a stall never replaces anything
            self.assertFalse(network.write_guard_failure(root, network.STALL_KIND, staleness=20, tolerance=12))
            self.assertEqual(failure(root)['kind'], 'terminal')
        other = make_root(self)
        with patch.object(network.time, 'monotonic', return_value=5):
            self.assertTrue(network.write_guard_failure(other, network.STALL_KIND, staleness=20, tolerance=12))
            # a late terminal from the stalled guard cannot overwrite the stall; stall is write-once
            self.assertFalse(network.write_guard_failure(other, 'terminal'))
            self.assertFalse(network.write_guard_failure(other, network.STALL_KIND, staleness=99, tolerance=12))
        self.assertEqual(failure(other)['staleness'], 20)
        self.assertEqual([p.name for p in other.iterdir() if '.next-' in p.name], [])

    def test_d_other_security_causes_stay_unrecoverable(self):
        root = make_root(self)
        (root/'guard-failure.json').write_text(json.dumps({'kind': network.STALL_KIND, 'time': 1}))
        self.assertTrue(recovery.recoverable(RuntimeError(REVOKED), root))
        for error in [PermissionError(), OSError(), ValueError(), RuntimeError('lease_expired'),
                      RuntimeError('namespace_generation_changed'), RuntimeError('independent_guard_lost')]:
            with self.subTest(error=repr(error)):
                self.assertFalse(recovery.recoverable(error, root))
        wrapped = RuntimeError(REVOKED)
        wrapped.__cause__ = PermissionError()
        self.assertFalse(recovery.recoverable(wrapped, root))
        for kind in ('terminal', 'unknown', None):
            (root/'guard-failure.json').write_text(json.dumps({'kind': kind}))
            self.assertFalse(recovery.recoverable(RuntimeError(REVOKED), root))

    def test_d_revoke_stop_errors_still_fail_verify_closed(self):
        # Stall revocation whose container stop failed: the replacement gate stays closed.
        root = make_root(self)
        (root/'guard-failure.json').write_text(json.dumps({'kind': network.STALL_KIND, 'time': 1}))
        carrier = SimpleNamespace(dependents=[], id='carrier', worker_ack=None, receipt=root, generation='g')
        running = json.dumps([{'Id': 'carrier', 'Config': {'Labels': {'garage.audio.generation': 'g'}},
                               'State': {'Running': True}}])
        with patch.object(recovery, 'docker', return_value=running):
            with self.assertRaises(PermissionError):
                recovery.verify_closed(carrier)


def guard_fixture(testcase, mutate_record=None, mutate_inspect=None, docker_error=None):
    """Run ONE guard_main pass against fake docker; returns (revoke mock, docker calls, root)."""
    root = make_root(testcase)
    expected = {'pid': 123, 'birth': 'b', 'inode': 7}
    record = {'receipt': str(root), 'generation': 'g', 'owner': dict(expected), 'dhcp': dict(expected),
              'receiver': dict(expected), 'carrier': 'carrier', 'carrier_process': dict(expected),
              'inode': 7, 'address': '192.0.2.1',
              'dependent_processes': {'app': dict(expected), 'worker': dict(expected)}}
    if mutate_record:
        mutate_record(record)
    registry = root/'registry.json'
    registry.write_text(json.dumps(record))
    registry.chmod(0o600)
    (root/'lease.json').write_text(json.dumps({'event': 'bound', 'ip': '192.0.2.1', 'received': 0, 'seconds': 1000}))
    calls = []

    def docker(*args, **kwargs):
        calls.append((args, kwargs))
        if docker_error:
            raise docker_error
        items = []
        for identity in args[1:]:
            role = {'app': 'dashboard', 'worker': 'audio-device'}.get(identity)
            items.append({'Id': identity, 'Config': {'Labels': {'garage.audio.generation': 'g',
                                                                'com.docker.compose.service': role}},
                          'State': {'Pid': 123, 'Running': True, 'Status': 'running', 'ExitCode': 0}})
        if mutate_inspect:
            items = mutate_inspect(items)
        return json.dumps(items)
    original_stat = os.stat

    def stat(path, *args, **kwargs):
        return SimpleNamespace(st_ino=7) if str(path) == '/proc/123/ns/net' else original_stat(path, *args, **kwargs)
    sleeps = []

    def sleep(_):
        sleeps.append(1)
        raise Done()
    with patch.object(network.os, 'geteuid', return_value=0), patch.object(network, 'birth', return_value='b'), \
            patch.object(network, 'docker', side_effect=docker), patch.object(network.os, 'stat', side_effect=stat), \
            patch.object(network.time, 'monotonic', return_value=1), patch.object(network.time, 'sleep', side_effect=sleep), \
            patch.object(network.subprocess, 'check_output', return_value='[{"addr_info":[{"local":"192.0.2.1"}]}]'), \
            patch.object(network, 'revoke') as revoke:
        try:
            network.guard_main(registry)
        except Done:
            pass
    return revoke, calls, root, sleeps


class BatchedInspect(unittest.TestCase):
    def test_e_one_subprocess_call_per_pass_and_a_healthy_pass_publishes_heartbeat(self):
        revoke, calls, root, sleeps = guard_fixture(self)
        self.assertEqual(len(calls), 1)
        args, kwargs = calls[0]
        self.assertEqual(args, ('inspect', 'carrier', 'app', 'worker'))
        self.assertEqual(kwargs, {'timeout': network.GUARD_INSPECT_TIMEOUT})
        revoke.assert_not_called()
        self.assertEqual(json.loads((root/'guard-heartbeat.json').read_text()), {'time': 1})
        self.assertTrue((root/'authority/authority.json').exists())

    def test_e_all_existing_checks_survive_batching_and_fail_closed(self):
        def label(items):
            items[0]['Config']['Labels']['garage.audio.generation'] = 'other'
            return items

        def carrier_pid(items):
            items[0]['State']['Pid'] = 124
            return items

        def not_running(items):
            items[0]['State']['Running'] = False
            return items

        def dependent_label(items):
            items[2]['Config']['Labels']['garage.audio.generation'] = 'other'
            return items

        def dependent_stopped(items):
            items[1]['State'].update(Running=False, Status='created')
            return items
        cases = [
            ('carrier label', None, label, None, 'namespace_generation_changed', 'terminal'),
            ('carrier pid', None, carrier_pid, None, 'namespace_generation_changed', 'terminal'),
            ('carrier not running', None, not_running, None, 'namespace_generation_changed', 'terminal'),
            ('carrier birth', lambda r: r['carrier_process'].update(birth='x'), None, None,
             'namespace_generation_changed', 'terminal'),
            ('carrier netns inode', lambda r: r.update(inode=8), None, None, 'namespace_generation_changed', 'terminal'),
            ('dependent label', None, dependent_label, None, 'PermissionError', 'terminal'),
            ('dependent not running', None, dependent_stopped, None, 'dependent_generation_changed', 'terminal'),
            ('dependent birth', lambda r: r['dependent_processes']['app'].update(birth='x'), None, None,
             'dependent_generation_changed', 'terminal'),
            ('dependent pid', lambda r: r['dependent_processes']['app'].update(pid=999), None, None,
             'dependent_generation_changed', 'terminal'),
            ('dependent inode', lambda r: r['dependent_processes']['worker'].update(inode=9), None, None,
             'dependent_generation_changed', 'terminal'),
            ('missing id', None, lambda items: items[:2], None, 'inspect_batch_mismatch', 'terminal'),
            ('extra id', None, lambda items: items + [dict(items[0], Id='extra')], None,
             'inspect_batch_mismatch', 'terminal'),
            ('duplicate id', None, lambda items: [items[0], items[1], dict(items[1])], None,
             'inspect_batch_mismatch', 'terminal'),
            ('wrong exact id', None, lambda items: [dict(items[0], Id='carrier-prefix'), *items[1:]], None,
             'inspect_batch_mismatch', 'terminal'),
            ('nonzero exit', None, None, subprocess.CalledProcessError(1, ['docker', 'inspect']),
             'CalledProcessError', 'terminal'),
            ('malformed result', None, lambda items: {'not': 'a list'}, None, 'inspect_batch_mismatch', 'terminal'),
        ]
        for name, record, inspect, error, reason, kind in cases:
            with self.subTest(name):
                revoke, calls, root, _ = guard_fixture(self, record, inspect, error)
                self.assertEqual(len(calls), 1)
                self.assertEqual(revoke.call_count, 1)
                self.assertEqual(revoke.call_args.args[1], reason)
                self.assertEqual(failure(root)['kind'], kind)
                self.assertFalse((root/'guard-heartbeat.json').exists())

    def test_e_workload_crash_classification_and_ordering_are_preserved(self):
        def crash(items):
            items[1]['State'].update(Running=False, Status='exited', ExitCode=1, Pid=0)
            return items
        revoke, calls, root, _ = guard_fixture(self, None, crash)
        self.assertEqual(failure(root)['kind'], 'owned_workload_crash')
        self.assertEqual(revoke.call_args.args[1], 'owned_workload_crash')
        self.assertEqual(len(calls), 1)

        def crash_and_invalid(items):
            items = crash(items)
            items[2]['State']['Pid'] = 124  # sibling no longer the registered process
            return items
        revoke, calls, root, _ = guard_fixture(self, None, crash_and_invalid)
        self.assertEqual(failure(root)['kind'], 'terminal')  # security still wins over the crash

    def test_e_identity_checks_before_inspect_are_not_reordered(self):
        revoke, calls, root, _ = guard_fixture(self, lambda r: r['owner'].update(birth='x'))
        self.assertEqual(calls, [])  # owner_lost is detected before any docker call
        self.assertEqual(revoke.call_args.args[1], 'owner_lost')

    def test_inspect_timeout_skips_the_pass_without_publishing_health(self):
        revoke, calls, root, sleeps = guard_fixture(self, docker_error=subprocess.TimeoutExpired('docker', 6))
        revoke.assert_not_called()
        self.assertEqual(sleeps, [1])
        self.assertFalse((root/'guard-heartbeat.json').exists())
        self.assertFalse((root/'authority/authority.json').exists())
        self.assertFalse((root/'guard-failure.json').exists())

    def test_inspect_batch_unit(self):
        def item(identity):
            return {'Id': identity}
        with patch.object(network, 'docker', return_value=json.dumps([item('b'), item('a')])) as docker:
            self.assertEqual(sorted(network.inspect_batch(['a', 'b'])), ['a', 'b'])
            docker.assert_called_once_with('inspect', 'a', 'b', timeout=network.GUARD_INSPECT_TIMEOUT)
        for bad in ([], ['a', 'a'], ['a', ''], ['a', None]):
            with self.subTest(bad=bad), self.assertRaises(RuntimeError):
                network.inspect_batch(bad)


class Status(unittest.TestCase):
    def state(self, root, name, **fields):
        (root/'recovery-state.json').write_text(json.dumps({'schema': 1, 'invocation': 'i', 'state': name, 'retries': 1,
                                                            'generation': 'gen', 'observed': 90, **fields}))

    def snapshot(self, root):
        return sorted((str(p), p.stat().st_mtime_ns, p.stat().st_size) for p in root.rglob('*'))

    def test_f_every_state_has_an_explanation_and_restart_guidance(self):
        for name in ('starting', 'ready', 'backoff', 'stopped', 'terminal', 'exhausted'):
            with self.subTest(name):
                root = make_root(self)
                self.state(root, name, **({'delay': 30} if name == 'backoff' else {}))
                report = recovery.collect_status(root, now=100)
                self.assertEqual((report['state'], report['retries'], report['retries_remaining']), (name, 1, 2))
                self.assertEqual(report['state_age_seconds'], 10)
                self.assertTrue(report['explanation'] and report['restart_guidance'])
                text = recovery.format_status(report)
                self.assertIn('state: '+name, text)
                self.assertIn('restart guidance:', text)
                if name in ('terminal', 'exhausted'):
                    self.assertIn('systemctl start', report['restart_guidance'])
                if name == 'backoff':
                    self.assertEqual(report['backoff_delay_seconds'], 30)
                    self.assertIn('Do not restart', report['restart_guidance'])

    def test_f_unknown_without_state_and_with_garbage(self):
        root = make_root(self)
        self.assertEqual(recovery.collect_status(root, now=1)['state'], 'unknown')
        (root/'recovery-state.json').write_text('not json')
        self.assertEqual(recovery.collect_status(root, now=1)['state'], 'unknown')
        self.state(root, 'something-new')
        report = recovery.collect_status(root, now=100)
        self.assertEqual(report['state'], 'something-new')
        self.assertIn('Unrecognized', report['explanation'])

    def test_f_reports_generation_stall_revocation_and_heartbeat(self):
        root = make_root(self)
        self.state(root, 'backoff', delay=5, last_failure={'guard_failure': {'kind': 'guard_heartbeat_stall'}})
        old = root/'generation-100'
        (old/'authority').mkdir(parents=True)
        new = root/'generation-200'
        (new/'authority').mkdir(parents=True)
        (old/'guard-failure.json').write_text(json.dumps({'kind': 'terminal', 'time': 1}))
        (new/'guard-failure.json').write_text(json.dumps({'kind': 'guard_heartbeat_stall', 'time': 95,
                                                          'staleness': 13.25, 'tolerance': 12.0}))
        (new/'guard-heartbeat.json').write_text(json.dumps({'time': 81.5}))
        (new/'guard-revoked.json').write_text(json.dumps({'reason': 'guard_heartbeat_stall', 'time': 96,
                                                          'containers_stopped': {'a': True, 'b': True}}))
        (new/'driver-failure.json').write_text(json.dumps({'class': 'RuntimeError', 'recovery': 'recoverable',
                                                           'guard_exit': -15,
                                                           'guard_failure': {'kind': 'guard_heartbeat_stall'}}))
        report = recovery.collect_status(root, now=100)
        self.assertEqual(report['receipt'], 'generation-200')
        self.assertEqual(report['generation'], 'gen')
        self.assertEqual(report['heartbeat_age_seconds'], 18.5)
        self.assertEqual(report['guard_failure'], {'kind': 'guard_heartbeat_stall', 'staleness': 13.25,
                                                   'tolerance': 12.0, 'age_seconds': 5})
        self.assertEqual(report['last_revocation'], {'reason': 'guard_heartbeat_stall', 'age_seconds': 4,
                                                     'containers_stopped': True})
        self.assertEqual(report['driver_failure']['guard_exit'], -15)
        self.assertEqual(report['tolerance']['default_seconds'], 12.0)
        text = recovery.format_status(report)
        for fragment in ('guard_heartbeat_stall', 'staleness=13.25', 'heartbeat age (s): 18.5', 'last revocation:'):
            self.assertIn(fragment, text)

    def test_f_failed_stop_is_visible_in_revocation(self):
        root = make_root(self)
        self.state(root, 'terminal')
        receipt = root/'generation-1'
        receipt.mkdir()
        (receipt/'guard-revoked.json').write_text(json.dumps({'reason': 'x; stop-error:CalledProcessError', 'time': 1,
                                                              'containers_stopped': {'a': True, 'b': False}}))
        self.assertIs(recovery.collect_status(root, now=5)['last_revocation']['containers_stopped'], False)

    def test_f_ages_from_another_boot_are_not_reported(self):
        root = make_root(self)
        self.state(root, 'ready', observed=5000)
        receipt = root/'generation-1'
        receipt.mkdir()
        (receipt/'guard-heartbeat.json').write_text(json.dumps({'time': 6000}))
        report = recovery.collect_status(root, now=100)
        self.assertIsNone(report['state_age_seconds'])
        self.assertIsNone(report['heartbeat_age_seconds'])
        self.assertTrue(any('another' in n or 'ahead' in n for n in report['notes']))

    def test_f_status_is_read_only_and_takes_no_lock(self):
        root = make_root(self)
        self.state(root, 'terminal')
        receipt = root/'generation-1'
        receipt.mkdir()
        (receipt/'guard-failure.json').write_text(json.dumps({'kind': 'terminal', 'time': 1}))
        before = self.snapshot(root)
        out = io.StringIO()
        with patch.object(recovery, 'owner_lock', side_effect=AssertionError('lock')), \
                patch.object(recovery, 'atomic', side_effect=AssertionError('write')), \
                patch.object(network, 'atomic', side_effect=AssertionError('write')), \
                patch.object(Path, 'write_text', side_effect=AssertionError('write')), \
                patch.object(Path, 'mkdir', side_effect=AssertionError('write')), \
                contextlib.redirect_stdout(out):
            self.assertEqual(recovery.main(['status', '--root', str(root), '--json']), 0)
            parsed = json.loads(out.getvalue())
            self.assertEqual(parsed['state'], 'terminal')
            out.truncate(0)
            out.seek(0)
            self.assertEqual(recovery.main(['status', '--root', str(root)]), 0)
            self.assertIn('state: terminal', out.getvalue())
        self.assertEqual(self.snapshot(root), before)

    def test_f_missing_or_unreadable_root_is_reported_not_created(self):
        root = make_root(self)/'absent'
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertEqual(recovery.main(['status', '--root', str(root)]), 2)
        self.assertFalse(root.exists())
        self.assertIn('not found', err.getvalue())
        readable = make_root(self)
        with patch.object(recovery.os, 'access', return_value=False), contextlib.redirect_stderr(err):
            self.assertEqual(recovery.main(['status', '--root', str(readable)]), 2)

    def test_supervisor_records_classification_name_only(self):
        root = make_root(self)

        def generation():
            receipt = root/'generation-7'
            receipt.mkdir(exist_ok=True)
            (receipt/'guard-failure.json').write_text(json.dumps(
                {'kind': 'guard_heartbeat_stall', 'time': 1, 'staleness': 14, 'tolerance': 12, 'detail': 'secret'}))
            (receipt/'driver-failure.json').write_text(json.dumps({'class': 'RuntimeError', 'recovery': 'recoverable',
                                                                   'guard_exit': -15, 'message': 'private detail'}))
            return 'recoverable'
        stop = SimpleNamespace(is_set=lambda: False, wait=lambda _: False)
        self.assertEqual(recovery.Supervisor(root, stop, lambda: 0).run(generation), 'exhausted')
        state = json.loads((root/'recovery-state.json').read_text())
        self.assertEqual(state['last_failure']['guard_failure'],
                         {'kind': 'guard_heartbeat_stall', 'staleness': 14, 'tolerance': 12})
        self.assertEqual(state['last_failure']['driver_guard_exit'], -15)
        self.assertNotIn('private detail', json.dumps(state))
        self.assertNotIn('secret', json.dumps(state))


class SpyEvent(threading.Event):
    """Event that reports when a thread starts waiting on it (deterministic hand-off, no sleeps)."""

    def __init__(self):
        super().__init__()
        self.waiting = threading.Event()

    def wait(self, timeout=None):
        self.waiting.set()
        return super().wait(timeout)


class BlockingGuard(FakeGuard):
    """The guard dies on SIGTERM, but wait() does not return until `release` is set: the window in
    which the guard is already gone while the watcher has not yet recorded its classification."""

    def __init__(self):
        super().__init__()
        self.gone = threading.Event()
        self.release = threading.Event()

    def wait(self, timeout=None):
        self.gone.set()
        if not self.release.wait(10):
            raise AssertionError('test deadlock: watcher was never released')
        return super().wait(timeout)


class DriverClassificationRace(unittest.TestCase):
    def test_m1_driver_classifies_recoverable_when_it_races_the_watcher_stall_path(self):
        guard = BlockingGuard()
        carrier, root, calls, revoke = watcher(self, guard)
        carrier.guard_verdict = SpyEvent()
        outcome, errors = [], []

        def run(target, *args):
            def body():
                try:
                    target(*args)
                except BaseException as error:  # surfaced by the assertions below
                    errors.append(error)
            thread = threading.Thread(target=body, daemon=True)
            thread.start()
            return thread
        with patch.object(network, 'revoke', side_effect=revoke):
            watcher_thread = run(carrier.revoke_stalled_guard, 13.25)
            self.assertTrue(guard.gone.wait(5))
            # The window: guard gone (driver's healthy() would raise), no classification yet.
            self.assertIsNotNone(guard.poll())
            self.assertFalse((root/'guard-failure.json').exists())
            self.assertTrue(carrier.stall_pending.is_set())
            self.assertFalse(recovery.recoverable(RuntimeError(REVOKED), root))  # what the unfixed driver saw
            driver_thread = run(lambda: outcome.append(recovery.classify_failure(RuntimeError(REVOKED), root, carrier)))
            self.assertTrue(carrier.guard_verdict.waiting.wait(5))  # driver is blocked on the verdict
            self.assertEqual(outcome, [])
            guard.release.set()  # watcher resumes: writes the stall, then revokes
            watcher_thread.join(5)
            driver_thread.join(5)
        self.assertEqual(errors, [])
        self.assertEqual(outcome, ['recoverable'])
        self.assertEqual(failure(root)['kind'], network.STALL_KIND)
        self.assertEqual([c['reason'] for c in calls], [network.STALL_KIND])

    def test_m1_driver_sees_terminal_when_the_watcher_decides_terminal(self):
        guard = FakeGuard(dies='never')  # cannot be stopped: terminal, no stall file
        carrier, root, calls, revoke = watcher(self, guard)
        with patch.object(network, 'revoke', side_effect=revoke), \
                patch.object(network, 'GUARD_TERMINATE_WAIT', 0), patch.object(network, 'GUARD_KILL_WAIT', 0):
            carrier.revoke_stalled_guard(20)
        self.assertTrue(carrier.guard_verdict.is_set())
        self.assertEqual(recovery.classify_failure(RuntimeError(REVOKED), root, carrier), 'terminal')

    def test_m1_watcher_that_never_finalizes_is_terminal_after_the_bounded_wait(self):
        guard = FakeGuard(returncode=-signal.SIGTERM)
        carrier, root, calls, revoke = watcher(self, guard)
        carrier.stall_pending.set()  # watcher claimed the stall and then went silent
        carrier.guard_verdict = SpyEvent()
        # Even a stall file on disk is not trusted without the watcher's final verdict.
        (root/'guard-failure.json').write_text(json.dumps({'kind': network.STALL_KIND, 'time': 1}))
        with patch.object(network, 'GUARD_VERDICT_WAIT', 0.01):
            self.assertFalse(carrier.await_guard_verdict())
            self.assertEqual(recovery.classify_failure(RuntimeError(REVOKED), root, carrier), 'terminal')
        self.assertTrue(carrier.guard_verdict.waiting.is_set())

    def test_the_wait_bound_covers_terminate_kill_and_a_margin(self):
        self.assertGreater(network.GUARD_VERDICT_WAIT, network.GUARD_TERMINATE_WAIT + network.GUARD_KILL_WAIT)

    def test_unrelated_failures_with_a_healthy_guard_are_not_delayed(self):
        carrier, root, calls, revoke = watcher(self, FakeGuard())
        carrier.guard_verdict = SpyEvent()
        self.assertTrue(carrier.await_guard_verdict())
        self.assertFalse(carrier.guard_verdict.waiting.is_set())
        self.assertEqual(recovery.classify_failure(PermissionError('x'), root, carrier), 'terminal')
        carrier.guard = None  # failure before the guard existed
        self.assertTrue(carrier.await_guard_verdict())

    def test_guard_that_exited_by_itself_is_released_by_the_watcher(self):
        guard = FakeGuard(returncode=1)
        carrier, root, calls, revoke = watcher(self, guard, heartbeat=None)
        (root/'guard-failure.json').write_text(json.dumps({'kind': 'owned_workload_crash', 'time': 1}))
        run_watcher(carrier, revoke, Clock(0))
        self.assertTrue(carrier.await_guard_verdict(0.01))
        self.assertTrue(carrier.guard_verdict.is_set())
        self.assertEqual(recovery.classify_failure(RuntimeError(REVOKED), root, carrier), 'recoverable')

    def test_every_watcher_exit_path_releases_the_driver(self):
        # deliberate stop before the first poll
        carrier, root, calls, revoke = watcher(self, FakeGuard())
        carrier.closed = True
        run_watcher(carrier, revoke, Clock(0))
        self.assertTrue(carrier.guard_verdict.is_set())
        self.assertEqual(calls, [])
        # deliberate stop while the stalled guard is being terminated
        guard = FakeGuard()
        carrier, root, calls, revoke = watcher(self, guard)
        guard.on_terminate = lambda: setattr(carrier, 'closed', True)
        run_watcher(carrier, revoke, Clock(0, 13))
        self.assertTrue(carrier.guard_verdict.is_set())
        self.assertFalse((root/'guard-failure.json').exists())


class WatcherRobustness(unittest.TestCase):
    def test_malformed_heartbeats_are_treated_like_a_missing_heartbeat(self):
        for text in ['{"time": "abc"}', '{"time": null}', '{"time": [1]}', '{"time": true}', '[]', '"x"', '7',
                     '{"time": 1e999}', '{"time": 9999}', '{"time": -1e999}', '{}', '']:
            with self.subTest(text=text):
                carrier, root, calls, revoke = watcher(self, FakeGuard())
                (root/'guard-heartbeat.json').write_text(text)
                run_watcher(carrier, revoke, Clock(0, 5, 13))
                # The watcher survived the parse and measured staleness from its own start.
                self.assertEqual([c['reason'] for c in calls], [network.STALL_KIND])
                self.assertEqual(failure(root)['staleness'], 13)
                self.assertFalse(carrier.watcher_failed.is_set())

    def test_malformed_or_missing_after_a_good_heartbeat_keeps_the_last_good_value(self):
        for damage in (lambda root: (root/'guard-heartbeat.json').write_text('{"time": "x"}'),
                       lambda root: (root/'guard-heartbeat.json').unlink(),
                       lambda root: (root/'guard-heartbeat.json').write_text('{"tim')):
            carrier, root, calls, revoke = watcher(self, FakeGuard(), heartbeat=5.0)
            run_watcher(carrier, revoke, Clock(0, 6, 17.5), on_sleep=lambda _n, root=root, damage=damage: damage(root))
            self.assertEqual([c['reason'] for c in calls], [network.STALL_KIND])
            self.assertEqual(failure(root)['staleness'], 12.5)  # from 5.0, not refreshed by the damage

    def test_unexpected_exception_revokes_terminally_and_never_kills_the_watcher_silently(self):
        guard = FakeGuard()
        carrier, root, calls, revoke = watcher(self, guard)
        with patch.object(network.Carrier, 'guard_watch_loop', side_effect=ZeroDivisionError), \
                contextlib.redirect_stderr(io.StringIO()) as err:
            run_watcher(carrier, revoke, Clock(0))
        self.assertEqual([c['reason'] for c in calls], ['independent_guard_lost'])
        self.assertFalse((root/'guard-failure.json').exists())  # never a stall
        self.assertTrue(carrier.watcher_failed.is_set())
        self.assertTrue(carrier.guard_verdict.is_set())
        self.assertIn('ZeroDivisionError', err.getvalue())
        self.assertNotIn('Traceback', err.getvalue())
        # healthy() refuses even if the revocation record were somehow absent
        (root/'authority/revoked.json').unlink()
        with self.assertRaisesRegex(RuntimeError, REVOKED):
            carrier.healthy()
        self.assertFalse(recovery.recoverable(RuntimeError(REVOKED), root))

    def test_exception_inside_the_stall_path_is_terminal(self):
        guard = FakeGuard()
        carrier, root, calls, revoke = watcher(self, guard)
        with patch.object(network, 'write_guard_failure', side_effect=OSError('disk')), \
                contextlib.redirect_stderr(io.StringIO()):
            run_watcher(carrier, revoke, Clock(0, 13))
        self.assertEqual([c['reason'] for c in calls], ['independent_guard_lost'])
        self.assertFalse((root/'guard-failure.json').exists())
        self.assertTrue(carrier.watcher_failed.is_set() and carrier.guard_verdict.is_set())

    def test_exception_while_a_deliberate_stop_is_in_progress_does_not_revoke(self):
        carrier, root, calls, revoke = watcher(self, FakeGuard())
        carrier.closed = True
        with patch.object(network.Carrier, 'guard_watch_loop', side_effect=ZeroDivisionError), \
                contextlib.redirect_stderr(io.StringIO()):
            run_watcher(carrier, revoke, Clock(0))
        self.assertEqual(calls, [])
        self.assertTrue(carrier.guard_verdict.is_set())


class StatusEffectiveTolerance(unittest.TestCase):
    def test_default_environment_and_invalid_are_reported_without_echo(self):
        env = network.HEARTBEAT_TOLERANCE_ENV
        report = recovery.tolerance_report({})
        self.assertEqual((report['effective_seconds'], report['effective_source']), (12.0, 'default'))
        report = recovery.tolerance_report({env: '30'})
        self.assertEqual((report['effective_seconds'], report['effective_source']), (30.0, 'environment'))
        for bad in ('s3cret-abc', '5'):
            report = recovery.tolerance_report({env: bad})
            self.assertEqual((report['effective_seconds'], report['effective_source']), (None, 'invalid'))
            self.assertNotIn(bad, json.dumps(report))

    def test_status_command_labels_the_effective_value(self):
        root = make_root(self)
        env = network.HEARTBEAT_TOLERANCE_ENV
        for value, fragment in (('45', '45 s (environment)'), ('s3cret-abc', 'INVALID')):
            out = io.StringIO()
            with patch.dict(os.environ, {env: value}), contextlib.redirect_stdout(out):
                self.assertEqual(recovery.main(['status', '--root', str(root)]), 0)
                text = out.getvalue()
                out.truncate(0)
                out.seek(0)
                self.assertEqual(recovery.main(['status', '--root', str(root), '--json']), 0)
                parsed = json.loads(out.getvalue())
            self.assertIn("effective in this process's environment", text)
            self.assertIn(fragment, text)
            self.assertNotIn('s3cret', text+json.dumps(parsed))
            self.assertIn('scope', parsed['tolerance'])
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop(env, None)
            self.assertEqual(recovery.collect_status(root, now=1)['tolerance']['effective_seconds'], 12.0)


if __name__ == '__main__':
    unittest.main()
