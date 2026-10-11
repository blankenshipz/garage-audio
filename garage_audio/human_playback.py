"""Root-installed interactive operator helper; agents must never run it.

Passwords are read exclusively from controlling /dev/tty without echo. No
password/enrollment endpoint, argv/env credential, ordinary stdin or bearer grant.
"""
import argparse
import hashlib
import http.client
import json
import os
from pathlib import Path
import secrets
import select
import socket
import subprocess
import sys
import termios
import time
from .human_authority import HumanVerifier

DIRECTORY=Path('/etc/garage-audio-human')
VERIFIER=DIRECTORY/'verifier.json'
STATE=Path('/var/lib/garage-audio-dsp')
SOCKET='/run/garage-audio/control.sock'

def controlling_tty():
    if os.getuid()!=0 or os.geteuid()!=0:raise PermissionError('administrator execution required')
    if any(k in os.environ for k in ('GARAGE_HUMAN_PASSWORD','GARAGE_PLAYBACK_PASSWORD','HUMAN_PASSWORD')):
        raise PermissionError('password environment variables are forbidden')
    if not all(stream.isatty() for stream in (sys.stdin,sys.stdout,sys.stderr)):
        raise PermissionError('interactive terminal required; no ordinary stdin')
    tty=open('/dev/tty','r+',buffering=1)
    if not tty.isatty() or os.tcgetpgrp(tty.fileno())!=os.getpgrp():
        tty.close();raise PermissionError('foreground controlling terminal required')
    return tty

def password(tty,prompt):
    original=termios.tcgetattr(tty.fileno());hidden=original.copy();hidden[3]&=~termios.ECHO
    tty.write(prompt);tty.flush()
    try:
        termios.tcsetattr(tty.fileno(),termios.TCSAFLUSH,hidden)
        value=tty.readline(1026)
        if not value.endswith('\n') or not 16<=len(value.rstrip('\n'))<=1024:
            raise ValueError('password must contain16..1024 characters')
        return value.rstrip('\n')
    finally:
        termios.tcsetattr(tty.fileno(),termios.TCSAFLUSH,original);tty.write('\n');tty.flush()

def enroll(tty):
    if VERIFIER.exists() or VERIFIER.is_symlink():raise PermissionError('verifier already exists; no automatic credential replacement')
    DIRECTORY.mkdir(mode=0o700,exist_ok=True)
    info=DIRECTORY.lstat()
    if DIRECTORY.is_symlink() or info.st_uid!=0 or info.st_mode&0o077:
        raise PermissionError('independent root-private verifier directory required')
    first=password(tty,'Choose separate playback password (not your Spotify/sudo password): ')
    if first!=password(tty,'Repeat playback password: '):raise ValueError('passwords differ')
    salt=secrets.token_bytes(32)
    value=dict(user='operator',realm='garage-native-playback',version=1,salt=salt.hex(),iterations=600000,
               password_hash=hashlib.pbkdf2_hmac('sha256',first.encode(),salt,600000).hex())
    fd=os.open(VERIFIER,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'w') as target:json.dump(value,target);target.flush();os.fsync(target.fileno())
    print('Independent verifier enrolled. No service started and no sound authority granted.')

def exchange(connection,payload):
    connection.sendall(json.dumps(payload).encode()+b'\n');data=b''
    while not data.endswith(b'\n'):
        part=connection.recv(4096)
        if not part or len(data)+len(part)>65536:raise RuntimeError('broker disconnected/invalid response')
        data+=part
    result=json.loads(data)
    if not result.get('ok'):raise RuntimeError(result.get('error','broker refused'))
    return result['result']

def user_command(command):
    identity=json.loads(Path('/etc/garage-audio/identity.json').read_text())
    uid=identity['admin_uid']
    import pwd
    user=pwd.getpwuid(uid).pw_name
    return subprocess.run(['/usr/sbin/runuser','-u',user,'--','/usr/bin/env',f'XDG_RUNTIME_DIR=/run/user/{uid}',
                           *command],check=True,timeout=20,cwd=Path(pwd.getpwuid(uid).pw_dir)/'garage-audio')

def user_units(action):
    units=['garage-player.service','garage-dsp.service'] if action=='start' else ['garage-dsp.service']
    return user_command(['/usr/bin/systemctl','--user',action,*units])

def ma_request(player,command):
    """Fixed local Slimproto control; bounded reply, no credentials/queue clear."""
    c=http.client.HTTPConnection('127.0.0.1',9000,timeout=2)
    try:
        c.request('POST','/jsonrpc.js',json.dumps({'id':1,'method':'slim.request','params':[player,command]}),{'Content-Type':'application/json'})
        r=c.getresponse();body=r.read(32769)
        if r.status!=200 or len(body)>32768:raise RuntimeError('bounded MA reply required')
        value=json.loads(body)
        if 'error' in value:raise RuntimeError('MA operation rejected')
        return value.get('result')
    finally:c.close()

def transport_registered():
    result=ma_request('',['players',0,100])
    players=result['players_loop']
    if not isinstance(players,list) or len(players)>100:raise ValueError('bounded inventory required')
    return len([p for p in players if isinstance(p,dict) and p.get('playerid')=='02:00:00:00:21:01'
                and p.get('name')=='Garage Speakers' and p.get('connected') in (1,True)])==1

def stop_pending_music():
    # Pause toggles in installed Slimproto; it is NEVER a safety barrier.
    ma_request('02:00:00:00:21:01',['stop'])
    status=ma_request('02:00:00:00:21:01',['status',0,1])
    # Installed Slimproto omits mode entirely when powered off. Its power-off
    # path calls stop(), and play_url powers on before playback. Require exact
    # connected identity/no pending play; contradictory mode still fails closed.
    powered_off=(isinstance(status,dict) and 'mode' not in status
                 and status.get('power')==0 and status.get('player_connected')==1
                 and status.get('player_name')=='Garage Speakers'
                 and status.get('waitingToPlay')==0)
    if not isinstance(status,dict) or (status.get('mode')!='stop' and not powered_off):
        raise RuntimeError('fresh stopped MA transport required before capture')

def prepare_transport(cancel=None):
    user_command(['/usr/bin/systemctl','--user','start','garage-player.service'])
    deadline=time.monotonic()+15
    while not transport_registered():
        if cancel is not None and cancel.is_set():raise InterruptedError('preparation cancelled')
        if time.monotonic()>=deadline:raise TimeoutError('persistent MA transport registration')
        time.sleep(.1)
    if cancel is not None and cancel.is_set():raise InterruptedError('preparation cancelled')
    stop_pending_music()

def review_operation(tty):
    tty.write('Device: pinned UMC1820, serial D72E24B7; OUT3/4 only; sub/other outputs silent.\n'
              'Listening limit: fixed -60dB, maximum120 seconds, no automatic restart.\n'
              'First stage registers Garage Speakers MUTED. No music starts automatically.\n'
              'Enter stops the window; closing this helper also stops the owned DSP.\n'
              'Type PREPARE to authenticate and prepare the muted player, or Enter to cancel: ')
    tty.flush()
    if tty.readline(64).strip()!='PREPARE':raise PermissionError('operation review cancelled')

def approve_listening(tty,connection=None,generation=None):
    tty.write('Garage Speakers is prepared MUTED. Check/select it in Music Assistant.\n'
              'Keep this terminal open; muted review lasts at most120 seconds.\n'
              'Type LISTEN to authorize OUT3/4 only at -60dB for120 seconds; Enter cancels: ')
    tty.flush()
    if connection is not None:
        deadline=time.monotonic()+120
        while not select.select([tty],[],[],.25)[0]:
            if time.monotonic()>=deadline:raise PermissionError('muted review expired; restart with fresh authentication')
            status=exchange(connection,{'cmd':'status'})
            if not status['muted'] or status['volume_db']!=-60 or status['generation']!=generation:
                raise PermissionError('muted review binding/readback changed')
    if tty.readline(64).strip()!='LISTEN':raise PermissionError('listening cancelled before unmute')

def listen(tty):
    review_operation(tty)  # Scope and stop control are visible BEFORE any startup.
    verifier=HumanVerifier(STATE,str(VERIFIER))
    identity=verifier.authenticate('operator',password(tty,'Independent playback password: '),secrets.token_hex(16))
    if not verifier.current(identity,fresh=True):raise PermissionError('fresh human authentication required')
    connection=None
    try:
        # Shared with the phone path: no pending old PCM may precede capture.
        prepare_transport()
        # Only this authenticated human-owned invocation starts native services.
        subprocess.run(['/usr/bin/systemctl','start','garage-audio-dsp.service'],check=True,timeout=20)
        for attempt in range(50):
            connection=socket.socket(socket.AF_UNIX);connection.settimeout(2)
            try:connection.connect(SOCKET);break
            except (FileNotFoundError,ConnectionRefusedError):
                connection.close();connection=None;time.sleep(.1)
        if connection is None:raise RuntimeError('native control socket did not become ready')
        status=exchange(connection,{'cmd':'status'})
        if not status['muted'] or status['volume_db']!=-60:raise PermissionError('muted startup readback required')
        import pwd
        admin=json.loads(Path('/etc/garage-audio/identity.json').read_text())['admin_uid']
        home=Path(pwd.getpwuid(admin).pw_dir)/'garage-audio'
        user_command([str(home/'venv/bin/python'),'-m','garage_audio.producer','--rearm'])
        user_units('start')  # All existing startup controls initialize muted/-60.
        # Producer initialization increments the broker generation. Observe it
        # only after systemd reports the player startup controls have completed.
        status=exchange(connection,{'cmd':'status'})
        approve_listening(tty,connection,status['generation'])  # Authenticated muted pause, no grant yet.
        if not verifier.current(identity,fresh=True):
            identity=verifier.authenticate('operator',password(tty,'Fresh independent playback password: '),identity.session)
        if not verifier.current(identity,fresh=True):raise PermissionError('fresh authentication required before unmute')
        result=exchange(connection,{'cmd':'stereo-open','generation':status['generation']})
        print('120-second stereo window active at fixed -60dB. Start music in MA. Press Enter to STOP.')
        deadline=time.monotonic()+result['seconds']
        while time.monotonic()<deadline:
            if select.select([tty],[],[],.25)[0]:tty.readline();break
            if not verifier.current(identity):raise PermissionError('human authority revoked/changed')
            exchange(connection,{'cmd':'stereo-heartbeat','generation':result['generation']})
    finally:
        verifier.revoke(identity.session)
        if connection is not None:connection.close()  # Broker independently ends exact DSP generation.
        try:user_units('stop')
        finally:subprocess.run(['/usr/bin/systemctl','stop','garage-audio-dsp.service'],check=True,timeout=20)
        print('Listening window ended. A new window requires fresh independent authentication.')

def main():
    parser=argparse.ArgumentParser(description='Human-only native stereo enrollment/listening; no password arguments')
    parser.add_argument('action',choices=('enroll','listen'))
    args=parser.parse_args()
    with controlling_tty() as tty:
        if args.action=='enroll':enroll(tty)
        else:listen(tty)
