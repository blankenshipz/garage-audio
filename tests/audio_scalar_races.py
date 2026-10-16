"""Only expiry/listener-instance regressions; isolated synthetic authority, no MA."""
import hashlib,json,os,socket,subprocess,tempfile,threading,time,unittest
from pathlib import Path
from unittest.mock import patch
from garage_audio.audio_container import Worker
from garage_audio import audio_receiver as receiver
from garage_audio.audio_qualification import issue

class Expiry(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();root=Path(self.temp.name)
        lease=root/'lease';lease.write_text('{}');lease.chmod(0o444)
        policy=root/'policy'/'attempt.json';store=root/'ledger';issue(policy,store)
        self.w=Worker(root/'run',root/'evidence','scalar',lease,'unit',policy,store)
        self.w.network_valid=lambda:True
        self.w.last_observation=time.monotonic()
    def tearDown(self):
        self.w.closed.set();self.w.halt('unit');self.w.scalar.close();self.temp.cleanup()
    def expire(self):
        self.w.scalar_started=time.monotonic()-301
        end=time.monotonic()+1
        while not self.w.scalar_terminal and time.monotonic()<end:time.sleep(.01)
        self.assertTrue(self.w.scalar_terminal)
        self.assertFalse(self.w.closed.is_set())
    def test_held_start_released_after_expiry(self):
        entered=threading.Event();release=threading.Event();errors=[];spawn=[]
        original=self.w.scalar.writer
        def held(segment):
            fd=original(segment);entered.set();release.wait(2);return fd
        def start():
            try:self.w.start()
            except Exception as e:errors.append(type(e).__name__)
        def child(*args,**kwargs):
            spawn.append(True);raise RuntimeError('must not spawn')
        with patch.object(self.w.scalar,'writer',held),patch('garage_audio.audio_container.subprocess.Popen',side_effect=child):
            thread=threading.Thread(target=start);thread.start();self.assertTrue(entered.wait(1))
            self.expire();release.set();thread.join(2)
        self.assertFalse(thread.is_alive());self.assertEqual(spawn,[]);self.assertTrue(errors)
        with self.assertRaises(Exception):
            self.w.event({'epoch':'unit','sequence':0,'kind':'baseline','state':'playing','active':True,'logged_in':True,'volume':20})
    def test_expiry_while_capture_or_dsp_launch_reaps(self):
        for held_kind in ('capture','dsp'):
            if self.w.scalar_terminal:self.tearDown();self.setUp()
            entered=threading.Event();release=threading.Event();children=[];errors=[]
            popen=subprocess.Popen
            def launch(argv,**kwargs):
                child=popen(['sleep','20'],stdin=kwargs.get('stdin'),stdout=subprocess.PIPE,stderr=subprocess.DEVNULL)
                children.append(child)
                if argv[3]==held_kind:entered.set();release.wait(2)
                return child
            def start():
                try:self.w.start()
                except Exception as e:errors.append(type(e).__name__)
            before=len(list(Path('/proc/self/fd').iterdir()))
            with patch('garage_audio.audio_container.subprocess.Popen',side_effect=launch):
                t=threading.Thread(target=start);t.start();self.assertTrue(entered.wait(1))
                self.expire();release.set();t.join(3)
            self.assertFalse(t.is_alive());self.assertTrue(errors)
            self.assertTrue(all(c.poll() is not None for c in children));self.assertEqual(self.w.children,[])
            for child in children:
                if child.stdout:child.stdout.close()
            self.assertEqual(len(list(Path('/proc/self/fd').iterdir())),before)

class SocketInstances(unittest.TestCase):
    def setUp(self):
        self.assertEqual(os.getuid(),1000,'isolated kernel ownership gate uses UID1000')
        self.pid=os.getpid();self.ns=Path('/proc/self/ns/net').stat().st_ino
        self.sockets=[];self.ws=self.listener('127.0.0.1')
        data=Path('/'+receiver.DATA);data.mkdir(parents=True,exist_ok=True)
        (data/'ws.addr').write_text('127.0.0.1');(data/'ws.port').write_text(str(self.ws.getsockname()[1]))
        self.endpoint=receiver.ReceiverEndpoint(hashlib.sha256(Path('/proc/self/exe').read_bytes()).hexdigest())
    def listener(self,address,port=0,family=socket.AF_INET):
        s=socket.socket(family,socket.SOCK_STREAM);s.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
        s.bind((address,port));s.listen();self.sockets.append(s);return s
    def tearDown(self):
        for s in self.sockets:s.close()
    def test_same_pid_port_ws_replacement_changes_proof(self):
        first=self.endpoint.observe(self.pid,self.ns);port=self.ws.getsockname()[1]
        self.ws.close();self.ws=self.listener('127.0.0.1',port)
        second=self.endpoint.observe(self.pid,self.ns)
        self.assertNotEqual(first,second)
    def test_same_pid_port_pairing_replacement_rejected(self):
        observation=self.endpoint.observe(self.pid,self.ns)
        pairing=self.listener('0.0.0.0');port=pairing.getsockname()[1]
        method=getattr(receiver,'pairing_instances',receiver.qualify_pairing)
        first=method(observation,self.pid,self.ns,'192.0.2.1',[port])
        pairing.close();self.listener('0.0.0.0',port)
        second=method(observation,self.pid,self.ns,'192.0.2.1',[port])
        self.assertNotEqual(first,second)
        if hasattr(receiver,'verify_pairing_instances'):
            with self.assertRaises(PermissionError):receiver.verify_pairing_instances(observation,self.pid,self.ns,'192.0.2.1',first)
    def test_ipv6_wildcard_instances(self):
        observation=self.endpoint.observe(self.pid,self.ns)
        pairing=self.listener('::',family=socket.AF_INET6);port=pairing.getsockname()[1]
        method=getattr(receiver,'pairing_instances',receiver.qualify_pairing)
        records=method(observation,self.pid,self.ns,'192.0.2.1',[port])
        self.assertTrue(records)
        self.assertEqual(records[0]['family'],'ipv6')

if __name__=='__main__':unittest.main(verbosity=2)
