"""Synthetic MA public-model fixtures; no live playback/credential/gain proof."""
import asyncio
import copy
import time
import unittest
from garage_audio.audio_observer import MAReadback, ReceiverState, drive

PLAYER = 'fixture-player'
PROVIDER = 'fixture-connect-instance'
URI = f'{PROVIDER}://audio_source/{PLAYER}'


def source_player():
    return {'player_id': PLAYER, 'available': True, 'playback_state': 'playing',
            'active_source': URI, 'current_media': {'media_type': 'audio_source', 'uri': URI}}


class Readback(MAReadback):
    def __init__(self, player=None):
        super().__init__(None, '/unused-fixture-token', PLAYER)
        self.player_value = source_player() if player is None else player
        self.calls = []

    async def rpc(self, command, args):
        self.calls.append((command, args))
        if command == 'player_queues/get' and args == {'queue_id': PLAYER}:
            # Preserve actual idle ordinary-queue semantics for the OLD accessor
            # run too, so its positive failure is false corroboration, not an RPC stub error.
            return {'active': False, 'state': 'idle', 'current_item': {'media_item': {
                'media_type': 'track', 'provider': 'other-provider', 'item_id': 'old-item'}}}
        if command != 'players/get' or args != {'player_id': PLAYER}:
            raise AssertionError('only fixed player readback permitted')
        return copy.deepcopy(self.player_value)


class SourceReadbackTests(unittest.IsolatedAsyncioTestCase):
    async def test_direct_source_with_inactive_stale_ordinary_queue(self):
        player = source_player()
        stale_queue = {'active': False, 'state': 'idle', 'current_item': {'media_item': {
            'media_type': 'track', 'provider': 'other-provider', 'item_id': 'old-item'}}}
        # Exact old accessor failure against the observed public-model shape.
        media = stale_queue['current_item']['media_item']
        old_result = player['available'] is True and player['playback_state'] == 'playing' \
            and media.get('provider') == PROVIDER and media.get('media_type') == 'audio_source' \
            and media.get('item_id') == PLAYER
        self.assertFalse(old_result)
        ma = Readback(player)
        self.assertTrue(await ma.corroborate(PROVIDER))
        self.assertEqual(ma.calls, [('players/get', {'player_id': PLAYER})])

    async def test_exact_identity_availability_and_current_source_required(self):
        cases = [
            ('wrong_player', {'player_id': 'other-player'}),
            ('unavailable', {'available': False}),
            ('missing_available', {'available': None}),
            ('paused_historical_uri', {'playback_state': 'paused'}),
            ('no_active_source', {'active_source': None}),
            ('wrong_provider', {'active_source': 'other://audio_source/'+PLAYER}),
            ('wrong_target', {'active_source': PROVIDER+'://audio_source/other-player'}),
            ('uri_prefix_not_exact', {'active_source': URI+'?ignored=true'}),
            ('wrong_media_uri', {'current_media': {'media_type': 'audio_source', 'uri': 'other'}}),
            ('wrong_media_type', {'current_media': {'media_type': 'track', 'uri': URI}}),
            ('missing_current_media', {'current_media': None}),
        ]
        for name, update in cases:
            with self.subTest(name=name):
                value = source_player();value.update(update)
                self.assertFalse(await Readback(value).corroborate(PROVIDER))
        self.assertFalse(await Readback({}).corroborate(PROVIDER))
        self.assertFalse(await Readback().corroborate('other-instance'))

    async def run_driver(self, receiver, ma, seconds=.08):
        events = []
        pending = None
        async def emit(kind, ticket=None):
            nonlocal pending
            events.append((kind, ticket))
            if kind == 'prepare':pending = 'server-one-use-fixture-ticket'
            return {'pending_ticket': pending, 'state': 'off'}
        async def forbidden_pause():
            raise AssertionError('no public stop fixture or receiver command expected')
        task = asyncio.create_task(drive(receiver, ma, PROVIDER, emit, forbidden_pause))
        try:await asyncio.sleep(seconds)
        finally:
            task.cancel();await asyncio.gather(task, return_exceptions=True)
        return events

    def receiver(self):
        value = ReceiverState('fixture-current-generation')
        value.accept({'type': 'auth_state', 'logged_in': True, 'is_active': True})
        value.accept({'type': 'playback_state', 'status': 'playing', 'volume': 40, 'is_active': True})
        return value

    async def test_snapshot_or_local_ma_play_cannot_mint_ticket(self):
        # MA may play the EXACT source after a local MA command; without a genuine
        # private receiver delta, this remains metadata, not playback admission.
        receiver = self.receiver()
        events = await self.run_driver(receiver, Readback())
        self.assertIsNone(receiver.ticket)
        self.assertFalse(any(kind in ('prepare', 'delta') for kind, _ in events))

    async def test_fresh_remote_delta_with_exact_direct_source_is_one_use(self):
        receiver = self.receiver()
        receiver.accept({'type': 'playback_changed', 'status': 'playing'})
        events = await self.run_driver(receiver, Readback())
        self.assertEqual(sum(kind == 'prepare' for kind, _ in events), 1)
        self.assertEqual(sum(kind == 'delta' for kind, _ in events), 1)
        self.assertIsNone(receiver.ticket)
        # The same current MA source and a snapshot cannot replay consumed delta.
        receiver.accept({'type': 'playback_state', 'status': 'playing'})
        replay = await self.run_driver(receiver, Readback())
        self.assertFalse(any(kind in ('prepare', 'delta') for kind, _ in replay))

    async def test_stale_generation_expiry_and_auth_loss_deny(self):
        for reason in ('generation', 'expired', 'auth', 'inactive'):
            with self.subTest(reason=reason):
                receiver = self.receiver()
                receiver.accept({'type': 'playback_changed', 'status': 'playing'})
                if reason == 'generation':receiver.epoch = 'new-generation'
                elif reason == 'expired':receiver.ticket.expires = time.monotonic()-1
                elif reason == 'auth':receiver.accept({'type': 'auth_state', 'logged_in': False, 'is_active': True})
                else:receiver.accept({'type': 'device_changed', 'is_active': False})
                events = await self.run_driver(receiver, Readback())
                self.assertFalse(any(kind in ('prepare', 'delta') for kind, _ in events))

    async def test_remote_delta_wrong_source_cannot_complete_admission(self):
        receiver = self.receiver()
        receiver.accept({'type': 'playback_changed', 'status': 'playing'})
        wrong = source_player();wrong['active_source'] = 'other://audio_source/'+PLAYER
        events = await self.run_driver(receiver, Readback(wrong))
        self.assertFalse(any(kind == 'delta' for kind, _ in events))


if __name__ == '__main__':
    unittest.main()
