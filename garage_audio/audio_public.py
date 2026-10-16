"""Silence/status-only client for the worker's public Unix socket; never event authority."""
import json
import os
import math
import re
import socket
import struct
import time
from . import audio_config

SCHEMAS = {op:([],[]) for op in ('audio.status','audio.stop','audio.pause')}
OPERATION_SCHEMAS = {op:{'type':'object','properties':{},'required':[],
                         'additionalProperties':False} for op in SCHEMAS}
SCHEMAS.update(audio_config.SCHEMAS)
OPERATION_SCHEMAS.update(audio_config.OPERATION_SCHEMAS)
MAX_REPLY = 16384
TIMEOUT = 2.0
PUBLIC_SOCKET = '/run/garage-audio/public/control.sock'
STATUS_FIELDS = frozenset(('state','reason','generation','device_mode','device_open',
    'owned_children','segment','receiver_volume','headroom_db','outputs','unused_outputs_zero',
    'watchdog_seconds','deadline_remaining','authority_persisted','physical_verified',
    'device_verified','observer_available','receiver_available'))


class AudioFailure(Exception):
    def __init__(self,code,message):
        self.code,self.message,self.requirements = code,message,[
            'Inspect the private Docker audio worker and public socket readiness; no silence or device success is inferred']


def deployment_mode():
    mode = os.environ.get('GARAGE_DEPLOYMENT_MODE','docker')
    if mode not in ('docker','legacy-native'):
        raise AudioFailure('deployment_mode_invalid','Unknown server deployment mode')
    return mode


def _object(pairs):
    result = {}
    for key,value in pairs:
        if key in result: raise ValueError('Duplicate reply field')
        result[key] = value
    return result


def _status(result):
    if (result.get('state') not in ('off','paused','playing')
            or result.get('device_mode') not in ('file','hardware')
            or type(result.get('device_open')) is not bool
            or type(result.get('physical_verified')) is not bool):
        raise ValueError('Invalid worker status')
    for field in ('device_verified','observer_available','receiver_available','unused_outputs_zero','authority_persisted'):
        if field in result and type(result[field]) is not bool: raise ValueError('Invalid status flag')
    if 'reason' in result and (not isinstance(result['reason'],str) or not re.fullmatch(r'[a-z0-9_.-]{1,80}',result['reason'])):
        raise ValueError('Invalid reason code')
    if 'generation' in result and (not isinstance(result['generation'],str) or not re.fullmatch(r'[a-f0-9]{32}',result['generation'])):
        raise ValueError('Invalid generation')
    if 'owned_children' in result and (not isinstance(result['owned_children'],list)
            or len(result['owned_children'])>4 or any(type(pid) is not int or not 1<=pid<2**31 for pid in result['owned_children'])):
        raise ValueError('Invalid child readback')
    if 'outputs' in result:
        outputs = result['outputs']
        if result['device_mode'] != 'file' and outputs != [3,4]: raise ValueError('Unexpected hardware output map')
        if (not isinstance(outputs,list) or not 2 <= len(outputs) <= 3 or
                any(type(v) is not int or not 1 <= v <= 12 for v in outputs) or len(set(outputs)) != len(outputs)):
            raise ValueError('Invalid output map')
    if 'headroom_db' in result and (type(result['headroom_db']) not in (int,float) or
            not math.isfinite(result['headroom_db']) or not -60 <= result['headroom_db'] <= -6 or
            (result['device_mode'] != 'file' and result['headroom_db'] != -6)):
        raise ValueError('Unexpected headroom')
    for field in ('segment','watchdog_seconds','deadline_remaining'):
        if field in result and (type(result[field]) not in (int,float) or not math.isfinite(result[field]) or not 0<=result[field]<=86400):
            raise ValueError('Invalid numeric status')
    if 'receiver_volume' in result and result['receiver_volume'] is not None and (
            type(result['receiver_volume']) is not int or not 0<=result['receiver_volume']<=100):
        raise ValueError('Invalid receiver volume')
    return {key:value for key,value in result.items() if key in STATUS_FIELDS}


def request(operation, arguments=None):
    if operation not in SCHEMAS:
        raise ValueError('Public audio operation unavailable')
    path = os.environ.get('GARAGE_AUDIO_CONTROL_SOCKET',PUBLIC_SOCKET)
    if not isinstance(path,str) or not path.startswith('/') or len(path.encode()) > 103:
        raise AudioFailure('audio_configuration_invalid','Public audio socket configuration invalid')
    timeout = 15.0 if operation in audio_config.SCHEMAS else TIMEOUT
    deadline = time.monotonic() + timeout
    try:
        with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as client:
            client.settimeout(timeout)
            client.connect(path)
            # Reciprocal Linux identity check; the worker admits only dashboard UID1000.
            if hasattr(socket,'SO_PEERCRED'):
                _,uid,_ = struct.unpack('3i',client.getsockopt(socket.SOL_SOCKET,socket.SO_PEERCRED,12))
                if uid != 1002:
                    raise AudioFailure('audio_peer_rejected','Public audio worker identity rejected')
            remaining = deadline-time.monotonic()
            if remaining <= 0: raise TimeoutError
            client.settimeout(remaining)
            frame = {'operation':operation}
            if operation in audio_config.SCHEMAS: frame['arguments'] = arguments or {}
            client.sendall(json.dumps(frame,separators=(',',':')).encode()+b'\n')
            data = bytearray()
            while b'\n' not in data:
                remaining = deadline-time.monotonic()
                if remaining <= 0: raise TimeoutError
                client.settimeout(remaining)
                part = client.recv(min(4096,MAX_REPLY+1-len(data)))
                if not part: raise ValueError('Incomplete frame')
                data.extend(part)
                if len(data)>MAX_REPLY: raise ValueError('Oversized frame')
            if data.count(b'\n') != 1 or data[-1:] != b'\n':
                raise ValueError('Exact single frame required')
            reply = json.loads(data,object_pairs_hook=_object,
                parse_constant=lambda _: (_ for _ in ()).throw(ValueError('Nonfinite reply')))
            if not isinstance(reply,dict) or type(reply.get('ok')) is not bool:
                raise ValueError('Invalid envelope')
            if reply['ok'] is False:
                if set(reply) != {'ok','error'}: raise ValueError('Invalid error envelope')
                raise AudioFailure('audio_operation_rejected','Public audio operation rejected by worker')
            if set(reply) != {'ok','result'} or not isinstance(reply['result'],dict):
                raise ValueError('Invalid success envelope')
            result = reply['result']
            return result if operation in audio_config.SCHEMAS else _status(result)
    except AudioFailure:
        raise
    except (OSError,ValueError,TypeError,OverflowError,UnicodeError):
        raise AudioFailure('audio_unavailable','Docker audio worker unavailable or reply invalid') from None
