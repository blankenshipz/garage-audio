"""Supported Soloist/MA observation in their private container network namespace.

No control commands except state queries. Events do not identify command origin:
deployment must restrict all control-capable clients, not infer human presence.
"""
import asyncio
import json
import os
from pathlib import Path
import stat
import uuid
import time
import hashlib
import threading
from collections import deque
from dataclasses import dataclass

import aiohttp
from .audio_container import request


class DecisionTrace:
    """Small scalar-only diagnostic ring; failures never change admission or observation."""
    EVENTS = {'receiver', 'fresh_delta', 'ticket_rejected', 'prepare', 'corroboration',
              'delta', 'ipc', 'exception', 'disconnect'}
    ENUMS = {'kind': {'auth_state', 'device_changed', 'volume_changed', 'playback_state',
                     'playback_changed', 'baseline', 'prepare', 'delta', 'heartbeat', 'lost'},
             'state': {'playing', 'paused', 'idle', 'buffering', 'unknown'},
             'reason': {'no_ticket', 'epoch', 'expired', 'state', 'inactive', 'auth',
                        'volume', 'stale_playback', 'revoked', 'replaced', 'rpc_error'},
              'error': {'PermissionError', 'RuntimeError', 'ValueError', 'KeyError',
                        'TimeoutError', 'ConnectionResetError', 'TypeError', 'AttributeError',
                        'JSONDecodeError', 'InvalidStateError', 'ClientConnectionError',
                        'ClientConnectionResetError', 'ClientPayloadError',
                        'ClientConnectorError', 'ServerDisconnectedError', 'other'},
             'origin': {'observe', 'drive', 'rpc', 'corroborate', 'accept', 'other'},
             'ipc_error': {'peer_uid', 'network_generation', 'schema', 'sequence',
                           'baseline', 'ticket', 'scalar_terminal', 'unavailable',
                           'device_ownership', 'device_mapping', 'device_identity', 'other'}}
    BOOLS = {'active', 'logged_in', 'volume_known', 'ticket_created', 'eligible', 'ok',
             'qualifies', 'token_bound', 'consumed'}

    def __init__(self, path):
        self.path = Path(path)
        self.rows = deque(maxlen=64)
        self.first_failure = None
        self.first_failure_context = []
        self.dropped = self.write_failures = 0
        self.previous = {}
        self.lock = threading.Lock()
        self.stop = threading.Event()
        self.version = self.attempted_version = self.contention_drops = 0
        self.thread = threading.Thread(target=self._sink, daemon=True)
        try:
            self.thread.start()
        except Exception:
            self.write_failures += 1

    def record(self, event, **fields):
        # One observer-loop producer; no disk I/O, queue, wait or sink-held I/O lock.
        try:
            self._record(event, fields)
        except Exception:
            self.write_failures += 1

    def _record(self, event, fields):
        if event not in self.EVENTS:
            return
        row = {'event': event}
        for key, value in fields.items():
            if key in self.BOOLS and type(value) is bool:
                row[key] = value
            elif key in self.ENUMS and isinstance(value, str) and value in self.ENUMS[key]:
                row[key] = value
            elif key == 'sequence' and type(value) is int and 0 <= value < 2**63:
                row[key] = value
            elif key == 'error_line' and type(value) is int and 0 < value <= 10000:
                row[key] = value
            elif key == 'epoch' and isinstance(value, str) and len(value) <= 64:
                row['epoch_fingerprint'] = hashlib.sha256(value.encode()).hexdigest()[:12]
            elif key == 'ticket' and isinstance(value, str) and len(value) <= 128:
                row['ticket_id'] = hashlib.sha256(value.encode()).hexdigest()[:12]
        if not self.lock.acquire(blocking=False):
            self.contention_drops += 1
            return
        try:
            self._append(event, row)
        finally:
            self.lock.release()

    def _append(self, event, row):
        # Repeated snapshots/heartbeats cannot exhaust capture while waiting for a human.
        comparable = {k:v for k,v in row.items() if k != 'sequence'}
        if event in ('receiver', 'ipc'):
            key = (event, row.get('kind'))
            if comparable == self.previous.get(key):
                return
            self.previous[key] = comparable
        row['observed_ns'] = time.monotonic_ns()
        if len(self.rows) == self.rows.maxlen:
            self.dropped += 1
        self.rows.append(row)
        if self.first_failure is None and (event in ('ticket_rejected', 'exception') or row.get('ok') is False):
            self.first_failure = row
            self.first_failure_context = list(self.rows)[-8:]
        self.version += 1

    def _sink(self):
        # Coalesce versions into one bounded snapshot. Producer never waits for this I/O.
        while not self.stop.wait(.01):
            try:
                with self.lock:
                    version = self.version
                    if version == self.attempted_version:
                        continue
                    snapshot = {'schema': 1, 'first_failure': self.first_failure,
                                'first_failure_context': list(self.first_failure_context),
                                'dropped': self.dropped+self.contention_drops,
                                'write_failures': self.write_failures, 'rows': list(self.rows)}
                try:
                    self._persist(snapshot)  # NEVER holds producer lock across sink I/O
                except Exception:
                    self.write_failures += 1
                self.attempted_version = version
            except Exception:
                self.write_failures += 1

    def _persist(self, snapshot):
        data = json.dumps(snapshot, separators=(',', ':')).encode()
        if len(data) > 32768:
            raise ValueError('bounded diagnostic')
        temporary = self.path.with_name(self.path.name+'.next')
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
        os.replace(temporary, self.path)

    def close(self, timeout=.1):
        try:
            self.stop.set()
            if self.thread.ident is not None:
                self.thread.join(timeout=min(.1, max(0, timeout)))
            return not self.thread.is_alive()  # stalled daemon cannot hold process exit
        except Exception:
            self.write_failures += 1
            return False

    def exception(self, error, **fields):
        # No exception text, traceback rendering, paths, locals or receiver payload.
        # Inspect at most16 frames; retain ONLY an allowlisted function and scalar line.
        try:
            origin, line = 'other', None
            tb = error.__traceback__
            for _ in range(16):
                if tb is None:
                    break
                code = tb.tb_frame.f_code
                if code.co_filename == __file__ and code.co_name in self.ENUMS['origin']:
                    origin, line = code.co_name, tb.tb_lineno
                tb = tb.tb_next
            name = type(error).__name__
            self.record('exception', error=name if name in self.ENUMS['error'] else 'other',
                        origin=origin, error_line=line, **fields)
        except Exception:
            self.write_failures += 1

    def ipc_failure(self, error, **fields):
        try:
            category = {'peer_not_admitted': 'peer_uid',
                        'current immutable network generation required': 'network_generation',
                        'exact observer fields required': 'schema',
                        'stale observer epoch/sequence': 'sequence',
                        'fresh baseline/current volume required': 'baseline',
                        'fresh observer baseline and current volume required': 'baseline',
                        'fresh one-use worker ticket required': 'ticket',
                        'scalar qualification terminal': 'scalar_terminal',
                        'scalar qualification terminal or launch revoked': 'scalar_terminal',
                        'audio IPC reply unavailable': 'unavailable'}.get(str(error), 'other')
            category = {
                'exclusive numeric UID1002/GID1003 mode0600 required': 'device_ownership',
                'verified UMC node missing from container; recreate identity-bound mapping': 'device_mapping',
                'exact mapped node identity/permissions required': 'device_mapping',
                'USB identity/capabilities changed; no remap or fallback': 'device_identity',
                'USB connection or node identity changed; revoke current generation': 'device_identity',
                'UMC card missing or ambiguous; no fallback': 'device_identity',
            }.get(str(error), category)
            self.record('ipc', ok=False, ipc_error=category,
                        error=type(error).__name__ if type(error).__name__ in self.ENUMS['error'] else 'other', **fields)
        except Exception:
            self.write_failures += 1


class MAReadback:
    def __init__(self, session, token_file, player):
        self.session, self.token_file, self.player = session, Path(token_file), player
        self.ws = None
        self.sequence = 0

    async def rpc(self, command, args):
        for attempt in range(2):
            try:
                self.sequence += 1
                key = str(self.sequence)
                await self.ws.send_json({'message_id': key, 'command': command, 'args': args})
                async with asyncio.timeout(1):
                    while True:
                        packet = await self.ws.receive()
                        if packet.type != aiohttp.WSMsgType.TEXT:
                            raise aiohttp.ServerDisconnectedError('MA observation disconnected')
                        value = json.loads(packet.data)
                        if value.get('message_id') == key:
                            if value.get('error_code') is not None:
                                raise RuntimeError('MA readback denied')
                            return value.get('result')
            except aiohttp.ClientConnectionError:
                # An authenticated socket may reset while the receiver is idle.
                # Reauthenticate the SAME scoped reference and retry only a read,
                # once. Auth denial, bad JSON/schema and cancellation still fail.
                # This never renews a receiver ticket; drive checks original expiry.
                if command != 'players/get' or attempt:
                    raise
                await self.ws.close()
                await self.connect()

    async def connect(self):
        info = self.token_file.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077 or info.st_size > 8192:
            raise PermissionError('private MA credential reference required')
        self.ws = await self.session.ws_connect('http://127.0.0.1:8095/ws')
        await self.rpc('auth', {'token': self.token_file.read_text().strip()})

    async def corroborate(self, provider):
        player = await self.rpc('players/get', {'player_id': self.player})
        # Supported direct AudioSource sessions live on the player, not its idle
        # ordinary queue. Derive identity ONLY from the server-pinned provider/player;
        # never adopt a reported URI or metadata as fresh-Play authority.
        source_uri = f'{provider}://audio_source/{self.player}'
        media = (player or {}).get('current_media') or {}
        return (player or {}).get('player_id') == self.player \
            and player.get('available') is True and player.get('playback_state') == 'playing' \
            and player.get('active_source') == source_uri \
            and media.get('media_type') == 'audio_source' and media.get('uri') == source_uri


@dataclass
class FreshPlay:
    epoch: str
    expires: float
    token: str | None = None


class ReceiverState:
    """Snapshots update metadata only; only a fresh playing delta can mint a ticket."""
    def __init__(self, epoch, trace=None):
        self.epoch = epoch
        self.trace = trace
        self.event_sequence = 0
        self.state, self.active, self.logged_in, self.volume = 'unknown', False, False, None
        self.ticket = None
        self.last_received = self.last_playback = time.monotonic()
        self.changed = asyncio.Event()

    def note(self, event, **fields):
        if self.trace is not None:
            try:
                self.trace.record(event, epoch=self.epoch, active=self.active, logged_in=self.logged_in,
                                  sequence=self.event_sequence, volume_known=self.volume is not None, **fields)
            except Exception:
                pass  # diagnostics must not change receiver authority

    def rejection(self, ticket):
        if ticket is None: return 'no_ticket'
        if self.ticket is not ticket: return 'replaced'
        if ticket.epoch != self.epoch: return 'epoch'
        if time.monotonic() >= ticket.expires: return 'expired'
        if self.state != 'playing': return 'state'
        if not self.active: return 'inactive'
        if not self.logged_in: return 'auth'
        if self.volume is None: return 'volume'
        if time.monotonic()-self.last_playback > 1.5: return 'stale_playback'
        return None

    def eligible(self, ticket):
        return self.ticket is ticket and ticket.epoch == self.epoch and time.monotonic() < ticket.expires \
            and self.state == 'playing' and self.active and self.logged_in and self.volume is not None \
            and time.monotonic()-self.last_playback <= 1.5

    def accept(self, value):
        kind = value.get('type')
        was_active = self.active
        if kind == 'auth_state':
            self.logged_in = value.get('logged_in') is True
            self.active = value.get('is_active') is True
        elif kind == 'device_changed':
            self.active = value.get('is_active') is True
        elif kind == 'volume_changed':
            self.volume = value.get('volume')
        elif kind in ('playback_state', 'playback_changed'):
            self.last_playback = time.monotonic()
            self.state = value.get('status', 'unknown')
            if 'volume' in value:
                self.volume = value['volume']
            if 'is_active' in value:
                self.active = value['is_active'] is True
            if self.state != 'playing':
                self.ticket = None
            elif kind == 'playback_changed' and self.logged_in:
                self.ticket = FreshPlay(self.epoch, time.monotonic()+1.5)
        else:
            return False
        # Soloist can report fresh Play before device activation, followed by an
        # unchanged inactive auth snapshot. That snapshot is not a revocation of
        # an already-active receiver. Keep only the original bounded fresh ticket;
        # it still cannot qualify until actual active/auth/MA/worker checks pass.
        # Real deactivation (including playback snapshots) and logout revoke it.
        if not self.logged_in or (was_active and not self.active):
            self.ticket = None
        self.event_sequence += 1
        self.last_received = time.monotonic()
        if self.state not in ('playing', 'paused', 'idle', 'buffering', 'unknown'):
            self.state, self.ticket = 'unknown', None
        if self.volume is not None and (type(self.volume) is not int or not 0 <= self.volume <= 100):
            self.volume, self.ticket = None, None
        self.note('receiver', kind=kind, state=self.state)
        if kind == 'playback_changed' and self.state == 'playing':
            self.note('fresh_delta', ticket_created=self.ticket is not None,
                      eligible=self.ticket is not None and self.eligible(self.ticket))
        self.changed.set()
        return True

    def disconnect(self):
        self.note('disconnect')
        self.ticket = None
        self.active = self.logged_in = False
        self.changed.set()


async def drive(state, ma, provider, emit, pause):
    """Wait for MA's deferred source claim, while receiver revocations remain live."""
    corroboration = None
    admitted = None
    observed_stop = None
    health = None
    last_ma_check = 0
    try:
        while True:
            state.changed.clear()
            if time.monotonic()-state.last_received > 1.5 or time.monotonic()-state.last_playback > 1.5:
                state.disconnect()
                await emit('lost')
                raise RuntimeError('fresh receiver observation unavailable')
            ticket = state.ticket
            reply = await emit('heartbeat')
            if reply.get('reason') == 'public_silence_only' and reply.get('control_generation') != observed_stop:
                observed_stop = reply.get('control_generation')
                state.ticket = None
                if state.logged_in:
                    await pause()  # supported silence-only pause; never play/seek/volume
            if admitted is not None and reply.get('pending_ticket') != admitted.token:
                state.note('ticket_rejected', reason='revoked')
                state.ticket = None  # public Stop removed the server-owned pending ticket
            if ticket is not state.ticket or ticket is None or not state.eligible(ticket):
                if ticket is not None:
                    state.note('ticket_rejected', reason=state.rejection(ticket) or 'replaced')
                if corroboration:
                    corroboration.cancel()
                    await asyncio.gather(corroboration, return_exceptions=True)
                    corroboration = None
                admitted = None
            elif admitted is not ticket:
                if health:
                    health.cancel()
                    await asyncio.gather(health, return_exceptions=True)
                    health = None
                prepared = await emit('prepare')
                state.note('prepare', ok=True, token_bound=prepared.get('pending_ticket') is not None,
                           ticket=prepared.get('pending_ticket'))
                if state.eligible(ticket):
                    ticket.token = prepared['pending_ticket']
                    admitted = ticket
            if admitted is not None and state.eligible(admitted):
                if corroboration is None:
                    corroboration = asyncio.create_task(ma.corroborate(provider))
                wake = asyncio.create_task(state.changed.wait())
                done, _ = await asyncio.wait([wake, corroboration], timeout=.05,
                                              return_when=asyncio.FIRST_COMPLETED)
                wake.cancel()
                await asyncio.gather(wake, return_exceptions=True)
                if corroboration in done:
                    qualifies = corroboration.result()
                    state.note('corroboration', qualifies=qualifies, eligible=state.eligible(admitted))
                    corroboration = None
                    if qualifies and state.eligible(admitted):
                        await emit('delta', admitted.token)
                        state.note('delta', ok=True, consumed=True,
                                   ticket=admitted.token)
                        state.ticket = None
                        admitted = None
                    # false readback retries within the SAME bounded fresh ticket, never renews it
            else:
                if reply.get('state') == 'playing' and state.state == 'playing' and state.active and state.logged_in:
                    if health is None and time.monotonic()-last_ma_check >= .5:
                        health = asyncio.create_task(ma.corroborate(provider))
                    if health and health.done():
                        qualifies = health.result()
                        health = None
                        last_ma_check = time.monotonic()
                        if not qualifies:
                            raise RuntimeError('active MA source/credential observation lost')
                elif health:
                    health.cancel()
                    await asyncio.gather(health, return_exceptions=True)
                    health = None
                try:
                    await asyncio.wait_for(state.changed.wait(), .1)
                except TimeoutError:
                    pass
    finally:
        if corroboration:
            corroboration.cancel()
            await asyncio.gather(corroboration, return_exceptions=True)
        if health:
            health.cancel()
            await asyncio.gather(health, return_exceptions=True)


async def observe():
    if os.environ.get('GARAGE_AUDIO_OBSERVER_ENABLED') != 'private-namespace-qualified':
        raise RuntimeError('production observer disabled: private namespace qualification required')
    from .audio_boot_wait import network_valid
    network_path=os.environ['GARAGE_AUDIO_NETWORK_LEASE']
    network_generation=os.environ['GARAGE_AUDIO_NETWORK_GENERATION']
    endpoint = Path(os.environ['GARAGE_SOLOIST_RUNTIME'])
    token = os.environ['GARAGE_MA_TOKEN_FILE']
    provider = os.environ['GARAGE_CONNECT_PROVIDER']
    player = os.environ.get('GARAGE_AUDIO_PLAYER', '02:00:00:00:21:01')
    sock = Path('/run/garage-audio/events/event.sock')
    trace = None
    try:
        trace = DecisionTrace('/tmp/garage-audio-observer-decisions.json')
    except Exception:
        pass  # diagnostic setup only; no authority operation is inside this guard
    while True:
        epoch, sequence = uuid.uuid4().hex, 0
        state = ReceiverState(epoch, trace)
        emit_lock = asyncio.Lock()
        async def emit(kind, ticket=None):
            nonlocal sequence
            if kind!='lost' and not network_valid(network_path,network_generation):
                raise PermissionError('observer network generation revoked')
            async with emit_lock:
                sequence += 1
                value = {'epoch': epoch, 'sequence': sequence, 'kind': kind, 'state': state.state,
                         'active': state.active, 'logged_in': state.logged_in, 'volume': state.volume}
                if ticket is not None:
                    value['ticket'] = ticket
                try:
                    reply = await asyncio.to_thread(request, sock, value)
                except Exception as error:
                    if trace is not None:
                        trace.ipc_failure(error, epoch=epoch, sequence=sequence, kind=kind)
                    raise
                if kind != 'heartbeat' and trace is not None:
                    trace.record('ipc', epoch=epoch, sequence=sequence, kind=kind, ok=True)
                return reply
        try:
            await emit('baseline')
            # Runtime files are hints; an isolated trusted namespace is the admission boundary.
            for name in ('ws.addr', 'ws.port'):
                info = (endpoint/name).lstat()
                if not stat.S_ISREG(info.st_mode) or info.st_size > 128:
                    raise PermissionError('invalid receiver endpoint')
            if (endpoint/'ws.addr').read_text().strip() != '127.0.0.1':
                raise PermissionError('receiver must bind private loopback')
            port = int((endpoint/'ws.port').read_text())
            if not 1 <= port <= 65535:
                raise PermissionError('invalid receiver port')
            async with aiohttp.ClientSession() as session:
                ma = MAReadback(session, token, player)
                await ma.connect()
                async with session.ws_connect(f'http://127.0.0.1:{port}/', heartbeat=1) as ws:
                    async def pause():
                        await ws.send_json({'type': 'command', 'command': 'pause'})
                    driver = asyncio.create_task(drive(state, ma, provider, emit, pause))
                    last_query = 0
                    try:
                        while True:
                            if not network_valid(network_path,network_generation):
                                raise PermissionError('observer network generation revoked')
                            if driver.done():
                                driver.result()
                            if asyncio.get_running_loop().time() - last_query >= .5:
                                await ws.send_json({'type': 'command', 'command': 'get_auth_state'})
                                if state.logged_in:
                                    await ws.send_json({'type': 'command', 'command': 'get_state'})
                                last_query = asyncio.get_running_loop().time()
                            try:
                                packet = await asyncio.wait_for(ws.receive(), .6)
                            except TimeoutError:
                                continue
                            if packet.type != aiohttp.WSMsgType.TEXT:
                                raise RuntimeError('receiver connection lost')
                            state.accept(json.loads(packet.data))
                    finally:
                        state.disconnect()
                        driver.cancel()
                        await asyncio.gather(driver, return_exceptions=True)
        except asyncio.CancelledError:
            try:
                await emit('lost')
            finally:
                if trace is not None:
                    trace.close()
            raise
        except Exception as error:
            if trace is not None:
                trace.exception(error, epoch=epoch)
            # Never log credentials, receiver payloads, paths, or provider metadata.
            try:
                await emit('lost')
            except Exception:
                pass
            await asyncio.sleep(1)


if __name__ == '__main__':
    asyncio.run(observe())
