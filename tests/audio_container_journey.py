"""Runs inside admitted transport container: real MA/player/PW/DSP FILE journey.
Receiver event seam is synthetic; no real Soloist/Spotify/phone claim.
"""
import array
import json
import math
from pathlib import Path
import threading
import time
import urllib.request
from garage_audio.audio_container import request

ROOT = Path('/run/garage-audio')
EVIDENCE = Path('/evidence')


def api(path, value=None):
    data = None if value is None else json.dumps(value).encode()
    req = urllib.request.Request('http://music-assistant:18088'+path, data=data,
        headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=5) as response:
        return json.load(response)


def wait_state(expected):
    for _ in range(100):
        state = api('/fixture/state')
        if state['state'] == expected:
            return state
        time.sleep(.1)
    raise AssertionError(f'actual MA state {expected} absent')


def main():
    for _ in range(150):
        try:
            state = api('/fixture/state')
            if state['available']:
                break
        except Exception:
            pass
        time.sleep(.2)
    else:
        raise AssertionError('real MA/Squeezelite registration absent')
    epoch = 'synthetic-receiver-1'
    seq = 0
    receiver = {'state': 'playing', 'active': True, 'logged_in': True, 'volume': 100}
    lock = threading.Lock()
    heartbeat = threading.Event()
    stopped = threading.Event()
    def event(kind, override=None):
        nonlocal seq
        with lock:
            # Explicit synthetic trusted-observer seam: mint the same one-use
            # worker ticket as the supported observer, not snapshot authority.
            ticket = None
            if kind == 'delta' and receiver['state'] == 'playing' and not override:
                seq += 1
                prepared = request(ROOT/'events/event.sock',
                    dict(receiver, epoch=epoch, sequence=seq, kind='prepare'))
                ticket = prepared['pending_ticket']
            seq += 1
            value = dict(receiver, epoch=epoch, sequence=seq, kind=kind)
            if ticket:
                value['ticket'] = ticket
            value.update(override or {})
            return request(ROOT/'events/event.sock', value)
    def keepalive():
        while not stopped.wait(.5):
            if heartbeat.is_set():
                event('heartbeat')
    keeper = threading.Thread(target=keepalive)
    keeper.start()
    try:
        baseline = event('baseline')
        assert baseline['state'] == 'off' and not baseline['owned_children']
        assert api('/fixture/state')['available']
        event('heartbeat')
        assert not list(EVIDENCE.glob('*.f32')), 'snapshot/heartbeat started output'
        print('PASS trulyOFF_registration_alive_snapshotdeny', flush=True)
        api('/fixture/command', {'op': 'volume', 'value': 100})
        api('/fixture/command', {'op': 'play'})
        wait_state('playing')
        event('delta')
        heartbeat.set()
        time.sleep(5)
        api('/fixture/command', {'op': 'pause'})
        paused = wait_state('paused')
        receiver['state'] = 'paused'
        output = event('delta')
        assert output['state'] == 'paused' and not output['owned_children'], output
        p0 = paused['elapsed']
        time.sleep(2)
        assert abs(api('/fixture/state')['elapsed']-p0) < 1.5, 'paused MA clock advances'
        files = list(EVIDENCE.glob('*.f32'))
        assert len(files) == 1
        samples = array.array('f', files[0].read_bytes())
        assert len(samples) > 48000*12
        peaks = [max(abs(v) for v in samples[c::12]) for c in range(12)]
        assert all(peaks[c] == 0 for c in range(12) if c not in (2, 3)), peaks
        # Actual Squeezelite unity path and fixed Camilla headroom, not a fake direct PCM proof.
        assert abs(peaks[2] - 10000/32768*10**(-6/20)) < .012, peaks
        assert abs(peaks[3] - 5000/32768*10**(-6/20)) < .012, peaks
        print('PASS actualMA_Squeezelite_PipeWire_CamillaFILE_gain_routes', json.dumps(peaks), flush=True)
        api('/fixture/command', {'op': 'volume', 'value': 50})
        api('/fixture/command', {'op': 'play_resume'})
        resumed = wait_state('playing')
        assert resumed['volume'] == 50 and resumed['elapsed'] >= p0-1.5
        receiver.update(state='playing', volume=50)
        event('delta')
        time.sleep(3)
        api('/fixture/command', {'op': 'stop'})
        wait_state('idle')
        receiver['state'] = 'idle'
        stopped_state = event('delta')
        assert stopped_state['state'] == 'off' and not stopped_state['owned_children']
        assert api('/fixture/state')['available']
        second = sorted(EVIDENCE.glob('*.f32'))[-1]
        lower = array.array('f', second.read_bytes())
        lower_peaks = [max(abs(v) for v in lower[c::12]) for c in range(12)]
        assert 0 < lower_peaks[2] < peaks[2] and 0 < lower_peaks[3] < peaks[3], lower_peaks
        assert all(lower_peaks[c] == 0 for c in range(12) if c not in (2, 3))
        print('PASS pause_clock_resume_position_volume_stop_registration', flush=True)
        receiver['state'] = 'playing'
        event('baseline')
        event('heartbeat')
        assert len(list(EVIDENCE.glob('*.f32'))) == 2
        epoch = 'synthetic-receiver-2'
        event('baseline')
        try:
            event('delta', {'epoch': 'synthetic-receiver-1'})
        except RuntimeError:
            pass
        else:
            raise AssertionError('old epoch admitted')
        event('baseline')
        api('/fixture/command', {'op': 'play'})
        wait_state('playing')
        event('delta')
        heartbeat.clear()
        time.sleep(3.5)
        expired = event('heartbeat')  # readback without Stop/baseline masking the watchdog
        assert expired['state'] == 'off' and not expired['owned_children'], expired
        assert expired['reason'] == 'watchdog_or_deadline', expired
        event('baseline')
        assert len(list(EVIDENCE.glob('*.f32'))) == 3
        print('PASS reconnect_snapshotdeny_oldepochdeny_watchdog', flush=True)
        print('COVERAGE actual_MA_transport_PipeWire_CamillaFILE; synthetic_receiver_events; no_Soloist_Spotify_device_sound', flush=True)
    finally:
        heartbeat.clear()
        stopped.set()
        keeper.join()
        try:
            api('/fixture/command', {'op': 'stop'})
        finally:
            receiver['state'] = 'idle'
            event('baseline')


if __name__ == '__main__':
    main()
