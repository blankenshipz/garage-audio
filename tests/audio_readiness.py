"""FILE/metadata-only playback status fixtures; no hardware or real receiver."""
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from garage_audio import audio_public
from garage_audio.audio_container import Worker, WATCHDOG


class PublicReadinessTests(unittest.TestCase):
    standby = dict(state='off', device_mode='standby', device_open=False,
                   physical_verified=False, owned_children=[], standby=True,
                   qualification_terminal=True, scalar_readers_ready=False)

    def test_standby_status_stop_and_pause_allow_safe_projection_only(self):
        import struct
        class SocketFixture:
            def __init__(self, chunks): self.chunks = chunks
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def settimeout(self, value): pass
            def connect(self, path): pass
            def sendall(self, data): pass
            def recv(self, size): return self.chunks.pop(0)
            def getsockopt(self, *args): return struct.pack('3i', 1, 1002, 1003)
        for operation in ('audio.status', 'audio.stop', 'audio.pause'):
            value = dict(self.standby, qualification_attempt='private-not-forwarded')
            frame = json.dumps({'ok': True, 'result': value}).encode()+b'\n'
            with patch.object(audio_public.socket, 'socket', return_value=SocketFixture([frame])):
                reply = audio_public.request(operation)
            self.assertEqual(reply['device_mode'], 'standby')
            self.assertTrue(reply['standby'])
            self.assertNotIn('qualification_attempt', reply)
        for update in ({'state':'playing'}, {'device_open':True}, {'owned_children':[123]},
                       {'standby':False}, {'qualification_terminal':False},
                       {'scalar_readers_ready':True}, {'physical_verified':True}):
            with self.subTest(update=update), self.assertRaises(ValueError):
                audio_public._status(dict(self.standby, **update))
        self.assertNotIn('audio.play', audio_public.SCHEMAS)

    def test_worker_readiness_expires_without_extending_playback_authority(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(threading.Thread, 'start'):
            worker = Worker(Path(tmp)/'ipc', Path(tmp)/'evidence')
            self.addCleanup(worker.closed.set)
            self.assertEqual(worker.status()['readiness'], 'observation_unavailable')
            event = dict(epoch='fixture', sequence=0, kind='baseline', state='idle',
                         active=False, logged_in=False, volume=20)
            self.assertEqual(worker.event(event)['readiness'], 'receiver_unavailable')
            event.update(sequence=1, kind='heartbeat', active=True, logged_in=True)
            self.assertEqual(worker.event(event)['readiness'], 'waiting_for_play')
            self.assertEqual(worker.last_observation, 0)
            event.update(sequence=2, kind='prepare', state='playing')
            self.assertEqual(worker.event(event)['readiness'], 'awaiting_source')
            self.assertEqual(worker.children, [])
            self.assertEqual(worker.last_observation, 0)
            for state in ('playing', 'paused'):
                worker.state = state  # metadata only; no child/device launch
                self.assertEqual(worker.status()['readiness'], state)
            worker.state = 'off'
            with patch.object(worker, 'network_valid', return_value=False):
                self.assertEqual(worker.status()['readiness'], 'network_unavailable')
            with patch('garage_audio.audio_container.time.monotonic', return_value=worker.last_event_received+WATCHDOG+.1):
                value = audio_public._status(worker.status())
                self.assertEqual(value['readiness'], 'observation_unavailable')
                self.assertFalse(value['observer_available'])
                self.assertFalse(value['receiver_available'])
            event.update(sequence=3, kind='lost')
            self.assertEqual(worker.event(event)['readiness'], 'observation_unavailable')
            with self.assertRaises(ValueError):
                audio_public._status(dict(self.standby, readiness='raw private payload'))

    def test_rejected_and_slow_events_cannot_rejuvenate_readiness(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(threading.Thread,'start'):
            worker=Worker(Path(tmp)/'ipc',Path(tmp)/'evidence');self.addCleanup(worker.closed.set)
            now=[10.0]
            with patch('garage_audio.audio_container.time.monotonic',side_effect=lambda:now[0]):
                event=dict(epoch='fixture',sequence=0,kind='baseline',state='playing',active=True,logged_in=True,volume=20)
                worker.event(event);self.assertEqual(worker.last_event_received,10)
                now[0]+=WATCHDOG+1
                event.update(sequence=1,kind='delta',ticket='invalid')
                with self.assertRaises(ValueError):worker.event(event)
                self.assertEqual(worker.last_event_received,10)
                self.assertEqual(worker.status()['readiness'],'observation_unavailable')
                del event['ticket'];event.update(sequence=2,kind='prepare')
                received=now[0]
                def slow_check():now[0]+=WATCHDOG+1;return True
                with patch.object(worker,'scalar_valid_locked',side_effect=slow_check):
                    value=worker.event(event)
                self.assertEqual(worker.last_event_received,received)
                self.assertEqual(value['readiness'],'observation_unavailable')
                self.assertEqual(worker.last_observation,0)
                self.assertEqual(worker.children,[])

