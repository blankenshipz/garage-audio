"""Immutable narrow policy boundary; raw Camilla lives in private loopback.

Production paths/code are root owned. Unix peer credentials authorize only the
administrator UID. No command accepts arbitrary DSP config, device or ceiling.
"""
import argparse
import copy
import fcntl
import json
import os
from pathlib import Path
import socket
import struct
import subprocess
import threading
import time
from types import SimpleNamespace
import uuid
from camilladsp import CamillaClient
from .control import apply_graph, active_profile, store, readback, prove_mute, connect_guarded, edit_profile
from .profile import DEFAULT, validate, hardware_config, compile_profile, number
from .hardware import bound_device

class Policy:
    def __init__(self,state,client_factory,config_factory,owned_process,authorize_hardware=lambda:None):
        self.state=state;self.client_factory=client_factory;self.config_factory=config_factory
        self.process=owned_process;self.admit=authorize_hardware
        self.epoch=uuid.uuid4().hex;self.lifecycle=0;self.lock=threading.RLock()
    def generation(self):
        if self.process.poll() is not None:raise RuntimeError('owned DSP exited')
        return f'{self.epoch}:{self.lifecycle}:{self.process.pid}'
    def stop(self):
        # Kill only the owned private DSP, closing FIFO/producer path. systemd
        # recovery starts fresh private namespace, generation and mute/-60.
        if self.process.poll() is None:self.process.kill()
    def execute(self,payload):
        common={'cmd','generation','port','isolated_file_test_state','attended_profile'}
        fields={'status':set(),'mute':set(),'volume':{'db'},'initialize':set(),
                'profile':{'profile','path'},'rollback':set(),'unmute':{'attended'},
                'channel':{'channel','field','value'},'sub-trim':{'db'},'peq-bypass':{'enabled'},
                'crossover':{'hz'},'peq':{'channel','index','freq','q','gain'},
                'peq-delete':{'channel','index'},'peq-clear':{'channel'},
                'commission-window':{'channel','seconds','attended'}}
        cmd=payload.get('cmd')
        with self.lock,(self.state/'control.lock').open('a') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX)
            c=self.client_factory()
            try:
                connect_guarded(c,self.stop)
                if cmd not in fields or set(payload)-common-fields.get(cmd,set()):raise ValueError('unsupported narrow policy command/fields')
                if payload.get('isolated_file_test_state') is not None or payload.get('port',1234)!=1234:
                    raise ValueError('production IPC never forwards test/raw endpoints')
                generation=self.generation()
                if cmd!='status' and payload.get('generation')!=generation:raise ValueError('stale DSP generation; never recovery-unmute')
                if cmd=='initialize':
                    self.lifecycle+=1
                    self.admit();prove_mute(c)
                    profile=active_profile(self.state) or copy.deepcopy(DEFAULT)
                    apply_graph(c,profile,self.state,self.config_factory(profile),generation=self.generation)
                elif cmd=='mute':prove_mute(c)
                elif cmd=='volume':
                    number(payload['db'],-90,-20);c.volume.set_main_volume(payload['db'])
                elif cmd=='unmute':
                    self.admit()
                    if payload.get('attended')!='PRESENT-GAINS-LOW' or not (self.state/'physical-map-verified').is_file():raise ValueError('attended physical-map receipt required')
                    number(c.volume.main_volume(),-90,-20);c.volume.set_main_mute(False)
                elif cmd=='commission-window':
                    self.admit();self.commission(c,payload)
                    result={'state':'DSP_STOPPED','mute_proof':'owned DSP process exited',
                            'generation_ended':generation,'window_seconds':payload['seconds']}
                    self.audit(cmd,result);return result
                elif cmd!='status':
                    self.admit()
                    if cmd=='profile':profile=validate(payload['profile'])
                    elif cmd=='rollback':profile=store(self.state)['known_good']
                    else:profile=edit_profile(active_profile(self.state) or DEFAULT,SimpleNamespace(**payload))
                    apply_graph(c,profile,self.state,self.config_factory(profile),
                        authorized_unmuted=payload.get('attended_profile')=='PRESENT-GAINS-LOW',generation=self.generation)
                result=readback(c);result['generation']=self.generation()
                self.audit(cmd,result);return result
            except Exception as error:
                try:prove_mute(c);muted=True
                except Exception:self.stop();muted='unknown'
                self.audit(cmd,{'failed':type(error).__name__,'muted':muted})
                raise
            finally:c.disconnect()
    def audit(self,cmd,result):
        with (self.state/'audit.jsonl').open('a') as f:
            f.write(json.dumps({'time':time.time(),'command':cmd,'effective':result})+'\n');f.flush();os.fsync(f.fileno())
    def commission(self,c,payload):
        if payload.get('attended')!='PRESENT-GAINS-LOW':raise ValueError('attended confirmation required')
        number(payload['seconds'],1,5)
        if payload['channel'] not in ('left','right','sub') or not c.volume.main_mute():raise ValueError('single-channel commissioning must begin muted')
        original=active_profile(self.state) or copy.deepcopy(DEFAULT)
        profile=copy.deepcopy(original)
        for channel in profile['channels']:profile['channels'][channel]['mute']=channel!=payload['channel']
        prove_mute(c);apply_graph(c,profile,self.state,self.config_factory(profile),persist=False,generation=self.generation)
        # Hard bounded producer-independent stop, not an unproved mute request.
        # End this generation at deadline; recovery always loads original
        # durable profile muted/-60. No transient commissioning graph persisted.
        killer=threading.Timer(payload['seconds'],self.stop);killer.start()
        try:
            c.volume.set_main_mute(False);self.process.wait(timeout=payload['seconds']+2)
        finally:
            self.stop();killer.cancel()

def serve(policy,path,admin_uid,group=None):
    path=Path(path)
    if path.exists():path.unlink()
    with socket.socket(socket.AF_UNIX) as server:
        server.bind(str(path));os.chmod(path,0o660 if group is not None else 0o600)
        if group is not None:os.chown(path,-1,group)
        server.listen(8)
        while policy.process.poll() is None:
            server.settimeout(1)
            try:peer,_=server.accept()
            except TimeoutError:continue
            with peer:
                peer.settimeout(3)
                uid=struct.unpack('3i',peer.getsockopt(socket.SOL_SOCKET,socket.SO_PEERCRED,12))[1]
                try:
                    if uid!=admin_uid:raise PermissionError('Unix peer UID not authorized')
                    data=b''
                    while not data.endswith(b'\n'):
                        part=peer.recv(4096)
                        if not part:raise ValueError('incomplete request')
                        data+=part
                        if len(data)>32768:raise ValueError('request too large')
                    payload=json.loads(data)
                    if not isinstance(payload,dict):raise ValueError('object request required')
                    response={'ok':True,'result':policy.execute(payload)}
                except Exception as error:response={'ok':False,'error':type(error).__name__+': '+str(error)}
                peer.sendall(json.dumps(response,allow_nan=False).encode()+b'\n')

def main():
    import grp
    from .daemon import run
    run()

if __name__=='__main__':main()
