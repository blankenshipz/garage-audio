"""Private in-process Soloist transition authority; never phone identity proof.

The pinned receiver adapter, not an HTTP/JSON caller, constructs this object.
It is useful only behind the kernel control-port guard, immutable MA process
admission and an observed live daemon. It intentionally fails closed on missing
fields, local commands, snapshots, recovery and connection teardown.
"""
from dataclasses import dataclass
import time

TARGET = '02:00:00:00:21:01'
PLAYING = 'playing'
NONPLAYING = frozenset(('paused', 'stopped', 'idle'))


@dataclass(frozen=True)
class ReceiverBinding:
    provider: str
    daemon: tuple  # native-observed PID/starttime/boot/image binding, not just PID
    epoch: int
    target: str = TARGET
    port: int = 0  # zero is isolated fixture only; root bridge always supplies reserved port


class Handoff:
    """Identity capability, not serialized actor/presence/source claims."""
    __slots__ = ('binding', 'sequence', 'generation', '_owner', '_used')

    def __init__(self, owner, binding, sequence, generation):
        self.binding = binding
        self.sequence = sequence
        self.generation = generation
        self._owner = owner
        self._used = False


class ReceiverAuthority:
    def __init__(self, *, clock=time.monotonic):
        self.clock = clock
        self.binding = None
        self.sequence = 0
        self.generation = 0
        self._token = None
        self._baseline = None
        self._buffering_baseline = None
        self._pending_remote_play = None
        self._logged_in = False
        self._active = False
        self._source_selected = False
        self._local_poison = False
        self._closed = True
        self.state = 'waiting_for_spotify_play'
        self.last = 0.0
        self.pause_deadline = None

    def attach(self, binding, *, port_protected, daemon_alive):
        self.revoke()
        if not isinstance(binding, ReceiverBinding) or binding.target != TARGET:
            raise PermissionError('fixed installed receiver target required')
        if not binding.provider or not binding.daemon or type(binding.epoch) is not int or binding.epoch < 1:
            raise PermissionError('observed provider, daemon and fresh WebSocket epoch required')
        if not port_protected or not daemon_alive:
            raise PermissionError('protected control port and actual live daemon required')
        self.binding = binding
        self._closed = False
        self.sequence = 0
        self._baseline = None
        self._local_poison = False
        self.state = 'waiting_for_spotify_play'

    def revoke(self):
        self.generation += 1
        self._token = None
        self._closed = True
        self._source_selected = False
        self._baseline = None
        self._buffering_baseline = None
        self._pending_remote_play = None
        self._active = False
        self._logged_in = False
        self.pause_deadline = None
        self.state = 'stopped'

    def end_window(self,reopen=False):
        """Close native scope, not a still-live receiver's future Play eligibility."""
        self.generation += 1
        self._token = None
        self._buffering_baseline = None
        self._pending_remote_play = None
        self._source_selected = False
        self.pause_deadline = None
        self.state = 'stopped'
        if reopen:self._closed=False

    def local_command(self, command, *, handoff=None):
        """Activation/Play/Resume can never create fresh sound authority.

        Only the single exact consumed queue handoff may activate the already
        authorized receiver stream. Generic API calls are denied BEFORE sending.
        Direct wrappers must cover both _send_command and ws.send_json.
        """
        safe=('pause','deactivate','volume','set_volume','get_queue','get_state','get_auth_state','status')
        if command in safe:
            if command in ('pause','deactivate'):self._pending_remote_play=None
            return
        if command not in ('play','resume','activate') and self._token is not None and self._token._used and self.live():
            return  # queue/seek/options can continue an existing genuine scope
        if (handoff is self._token and handoff is not None and handoff._used
                and handoff._owner is self and self.live()):
            return
        self._local_poison = True
        self.end_window();self._baseline=None
        raise PermissionError('local MA activation is not Spotify receiver Play authority')

    def event(self, binding, sequence, kind, *, status=None, logged_in=None, active=None):
        """Accept fields extracted from actual callback, never caller JSON claims."""
        if self._closed or binding != self.binding:
            return None
        if type(sequence) is not int or sequence <= self.sequence:
            self.revoke()
            raise PermissionError('stale/replayed receiver event')
        self.sequence = sequence
        self.last = self.clock()
        pending=self._pending_remote_play
        if pending is not None and (pending[0]!=binding or self.clock()>=pending[2]):
            self._pending_remote_play=None
        if kind == 'auth_state':
            if type(logged_in) is not bool or type(active) is not bool:
                self.revoke()
                return None
            self._logged_in = logged_in
            self._active = active
            if not active:
                self._pending_remote_play=None;self._baseline='stopped'
            if not logged_in:
                self.end_window();self._active=False;self._baseline='stopped'
            return None
        if kind == 'device_changed':
            if type(active) is not bool or not active:
                self.end_window();self._active=False;self._baseline='stopped'
            else:
                self._active = True
                pending=self._pending_remote_play
                self._pending_remote_play=None
                if (pending is not None and pending[0]==binding and sequence>pending[1]
                        and self.clock()<pending[2] and self._logged_in
                        and not self._local_poison and self._token is None
                        and self._baseline==PLAYING):
                    self.generation+=1
                    self._token=Handoff(self,binding,sequence,self.generation)
                    self.state='preparing'
                    return self._token
            return None
        if kind not in ('playback_state', 'playback_changed'):
            return None
        if active is not None:
            if type(active) is not bool or not active:
                self.end_window();self._active=False;self._baseline='stopped'
                return None
            self._active = active
        if status=='buffering':
            self._pending_remote_play=None
            # Supported engine state, not a Play grant. Preserve a genuinely
            # nonplaying first-Play baseline, but never arm from a cold buffered
            # snapshot. An already-owned stream mutes and gets only10sec to
            # recover; longer network recovery needs a fresh user Pause/Play.
            if self._token is None:
                if kind=='playback_changed':
                    # Soloist emits buffering DELTA, buffering SNAPSHOT, then
                    # playing DELTA on genuine first Play. Preserve only the
                    # already-established nonplaying baseline of this delta's
                    # same authenticated epoch; the snapshot creates no grant.
                    self._buffering_baseline=self._baseline if self._baseline in NONPLAYING else None
                elif self._buffering_baseline is None:
                    self._baseline=None # cold snapshot still cannot arm
            else:
                self.state='buffering';self._baseline='paused'
                if self.pause_deadline is None:self.pause_deadline=self.clock()+10
            return None
        if status not in NONPLAYING | {PLAYING}:
            self.revoke()
            return None
        if kind == 'playback_state':
            # Snapshot establishes only an initial NONPLAYING baseline. A
            # snapshot may neither arm nor resume an existing native window.
            if self._token is None:
                if status!=PLAYING:self._pending_remote_play=None
                # A snapshot can preserve an existing real delta, never create
                # or complete it, and never refresh its two-second deadline.
                self._baseline = status if status in NONPLAYING or self._pending_remote_play is not None else None
            elif status != PLAYING:
                self._baseline=status
                self.state = 'paused' if status == 'paused' else 'stopped'
                if status != 'paused':self.end_window()
            return None
        previous = self._baseline
        self._buffering_baseline = None
        self._baseline = status
        if status in ('stopped','idle'):
            self._pending_remote_play=None
            # End sound immediately but keep this authenticated live socket
            # eligible for the next fresh remote nonplaying -> playing delta.
            self.generation += 1
            self._token = None
            self._source_selected = False
            self.pause_deadline = None
            self.state = 'stopped'
            self._local_poison=False
            return None
        if status == 'paused':
            self._pending_remote_play=None
            self._local_poison=False
            if self._token is not None:
                was_paused=self.state=='paused'
                self.state = 'paused'
                if not was_paused or self.pause_deadline is None:self.pause_deadline = self.clock() + 600
            return None
        if not self._logged_in or self._local_poison:
            return None
        if not self._active:
            # Reviewed late selection ordering: remember only this real delta;
            # no token/native prepare until an actual newer device-active delta.
            if (active is None and previous in NONPLAYING and self._token is None
                    and self._pending_remote_play is None):
                self._pending_remote_play=(binding,sequence,self.clock()+2)
            return None
        if previous not in NONPLAYING:
            return None  # no grant from repeated PLAYING/reconnect recovery
        if self._token is not None and not self._token._used and self.state=='buffering':
            # A decoder buffering→playing acknowledgement during the ORIGINAL
            # genuine Play's zero preparation is not a second user grant. Keep
            # that same one-use/epoch/sequence token; replacing it invalidates
            # the in-flight native handoff and closes the stream on every start.
            self.state='preparing';self.pause_deadline=None
            return None
        if self._token is not None and self._source_selected:
            self.state = 'playing'
            self.pause_deadline = None
            return None  # fresh remote Resume; same source, not another queue
        self.generation += 1
        self._token = Handoff(self, binding, sequence, self.generation)
        self.state = 'preparing'
        return self._token

    def consume(self, token, *, target, graph_generation):
        if (token is None or token is not self._token or token._owner is not self
                or token._used or token.binding != self.binding
                or token.generation != self.generation or target != TARGET
                or self._baseline != PLAYING or not graph_generation or not self.live()):
            raise PermissionError('fresh one-use receiver/current zero-graph handoff required')
        token._used = True
        self._source_selected = True
        self.graph_generation = graph_generation
        self.state = 'playing'

    def live(self, *, daemon_alive=True, ws_connected=True, source_current=True):
        now = self.clock()
        return (not self._closed and self._logged_in and self._active
                and not self._local_poison and daemon_alive and ws_connected
                and source_current and now - self.last < 10
                and (self.pause_deadline is None or now < self.pause_deadline))

    def renew(self, *, daemon_alive, ws_connected, source_current, graph_generation):
        # The adapter samples actual live objects once/sec; generic MA state
        # cannot call this to mint authority. A missed 10s bound is irreversible.
        if (not self.live(daemon_alive=daemon_alive, ws_connected=ws_connected,
                          source_current=source_current)
                or (not self._source_selected and self.state not in ('paused','buffering')) or graph_generation != self.graph_generation):
            self.revoke()
            raise PermissionError('receiver/stream/graph liveness lost')
        self.last = self.clock()
        return self.state
