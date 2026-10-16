"""Synthetic observer/MA race fixtures; no real Soloist/MA/user authorization proof."""
import asyncio
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from garage_audio.audio_container import Worker
from garage_audio.audio_observer import ReceiverState, drive


class PendingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.worker = Worker(Path(self.tmp.name)/'ipc', Path(self.tmp.name)/'evidence')
        self.state = ReceiverState('fixture-epoch')
        self.sequence = 0
        self.starts = 0
        self.cancelled = 0
        self.delay = .5
        self.qualifies = True
        def start():
            self.starts += 1
            self.worker.state = 'playing'
            self.worker.deadline = time.monotonic()+30
        self.start_patch = patch.object(self.worker, 'start', start)
        self.start_patch.start()
        self.state.accept({'type': 'auth_state', 'logged_in': True, 'is_active': True})
        self.state.accept({'type': 'playback_state', 'status': 'paused', 'volume': 40, 'is_active': True})
        async def emit(kind, ticket=None):
            self.sequence += 1
            value = dict(epoch='fixture-epoch', sequence=self.sequence, kind=kind,
                state=self.state.state, active=self.state.active, logged_in=self.state.logged_in,
                volume=self.state.volume)
            if ticket is not None:
                value['ticket'] = ticket
            return self.worker.event(value)
        self.emit = emit
        await emit('baseline')
        class MA:
            async def corroborate(_, provider):
                try:
                    await asyncio.sleep(self.delay)
                    return self.qualifies
                except asyncio.CancelledError:
                    self.cancelled += 1
                    raise
        async def pause():
            self.state.accept({'type': 'playback_changed', 'status': 'paused'})
        self.driver = asyncio.create_task(drive(self.state, MA(), 'synthetic-connect', emit, pause))

    async def asyncTearDown(self):
        self.driver.cancel()
        await asyncio.gather(self.driver, return_exceptions=True)
        self.worker.closed.set()
        self.worker.halt('test_shutdown')
        self.worker.guardian.join(timeout=1)
        self.start_patch.stop()
        self.tmp.cleanup()

    def play(self):
        self.state.accept({'type': 'playback_changed', 'status': 'playing'})

    async def test_snapshot_cannot_prepare_or_start(self):
        self.state.accept({'type': 'playback_state', 'status': 'playing', 'volume': 40, 'is_active': True})
        await asyncio.sleep(.65)
        self.assertEqual(self.starts, 0)
        self.assertIsNone(self.worker.pending_ticket)

    async def test_deferred_claim_then_actual_one_use_admission(self):
        self.play()
        await asyncio.sleep(.2)
        self.assertEqual(self.starts, 0)
        self.assertIsNotNone(self.worker.pending_ticket)
        await asyncio.sleep(.45)
        self.assertEqual(self.starts, 1)
        self.assertIsNone(self.worker.pending_ticket)

    async def test_public_stop_cancels_wait_then_fresh_play_without_new_baseline(self):
        self.play()
        await asyncio.sleep(.15)
        self.worker.halt('public_silence_only')
        await asyncio.sleep(.6)
        self.assertEqual(self.starts, 0)
        self.assertGreater(self.cancelled, 0)
        self.assertTrue(self.worker.baseline)
        self.delay = .05
        self.play()
        await asyncio.sleep(.3)
        self.assertEqual(self.starts, 1)
        self.assertTrue(self.worker.baseline)

    async def test_pause_and_auth_loss_cancel_pending(self):
        for message in ({'type': 'playback_changed', 'status': 'paused'},
                        {'type': 'auth_state', 'logged_in': False, 'is_active': False}):
            self.state.accept({'type': 'auth_state', 'logged_in': True, 'is_active': True})
            self.play()
            await asyncio.sleep(.12)
            self.state.accept(message)
            await asyncio.sleep(.55)
            self.assertEqual(self.starts, 0)
            self.assertIsNone(self.worker.pending_ticket)

    async def test_epoch_replacement_and_disconnect_cancel(self):
        self.play()
        await asyncio.sleep(.15)
        ticket = self.state.ticket
        self.state.epoch = 'reconnected-epoch'
        self.assertFalse(self.state.eligible(ticket))
        self.state.disconnect()
        await asyncio.sleep(.55)
        self.assertEqual(self.starts, 0)
        self.assertIsNone(self.worker.pending_ticket)

    async def test_snapshot_liveness_does_not_extend_fresh_ticket_deadline(self):
        self.delay = 2
        self.play()
        ticket = self.state.ticket
        deadline = ticket.expires
        for _ in range(6):
            await asyncio.sleep(.3)
            self.state.accept({'type': 'playback_state', 'status': 'playing', 'volume': 40, 'is_active': True})
            self.assertEqual(ticket.expires, deadline)
            self.assertIn(self.state.ticket, (None, ticket))
        self.assertEqual(self.starts, 0)
        self.assertGreater(self.cancelled, 0)


if __name__ == '__main__':
    unittest.main()
