"""Synthetic public Soloist payload shapes + bounded redaction diagnostics; no grants."""
import json
import asyncio
import tempfile
import time
import unittest
import threading
from unittest.mock import patch
from pathlib import Path
import aiohttp
from aiohttp import web
from garage_audio.audio_observer import DecisionTrace, ReceiverState, MAReadback, drive


def settled(trace):
    deadline=time.monotonic()+1
    while trace.attempted_version < trace.version and time.monotonic()<deadline:
        time.sleep(.005)  # TEST ONLY: never called on the observer producer path
    if trace.attempted_version < trace.version:
        raise AssertionError('diagnostic sink did not settle')


class ObserverTraceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name)/'trace.json'
        self.trace = DecisionTrace(self.path)
        self.state = ReceiverState('fixture-private-generation', self.trace)

    def tearDown(self):
        self.trace.close()
        self.tmp.cleanup()

    def rows(self):
        settled(self.trace)
        return json.loads(self.path.read_text())['rows']

    def test_public_flat_shapes_not_envelope_and_snapshot_never_authority(self):
        # Public SoloistAuthState/PlaybackState runtime dataclasses are flat type+
        # fields, not {'event': ..., 'data': ...}. Raw metadata is ignored by admission.
        self.state.accept({'type': 'auth_state', 'logged_in': True, 'is_active': True,
                           'device_name': 'PRIVATE_CANARY'})
        self.state.accept({'type': 'playback_state', 'status': 'playing', 'volume': 40,
                           'is_active': True, 'item': {'uri': 'PRIVATE_CANARY', 'entity_type': 'track'},
                           'context': {'uri': 'PRIVATE_CANARY', 'entity_type': 'playlist',
                                       'decorations': {'token': 'PRIVATE_CANARY'}},
                           'position': {'position_ms': 10, 'timestamp_ms': 1, 'speed': 1.0}})
        self.assertIsNone(self.state.ticket)
        self.assertFalse(self.state.accept({'type': 'position_sync', 'position': {'position_ms': 50, 'timestamp_ms': 1, 'speed': 1.0}}))
        self.assertFalse(self.state.accept({'type': 'event', 'data': {'status': 'playing'}}))
        settled(self.trace)
        self.assertNotIn('PRIVATE_CANARY', self.path.read_text())
        self.assertFalse(any(row['event']=='fresh_delta' for row in self.rows()))

    def test_flat_remote_delta_trace_and_freshness_unchanged(self):
        self.state.accept({'type': 'auth_state', 'logged_in': True, 'is_active': True})
        self.state.accept({'type': 'playback_changed', 'status': 'playing', 'volume': 40,
                           'is_active': True, 'available_actions': {}, 'options': {}})
        ticket = self.state.ticket
        self.assertTrue(self.state.eligible(ticket))
        delta = next(row for row in self.rows() if row['event']=='fresh_delta')
        self.assertTrue(delta['eligible']);self.assertTrue(delta['ticket_created'])
        deadline = ticket.expires
        self.state.accept({'type': 'playback_state', 'status': 'playing', 'volume': 40, 'is_active': True})
        self.assertIs(self.state.ticket, ticket);self.assertEqual(deadline,ticket.expires)
        ticket.expires = time.monotonic()-1
        self.assertFalse(self.state.eligible(ticket));self.assertEqual(self.state.rejection(ticket),'expired')
        self.state.epoch='different-generation'
        self.assertEqual(self.state.rejection(ticket),'epoch')
        self.state.ticket=None
        self.assertEqual(self.state.rejection(ticket),'replaced')

    def test_delta_without_auth_or_active_still_does_not_qualify(self):
        self.state.accept({'type':'playback_changed','status':'playing','volume':40,'is_active':True})
        self.assertIsNone(self.state.ticket)
        self.assertFalse(self.rows()[-1]['ticket_created'])
        self.state.accept({'type':'auth_state','logged_in':True,'is_active':False})
        self.state.accept({'type':'playback_changed','status':'playing','volume':40,'is_active':False})
        self.assertFalse(self.state.eligible(self.state.ticket))
        self.assertEqual(self.state.rejection(self.state.ticket),'inactive')

    def test_fresh_inactive_play_survives_unchanged_auth_until_observed_activation(self):
        # Retained 22:33:52Z sequence: Play inactive -> auth still inactive ->
        # device active .3s later. Previously the middle snapshot lost the ticket.
        self.state.accept({'type':'auth_state','logged_in':True,'is_active':False})
        self.state.accept({'type':'playback_changed','status':'playing','volume':40,'is_active':False})
        ticket=self.state.ticket;deadline=ticket.expires
        for kind in ('playback_state','auth_state','device_changed'):
            value={'type':kind,'is_active':False,'logged_in':True,'status':'playing','volume':40}
            self.state.accept(value)
            self.assertIs(self.state.ticket,ticket)
            self.assertFalse(self.state.eligible(ticket))
            self.assertEqual(ticket.expires,deadline)
        self.state.accept({'type':'device_changed','is_active':True})
        self.assertIs(self.state.ticket,ticket);self.assertTrue(self.state.eligible(ticket))

    def test_inactive_candidate_logout_expiry_and_real_deactivation_still_revoke(self):
        for kind in ('auth_state','device_changed','playback_state','playback_changed'):
            with self.subTest(kind=kind):
                state=ReceiverState('fixture')
                state.accept({'type':'auth_state','logged_in':True,'is_active':True})
                state.accept({'type':'playback_changed','status':'playing','volume':40,'is_active':True})
                self.assertTrue(state.eligible(state.ticket))
                state.accept({'type':kind,'logged_in':True,'is_active':False,'status':'playing','volume':40})
                self.assertIsNone(state.ticket)
        for ending in ('logout','expired'):
            state=ReceiverState('fixture')
            state.accept({'type':'auth_state','logged_in':True,'is_active':False})
            state.accept({'type':'playback_changed','status':'playing','volume':40,'is_active':False})
            ticket=state.ticket
            if ending=='logout':
                state.accept({'type':'auth_state','logged_in':False,'is_active':False})
                self.assertIsNone(state.ticket)
            else:
                ticket.expires=time.monotonic()-1
                state.accept({'type':'auth_state','logged_in':True,'is_active':False})
                state.accept({'type':'device_changed','is_active':True})
                self.assertFalse(state.eligible(ticket));self.assertEqual(state.rejection(ticket),'expired')

    def test_ring_preserves_first_failure_and_honest_drop_counter(self):
        self.trace.record('ipc',kind='prepare',ok=False,error='RuntimeError',ipc_error='ticket',
                          token='PRIVATE_CANARY',uri='PRIVATE_CANARY',message='PRIVATE_CANARY')
        for sequence in range(1000):
            self.trace.record('prepare',sequence=sequence,ok=True,token_bound=True)
        settled(self.trace);doc=json.loads(self.path.read_text())
        self.assertEqual(len(doc['rows']),64);self.assertEqual(doc['dropped'],937)
        self.assertEqual(doc['first_failure']['ipc_error'],'ticket')
        self.assertEqual(doc['first_failure_context'][0]['ipc_error'],'ticket')
        self.assertNotIn('PRIVATE_CANARY',self.path.read_text())
        self.assertLessEqual(self.path.stat().st_size,32768)
        self.assertEqual(self.path.stat().st_mode & 0o777,0o600)
        # Logging remains live at cap: later branch is recorded, no observation stop.
        self.trace.record('delta',ok=True,consumed=True)
        self.assertEqual(self.rows()[-1]['event'],'delta')

    def test_trace_write_failure_does_not_disable_receiver(self):
        self.trace.path=Path(self.tmp.name)/'missing'/'trace.json'
        self.state.accept({'type':'auth_state','logged_in':True,'is_active':True})
        self.state.accept({'type':'playback_changed','status':'playing','volume':40,'is_active':True})
        self.assertTrue(self.state.eligible(self.state.ticket))
        settled(self.trace)
        self.assertGreater(self.trace.write_failures,0)

    def test_device_admission_failure_categories_are_actionable_without_raw_errors(self):
        for message, category in [
                ('exclusive numeric UID1002/GID1003 mode0600 required', 'device_ownership'),
                ('verified UMC node missing from container; recreate identity-bound mapping', 'device_mapping'),
                ('exact mapped node identity/permissions required', 'device_mapping'),
                ('USB connection or node identity changed; revoke current generation', 'device_identity'),
                ('fresh one-use worker ticket required', 'ticket'),
                ('PRIVATE_CANARY', 'other')]:
            self.trace.ipc_failure(RuntimeError(message), kind='delta')
            self.assertEqual(self.rows()[-1]['ipc_error'], category)
        self.assertNotIn('PRIVATE_CANARY', self.path.read_text())

    def test_idle_alternating_snapshot_types_do_not_fill_trace(self):
        for _ in range(1000):
            self.state.accept({'type':'auth_state','logged_in':True,'is_active':True})
            self.state.accept({'type':'playback_state','status':'playing','volume':40,'is_active':True})
        settled(self.trace);doc=json.loads(self.path.read_text())
        self.assertLessEqual(len(doc['rows']),4)
        self.assertIsNone(self.state.ticket)

    def test_filter_hash_and_enqueue_failures_are_diagnostic_only(self):
        with patch('garage_audio.audio_observer.hashlib.sha256',side_effect=RuntimeError('private-canary')):
            self.state.accept({'type':'auth_state','logged_in':True,'is_active':True})
            self.state.accept({'type':'playback_changed','status':'playing','volume':40,'is_active':True})
        self.assertTrue(self.state.eligible(self.state.ticket))
        with patch.object(self.trace,'_append',side_effect=RuntimeError('private-canary')):
            self.trace.record('prepare',ok=True)
        class BadError(Exception):
            def __str__(self):raise RuntimeError('private-canary')
        self.trace.ipc_failure(BadError())
        self.assertGreaterEqual(self.trace.write_failures,3)
        self.assertTrue(self.state.eligible(self.state.ticket))
        # A busy snapshot lock drops diagnostics, never waits or changes authority.
        self.trace.lock.acquire()
        try:
            started=time.monotonic();self.trace.record('delta',ok=True)
            self.assertLess(time.monotonic()-started,.1)
            self.assertGreater(self.trace.contention_drops,0)
        finally:self.trace.lock.release()


class DriverTraceTests(unittest.IsolatedAsyncioTestCase):
    async def test_observed_inactive_auth_race_activates_same_fresh_play_without_another_track(self):
        state=ReceiverState('fixture-epoch')
        state.accept({'type':'auth_state','logged_in':True,'is_active':False})
        state.accept({'type':'playback_changed','status':'playing','volume':40,'is_active':False})
        ticket=state.ticket;pending=None;consumed=asyncio.Event();calls=[]
        async def emit(kind,token=None):
            nonlocal pending
            calls.append(kind)
            if kind=='prepare':pending='fixture-worker-ticket'
            if kind=='delta':
                self.assertEqual(token,pending);pending=None;consumed.set()
            return {'state':'off','pending_ticket':pending}
        class MA:
            async def corroborate(self,provider):return True
        async def forbidden_pause():raise AssertionError('no receiver command')
        task=asyncio.create_task(drive(state,MA(),'fixture',emit,forbidden_pause))
        try:
            await asyncio.sleep(.01)
            state.accept({'type':'auth_state','logged_in':True,'is_active':False})
            self.assertIs(state.ticket,ticket);self.assertNotIn('prepare',calls)
            state.accept({'type':'device_changed','is_active':True})
            await asyncio.wait_for(consumed.wait(),.3)
            self.assertEqual(calls.count('delta'),1);self.assertIsNone(state.ticket)
        finally:
            task.cancel();await asyncio.gather(task,return_exceptions=True)

    async def test_exception_reports_actual_observer_function_without_payload_or_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            trace=DecisionTrace(Path(tmp)/'trace.json')
            ma=MAReadback(None,'fixture-reference','fixture-player')
            async def malformed_fixture(command,args):return ['PRIVATE_CANARY']
            ma.rpc=malformed_fixture
            try:
                with self.assertRaises(AttributeError) as raised:
                    await ma.corroborate('fixture-provider')
                # assertRaises removes traceback; collect a second explicit exception.
                try:await ma.corroborate('fixture-provider')
                except AttributeError as error:trace.exception(error,epoch='PRIVATE_CANARY')
                settled(trace);doc=json.loads(trace.path.read_text());row=doc['first_failure']
                self.assertEqual(row['error'],'AttributeError');self.assertEqual(row['origin'],'corroborate')
                self.assertGreater(row['error_line'],0)
                self.assertNotIn('PRIVATE_CANARY',trace.path.read_text())
                class PrivateError(Exception):
                    def __str__(self):raise AssertionError('must never format exception')
                trace.exception(PrivateError('PRIVATE_CANARY'))
                trace.record('exception',origin='PRIVATE_CANARY',error_line=10001,error='PRIVATE_CANARY')
                settled(trace);rows=json.loads(trace.path.read_text())['rows']
                self.assertEqual(rows[-2]['error'],'other');self.assertEqual(rows[-2]['origin'],'other')
                self.assertNotIn('error_line',rows[-1]);self.assertNotIn('PRIVATE_CANARY',trace.path.read_text())
                self.assertLessEqual(trace.path.stat().st_size,32768)
            finally:trace.close()

    async def test_observed_late_activation_prepare_then_exception_keeps_authority_unconsumed(self):
        with tempfile.TemporaryDirectory() as tmp:
            trace=DecisionTrace(Path(tmp)/'trace.json');state=ReceiverState('fixture-epoch',trace)
            state.accept({'type':'auth_state','logged_in':True,'is_active':False})
            state.accept({'type':'playback_changed','status':'playing','volume':100,'is_active':False})
            ticket=state.ticket;self.assertIsNotNone(ticket);self.assertFalse(state.eligible(ticket))
            # Real r13 order: playingdelta while inactive, device/auth active later.
            state.accept({'type':'device_changed','is_active':True})
            state.accept({'type':'auth_state','logged_in':True,'is_active':True})
            self.assertTrue(state.eligible(ticket));calls=[]
            async def emit(kind,token=None):
                calls.append(kind);return {'state':'off','pending_ticket':'fixture-one-use'}
            class MA:
                async def corroborate(self,provider):raise TypeError('PRIVATE_CANARY')
            async def forbidden_pause():raise AssertionError('no receiver command')
            try:
                try:await drive(state,MA(),'fixture',emit,forbidden_pause)
                except TypeError as error:trace.exception(error,epoch=state.epoch)
                else:self.fail('authority exception must propagate')
                self.assertIn('prepare',calls);self.assertNotIn('delta',calls)
                settled(trace);doc=json.loads(trace.path.read_text())
                self.assertTrue(any(r['event']=='prepare' and r['token_bound'] for r in doc['rows']))
                self.assertEqual(doc['first_failure']['error'],'TypeError')
                self.assertEqual(doc['first_failure']['origin'],'drive')
                self.assertNotIn('PRIVATE_CANARY',trace.path.read_text())
            finally:trace.close()

    async def test_decision_trace_observes_prepare_corroboration_consumption_not_token(self):
        with tempfile.TemporaryDirectory() as tmp:
            trace=DecisionTrace(Path(tmp)/'trace.json');state=ReceiverState('private-generation',trace)
            state.accept({'type':'auth_state','logged_in':True,'is_active':True})
            state.accept({'type':'playback_changed','status':'playing','volume':40,'is_active':True})
            token='PRIVATE_BEARER_CANARY';pending=None;consumed=0
            async def emit(kind,ticket=None):
                nonlocal pending,consumed
                if kind=='prepare':pending=token
                if kind=='delta':
                    self.assertEqual(ticket,token);pending=None;consumed+=1
                return {'state':'off','pending_ticket':pending}
            class MA:
                async def corroborate(self,provider):return True
            async def forbidden_pause():raise AssertionError('no receiver command permitted')
            task=asyncio.create_task(drive(state,MA(),'fixture-provider',emit,forbidden_pause))
            try:await asyncio.sleep(.05)
            finally:task.cancel();await asyncio.gather(task,return_exceptions=True)
            self.assertEqual(consumed,1);self.assertIsNone(state.ticket)
            settled(trace)
            content=trace.path.read_text();self.assertNotIn(token,content);self.assertNotIn('private-generation',content)
            rows=json.loads(content)['rows'];events={r['event'] for r in rows}
            self.assertTrue({'fresh_delta','prepare','corroboration','delta'} <= events)
            self.assertTrue(next(r for r in rows if r['event']=='delta')['consumed'])
            prepared=next(r for r in rows if r['event']=='prepare')
            consumed_row=next(r for r in rows if r['event']=='delta')
            self.assertEqual(prepared['ticket_id'],consumed_row['ticket_id'])
            self.assertEqual(len(prepared['ticket_id']),12)
            trace.close()

    async def test_held_and_failed_sink_cannot_delay_admission_or_revocation(self):
        for fail in (False,True):
            with self.subTest(fail=fail),tempfile.TemporaryDirectory() as tmp:
                entered=threading.Event();release=threading.Event()
                class HeldTrace(DecisionTrace):
                    def _persist(self,snapshot):
                        entered.set();release.wait(2)
                        if fail:raise OSError('private-canary')
                        super()._persist(snapshot)
                trace=HeldTrace(Path(tmp)/'trace.json');trace.record('prepare',ok=True)
                self.assertTrue(await asyncio.to_thread(entered.wait,.5))
                state=ReceiverState('fixture-generation',trace)
                state.accept({'type':'auth_state','logged_in':True,'is_active':True})
                state.accept({'type':'playback_changed','status':'playing','volume':40,'is_active':True})
                pending=None;consumed=asyncio.Event();prepared=asyncio.Event();cancelled=asyncio.Event()
                wait_source=False;gate=asyncio.Event()
                async def emit(kind,ticket=None):
                    nonlocal pending
                    if kind=='prepare':pending='private-canary';prepared.set()
                    if kind=='delta':pending=None;consumed.set()
                    return {'state':'off','pending_ticket':pending}
                class MA:
                    async def corroborate(self,provider):
                        if wait_source:
                            try:await gate.wait()
                            except asyncio.CancelledError:cancelled.set();raise
                        return True
                async def forbidden_pause():raise AssertionError('no receiver command')
                started=time.monotonic();task=asyncio.create_task(drive(state,MA(),'fixture',emit,forbidden_pause))
                try:
                    await asyncio.wait_for(consumed.wait(),.3)
                    self.assertLess(time.monotonic()-started,.3)
                    self.assertFalse(release.is_set())  # sink is STILL held through admission
                    wait_source=True;prepared.clear()
                    state.accept({'type':'playback_changed','status':'playing','volume':40,'is_active':True})
                    await asyncio.wait_for(prepared.wait(),.3)
                    await asyncio.sleep(.01)
                    started=time.monotonic()
                    state.accept({'type':'auth_state','logged_in':False,'is_active':False})
                    await asyncio.wait_for(cancelled.wait(),.3)
                    self.assertLess(time.monotonic()-started,.3);self.assertIsNone(state.ticket)
                    started=time.monotonic();self.assertFalse(trace.close(.02))
                    self.assertLess(time.monotonic()-started,.1)
                finally:
                    task.cancel();await asyncio.gather(task,return_exceptions=True)
                    release.set();await asyncio.to_thread(trace.thread.join,.5)
                    self.assertFalse(trace.thread.is_alive())
                if fail:self.assertGreater(trace.write_failures,0)


class MAConnectionTests(unittest.IsolatedAsyncioTestCase):
    async def scenario(self, reject_second=False):
        # Real aiohttp ClientWebSocketResponse and writer, exact observed reset-at-send.
        # Synthetic MA server/auth token only; no worker/receiver or music commands.
        connections=0;calls=[]
        async def handler(request):
            nonlocal connections
            connections+=1;number=connections
            ws=web.WebSocketResponse();await ws.prepare(request)
            async for packet in ws:
                v=json.loads(packet.data);command=v['command'];calls.append((number,command))
                if command=='auth':
                    denied=reject_second and number==2
                    await ws.send_json({'message_id':v['message_id'],'error_code':1 if denied else None,'result':True})
                    if number==1:
                        await asyncio.sleep(.01)
                        request.transport.close()  # reset while authenticated socket is idle
                        break
                elif command=='players/get':
                    player=v['args']['player_id'];uri=f'fixture-provider://audio_source/{player}'
                    await ws.send_json({'message_id':v['message_id'],'result':{'player_id':player,'available':True,'playback_state':'playing','active_source':uri,'current_media':{'uri':uri,'media_type':'audio_source'}}})
                else:raise AssertionError('read-only commands only')
            return ws
        app=web.Application();app.router.add_get('/ws',handler)
        runner=web.AppRunner(app);await runner.setup();site=web.TCPSite(runner,'127.0.0.1',8095);await site.start()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                token=Path(tmp)/'fixture-token';token.write_text('fixture-only');token.chmod(0o600)
                async with aiohttp.ClientSession() as session:
                    ma=MAReadback(session,token,'fixture-player');await ma.connect()
                    self.assertIsInstance(ma.ws,aiohttp.ClientWebSocketResponse)
                    await asyncio.sleep(.05)
                    self.assertFalse(ma.ws.closed)  # stale writer not detected by closed property
                    try:
                        if reject_second:
                            with self.assertRaisesRegex(RuntimeError,'MA readback denied'):
                                await ma.corroborate('fixture-provider')
                            self.assertEqual(calls,[(1,'auth'),(2,'auth')])
                        else:
                            self.assertTrue(await ma.corroborate('fixture-provider'))
                            self.assertEqual(calls,[(1,'auth'),(2,'auth'),(2,'players/get')])
                        self.assertEqual(connections,2)
                    finally:await ma.ws.close()
        finally:await runner.cleanup()

    async def test_idle_socket_reset_reauthenticates_before_one_read_only_retry(self):
        await self.scenario()

    async def test_reauthentication_denial_never_retries_player_query(self):
        await self.scenario(reject_second=True)


if __name__=='__main__':unittest.main()
