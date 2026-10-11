"""Finite stereo authority, never persisted or accepted from client arguments.

Root administrators are trusted. Ordinary Unix peers cannot become root helpers
by supplying a role, consent string, FILE grant, PID or descriptor in a payload.
"""
import copy
import hashlib
import json
import os
from pathlib import Path
import select
import time
import uuid
from .profile import DEFAULT

SECONDS = 120
CEILING = -60
LISTEN_HOURS = (1,2,4)
LISTEN_POLICY = 'fosi-line-v1'
NORMAL_CEILING = 0

def listening_scope(hours,policy):
    if type(hours) is not int or hours not in LISTEN_HOURS or policy!=LISTEN_POLICY:
        raise PermissionError('fixed amplifier-controlled line policy and human duration required')
    return hours*3600,NORMAL_CEILING
ROOT = Path('/usr/local/libexec/garage-audio')

def stereo_profile():
    profile=copy.deepcopy(DEFAULT)
    profile['name']='basic-stereo-uncalibrated'
    profile['channels']['sub']['mute']=True
    return profile

def stereo_graph(config,ceiling=CEILING,ramp_ms=0):
    """Internal root compiler output only: two destinations, ten silent outputs."""
    config=copy.deepcopy(config)
    if config['devices']['samplerate']!=48000 or config['devices']['capture']['channels']!=2:
        raise ValueError('stereo lease requires48k/two input channels')
    playback=config['devices']['playback']
    if playback['channels']!=12 or playback['format']!='S24_3_LE':
        raise ValueError('first-listen policy requires verified12/packed24')
    if ceiling not in (CEILING,NORMAL_CEILING) or ramp_ms not in (0,400):raise ValueError('fixed native graph scope required')
    config['devices']['volume_limit']=ceiling
    config['devices']['volume_ramp_time']=ramp_ms
    route=config['mixers']['route']
    route['mapping']=[{'dest':2,'sources':[{'channel':0,'gain':0}]},
                      {'dest':3,'sources':[{'channel':1,'gain':0}]}]
    config['pipeline']=[step for step in config['pipeline']
                        if not (step['type']=='Filter' and step.get('channels')==[4])]
    return config

def normal_line_graph(config):
    """Fresh flat stereo lease, never overwrite a stored room/correction profile.

    Unity master with one -6dB processing-headroom stage. No crossover/sub route,
    EQ boost, mono sum, polarity change or delay. Exact OUT3/4; ten silent outputs.
    """
    config=stereo_graph(config,NORMAL_CEILING,400)
    config['title']='Garage stereo line — unmeasured, Fosi controls volume'
    config['filters']={'headroom':{'type':'Gain','parameters':{'gain':-6.0}},
        'left_gain':{'type':'Gain','parameters':{'gain':0.0,'inverted':False,'mute':False}},
        'right_gain':{'type':'Gain','parameters':{'gain':0.0,'inverted':False,'mute':False}},
        'left_delay':{'type':'Delay','parameters':{'delay':0.0,'unit':'ms','subsample':True}},
        'right_delay':{'type':'Delay','parameters':{'delay':0.0,'unit':'ms','subsample':True}}}
    config['pipeline']=[{'type':'Filter','channels':[0,1],'names':['headroom']},
        {'type':'Mixer','name':'route'},
        {'type':'Filter','channels':[2],'names':['left_gain','left_delay']},
        {'type':'Filter','channels':[3],'names':['right_gain','right_delay']}]
    return config

def graph_digest(config):
    if not isinstance(config,dict):raise ValueError('missing graph readback')
    graph={k:config[k] for k in ('devices','filters','mixers','pipeline')}
    return hashlib.sha256(json.dumps(graph,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()

def preparation_graph(config):
    """Administrative setup has no signal destinations, even if mute fails.

    Same real capture/playback devices as listening; not a null player. Empty
    route mapping zeroes every output, including OUT3/4. Master remains muted.
    """
    config=stereo_graph(config)
    config['mixers']['route']['mapping']=[]
    for name,value in config['filters'].items():
        if name.endswith('_gain') and value.get('type')=='Gain':
            value['parameters']['mute']=True
    return config

def boot_id():
    return Path('/proc/sys/kernel/random/boot_id').read_text().strip()

class Peer:
    """Kernel-observed peer, owned by one accepted connection, not JSON data."""
    def __init__(self,pid,uid,gid):
        self.pid,self.uid,self.gid=pid,uid,gid
        self.pidfd=None
    def authorize(self):
        if self.uid!=0:raise PermissionError('only native root human helper may arm stereo')
        expected=[str(ROOT/'venv/bin/python'),'-I',str(ROOT/'human-entry.py'),'listen']
        phone=[str(ROOT/'venv/bin/python'),'-I',str(ROOT/'speaker-entry.py'),'serve']
        argv=Path(f'/proc/{self.pid}/cmdline').read_bytes().rstrip(b'\0').split(b'\0')
        observed=[v.decode() for v in argv]
        if observed not in (expected,phone):
            raise PermissionError('root peer is not the installed interactive human helper')
        self.realm='phone' if observed==phone else 'cli'
        assets=(ROOT/'speaker-entry.py',ROOT/'garage_audio/speaker_controller.py',ROOT/'garage_audio/speaker_realm.py',ROOT/'garage_audio/ma_bridge.py',ROOT/'garage_audio/ma_reference.py',ROOT/'garage_audio/connect_port.py',ROOT/'garage_audio/spotify_socket_guard.py') if observed==phone else (ROOT/'human-entry.py',ROOT/'garage_audio/human_playback.py')
        for item in (ROOT,*assets):
            info=item.lstat()
            if item.is_symlink() or info.st_uid!=0 or info.st_mode&0o022:
                raise PermissionError('helper code is not immutable root-owned code')
        # pidfd remains tied to this exact process even if a PID is recycled.
        if self.pidfd is None:self.pidfd=os.pidfd_open(self.pid)
        if not self.alive():raise PermissionError('helper exited during admission')
        return self
    def alive(self):
        return self.pidfd is not None and not select.select([self.pidfd],[],[],0)[0]
    def close(self):
        if self.pidfd is not None:os.close(self.pidfd);self.pidfd=None

class Lease:
    def __init__(self,peer,binding,*,clock=time.monotonic,hours=None,policy=None,connect=False):
        if peer.uid!=0 or not peer.alive():raise PermissionError('live authenticated root-helper peer required')
        self.peer=peer;self.binding=copy.deepcopy(binding);self.clock=clock
        self.connect=connect
        if connect and (hours is not None or policy is not None):raise PermissionError('receiver lease has no caller duration/profile')
        self.normal=connect or hours is not None
        seconds,self.ceiling=(10,NORMAL_CEILING) if connect else (listening_scope(hours,policy) if self.normal else (SECONDS,CEILING))
        self.policy=LISTEN_POLICY if connect else (policy if self.normal else 'diagnostic-60');self.clipped_start=0
        now=clock();self.deadline=now+seconds;self.id=uuid.uuid4().hex;self.revoked=False
        self.last=now;self.idle_deadline=now+600 if self.normal else None
        self.transport='awaiting_play' if self.normal else 'playing';self.played=False
    def valid(self,binding):
        now=self.clock()
        return not self.revoked and now<self.deadline and self.peer.alive() and binding==self.binding and (not self.normal or (now-self.last<10 and (self.idle_deadline is None or now<self.idle_deadline)))
    def heartbeat(self,binding):
        if not self.valid(binding):raise PermissionError('listening lease expired; never renew expired authority')
        self.last=self.clock()
        if self.connect:self.deadline=self.last+10
    def observe(self,state):
        if not self.normal or state not in ('playing','paused','idle','off'):raise PermissionError('exact MA transport state required')
        if state=='playing':self.played=True;self.idle_deadline=None
        elif state=='paused':
            if self.transport!='paused':self.idle_deadline=self.clock()+600 if self.connect else min(self.deadline,self.clock()+600)
        elif self.played or state=='off':self.revoke()
        # Initial idle is bounded warm readiness, never auto-play or a renewal.
        self.transport=state
    def revoke(self):self.revoked=True
