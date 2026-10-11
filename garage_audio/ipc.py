"""Permissioned Unix IPC; no raw DSP config/API forwarding."""
import json
import socket

SOCKET='/run/garage-audio/control.sock'
def request(payload, path=SOCKET, timeout=15):
    if payload.get('cmd')!='status' and 'generation' not in payload:
        payload=dict(payload,generation=request({'cmd':'status'},path,timeout)['generation'])
    with socket.socket(socket.AF_UNIX) as s:
        s.settimeout(timeout);s.connect(path)
        s.sendall(json.dumps(payload,allow_nan=False).encode()+b'\n')
        data=b''
        while not data.endswith(b'\n'):
            chunk=s.recv(4096)
            if not chunk:raise OSError('policy broker disconnected')
            data+=chunk
            if len(data)>65536:raise ValueError('oversized control reply')
        reply=json.loads(data)
        if not reply['ok']:raise RuntimeError(reply['error'])
        return reply['result']
