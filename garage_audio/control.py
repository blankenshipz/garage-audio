"""Serialized official-client controls: mute during reload, preserve safe state."""
import argparse
import copy
import fcntl
import json
import os
from pathlib import Path
import time
import subprocess
from camilladsp import CamillaClient
from websocket import setdefaulttimeout
from .profile import load, validate, hardware_config, compile_profile
from .profile import number
from .hardware import bound_device

ROOT = Path(__file__).resolve().parents[1]
setdefaulttimeout(5)

def atomic(path, obj):
    tmp = path.with_suffix('.tmp')
    with tmp.open('w') as f:
        json.dump(obj, f, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
    fd=os.open(path.parent,os.O_RDONLY|os.O_DIRECTORY)
    try:os.fsync(fd)
    finally:os.close(fd)

def store(state):
    path=state/'profile-store.json'
    if path.exists():
        value=json.loads(path.read_text())
        return {'active':validate(value['active']),'known_good':validate(value['known_good'])}
    active=load(state/'active-profile.json') if (state/'active-profile.json').exists() else None
    return {'active':active,'known_good':active}

def active_profile(state):
    return store(state)['active']

def prove_mute(c):
    c.volume.set_main_mute(True);c.volume.set_main_volume(-60)
    if not c.volume.main_mute() or c.volume.main_volume()!=-60:raise RuntimeError('cannot prove mute/-60')

def fail_close(c, stop_producer):
    try:prove_mute(c);return True
    except Exception:stop_producer();return False

def connect_guarded(c, stop_producer):
    try:c.connect()
    except Exception:
        fail_close(c,stop_producer)
        raise

def stop_project_producer():
    return subprocess.run(['systemctl','--user','stop','garage-player','garage-dsp'],check=False,timeout=10,capture_output=True)

def readback(c):
    config = c.config.active() or {}
    return {'volume_db': c.volume.main_volume(), 'muted': c.volume.main_mute(),
            'state': str(c.general.state()), 'clipped': c.status.clipped_samples(),
            'processing_load': c.status.processing_load(),
            'ceiling_db': config.get('devices', {}).get('volume_limit'),
            'crossover': {k: config.get('filters', {}).get(k) for k in ('hp', 'lp')},
            'channel_filters': {k: v for k, v in config.get('filters', {}).items()
                                if k.endswith(('_gain', '_delay')) or '_peq_' in k}}

def apply(c, p, state, alsa, authorized_unmuted=False):
    # Safety policy compiled here, never accept an arbitrary Camilla config.
    p = validate(p)
    candidate = hardware_config(p, alsa)
    apply_graph(c, p, state, candidate, authorized_unmuted)

def apply_graph(c, p, state, candidate, authorized_unmuted=False, persist=True, generation=None):
    """Internal transaction shared by hardware CLI and isolated file tests."""
    previous = c.config.active()
    prior_volume = c.volume.main_volume()
    prior_mute = c.volume.main_mute()
    epoch=generation() if generation else None
    saved=None
    had_store=(state/'profile-store.json').exists()
    committed=False
    prove_mute(c)
    # Commands acknowledge target settings before the400ms DSP ramp completes.
    time.sleep(.45)
    try:
        saved=store(state) if persist else None
        number(prior_volume, -90, -20)
        if not prior_mute and not authorized_unmuted:
            raise ValueError('active attended authorization required to preserve existing unmute')
        if not prior_mute and generation is None:
            raise ValueError('unmute preservation requires owned DSP generation')
        validated = c.config.validate(candidate)
        c.config.set_active(validated)
        for _ in range(30):
            time.sleep(.1)
            current = c.config.active()
            if current and all(current[key] == validated[key] for key in ('filters','mixers','pipeline','devices')):
                if c.general.state().name not in ('RUNNING', 'PAUSED'):
                    continue
                if not c.volume.main_mute() or c.volume.main_volume() != -60:
                    raise RuntimeError('reload mute/-60 boundary not effective')
                if generation and generation()!=epoch:raise RuntimeError('DSP generation changed during transaction')
                if persist:
                    previous_profile=saved['active'] or validate(p)
                    new_store={'active':validate(p),'known_good':previous_profile}
                    committed=True
                    atomic(state/'profile-store.json',new_store)
                    if store(state)!=new_store:raise RuntimeError('durable profile readback mismatch')
                # All durable writes/readback precede the LAST mute restore.
                c.volume.set_main_volume(prior_volume)
                if generation and generation()!=epoch:raise RuntimeError('DSP generation changed before mute restore')
                if not prior_mute:
                    c.volume.set_main_mute(False)
                if c.volume.main_mute() != prior_mute or abs(c.volume.main_volume()-prior_volume) > .01:
                    raise RuntimeError('prior master state could not be restored')
                return
        raise RuntimeError('effective graph did not become ready')
    except Exception:
        # Silence/readback FIRST. Never reload a rollback graph while unmuted.
        prove_mute(c)
        if previous:
            c.config.set_active(previous)
        prove_mute(c)
        if committed:
            if saved['active'] is not None:atomic(state/'profile-store.json',saved)
            elif not had_store:(state/'profile-store.json').unlink(missing_ok=True)
        raise

def edit_profile(p, args):
    p = validate(p)
    if args.cmd == 'channel': p['channels'][args.channel][args.field] = args.value
    elif args.cmd == 'sub-trim': p['sub_trim'] = args.db
    elif args.cmd == 'peq-bypass': p['peq_bypass'] = args.enabled
    elif args.cmd == 'crossover': p['crossover_hz'] = args.hz
    elif args.cmd == 'peq':
        eq = p['channels'][args.channel]['peq']
        if not 0 <= args.index <= len(eq): raise ValueError('PEQ index must replace an existing slot or append at length')
        value = {'freq': args.freq, 'q': args.q, 'gain': args.gain}
        if args.index == len(eq): eq.append(value)
        else: eq[args.index] = value
    elif args.cmd == 'peq-delete':
        eq = p['channels'][args.channel]['peq']
        if not 0 <= args.index < len(eq): raise ValueError('PEQ index absent')
        del eq[args.index]
    elif args.cmd == 'peq-clear': p['channels'][args.channel]['peq'] = []
    else: raise ValueError('unknown edit')
    return validate(p)

def commissioning_window(c, p, state, factory, channel, seconds, port):
    """Attended-only bounded -60dB single-branch window; no sound generation."""
    number(seconds, 1, 5)
    if not c.volume.main_mute(): raise ValueError('commissioning must begin muted')
    original = validate(p)
    test = copy.deepcopy(original)
    for name in test['channels']: test['channels'][name]['mute'] = name != channel
    c.volume.set_main_volume(-60)
    apply_graph(c, test, state, factory(test), persist=False)
    guardian = subprocess.Popen([str(ROOT/'venv/bin/python'), '-m', 'garage_audio.watchdog',
                                 '--port', str(port), '--seconds', str(seconds)],
                                cwd=ROOT, start_new_session=True, stdout=subprocess.PIPE, text=True)
    if guardian.stdout.readline().strip() != 'armed':
        raise RuntimeError('independent automatic-mute guardian did not arm')
    guardian.stdout.close()
    try:
        c.volume.set_main_mute(False)
        time.sleep(seconds)
    finally:
        c.volume.set_main_mute(True);c.volume.set_main_volume(-60)
        apply_graph(c, original, state, factory(original), persist=False)
        guardian.wait(timeout=10)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--port', type=int, default=1234)
    ap.add_argument('--isolated-file-test-state', help='CI only: state below project/state; requires existing FILE/null backend and nonproduction port')
    ap.add_argument('--attended-profile', choices=['PRESENT-GAINS-LOW'],
                    help='required only to preserve a currently unmuted master on successful profile/edit')
    sub = ap.add_subparsers(dest='cmd', required=True)
    sub.add_parser('status')
    sub.add_parser('mute')
    v = sub.add_parser('volume'); v.add_argument('db', type=float)
    u = sub.add_parser('unmute'); u.add_argument('--attended', required=True, choices=['PRESENT-GAINS-LOW'])
    cw = sub.add_parser('commission-window'); cw.add_argument('channel', choices=['left','right','sub'])
    cw.add_argument('--seconds', type=float, default=3)
    cw.add_argument('--attended', required=True, choices=['PRESENT-GAINS-LOW'])
    p = sub.add_parser('profile'); p.add_argument('path')
    sub.add_parser('rollback')
    ch = sub.add_parser('channel'); ch.add_argument('channel', choices=['left','right','sub'])
    ch.add_argument('field', choices=['gain','mute','delay_ms','invert']); ch.add_argument('value', type=json.loads)
    s = sub.add_parser('sub-trim'); s.add_argument('db', type=float)
    b = sub.add_parser('peq-bypass'); b.add_argument('enabled', type=json.loads)
    x = sub.add_parser('crossover'); x.add_argument('hz', type=float)
    eq = sub.add_parser('peq'); eq.add_argument('channel', choices=['left','right','sub'])
    eq.add_argument('index', type=int); eq.add_argument('freq', type=float); eq.add_argument('q', type=float); eq.add_argument('gain', type=float)
    for cmd in ('peq-delete', 'peq-clear'):
        e = sub.add_parser(cmd); e.add_argument('channel', choices=['left','right','sub'])
        if cmd == 'peq-delete': e.add_argument('index', type=int)
    args = ap.parse_args()
    if not args.isolated_file_test_state:
        from .ipc import request
        try:
            payload=vars(args).copy()
            if args.cmd=='profile':payload['profile']=load(args.path)
            print(json.dumps(request(payload)))
        except Exception:
            # Cannot prove broker/DSP mute; close ONLY the project PCM source.
            stop_project_producer()
            raise
        return
    state = ROOT / 'state'
    if args.isolated_file_test_state:
        state=Path(args.isolated_file_test_state).resolve()
        if not state.is_relative_to(ROOT/'state') or args.port == 1234:
            raise ValueError('isolated state must be under project/state with a nonproduction port')
        if args.cmd in ('unmute','commission-window'):
            raise ValueError('hardware commissioning/unmute CLI is not a FILE test command')
    state.mkdir(exist_ok=True)
    with (state / 'control.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        c = CamillaClient('127.0.0.1', args.port)
        stop=stop_project_producer
        try:
            connect_guarded(c,stop)
            isolated_devices=None
            if args.isolated_file_test_state:
                cfg=c.config.active() or {}
                isolated_devices=cfg.get('devices',{})
                if isolated_devices.get('capture',{}).get('type') != 'Stdin' or isolated_devices.get('playback',{}).get('type') != 'File' or isolated_devices['playback'].get('filename') != '/dev/null':
                    raise ValueError('test control refuses all hardware; existing Stdin→FILE /dev/null required')
            if args.cmd == 'mute': c.volume.set_main_mute(True)
            elif args.cmd == 'volume':
                if not -90 <= args.db <= -20: raise ValueError('volume must be -90..-20 dB')
                c.volume.set_main_volume(args.db)
                assert abs(c.volume.main_volume() - args.db) < .01
            elif args.cmd == 'unmute':
                bound_device(state)
                # Hardware commissioning marker is written only after physical acceptance.
                if not (state / 'physical-map-verified').is_file():
                    raise ValueError('physical map not verified; attended commissioning required')
                if c.volume.main_volume() > -20: raise ValueError('unsafe effective volume')
                c.volume.set_main_mute(False)
            elif args.cmd == 'commission-window':
                alsa=bound_device(state)
                current=c.config.active() or {}
                playback=current.get('devices',{}).get('playback',{})
                if playback.get('type') != 'Alsa' or playback.get('device') != alsa:
                    raise ValueError('commissioning requires the explicitly configured UMC hardware graph')
                p=load(state/'active-profile.json')
                commissioning_window(c,p,state,lambda profile:hardware_config(profile,alsa),args.channel,args.seconds,args.port)
            elif args.cmd != 'status':
                alsa = None if isolated_devices else bound_device(state)
                if args.cmd == 'profile': p = load(args.path)
                elif args.cmd == 'rollback': p = store(state)['known_good']
                else:
                    p = edit_profile(active_profile(state), args)
                if isolated_devices:
                    candidate=compile_profile(p,isolated_devices['capture'],isolated_devices['playback'])
                    apply_graph(c,p,state,candidate,authorized_unmuted=args.attended_profile is not None)
                else: apply(c, p, state, alsa, authorized_unmuted=args.attended_profile is not None)
            result = readback(c)
            print(json.dumps(result))
            with (state / 'audit.jsonl').open('a') as audit:
                audit.write(json.dumps({'time': time.time(), 'command': args.cmd, 'effective': result})+'\n')
        except Exception as e:
            muted = 'unknown'
            try:
                c.volume.set_main_mute(True)
                c.volume.set_main_volume(-60)
                muted = c.volume.main_mute()
            except Exception:
                # A dead control connection cannot prove mute. Stop ONLY our
                # player/DSP instead of carrying on with an unknown level.
                subprocess.run(['systemctl','--user','stop','garage-player','garage-dsp'],
                               check=False, timeout=10, capture_output=True)
            with (state / 'audit.jsonl').open('a') as audit:
                audit.write(json.dumps({'time': time.time(), 'command': args.cmd, 'failed': type(e).__name__, 'muted': muted})+'\n')
            raise
        finally: c.disconnect()

if __name__ == '__main__': main()
