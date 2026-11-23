"""Stdlib clients for the supervisor/service manager, Docker (read-only), Music Assistant and CamillaDSP.

All clients are small and injectable: tool handlers receive them through
``Clients`` so tests substitute fakes. No client ever runs a shell, uses sudo or
returns credential bytes or raw upstream error bodies.
"""
import base64
import hashlib
import json
import os
import re
import socket
import struct
import subprocess
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

from .config import read_private_secret
from .errors import OpsError

MAX_OUTPUT = 512 * 1024


# ---- redaction -------------------------------------------------------------------
_REDACTIONS = [
    (re.compile(r'(?i)(authorization\s*[:=]\s*)(bearer|basic)?\s*[^\s,;"\']+'), r'\1[REDACTED]'),
    (re.compile(r'(?i)\b(bearer)\s+[A-Za-z0-9._~+/=-]{8,}'), r'\1 [REDACTED]'),
    (re.compile(r'(?i)\b([a-z0-9_.-]*(?:token|secret|password|passwd|api[_-]?key|credential|cookie|session)[a-z0-9_.-]*)'
                r'(\s*[:=]\s*|"\s*:\s*")([^\s,;"\'&]+)'), r'\1\2[REDACTED]'),
    (re.compile(r'\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}'), '[REDACTED_JWT]'),
    (re.compile(r'\b[0-9a-fA-F]{32,}\b'), '[REDACTED_HEX]'),
    (re.compile(r'\b[A-Za-z0-9+/_-]{40,}={0,2}'), '[REDACTED_BLOB]'),
]
_CONTROL = re.compile(r'[\x00-\x08\x0b-\x1f\x7f]')


def redact(text, max_line=400):
    """Remove credential-looking values and control characters; bound each line."""
    lines = []
    for line in str(text).splitlines():
        line = _CONTROL.sub('', line)
        for pattern, replacement in _REDACTIONS:
            line = pattern.sub(replacement, line)
        lines.append(line[:max_line] + ('…' if len(line) > max_line else ''))
    return lines


# ---- subprocess (systemctl, journalctl, docker) ---------------------------------
def run_argv(argv, timeout):
    """Run argv without a shell. Returns (returncode, stdout, stderr), bounded."""
    if not isinstance(argv, list) or not argv or not all(isinstance(a, str) for a in argv):
        raise OpsError('internal', 'command must be an argv list')
    env = {'PATH': os.environ.get('PATH', '/usr/bin:/bin'), 'LC_ALL': 'C', 'SYSTEMD_PAGER': '', 'PAGER': 'cat',
           'SYSTEMD_COLORS': '0'}
    for key in ('DOCKER_HOST', 'XDG_RUNTIME_DIR', 'DBUS_SESSION_BUS_ADDRESS', 'HOME'):
        if key in os.environ:
            env[key] = os.environ[key]
    try:
        result = subprocess.run(argv, capture_output=True, timeout=timeout, env=env, stdin=subprocess.DEVNULL,
                                check=False)
    except FileNotFoundError:
        raise OpsError('command_missing', f'{os.path.basename(argv[0])} is not installed or not on PATH') from None
    except subprocess.TimeoutExpired:
        raise OpsError('command_timeout', f'{os.path.basename(argv[0])} did not finish within {timeout} s') from None
    except OSError:
        raise OpsError('command_failed', f'{os.path.basename(argv[0])} could not be started') from None
    out = result.stdout[:MAX_OUTPUT].decode('utf-8', 'replace')
    err = result.stderr[:MAX_OUTPUT].decode('utf-8', 'replace')
    return result.returncode, out, err


UNIT_PROPERTIES = ('LoadState', 'ActiveState', 'SubState', 'Result', 'NRestarts', 'ExecMainStatus',
                   'ExecMainCode', 'ActiveEnterTimestamp', 'InactiveEnterTimestamp', 'StateChangeTimestamp')


class SystemClient:
    """Service manager (sanctioned recovery path), journal and read-only Docker inspection."""

    def __init__(self, config, runner=run_argv):
        self.config = config
        self.run = runner

    def unit_state(self, unit=None):
        unit = unit or self.config.unit
        code, out, _err = self.run(self.config.systemctl + ['show', unit, '--no-pager',
                                                            '--property=' + ','.join(UNIT_PROPERTIES)],
                                   self.config.supervisor_timeout)
        if code != 0:
            raise OpsError('unit_state_unavailable', 'the service manager did not report the unit state', unit=unit)
        state = {}
        for line in out.splitlines():
            key, sep, value = line.partition('=')
            if sep and key in UNIT_PROPERTIES:
                state[key] = value.strip()[:128]
        state['unit'] = unit
        return state

    def unit_action(self, action):
        if action not in ('start', 'restart'):
            raise OpsError('internal', 'only start and restart are sanctioned')
        argv = self.config.systemctl + [action, '--no-block', self.config.unit]
        code, _out, err = self.run(argv, self.config.supervisor_timeout)
        return {'command': argv, 'exit_status': code,
                'stderr': redact(err)[-10:] if code != 0 else []}

    def containers(self):
        """Read-only container states from docker ps/inspect (no env, args or mounts returned)."""
        ids = []
        if self.config.container_label:
            code, out, _err = self.run(self.config.docker + ['ps', '-a', '--no-trunc', '--format', '{{.ID}}',
                                                             '--filter', 'label=' + self.config.container_label], 20)
            if code != 0:
                raise OpsError('docker_unavailable', 'docker ps failed (daemon down or no permission)')
            ids += [line.strip() for line in out.splitlines() if line.strip()]
        ids += [name for name in self.config.container_names if name not in ids]
        if not ids:
            return []
        code, out, _err = self.run(self.config.docker + ['inspect', '--type', 'container'] + ids, 20)
        try:
            items = json.loads(out) if out.strip() else []
        except ValueError:
            raise OpsError('docker_unavailable', 'docker inspect returned unreadable output') from None
        if code != 0 and not items:
            raise OpsError('docker_unavailable', 'docker inspect failed (missing containers, daemon down or no permission)')
        found = []
        for item in items if isinstance(items, list) else []:
            state = item.get('State') or {}
            labels = (item.get('Config') or {}).get('Labels') or {}
            health = state.get('Health') or {}
            found.append({
                'name': str(item.get('Name', '')).lstrip('/')[:128],
                'status': state.get('Status'), 'running': state.get('Running'),
                'restarting': state.get('Restarting'), 'exit_code': state.get('ExitCode'),
                'oom_killed': state.get('OOMKilled'), 'started_at': state.get('StartedAt'),
                'finished_at': state.get('FinishedAt'), 'health': health.get('Status'),
                'restart_count': item.get('RestartCount'),
                'service': labels.get('com.docker.compose.service'),
                'generation_label': labels.get(self.config.container_label) if self.config.container_label else None,
            })
        missing = [name for name in self.config.container_names
                   if name not in {c['name'] for c in found}]
        for name in missing:
            found.append({'name': name, 'status': 'missing', 'running': False})
        return found

    def unit_logs(self, unit, lines, since=None):
        argv = self.config.journalctl + ['-u', unit, '-n', str(lines), '--no-pager', '-o', 'short-iso']
        if since:
            argv += ['--since', since]
        code, out, _err = self.run(argv, 20)
        if code != 0:
            raise OpsError('logs_unavailable', 'journalctl failed (no permission to read this unit journal?)', unit=unit)
        return redact(out)[-lines:]

    def container_logs(self, name, lines, since=None):
        argv = self.config.docker + ['logs', '--tail', str(lines), '--timestamps']
        if since:
            argv += ['--since', since]
        code, out, err = self.run(argv + [name], 20)
        if code != 0:
            raise OpsError('logs_unavailable', 'docker logs failed', container=name)
        return redact(out + err)[-lines:]


# ---- Music Assistant ---------------------------------------------------------------
class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class MAClient:
    """Music Assistant server JSON RPC (POST {url}/api), as used by ma_enrollment.MA."""

    def __init__(self, config, opener=None):
        self.config = config
        self.opener = opener or urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())

    def _token(self):
        if not self.config.ma_token_file:
            raise OpsError('ma_not_configured', 'music_assistant.token_file is not configured')
        token = read_private_secret(self.config.ma_token_file, 'Music Assistant token')
        if not token or any(c.isspace() for c in token) or not token.isascii():
            raise OpsError('ma_credential_invalid', 'Music Assistant token file does not contain a token')
        return token

    def _open(self, request):
        try:
            with self.opener.open(request, timeout=self.config.ma_timeout) as response:
                status = response.status
                raw = response.read(MAX_OUTPUT * 8 + 1)
        except urllib.error.HTTPError as error:
            code = error.code
            raise OpsError('ma_authentication_failed' if code == 401 else 'ma_forbidden' if code == 403
                           else 'ma_error', f'Music Assistant answered HTTP {code}', http_status=code) from None
        except (OSError, ValueError):
            raise OpsError('ma_unreachable', 'Music Assistant is not reachable at the configured URL') from None
        if status != 200 or len(raw) > MAX_OUTPUT * 8:
            raise OpsError('ma_error', 'Music Assistant returned an unexpected response', http_status=status)
        try:
            return json.loads(raw)
        except ValueError:
            raise OpsError('ma_error', 'Music Assistant returned invalid JSON') from None

    def info(self):
        request = urllib.request.Request(self.config.ma_url + '/info', method='GET')
        value = self._open(request)
        if not isinstance(value, dict):
            raise OpsError('ma_error', 'Music Assistant info is not an object')
        return {k: value.get(k) for k in ('server_version', 'schema_version', 'min_supported_schema_version',
                                           'onboard_done') if k in value}

    def call(self, command, args=None):
        headers = {'Content-Type': 'application/json', 'Authorization': 'Bearer ' + self._token()}
        body = json.dumps({'command': command, 'args': args or {}, 'message_id': 'garage-ops-mcp'}).encode()
        request = urllib.request.Request(self.config.ma_url + '/api', data=body, headers=headers, method='POST')
        value = self._open(request)
        if isinstance(value, dict) and set(value) & {'error_code', 'error'} and 'result' not in value:
            raise OpsError('ma_command_failed', f'Music Assistant rejected {command}',
                           error_code=value.get('error_code'))
        if isinstance(value, dict) and 'result' in value and 'message_id' in value:
            return value['result']
        return value


# ---- CamillaDSP websocket ----------------------------------------------------------
WS_GUID = '258EAFA5-E914-47DA-95CA-C5AB0DC85B11'
MAX_WS_MESSAGE = 8 * 1024 * 1024


class _WebSocket:
    """Minimal RFC 6455 text-message client (no extensions, no TLS): enough for CamillaDSP."""

    def __init__(self, host, port, timeout):
        self.sock = socket.create_connection((host, port), timeout=timeout)
        self.sock.settimeout(timeout)
        key = base64.b64encode(os.urandom(16)).decode()
        host_header = f'[{host}]:{port}' if ':' in host else f'{host}:{port}'
        request = (f'GET / HTTP/1.1\r\nHost: {host_header}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n'
                   f'Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n')
        self.sock.sendall(request.encode('ascii'))
        head = b''
        while b'\r\n\r\n' not in head:
            chunk = self.sock.recv(4096)
            if not chunk or len(head) > 16384:
                raise OSError('handshake failed')
            head += chunk
        head, _, self.buffer = head.partition(b'\r\n\r\n')
        lines = head.decode('latin-1').split('\r\n')
        if not lines[0].startswith('HTTP/1.1 101'):
            raise OSError('handshake rejected')
        headers = {k.strip().lower(): v.strip() for k, _, v in (line.partition(':') for line in lines[1:])}
        expected = base64.b64encode(hashlib.sha1((key + WS_GUID).encode()).digest()).decode()
        if headers.get('sec-websocket-accept') != expected:
            raise OSError('handshake accept mismatch')

    def _read(self, count):
        while len(self.buffer) < count:
            chunk = self.sock.recv(max(4096, count - len(self.buffer)))
            if not chunk:
                raise OSError('connection closed')
            self.buffer += chunk
        data, self.buffer = self.buffer[:count], self.buffer[count:]
        return data

    def _send_frame(self, opcode, payload):
        header = bytes([0x80 | opcode])
        length = len(payload)
        if length < 126:
            header += bytes([0x80 | length])
        elif length < 65536:
            header += bytes([0x80 | 126]) + struct.pack('!H', length)
        else:
            header += bytes([0x80 | 127]) + struct.pack('!Q', length)
        mask = os.urandom(4)
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        self.sock.sendall(header + mask + masked)

    def send_text(self, text):
        self._send_frame(0x1, text.encode('utf-8'))

    def recv_text(self):
        message = b''
        while True:
            first, second = self._read(2)
            opcode = first & 0x0F
            length = second & 0x7F
            if length == 126:
                length = struct.unpack('!H', self._read(2))[0]
            elif length == 127:
                length = struct.unpack('!Q', self._read(8))[0]
            if length > MAX_WS_MESSAGE or len(message) + length > MAX_WS_MESSAGE:
                raise OSError('message too large')
            mask = self._read(4) if second & 0x80 else None
            payload = self._read(length)
            if mask:
                payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
            if opcode == 0x9:
                self._send_frame(0xA, payload)
                continue
            if opcode == 0xA:
                continue
            if opcode == 0x8:
                raise OSError('connection closed by peer')
            if opcode in (0x0, 0x1, 0x2):
                message += payload
                if first & 0x80:
                    return message.decode('utf-8')
                continue
            raise OSError('unexpected frame')

    def close(self):
        try:
            self._send_frame(0x8, struct.pack('!H', 1000))
        except OSError:
            pass
        try:
            self.sock.close()
        except OSError:
            pass


class CamillaClient:
    """CamillaDSP websocket commands used by the ops server. One connection per query."""

    def __init__(self, config, connect=None):
        self.config = config
        self.connect = connect or (lambda: _WebSocket(config.camilla_host, config.camilla_port, config.camilla_timeout))

    def query(self, command, arg=None):
        try:
            ws = self.connect()
        except OSError:
            raise OpsError('camilladsp_unreachable', 'CamillaDSP websocket is not reachable at the configured address') from None
        try:
            ws.send_text(json.dumps(command if arg is None else {command: arg}))
            raw = ws.recv_text()
        except OSError:
            raise OpsError('camilladsp_unreachable', 'CamillaDSP websocket connection failed') from None
        finally:
            ws.close()
        try:
            reply = json.loads(raw)[command]
        except (ValueError, KeyError, TypeError):
            raise OpsError('camilladsp_error', 'CamillaDSP returned an unexpected reply', command=command) from None
        if 'error' in reply:
            raise OpsError('camilladsp_unsupported', f'CamillaDSP does not support {command}', command=command)
        result = reply.get('result')
        if result == 'Ok':
            return reply.get('value')
        state, message = (next(iter(result.items())) if isinstance(result, dict) and result else (result, None))
        raise OpsError('camilladsp_rejected', f'CamillaDSP rejected {command}: {state}', command=command,
                       reason=str(message)[:500] if message else None)

    def get_config(self):
        raw = self.query('GetConfigJson')
        try:
            value = json.loads(raw) if isinstance(raw, str) else raw
        except ValueError:
            raise OpsError('camilladsp_error', 'CamillaDSP active configuration is not JSON') from None
        if not isinstance(value, dict):
            raise OpsError('camilladsp_error', 'CamillaDSP has no active configuration')
        return value

    def validate_config(self, config):
        return self.query('ValidateConfigJson', json.dumps(config))

    def set_config(self, config):
        return self.query('SetConfigJson', json.dumps(config))

    def get_volume(self):
        return self.query('GetVolume')

    def set_volume(self, value):
        return self.query('SetVolume', float(value))

    def get_mute(self):
        return self.query('GetMute')

    def set_mute(self, value):
        return self.query('SetMute', bool(value))

    def get_signal_levels(self):
        return self.query('GetSignalLevels')

    def get_peaks_since_start(self):
        return self.query('GetSignalPeaksSinceStart')

    def get_clipped_samples(self):
        return self.query('GetClippedSamples')

    def get_state(self):
        return self.query('GetState')

    def get_version(self):
        return self.query('GetVersion')

    def get_processing_load(self):
        return self.query('GetProcessingLoad')


@dataclass
class Clients:
    system: Any
    ma: Any
    camilla: Any


def default_clients(config):
    return Clients(system=SystemClient(config), ma=MAClient(config), camilla=CamillaClient(config))
