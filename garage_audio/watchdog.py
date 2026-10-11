"""Independent attended-window fail-safe. Emergency mute is never an unmute."""
import argparse
import json
import subprocess
import time
from camilladsp import CamillaClient
from websocket import setdefaulttimeout
from .control import ROOT

def main():
    a=argparse.ArgumentParser();a.add_argument('--port',type=int,required=True);a.add_argument('--seconds',type=float,required=True);args=a.parse_args()
    if not 1 <= args.seconds <= 5:raise SystemExit('bounded1..5seconds required')
    setdefaulttimeout(2)
    print('armed',flush=True)
    time.sleep(args.seconds)
    stopped=False
    c=CamillaClient('127.0.0.1',args.port)
    try:
        c.connect();c.volume.set_main_mute(True);c.volume.set_main_volume(-60)
        muted=c.volume.main_mute()
        if not muted:raise RuntimeError('mute not effective')
    except Exception:
        # Stop ONLY our stream producer/player; dedicated daemon receives EOF.
        subprocess.run(['systemctl','--user','stop','garage-dsp'],check=False,capture_output=True,timeout=5)
        muted='unknown';stopped=True
    finally:c.disconnect()
    with (ROOT/'state/audit.jsonl').open('a') as f:
        f.write(json.dumps({'time':time.time(),'command':'commissioning-emergency-mute','muted':muted,'producer_stopped':stopped})+'\n')

if __name__=='__main__':main()
