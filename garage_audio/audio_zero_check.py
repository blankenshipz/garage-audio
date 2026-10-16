"""Operator-only bounded hardware preflight: synthetic ZERO input, mute/-60, no grant.

No observer events, MA call, source capture, real provider PCM or gain restoration.
Requires the same root device policy/exclusive lease and live network generation.
"""
import json,os,subprocess,threading,time
from pathlib import Path
from .audio_boot_wait import network_valid

def main():
    from .audio_device import DeviceLease,hardware_graph
    from .audio_container import line_graph
    from camilladsp import CamillaClient
    import yaml
    authority=os.environ['GARAGE_AUDIO_NETWORK_LEASE'];generation=os.environ['GARAGE_AUDIO_NETWORK_GENERATION']
    if not network_valid(authority,generation):raise PermissionError('live immutable network generation required')
    root=Path('/evidence');root.mkdir(exist_ok=True)
    lease=DeviceLease();dsp=None;stop=threading.Event();phase='config'
    try:
        graph=hardware_graph(line_graph('/not-used-provider-pcm'),lease.verify())
        path=root/'zero-preflight.yaml';path.write_text(yaml.safe_dump(graph))
        env=dict(os.environ,GARAGE_AUDIO_PARENT_PID=str(os.getpid()),LD_LIBRARY_PATH='/opt/garage-audio/dsp/lib')
        phase='dsp_spawn'
        with (root/'zero-preflight.stderr').open('ab',buffering=0) as log:
            dsp=subprocess.Popen(['python','-m','garage_audio.audio_child','dsp','-v','-a','127.0.0.1','-p','1234','--gain=-60','--mute',str(path)],
                stdin=subprocess.PIPE,stdout=subprocess.DEVNULL,stderr=log,env=env,pass_fds=(lease.fd,))
        def feed():
            try:
                while not stop.wait(.01):dsp.stdin.write(bytes(8192));dsp.stdin.flush()
            except (OSError,ValueError):pass
        threading.Thread(target=feed,daemon=True).start()
        phase='muted_readback';deadline=time.monotonic()+4;observed=None;last_errno=None
        while time.monotonic()<deadline:
            if dsp.poll() is not None:raise RuntimeError('zero preflight DSP exited')
            if not network_valid(authority,generation):raise PermissionError('network generation lost during zero preflight')
            lease.verify();client=CamillaClient('127.0.0.1',1234)
            try:
                client.connect()
                if not client.volume.main_mute() or client.volume.main_volume()!=-60:raise PermissionError('zero preflight mute/-60 required')
                def required(expected,actual):
                    if isinstance(expected,dict):return isinstance(actual,dict) and all(k in actual and required(v,actual[k]) for k,v in expected.items())
                    if isinstance(expected,list):return isinstance(actual,list) and len(actual)==len(expected) and all(required(a,b) for a,b in zip(expected,actual))
                    return expected==actual
                active=client.config.active()
                headroom=active['filters']['headroom']['parameters']
                if (not all(required(graph[k],active[k]) for k in ('devices','filters','mixers','pipeline'))
                        or any(step.get('bypassed') is True for step in active['pipeline'])
                        or headroom.get('scale') not in (None,'dB') or headroom.get('inverted') is True):
                    raise PermissionError('actual zero preflight routing graph differs')
                time.sleep(.1);lease.verify()
                if not network_valid(authority,generation) or not client.volume.main_mute() or client.volume.main_volume()!=-60:
                    raise PermissionError('stable zero preflight lifetime/mute readback lost')
                observed={'sound_authority':False,'synthetic_zero_input':True,'muted':True,'volume_db':-60,
                          'output_gain_db':-6,'out3_4_only':True,'unused_outputs_zero':True,'device_identity_verified':True,
                          'network_generation':generation,'physical_silence_verified':False}
                break
            except PermissionError:raise
            except OSError as error:
                last_errno=getattr(error,'errno',None);time.sleep(.05)
            finally:client.disconnect()
        if observed is None:
            (root/'zero-preflight-readback-failure.json').write_text(json.dumps({'phase':phase,'last_errno':last_errno,'listening_port_seen':any(':04D2 ' in line and ' 0A ' in line for line in Path('/proc/net/tcp').read_text().splitlines()),'sound_authority':False}))
            raise TimeoutError('zero preflight native readback deadline')
        (root/'zero-preflight.json').write_text(json.dumps(observed,indent=2));print(json.dumps(observed))
    except BaseException as error:
        # Only phase/type/errno; never arbitrary exception text or streaming data.
        (root/'zero-preflight-failure.json').write_text(json.dumps({'phase':phase,'error':type(error).__name__,'errno':getattr(error,'errno',None),'sound_authority':False}))
        raise
    finally:
        stop.set()
        if dsp is not None:
            if dsp.poll() is None:
                dsp.terminate()
                try:dsp.wait(timeout=3)
                except subprocess.TimeoutExpired:dsp.kill();dsp.wait(timeout=2)
            (root/'zero-preflight.exit').write_text(str(dsp.returncode))
        lease.close()

if __name__=='__main__':main()
