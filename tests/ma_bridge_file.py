"""Real MA auth/action -> kernel-checked Unix gateway -> actual Camilla FILE.

FILE replaces only hardware/native installation identity/MA registration through
the existing explicit FILE fixtures. Root gateway MA peer provenance is NOT mocked.
No production account, token, socket, hardware or physical sound is reachable.
"""
import hashlib,json,os,select,socket,subprocess,sys,threading,time
from pathlib import Path
from garage_audio.ma_bridge import Gateway,process_reference
from garage_audio.speaker_controller import Controller
from garage_audio.human_playback import exchange
import native_playback as fixtures

fixtures.ROOT=Path('/fixture')
fixtures.ENV={**os.environ,'LD_LIBRARY_PATH':'/fixture/tools/libs/usr/lib/x86_64-linux-gnu'}

class FileBackend:
    def __init__(self,fixture):self.f=fixture
    def registration(self):return True # explicitly synthetic FILE registration, NOT live MA player proof
    def start(self,cancel):
        f=self.f;f.peer.realm='phone';g=f.policy.generation()
        f.policy.execute({'cmd':'phone-start','generation':g},f.peer)
        status=f.policy.execute({'cmd':'initialize','generation':g})
        f.policy.execute({'cmd':'phone-prepare','generation':status['generation']},f.peer)
        a,b=socket.socketpair()
        def broker():
            with b:
                data=b''
                while True:
                    byte=b.recv(1)
                    if not byte:return
                    data+=byte
                    if byte==b'\n':
                        try:r={'ok':True,'result':f.policy.execute(json.loads(data),f.peer)}
                        except Exception as e:r={'ok':False,'error':type(e).__name__}
                        b.sendall(json.dumps(r).encode()+b'\n');data=b''
        threading.Thread(target=broker,daemon=True).start();a.settimeout(3)
        return a,exchange(a,{'cmd':'status'})
    def stop(self):
        self.f.policy.execute({'cmd':'speaker-stop'});self.f.process.wait(timeout=3)

def read_line(process,timeout=60):
    deadline=time.monotonic()+timeout;data=b''
    while not data.endswith(b'\n'):
        remaining=deadline-time.monotonic()
        if remaining<=0 or not select.select([process.stdout],[],[],remaining)[0]:raise AssertionError('MA worker milestone timeout')
        byte=os.read(process.stdout.fileno(),1)
        if not byte:raise AssertionError('MA worker exited before expected milestone')
        data+=byte
    return data.decode().strip()

def scenario(mode):
    f=fixtures.NativeFileTests();f.setUp();worker=None
    try:
        # Keep isolated helper alive beyond the genuine 120s expiry scenario.
        f.helper.terminate();f.helper.wait(timeout=3)
        f.helper=subprocess.Popen([sys.executable,'-c','import time;time.sleep(300)'])
        f.peer=fixtures.SyntheticPeer(f.helper)
        log=(f.logs/'ma.stderr.log').open('w')
        worker=subprocess.Popen([sys.executable,'/fixture/tests/ma_bridge_worker.py',mode],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=log,text=True,bufsize=1)
        assert read_line(worker)=='READY'
        provider='/app/venv/lib/python3.14/site-packages/music_assistant/providers/garage_speaker_control'
        assets={n:hashlib.sha256((Path(provider)/n).read_bytes()).hexdigest() for n in ('__init__.py','manifest.json','client.py','connect_authority.py','connect_adapter.py','connect_runtime.py')}
        reference={'process':process_reference(worker.pid),'provider_path':provider,'assets':assets}
        controller=Controller(FileBackend(f));gateway=Gateway(controller,reference)
        path=Path('/run/garage-speakers-ma/control.sock')
        threading.Thread(target=gateway.serve,args=(path,),daemon=True).start()
        for _ in range(50):
            if controller.ma_available:break
            time.sleep(.02)
        # Same-root parent has wrong PID/namespace reference: no generic root shortcut.
        with socket.socket(socket.AF_UNIX) as stranger:
            stranger.settimeout(2);stranger.connect(str(path))
            try:exchange(stranger,{'cmd':'prepare','session':'0'*64})
            except Exception:pass
            else:raise AssertionError('different root process accepted')
        denied=subprocess.run([sys.executable,'-c','import socket;s=socket.socket(socket.AF_UNIX);s.connect("/run/garage-speakers-ma/control.sock")'],preexec_fn=lambda:os.setuid(10001),stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        assert denied.returncode!=0 # actual non-root dashboard-equivalent UID denied by private socket
        worker.stdin.write('GO\n');worker.stdin.flush()
        if mode=='integration':
            assert read_line(worker)=='DENIED';assert controller.state=='stopped';assert f.policy.lease is None
        else:
            assert read_line(worker)=='PREPARED'
            if mode in ('stale','forged'):
                assert read_line(worker)=='DENIED';f.process.wait(timeout=5);assert f.policy.lease is None
            else:
                assert read_line(worker)=='LISTENING';assert controller.state=='listening';assert f.policy.lease is not None
                c=f.client();c.connect()
                try:assert c.volume.main_volume()==-60;assert not c.volume.main_mute()
                finally:c.disconnect()
                if mode=='death':worker.kill();worker.wait(timeout=3)
                else:
                    if mode=='rootdeath':f.helper.kill();f.helper.wait(timeout=3)
                    if mode=='stallstop':f.policy.lock.acquire()
                    worker.stdin.write('GO\n');worker.stdin.flush()
                    try:assert read_line(worker,140 if mode=='expiry' else 20)=='CLOSED'
                    finally:
                        if mode=='stallstop':f.policy.lock.release()
                f.process.wait(timeout=5)
        if worker.poll() is None:assert worker.wait(timeout=20)==0
        print('PASS',mode,'real MA action/gateway kernel identity + actual FILE lifecycle; no hardware',flush=True)
    finally:
        if worker and worker.poll() is None:worker.kill();worker.wait(timeout=3)
        if worker:
            worker.stdout.close();worker.stdin.close()
        if 'log' in locals():log.close()
        f.doCleanups()

if __name__=='__main__':
    for mode in sys.argv[1:] or ['integration','stale','forged','stop','logout','expired','permission','death','rootdeath','stallstop','expiry']:scenario(mode)
