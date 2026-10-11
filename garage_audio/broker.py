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
from .playback_lease import Peer, Lease, SECONDS, CEILING, boot_id, graph_digest, stereo_profile, stereo_graph, preparation_graph

class Policy:
    def __init__(self,state,client_factory,config_factory,owned_process,authorize_hardware=lambda:None,hardware_binding=None):
        self.state=state;self.client_factory=client_factory;self.config_factory=config_factory
        self.process=owned_process;self.admit=authorize_hardware
        self.epoch=uuid.uuid4().hex;self.lifecycle=0;self.lock=threading.RLock()
        self.hardware_binding=hardware_binding;self.lease=None;self.lease_timer=None;self.preparation_digest=None
        self.preparation_peer=None;self.preparation_timer=None;self.preparation_deadline=None
    def generation(self):
        if self.process.poll() is not None:raise RuntimeError('owned DSP exited')
        return f'{self.epoch}:{self.lifecycle}:{self.process.pid}'
    def stop(self):
        # Kill only the owned private DSP, closing FIFO/producer path. systemd
        # recovery starts fresh private namespace, generation and mute/-60.
        if self.process.poll() is None:self.process.kill()
    def revoke_stereo(self,reason):
        lease=self.lease
        if lease is None:return
        lease.revoke()
        # Kill FIRST, without waiting for the DSP control socket or broker lock.
        # This also closes buffered playback on helper death or a stalled readback.
        self.stop()
        if self.lease_timer is not None:self.lease_timer.cancel()
    def stereo_binding(self,config):
        if self.hardware_binding is None:raise PermissionError('native hardware binding unavailable')
        return {'boot':boot_id(),'generation':self.generation(),'hardware':self.hardware_binding(),
                'graph':graph_digest(config),'outputs':[3,4]}
    def stereo_open(self,c,peer,phone=False,hours=None,policy=None,connect=False):
        from .playback_lease import listening_scope,normal_line_graph
        _,ceiling=(10,0) if connect else (listening_scope(hours,policy) if hours is not None else (SECONDS,CEILING))
        if peer is None:raise PermissionError('kernel-observed root helper required')
        peer.authorize()
        if (getattr(peer,'realm','cli')=='phone')!=phone:raise PermissionError('distinct native authority command required')
        if phone and (self.preparation_digest is None or graph_digest(c.config.active())!=self.preparation_digest):
            raise PermissionError('exact zero-output preparation required')
        if phone and self.preparation_peer is not peer:raise PermissionError('preparation belongs to another controller connection')
        if self.lease is not None:raise PermissionError('no extension/replacement of a listening window')
        self.admit();prove_mute(c)
        base=self.config_factory(stereo_profile())
        candidate=normal_line_graph(base) if hours is not None or connect else stereo_graph(base)
        apply_graph(c,stereo_profile(),self.state,candidate,persist=False,generation=self.generation)
        expected=graph_digest(c.config.validate(candidate))
        if graph_digest(c.config.active())!=expected or not c.volume.main_mute() or c.volume.main_volume()!=CEILING:
            raise RuntimeError('stereo graph/mute/ceiling readback failed')
        binding=self.stereo_binding(c.config.active())
        self.lease=Lease(peer,binding,hours=hours,policy=policy,connect=connect)
        if self.lease.normal:self.lease.clipped_start=c.status.clipped_samples()
        if self.preparation_timer:self.preparation_timer.cancel()
        self.preparation_peer=None
        seconds=int(self.lease.deadline-self.lease.clock())+1 if hours is not None else SECONDS
        if not connect:
            self.lease_timer=threading.Timer(seconds,self.revoke_stereo,args=('deadline',))
            self.lease_timer.daemon=True;self.lease_timer.start()
        threading.Thread(target=self.guard_stereo,args=(self.lease,),daemon=True).start()
        threading.Thread(target=self.watch_stereo,args=(self.lease,),daemon=True).start()
        if not self.lease.valid(self.stereo_binding(c.config.active())):raise PermissionError('stereo binding changed before unmute')
        if hours is None and not connect:
            c.volume.set_main_volume(CEILING);c.volume.set_main_mute(False)
            if c.volume.main_mute() or c.volume.main_volume()!=CEILING:raise RuntimeError('stereo unmute readback failed')
        return {'generation':self.generation(),'seconds':10 if connect else (hours*3600 if hours is not None else SECONDS),'ceiling_db':ceiling,'outputs':[3,4],'mode':'spotify_connect' if connect else ('normal' if hours is not None else 'diagnostic')}
    def guard_stereo(self,lease):
        # Independent of client creation, websocket readback, disconnect and
        # broker locks. A hung DSP client cannot delay owner-death shutdown.
        while not lease.revoked:
            if not lease.valid(lease.binding):self.revoke_stereo('helper/deadline');return
            time.sleep(.02)
    def watch_stereo(self,lease):
        while not lease.revoked:
            if not lease.valid(lease.binding):self.revoke_stereo('helper/deadline');return
            c=None
            try:
                c=self.client_factory()
                c.connect();self.admit()
                if not lease.valid(self.stereo_binding(c.config.active())) or c.volume.main_volume()>lease.ceiling:
                    raise RuntimeError('lease binding/level changed')
                if lease.normal and c.status.clipped_samples()>lease.clipped_start:
                    raise RuntimeError('line output clipped; stop rather than continue distorted playback')
            except Exception:self.revoke_stereo('readback/hardware');return
            finally:
                if c is not None:
                    try:c.disconnect()
                    except Exception:self.revoke_stereo('disconnect failure')
            time.sleep(.1)
    def execute(self,payload,peer=None):
        if payload=={'cmd':'speaker-stop'}:
            # Kernel-admitted IPC only. No websocket/file/application lock before
            # emergency termination. Acknowledge only after owned child exits.
            self.revoke_stereo('speaker stop');self.stop()
            self.process.wait(timeout=3)
            return {'stopped':True}
        common={'cmd','generation','port','isolated_file_test_state'}
        fields={'status':set(),'mute':set(),'volume':{'db'},'initialize':set(),
                 'profile':{'profile','path'},'rollback':set(),'unmute':set(),
                'channel':{'channel','field','value'},'sub-trim':{'db'},'peq-bypass':{'enabled'},
                'crossover':{'hz'},'peq':{'channel','index','freq','q','gain'},
                'peq-delete':{'channel','index'},'peq-clear':{'channel'},
                    'commission-window':{'channel','seconds'},'stereo-open':set(),'phone-start':set(),'phone-prepare':set(),'phone-open':set(),'normal-open':{'hours','policy'},'normal-state':{'state'},'connect-open':set(),'connect-state':{'state'},'stereo-heartbeat':set()}
        cmd=payload.get('cmd')
        if self.lease is not None and cmd not in ('status','stereo-heartbeat','normal-state','connect-state'):
            self.revoke_stereo('control mutation')
            raise PermissionError('control change ends the listening generation; fresh authentication required')
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
                if cmd=='stereo-open':return self.stereo_open(c,peer)
                elif cmd=='phone-open':return self.stereo_open(c,peer,phone=True)
                elif cmd=='normal-open':
                    from .playback_lease import listening_scope
                    listening_scope(payload['hours'],payload['policy'])
                    return self.stereo_open(c,peer,phone=True,hours=payload['hours'],policy=payload['policy'])
                elif cmd=='connect-open':return self.stereo_open(c,peer,phone=True,connect=True)
                elif cmd in ('normal-state','connect-state'):
                    lease=self.lease
                    if lease is None or not lease.normal or lease.peer is not peer:raise PermissionError('owned normal session required')
                    if lease.connect != (cmd=='connect-state'):raise PermissionError('distinct receiver/manual lease heartbeat required')
                    lease.heartbeat(self.stereo_binding(c.config.active()))
                    previous=lease.transport;lease.observe(payload['state'])
                    if lease.revoked:self.revoke_stereo('MA stop/off');return {'ended':True}
                    if lease.transport!='playing':prove_mute(c)
                    elif previous!='playing':
                        # Fade native gate from silence; MA upstream volume stays
                        # its last actual user setting, never a cached louder value.
                        c.volume.set_main_mute(True);c.volume.set_main_volume(-90)
                        time.sleep(.4)
                        if not lease.valid(self.stereo_binding(c.config.active())):raise PermissionError('lease lost during muted fade preparation')
                        c.volume.set_main_mute(False);c.volume.set_main_volume(lease.ceiling)
                elif cmd=='phone-start':
                    if peer is None:raise PermissionError('native controller required')
                    peer.authorize()
                    if peer.realm!='phone' or self.preparation_peer is not None:raise PermissionError('distinct single phone preparation owner required')
                    self.admit();prove_mute(c)
                    if c.volume.main_volume()!=CEILING:raise PermissionError('muted/-60 startup required')
                    self.preparation_peer=peer
                    self.preparation_deadline=time.monotonic()+SECONDS
                    self.preparation_timer=threading.Timer(SECONDS,self.stop)
                    self.preparation_timer.daemon=True;self.preparation_timer.start()
                    def guard_preparation():
                        while self.preparation_peer is peer:
                            if not peer.alive():self.stop();return
                            time.sleep(.02)
                    threading.Thread(target=guard_preparation,daemon=True).start()
                elif cmd=='phone-prepare':
                    if self.preparation_peer is not peer or peer is None or not peer.alive() or time.monotonic()>=self.preparation_deadline:
                        raise PermissionError('early protected startup owner required')
                    self.admit();prove_mute(c)
                    if self.preparation_digest is None or graph_digest(c.config.active())!=self.preparation_digest or c.volume.main_volume()!=CEILING:
                        raise PermissionError('exact zero graph/-60 required')
                elif cmd=='stereo-heartbeat':
                    if self.lease is None or self.lease.peer is not peer or not self.lease.valid(self.stereo_binding(c.config.active())):
                        raise PermissionError('no live root-helper window on this connection')
                    if self.lease.normal:raise PermissionError('normal session needs verified MA transport heartbeat')
                elif cmd=='initialize':
                    self.lifecycle+=1
                    self.admit();prove_mute(c)
                    profile=stereo_profile()
                    apply_graph(c,profile,self.state,preparation_graph(self.config_factory(profile)),persist=False,generation=self.generation)
                    self.preparation_digest=graph_digest(c.config.active())
                elif cmd=='mute':prove_mute(c)
                elif cmd=='volume':
                    number(payload['db'],-90,-20);c.volume.set_main_volume(payload['db'])
                elif cmd=='unmute':
                    raise PermissionError('legacy unmute disabled; independent native human helper required')
                elif cmd=='commission-window':
                    raise PermissionError('legacy attended strings do not authorize sound')
                elif cmd!='status':
                    self.admit()
                    if cmd=='profile':profile=validate(payload['profile'])
                    elif cmd=='rollback':profile=store(self.state)['known_good']
                    else:profile=edit_profile(active_profile(self.state) or DEFAULT,SimpleNamespace(**payload))
                    apply_graph(c,profile,self.state,self.config_factory(profile),
                        authorized_unmuted=False,generation=self.generation)
                result=readback(c);result['generation']=self.generation()
                if self.lease is not None:result['listening']={'mode':'normal' if self.lease.normal else 'diagnostic','transport':self.lease.transport,'ceiling_db':self.lease.ceiling,'seconds_remaining':max(0,int(self.lease.deadline-self.lease.clock()))}
                result['preparation_zero']=self.preparation_digest is not None and graph_digest(c.config.active())==self.preparation_digest
                if self.preparation_deadline is not None:result['preparation_seconds_remaining']=max(0,self.preparation_deadline-time.monotonic())
                self.audit(cmd,result);return result
            except Exception as error:
                if self.lease is not None:self.revoke_stereo('command failure')
                try:prove_mute(c);muted=True
                except Exception:self.stop();muted='unknown'
                self.audit(cmd,{'failed':type(error).__name__,'muted':muted})
                raise
            finally:c.disconnect()
    def audit(self,cmd,result):
        with (self.state/'audit.jsonl').open('a') as f:
            f.write(json.dumps({'time':time.time(),'command':cmd,'effective':result})+'\n');f.flush();os.fsync(f.fileno())
    def commission(self,c,payload):
        raise PermissionError('legacy attended-string commissioning disabled')

def serve(policy,path,admin_uid,group=None):
    path=Path(path)
    if path.exists():path.unlink()
    with socket.socket(socket.AF_UNIX) as server:
        server.bind(str(path));os.chmod(path,0o660 if group is not None else 0o600)
        if group is not None:os.chown(path,-1,group)
        server.listen(8)
        slots=threading.BoundedSemaphore(16)
        def handle(peer_socket):
            observed=None
            try:
                with peer_socket:
                    peer_socket.settimeout(3)
                    pid,uid,gid=struct.unpack('3i',peer_socket.getsockopt(socket.SOL_SOCKET,socket.SO_PEERCRED,12))
                    peer_socket.settimeout(30 if uid==0 else 3)
                    observed=Peer(pid,uid,gid)
                    if uid not in (admin_uid,0):
                        peer_socket.sendall(b'{"ok":false,"error":"PermissionError: Unix peer UID not authorized"}\n');return
                    while policy.process.poll() is None:
                        data=b''
                        while not data.endswith(b'\n'):
                            part=peer_socket.recv(1)
                            if not part:return
                            data+=part
                            if len(data)>32768:raise ValueError('request too large')
                        try:
                            payload=json.loads(data)
                            if not isinstance(payload,dict):raise ValueError('object request required')
                            response={'ok':True,'result':policy.execute(payload,observed)}
                        except Exception as error:response={'ok':False,'error':type(error).__name__+': '+str(error)}
                        peer_socket.sendall(json.dumps(response,allow_nan=False).encode()+b'\n')
            except PermissionError:
                try:peer_socket.sendall(b'{"ok":false,"error":"PermissionError: Unix peer UID not authorized"}\n')
                except OSError:pass
            except (OSError,ValueError):pass
            finally:
                if observed is not None:
                    if policy.lease is not None and policy.lease.peer is observed:policy.revoke_stereo('helper disconnect')
                    if policy.preparation_peer is observed:policy.stop()
                    observed.close()
                slots.release()
        while policy.process.poll() is None:
            server.settimeout(1)
            try:peer,_=server.accept()
            except TimeoutError:continue
            if not slots.acquire(blocking=False):peer.close();continue
            threading.Thread(target=handle,args=(peer,),daemon=True).start()

def main():
    import grp
    from .daemon import run
    run()

if __name__=='__main__':main()
