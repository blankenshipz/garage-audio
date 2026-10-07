"""Serialized official-client controls. Every profile transaction stays muted."""
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
from .profile import load, validate, hardware_config

ROOT = Path(__file__).resolve().parents[1]
setdefaulttimeout(5)

def atomic(path, obj):
    tmp = path.with_suffix('.tmp')
    with tmp.open('w') as f:
        json.dump(obj, f, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)

def readback(c):
    config = c.config.active() or {}
    return {'volume_db': c.volume.main_volume(), 'muted': c.volume.main_mute(),
            'state': str(c.general.state()), 'clipped': c.status.clipped_samples(),
            'processing_load': c.status.processing_load(),
            'ceiling_db': config.get('devices', {}).get('volume_limit'),
            'channel_filters': {k: v for k, v in config.get('filters', {}).items()
                                if k.endswith(('_gain', '_delay')) or '_peq_' in k}}

def apply(c, p, state, alsa):
    # Safety policy compiled here, never accept an arbitrary Camilla config.
    p = validate(p)
    candidate = hardware_config(p, alsa)
    apply_graph(c, p, state, candidate)

def apply_graph(c, p, state, candidate):
    """Internal transaction shared by hardware CLI and isolated file tests."""
    previous = c.config.active()
    c.volume.set_main_mute(True)
    c.volume.set_main_volume(-60)
    try:
        validated = c.config.validate(candidate)
        c.config.set_active(validated)
        for _ in range(30):
            time.sleep(.1)
            current = c.config.active()
            if current and current['filters'] == validated['filters'] and current['mixers'] == validated['mixers']:
                if c.general.state().name not in ('RUNNING', 'PAUSED'):
                    continue
                assert c.volume.main_mute() and c.volume.main_volume() == -60
                if (state / 'active-profile.json').exists():
                    atomic(state / 'known-good-profile.json', load(state / 'active-profile.json'))
                atomic(state / 'active-profile.json', p)
                return
        raise RuntimeError('effective graph did not become ready')
    except Exception:
        if previous:
            c.config.set_active(previous)
        c.volume.set_main_mute(True)
        c.volume.set_main_volume(-60)
        raise

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--port', type=int, default=1234)
    sub = ap.add_subparsers(dest='cmd', required=True)
    sub.add_parser('status')
    sub.add_parser('mute')
    v = sub.add_parser('volume'); v.add_argument('db', type=float)
    u = sub.add_parser('unmute'); u.add_argument('--attended', required=True, choices=['PRESENT-GAINS-LOW'])
    p = sub.add_parser('profile'); p.add_argument('path')
    sub.add_parser('rollback')
    ch = sub.add_parser('channel'); ch.add_argument('channel', choices=['left','right','sub'])
    ch.add_argument('field', choices=['gain','mute','delay_ms','invert']); ch.add_argument('value', type=json.loads)
    s = sub.add_parser('sub-trim'); s.add_argument('db', type=float)
    b = sub.add_parser('peq-bypass'); b.add_argument('enabled', type=json.loads)
    args = ap.parse_args()
    state = ROOT / 'state'; state.mkdir(exist_ok=True)
    with (state / 'control.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        c = CamillaClient('127.0.0.1', args.port); c.connect()
        try:
            if args.cmd == 'mute': c.volume.set_main_mute(True)
            elif args.cmd == 'volume':
                if not -90 <= args.db <= -20: raise ValueError('volume must be -90..-20 dB')
                c.volume.set_main_volume(args.db)
                assert abs(c.volume.main_volume() - args.db) < .01
            elif args.cmd == 'unmute':
                # Hardware commissioning marker is written only after physical acceptance.
                if not (state / 'physical-map-verified').is_file():
                    raise ValueError('physical map not verified; attended commissioning required')
                if c.volume.main_volume() > -20: raise ValueError('unsafe effective volume')
                c.volume.set_main_mute(False)
            elif args.cmd != 'status':
                alsa = (state / 'alsa-device').read_text().strip()
                if args.cmd == 'profile': p = load(args.path)
                elif args.cmd == 'rollback': p = load(state / 'known-good-profile.json')
                else:
                    p = load(state / 'active-profile.json')
                    if args.cmd == 'channel': p['channels'][args.channel][args.field] = args.value
                    elif args.cmd == 'sub-trim': p['sub_trim'] = args.db
                    elif args.cmd == 'peq-bypass': p['peq_bypass'] = args.enabled
                apply(c, p, state, alsa)
            result = readback(c)
            print(json.dumps(result))
            with (state / 'audit.jsonl').open('a') as audit:
                audit.write(json.dumps({'time': time.time(), 'command': args.cmd, 'effective': result})+'\n')
        except Exception as e:
            muted = 'unknown'
            try:
                c.volume.set_main_mute(True)
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
