"""Private container audio lifecycle. FILE only until target identity qualification.

Public socket accepts silence/status only. Event socket is a trusted observer boundary,
not proof of who pressed Play. Neither socket accepts paths, commands or gain policy.
"""
import argparse
import json
import os
from pathlib import Path
import signal
import socket
import struct
import subprocess
import threading
import time
import uuid
import sys
import stat

RATE = 48000
WATCHDOG = 3.0
MAX_PLAY = 4 * 3600
MAX_PAUSE = 600
TICKET_SECONDS = 1.5


def line_graph(output):
    return {'title': 'Garage fixed fullrange OUT3/4 -6dB FILE',
            'devices': {'samplerate': RATE, 'chunksize': 1024,
                        'capture': {'type': 'Stdin', 'channels': 2, 'format': 'F32_LE'},
                        'playback': {'type': 'File', 'channels': 12,
                                     'filename': str(output), 'format': 'F32_LE'}},
            'filters': {'headroom': {'type': 'Gain', 'parameters': {'gain': -6.0}}},
            'mixers': {'route': {'channels': {'in': 2, 'out': 12}, 'mapping': [
                {'dest': 2, 'sources': [{'channel': 0, 'gain': 0}]},
                {'dest': 3, 'sources': [{'channel': 1, 'gain': 0}]}]}},
            'pipeline': [{'type': 'Filter', 'channels': [0, 1], 'names': ['headroom']},
                         {'type': 'Mixer', 'name': 'route'}]}


def request(path, value):
    with socket.socket(socket.AF_UNIX) as client:
        client.settimeout(4)
        client.connect(str(path))
        client.sendall(json.dumps(value).encode() + b'\n')
        data = b''
        while b'\n' not in data:
            part = client.recv(4096)
            if not part or len(data) > 16384:
                raise RuntimeError('audio IPC reply unavailable')
            data += part
        result = json.loads(data)
        if not result.get('ok'):
            raise RuntimeError(result.get('error', 'audio operation denied'))
        return result['result']


class Worker:
    def __init__(self, root, evidence, mode='file', network_lease=None, network_generation=None,
                 qualification_policy=None, qualification_store=None):
        if mode not in ('file', 'hardware', 'scalar', 'standby'):
            raise ValueError('explicit file or hardware mode required')
        self.mode = mode
        self.network_lease = Path(network_lease) if network_lease else None
        self.network_generation = network_generation
        if bool(self.network_lease) != bool(network_generation) or (mode in ('hardware', 'scalar', 'standby') and not self.network_lease):
            raise ValueError('hardware requires explicit generation-bound network admission')
        self.device_lease = None
        self.last_device_check = 0
        self.clipped_start = None
        if (qualification_policy or qualification_store) and mode not in ('scalar', 'standby'):
            raise PermissionError('qualification cannot be bypassed by changing audio mode')
        if mode == 'hardware':
            from .audio_device import load_policy
            load_policy('/device-policy.json')
        if mode in ('scalar', 'standby') and any(Path(p).exists() for p in ('/dev/snd', '/host-snd', '/host-asound', '/device-policy.json', '/device-lock')):
            raise PermissionError('scalar mode forbids sound device/policy mounts')
        self.root, self.evidence = Path(root), Path(evidence)
        self.evidence.mkdir(parents=True, exist_ok=True)
        self.generation = uuid.uuid4().hex
        self.epoch = None
        self.sequence = -1
        self.baseline = False
        self.active = False
        self.logged_in = False
        self.volume = None
        self.state = 'off'
        self.reason = 'startup_no_authority'
        self.last_event_received = None  # readback only; never renews playback authority
        self.last_observation = 0
        self.deadline = 0
        self.pause_deadline = 0
        self.children = []
        self.loaded_configuration = None
        self.children_lock = threading.Lock()
        self.stop_generation = 0
        self.pending_ticket = None
        self.segment = 0
        self.closed = threading.Event()
        self.scalar = None
        self.scalar_started = None
        self.scalar_terminal = False
        self.qualification = None
        if qualification_policy or qualification_store:
            if mode not in ('scalar', 'standby') or not qualification_policy or not qualification_store:
                raise PermissionError('qualification cannot be bypassed by changing audio mode')
            from .audio_qualification import Qualification
            self.qualification = Qualification(qualification_policy, qualification_store)
            saved = self.qualification.read()
            self.scalar_terminal = saved['terminal']
            self.segment, self.scalar_started = saved['segments'], saved['started']
        if mode == 'standby' and (self.qualification is None or not self.scalar_terminal):
            raise PermissionError('quiet standby requires the consumed qualification ledger')
        if mode == 'scalar':
            if self.qualification is None:
                raise PermissionError('explicit durable root-issued qualification required')
            from .audio_scalar import ScalarBank
            self.scalar = ScalarBank(self.evidence, self.generation)
        # Independent guardian does not acquire the command lock. Durable terminal
        # persistence runs AFTER audio halt and outside the state lock.
        self.command_lock = threading.Lock()
        from .audio_config import Configurations
        self.configurations = Configurations(self)
        self.guardian = threading.Thread(target=self.guard, daemon=True)
        self.guardian.start()
        if mode == 'hardware':
            threading.Thread(target=self.monitor_device, daemon=True).start()

    def halt(self, reason, paused=False):
        with self.children_lock:
            self.stop_generation += 1
            self.pending_ticket = None
            self.loaded_configuration = None
            self.state = 'paused' if paused else 'off'
            self.reason = reason
            children, self.children = self.children, []
            lease, self.device_lease = self.device_lease, None
            if not paused:
                self.deadline = 0
            else:
                self.pause_deadline = time.monotonic() + MAX_PAUSE
        for child in children:
            if child.poll() is None:
                try:
                    child.kill()
                except ProcessLookupError:
                    pass
        for child in children:
            child.wait(timeout=2)
        if lease:
            lease.close()  # after owned DSP exit; child also inherits the exclusive lock

    def guard(self):
        while not self.closed.wait(.05):
            now = time.monotonic()
            with self.children_lock:
                terminal = self.scalar and not self.scalar_valid_locked()
            if terminal:
                self.halt('scalar_reader_loss_or_qualification_expired')
                try:self.qualification.consume()
                except Exception:pass  # record failure cannot reopen the terminal gate
                self.scalar.closed.set()  # final descriptor cleanup has ONE owner: run()
                # Close AUDIO, not the receiver/namespace or public/event server.
                # Stay alive as silent terminal standby until explicit operator stop.
                while not self.closed.wait(.05):
                    if not self.network_valid():
                        self.baseline = False
                break
            if not self.network_valid():
                if self.state != 'off' or self.baseline or self.children:
                    self.halt('network_authority_lost')
                self.baseline = False
                if self.network_lease:
                    ack = {'generation': self.network_generation, 'state': self.state,
                           'owned_children': [c.pid for c in self.children if c.poll() is None],
                           'observed': time.monotonic(), 'physical_verified': False}
                    temporary = self.evidence/'network-closed.next'
                    temporary.write_text(json.dumps(ack))
                    os.replace(temporary, self.evidence/'network-closed.json')
            if self.device_lease is not None and now-self.last_device_check > 1:
                self.halt('device_monitor_lost')
            if self.state in ('playing', 'paused') and (
                    now - self.last_observation > WATCHDOG or
                    now >= self.deadline or
                    (self.state == 'paused' and now >= self.pause_deadline)):
                self.halt('watchdog_or_deadline')
            if self.state == 'playing' and any(c.poll() is not None for c in self.children):
                self.halt('owned_child_exited')

    def start(self):
        if self.mode == 'standby':
            raise PermissionError('standby has no audio authority')
        scalar_fd = None
        try:
            if self.scalar:
                self.check_scalar()
                scalar_fd = self.scalar.writer(self.segment+1)
            self.start_owned(scalar_fd)
        except BaseException:
            if self.scalar:
                self.halt('scalar_start_failed_or_terminal')
            raise
        finally:
            if scalar_fd is not None:
                os.close(scalar_fd)

    def start_owned(self, scalar_fd):
        if self.mode == 'standby':
            raise PermissionError('standby has no audio authority')
        if not self.network_valid():
            raise RuntimeError('network generation unavailable')
        with self.children_lock:
            if not self.scalar_valid_locked():
                raise RuntimeError('scalar qualification terminal')
            started_generation = self.stop_generation
            if self.scalar:
                if self.segment >= 8:
                    self.scalar.failed.set()
                    self.scalar_valid_locked()
                    raise RuntimeError('scalar segments exhausted')
                if self.scalar_started is None:
                    # Conservative temporary deadline while durable admission runs.
                    # No fsync/file lock may hold up the audio guardian's state lock.
                    self.scalar_started = time.monotonic()
            self.segment += 1
        if self.scalar:
            saved = self.qualification.admit_segment()  # fsync BEFORE launch, OUTSIDE state lock
            with self.children_lock:
                self.scalar_started = saved['started']
            self.check_scalar(started_generation)
        output = f'/proc/self/fd/{scalar_fd}' if self.scalar else self.evidence / f'{self.generation}-{self.segment}.f32'
        config = self.evidence / f'{self.generation}-{self.segment}.yaml'
        graph = line_graph(output)
        loaded_configuration = None
        if self.mode in ('file', 'hardware'):
            from .audio_config import compile_graph
            with self.configurations.lock:
                graph = compile_graph(self.configurations.state['configuration'], output)
                loaded_configuration = dict(self.configurations.state['configuration'])
        if self.mode == 'hardware':
            from .audio_device import DeviceLease, hardware_graph
            # Reject uncommissioned routes before acquiring or opening a device.
            graph = hardware_graph(graph, 'hw:CARD=UMC1820,DEV=0')
            lease = DeviceLease()
            with self.children_lock:
                if self.stop_generation != started_generation:
                    lease.close()
                    raise RuntimeError('device launch revoked')
                self.device_lease = lease
                self.last_device_check = time.monotonic()
            lease.verify()
        config.write_text(json.dumps(graph, allow_nan=False))  # JSON is valid YAML
        child_env = dict(os.environ, GARAGE_AUDIO_PARENT_PID=str(os.getpid()),
                         LD_LIBRARY_PATH='/opt/garage-audio/dsp/lib')
        capture_log = open(self.evidence / f'{self.generation}-{self.segment}.capture.stderr', 'ab', buffering=0)
        try:
            self.check_scalar(started_generation)
            capture = subprocess.Popen([sys.executable, '-m', 'garage_audio.audio_child', 'capture'],
                stdout=subprocess.PIPE, stderr=capture_log, env=child_env)
        finally:
            capture_log.close()
        def adopt(child):
            with self.children_lock:
                if self.stop_generation == started_generation and self.scalar_valid_locked():
                    self.children.append(child)
                    return
            child.kill()
            child.wait(timeout=2)
            if child.stdout:
                child.stdout.close()
            raise RuntimeError('child launch revoked')
        adopt(capture)
        try:
            source = capture.stdout
            if self.scalar:
                meter_log = open(self.scalar.root/f'{self.segment}.input.stderr', 'ab', buffering=0)
                try:
                    self.check_scalar(started_generation)
                    meter = subprocess.Popen([sys.executable, '-m', 'garage_audio.audio_child',
                        'scalar-input', str(self.scalar.root/f'{self.segment}.input.jsonl')],
                        stdin=source, stdout=subprocess.PIPE, stderr=meter_log, env=child_env)
                finally:
                    meter_log.close()
                source.close()
                adopt(meter)
                source = meter.stdout
            log = open(self.evidence / f'{self.generation}-{self.segment}.stderr', 'ab', buffering=0)
            try:
                options = [] if self.mode != 'hardware' else ['-a', '127.0.0.1', '-p', '1234', '--gain=-90', '--mute']
                lease_fd = (scalar_fd,) if self.scalar else (() if self.mode == 'file' else (lease.fd,))
                self.check_scalar(started_generation)
                dsp = subprocess.Popen([sys.executable, '-m', 'garage_audio.audio_child', 'dsp', *options, str(config)],
                    stdin=source, stdout=subprocess.DEVNULL, stderr=log,
                    env=child_env, pass_fds=lease_fd)
            finally:
                log.close()
            source.close()
            adopt(dsp)
            if self.mode == 'hardware':
                self.open_muted_device(lease, started_generation)
        except BaseException:
            self.halt('child_start_failed')
            raise
        with self.children_lock:
            if self.stop_generation != started_generation or not self.scalar_valid_locked() or time.monotonic()-self.last_observation > WATCHDOG or not self.network_valid():
                stale = True
            else:
                stale = False
                self.state, self.reason = 'playing', 'fresh_trusted_receiver_delta'
                self.loaded_configuration = loaded_configuration
                self.deadline = self.deadline or time.monotonic() + MAX_PLAY
        if stale:
            self.halt('child_launch_observation_expired')
            raise RuntimeError('child launch observation expired')

    def status(self):
        configuration = self.loaded_configuration or self.configurations.state['configuration']
        outputs = [configuration['left'], configuration['right']]
        if configuration['sub'] is not None:
            outputs.append(configuration['sub'])
        now = time.monotonic()
        observed = self.baseline and self.last_event_received is not None and 0 <= now-self.last_event_received <= WATCHDOG
        receiver = observed and self.active and self.logged_in
        network = self.network_valid()
        pending = self.pending_ticket
        if self.mode == 'standby' or self.scalar_terminal:
            readiness = 'standby'
        elif not network:
            readiness = 'network_unavailable'
        elif not observed:
            readiness = 'observation_unavailable'
        elif not receiver:
            readiness = 'receiver_unavailable'
        elif self.state in ('playing', 'paused'):
            readiness = self.state
        elif pending and now < pending[3]:
            readiness = 'awaiting_source'
        else:
            readiness = 'waiting_for_play'
        return {'state': self.state, 'reason': self.reason, 'generation': self.generation,
                'observer_available': bool(observed), 'receiver_available': bool(receiver),
                'readiness': readiness,
                'device_mode': self.mode, 'device_open': self.device_lease is not None,
                'owned_children': [c.pid for c in self.children if c.poll() is None],
                'segment': self.segment, 'receiver_volume': self.volume,
                'headroom_db': configuration['headroom_db'], 'outputs': outputs, 'unused_outputs_zero': True,
                'watchdog_seconds': WATCHDOG, 'deadline_remaining': max(0, self.deadline-time.monotonic()),
                'authority_persisted': False, 'physical_verified': False,
                'network_generation': self.network_generation, 'network_admitted': network,
                'scalar_readers_ready': bool(self.scalar and self.scalar.healthy()),
                'scalar_segments_remaining': max(0, 8-self.segment) if self.scalar else None,
                'standby': self.mode == 'standby' or self.scalar_terminal,
                'qualification_attempt': self.qualification.attempt if self.qualification else None,
                'qualification_terminal': self.scalar_terminal,
                'qualification_seconds_remaining': max(0, 300-(time.monotonic()-self.scalar_started))
                    if self.scalar_started is not None else None}

    def scalar_valid_locked(self):
        """Latch before halt; every in-flight launch/adoption observes the SAME state lock."""
        if not self.scalar:
            return True  # No new deadline/terminal policy for ordinary hardware/FILE.
        if self.scalar_terminal or self.closed.is_set() or not self.scalar.healthy() or (
                self.scalar_started is not None and time.monotonic()-self.scalar_started >= 300):
            self.scalar_terminal = True
            self.pending_ticket = None
            self.baseline = False
            return False
        return True

    def check_scalar(self, generation=None):
        with self.children_lock:
            valid = self.scalar_valid_locked() and (generation is None or self.stop_generation == generation)
        if not valid:
            self.halt('scalar_launch_revoked')
            raise RuntimeError('scalar qualification terminal or launch revoked')

    def network_valid(self):
        if self.network_lease is None:
            return self.mode == 'file'  # explicit legacy isolated FILE fixture only
        try:
            if (self.network_lease.parent/'revoked.json').exists():
                return False
            info = self.network_lease.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022 or info.st_size > 4096:
                return False
            value = json.loads(self.network_lease.read_text())
            now = time.monotonic()
            return value['generation'] == self.network_generation and value['healthy'] is True \
                and 0 <= now-value['tick'] <= 2 and now < value['expires']
        except (OSError, ValueError, KeyError, TypeError):
            return False

    def open_muted_device(self, lease, started_generation):
        from camilladsp import CamillaClient
        for _ in range(10):
            client = CamillaClient('127.0.0.1', 1234)
            try:
                client.connect()
                lease.verify()
                if self.device_lease is not lease or self.stop_generation != started_generation:
                    raise RuntimeError('muted device preparation revoked')
                if not self.network_valid():
                    raise RuntimeError('network authority expired during muted preparation')
                if time.monotonic()-self.last_observation > WATCHDOG:
                    raise RuntimeError('fresh receiver observation expired during muted preparation')
                if not client.volume.main_mute() or client.volume.main_volume() != -90:
                    raise RuntimeError('hardware startup mute/-90 not verified')
                self.clipped_start = client.status.clipped_samples()
                # Fixed unity master,400ms ramp; no source percent mapping or volume restoration.
                client.volume.set_main_mute(False)
                client.volume.set_main_volume(0)
                if client.volume.main_mute() or client.volume.main_volume() > 0:
                    raise RuntimeError('hardware fixed ceiling readback failed')
                self.last_device_check = time.monotonic()
                return
            except OSError:
                time.sleep(.05)
            finally:
                client.disconnect()
        raise RuntimeError('private muted DSP startup unavailable')

    def monitor_device(self):
        from camilladsp import CamillaClient
        while not self.closed.wait(.1):
            lease = self.device_lease
            if lease is None:
                continue
            client = CamillaClient('127.0.0.1', 1234)
            try:
                lease.verify()
                if self.state != 'playing':
                    if self.device_lease is lease:
                        self.last_device_check = time.monotonic()
                    continue
                client.connect()
                clipped = client.status.clipped_samples()
                if client.volume.main_volume() > 0 or (self.clipped_start is not None and clipped > self.clipped_start):
                    raise RuntimeError('fixed ceiling or clipped sample guard violated')
                if self.device_lease is lease:
                    self.last_device_check = time.monotonic()
            except Exception:
                if self.device_lease is lease:
                    self.halt('device_identity_or_dsp_guard_lost')
            finally:
                client.disconnect()

    def event(self, value):
        self.check_scalar()
        if not self.network_valid():
            self.halt('network_authority_lost')
            self.baseline = False
            raise ValueError('current immutable network generation required')
        if set(value) not in ({'epoch', 'sequence', 'kind', 'state', 'active', 'logged_in', 'volume'},
                             {'epoch', 'sequence', 'kind', 'state', 'active', 'logged_in', 'volume', 'ticket'}):
            raise ValueError('exact observer fields required')
        epoch, sequence, kind = value['epoch'], value['sequence'], value['kind']
        if not isinstance(epoch, str) or not 1 <= len(epoch) <= 64 or type(sequence) is not int:
            raise ValueError('invalid epoch/sequence')
        if kind not in ('baseline', 'prepare', 'delta', 'heartbeat', 'lost'):
            raise ValueError('invalid event kind')
        if value['state'] not in ('playing', 'paused', 'idle', 'buffering', 'unknown'):
            raise ValueError('unknown receiver state')
        if type(value['active']) is not bool or type(value['logged_in']) is not bool:
            raise ValueError('explicit auth/device state required')
        volume = value['volume']
        if volume is not None and (type(volume) is not int or not 0 <= volume <= 100):
            raise ValueError('invalid receiver volume')
        if self.mode == 'standby' and kind in ('prepare', 'delta'):
            raise PermissionError('standby has no audio authority')
        if kind == 'baseline':
            self.halt('snapshot_not_authority')
            self.epoch, self.sequence, self.baseline = epoch, sequence, True
        elif epoch != self.epoch or sequence <= self.sequence:
            self.halt('stale_event')
            self.baseline = False
            raise ValueError('stale observer epoch/sequence')
        else:
            self.sequence = sequence
        self.last_event_received = time.monotonic()
        self.active, self.logged_in, self.volume = value['active'], value['logged_in'], volume
        if kind == 'lost' or not self.active or not self.logged_in:
            self.halt('receiver_or_auth_lost')
            if kind == 'lost':
                self.baseline = False
        elif value['state'] in ('idle', 'unknown'):
            self.halt('receiver_stopped_or_unknown')
        elif kind == 'prepare' and value['state'] == 'playing':
            if not self.baseline or volume is None:
                raise ValueError('fresh baseline/current volume required')
            with self.children_lock:
                if not self.scalar_valid_locked():
                    raise ValueError('scalar qualification terminal')
                token = uuid.uuid4().hex
                self.pending_ticket = (token, self.epoch, self.stop_generation, time.monotonic()+TICKET_SECONDS)
            return dict(self.status(), pending_ticket=token)
        elif kind == 'delta' and value['state'] == 'playing':
            if not self.baseline or volume is None:
                raise ValueError('fresh observer baseline and current volume required')
            pending, self.pending_ticket = self.pending_ticket, None
            if pending is None or value.get('ticket') != pending[0] or pending[1] != self.epoch \
                    or pending[2] != self.stop_generation or time.monotonic() >= pending[3]:
                raise ValueError('fresh one-use worker ticket required')
            self.last_observation = time.monotonic()
            if self.state != 'playing':
                try:
                    self.start()
                except BaseException:
                    self.halt('output_start_failed')
                    raise
        elif value['state'] == 'paused':
            if self.state == 'playing':
                self.halt('receiver_paused', paused=True)
            else:
                self.pending_ticket = None
        if self.state in ('playing', 'paused'):
            self.last_observation = time.monotonic()
        result = dict(self.status(), control_generation=self.stop_generation)
        pending = self.pending_ticket
        result['pending_ticket'] = pending[0] if pending and time.monotonic() < pending[3] else None
        return result

    def serve(self, path, role, uid, gid):
        path.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(path.parent, 0o710)
        os.chown(path.parent, os.getuid(), gid)
        path.unlink(missing_ok=True)
        server = socket.socket(socket.AF_UNIX)
        server.bind(str(path))
        os.chown(path, os.getuid(), gid)
        os.chmod(path, 0o660)
        server.listen(8)
        server.settimeout(.2)
        while not self.closed.is_set():
            try:
                client, _ = server.accept()
            except TimeoutError:
                continue
            threading.Thread(target=self.handle_client, args=(client, role, uid), daemon=True).start()
        server.close()

    def handle_client(self, client, role, uid):
            with client:
                client.settimeout(1)
                try:
                    _, peer_uid, _ = struct.unpack('3i', client.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
                    owner_readonly = role == 'public' and peer_uid == 1002 and os.getuid() == 1002
                    if peer_uid != uid and not owner_readonly:
                        raise PermissionError('peer_not_admitted')
                    data = b''
                    while b'\n' not in data:
                        part = client.recv(4096)
                        if not part or len(data) + len(part) > 4096:
                            raise ValueError('bounded JSON frame required')
                        data += part
                    value = json.loads(data)
                    if peer_uid != uid and value not in ({'operation':'audio.status'}, {'operation':'audio.config.get','arguments':{}}):
                        raise PermissionError('device_owner_readonly_operation_required')
                    if role == 'public' and value.get('operation', '').startswith('audio.config.'):
                        operation = value['operation']
                        if set(value) != {'operation', 'arguments'}: raise ValueError('exact configuration frame required')
                        result = self.configurations.execute(operation, value['arguments'])
                    elif role == 'public' and value in ({'operation': 'audio.stop'}, {'operation': 'audio.pause'}):
                        self.halt('public_silence_only')
                        result = self.status()
                    else:
                      with self.command_lock:
                        if role == 'events':
                            result = self.event(value)
                        elif value == {'operation': 'audio.status'}:
                            result = self.status()
                        elif value in ({'operation': 'audio.stop'}, {'operation': 'audio.pause'}):
                            self.halt('public_silence_only')
                            result = self.status()
                        else:
                            raise PermissionError('operation_not_permitted')
                    reply = {'ok': True, 'result': result}
                except Exception as error:
                    if role == 'events':
                        self.halt('invalid_observer_message')
                        self.baseline = False
                    reply = {'ok': False, 'error': str(error)[:160]}
                client.sendall(json.dumps(reply).encode() + b'\n')
    def run(self):
        threads = [threading.Thread(target=self.serve, args=(self.root/'public/control.sock', 'public', 1000, 1003)),
                   threading.Thread(target=self.serve, args=(self.root/'events/event.sock', 'events', 1001, 1001))]
        for thread in threads:
            thread.start()
        def stop(*_):
            self.closed.set()
            self.halt('worker_exit')
        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGINT, stop)
        for thread in threads:
            thread.join()
        self.halt('worker_closed')
        if self.scalar:
            self.scalar.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', default='/run/garage-audio')
    parser.add_argument('--evidence', default='/evidence')
    parser.add_argument('--status', action='store_true')
    parser.add_argument('--mode', choices=('file', 'hardware', 'scalar', 'standby'), default='file')
    parser.add_argument('--qualification-policy', default=os.environ.get('GARAGE_AUDIO_QUALIFICATION_POLICY'))
    parser.add_argument('--qualification-store', default=os.environ.get('GARAGE_AUDIO_QUALIFICATION_STORE'))
    parser.add_argument('--network-lease', default=os.environ.get('GARAGE_AUDIO_NETWORK_LEASE'))
    parser.add_argument('--network-generation', default=os.environ.get('GARAGE_AUDIO_NETWORK_GENERATION'))
    args = parser.parse_args()
    if args.status:
        print(json.dumps(request(Path(args.root)/'public/control.sock', {'operation': 'audio.status'})))
    else:
        Worker(args.root, args.evidence, args.mode, args.network_lease, args.network_generation,
               args.qualification_policy, args.qualification_store).run()


if __name__ == '__main__':
    main()
