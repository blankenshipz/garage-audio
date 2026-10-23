"""Exact-owner scanner regression: disappearing unrelated FDs must not hide a real owner."""
import hashlib,os,socket,tempfile,threading,unittest
from pathlib import Path
from unittest.mock import patch
from garage_audio import audio_receiver as receiver

class ReceiverScan(unittest.TestCase):
    def setUp(self):
        self.assertEqual(os.getuid(),1000,'isolated Linux UID1000 kernel fixture required')
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.listener=socket.socket();self.listener.bind(('127.0.0.1',0));self.listener.listen()
        self.addCleanup(self.listener.close)
        self.pid=os.getpid();self.ns=Path('/proc/self/ns/net').stat().st_ino
        self.endpoint_dir=Path(self.temp.name)
        (self.endpoint_dir/'ws.addr').write_text('127.0.0.1')
        (self.endpoint_dir/'ws.port').write_text(str(self.listener.getsockname()[1]))
        self.pin=hashlib.sha256(Path('/proc/self/exe').read_bytes()).hexdigest()
        self.data=patch.object(receiver,'DATA',str(self.endpoint_dir).lstrip('/'));self.data.start();self.addCleanup(self.data.stop)
        self.endpoint=receiver.ReceiverEndpoint(self.pin)

    def test_unrelated_closed_fd_does_not_hide_actual_pinned_listener_owner(self):
        original_iterdir=Path.iterdir;original_readlink=os.readlink
        vanished=Path(f'/proc/{self.pid}/fd/999999')
        def iterdir(path):
            rows=list(original_iterdir(path))
            if path==Path(f'/proc/{self.pid}/fd'):return iter([vanished,*rows])
            return iter(rows)
        def readlink(path,*args,**kwargs):
            if Path(path)==vanished:raise FileNotFoundError('fixture unrelated FD closed')
            return original_readlink(path,*args,**kwargs)
        with patch.object(Path,'iterdir',iterdir),patch.object(receiver.os,'readlink',side_effect=readlink):
            observed=self.endpoint.observe(self.pid,self.ns)
        self.assertEqual(observed['pid'],self.pid)
        self.assertEqual(observed['binary_sha256'],self.pin)

    def test_missing_owner_and_ambiguous_owners_still_fail_closed(self):
        for copies,reason in ((0,'owner_absent'),(2,'owner_ambiguous')):
            original_glob=Path.glob
            def glob(path,pattern):
                if path==Path('/proc') and pattern=='[0-9]*':return iter([Path(f'/proc/{self.pid}')]*copies)
                return original_glob(path,pattern)
            with self.subTest(reason=reason),patch.object(Path,'glob',glob):
                with self.assertRaisesRegex(PermissionError,reason):self.endpoint.observe(self.pid,self.ns)

    def test_wrong_binary_pin_and_actual_listener_close_are_not_tolerated(self):
        with self.assertRaisesRegex(PermissionError,'binary pin differs'):
            receiver.ReceiverEndpoint('0'*64).observe(self.pid,self.ns)
        self.listener.close()
        with self.assertRaisesRegex(PermissionError,'owner_absent'):
            self.endpoint.observe(self.pid,self.ns)

    def test_listener_replacement_between_owner_proofs_is_rejected(self):
        original=receiver.listening_sockets;calls=0
        def changed(*args):
            nonlocal calls
            calls+=1;rows=original(*args)
            if calls>1:
                rows=[dict(r,socket_inode='fixture-new-instance') if r['port']==self.listener.getsockname()[1] else r for r in rows]
            return rows
        with patch.object(receiver,'listening_sockets',side_effect=changed):
            with self.assertRaisesRegex(PermissionError,'listener_changed'):self.endpoint.observe(self.pid,self.ns)

    def test_real_fd_churn_keeps_exact_listener_observable(self):
        stop=threading.Event()
        def churn():
            while not stop.is_set():
                fd=os.open('/dev/null',os.O_RDONLY);os.close(fd)
        thread=threading.Thread(target=churn);thread.start()
        try:
            baseline=self.endpoint.observe(self.pid,self.ns)
            for _ in range(60):self.assertEqual(self.endpoint.observe(self.pid,self.ns),baseline)
        finally:stop.set();thread.join(2)

    def test_unrelated_inaccessible_namespace_is_never_inspected(self):
        other=Path('/proc/99999999');original_glob=Path.glob;original_stat=os.stat
        def glob(path,pattern):
            if path==Path('/proc'):return iter([other,Path(f'/proc/{self.pid}')])
            return original_glob(path,pattern)
        def stat(path,*args,**kwargs):
            if Path(path)==other:return original_stat(f'/proc/{self.pid}')
            if Path(path)==other/'ns/net':raise PermissionError('fixture unrelated namespace EACCES')
            return original_stat(path,*args,**kwargs)
        original_iterdir=Path.iterdir
        def iterdir(path):
            if path==other/'fd':return iter(())
            return original_iterdir(path)
        with patch.object(Path,'glob',glob),patch.object(receiver.os,'stat',side_effect=stat),patch.object(Path,'iterdir',iterdir):
            self.assertEqual(self.endpoint.observe(self.pid,self.ns)['pid'],self.pid)

    def test_actual_owner_namespace_and_fd_table_denial_fail_closed(self):
        original_stat=os.stat;original_iterdir=Path.iterdir
        def stat(path,*args,**kwargs):
            if Path(path)==Path(f'/proc/{self.pid}/ns/net'):raise PermissionError('fixture owner namespace EACCES')
            return original_stat(path,*args,**kwargs)
        with patch.object(receiver.os,'stat',side_effect=stat):
            with self.assertRaises(PermissionError):self.endpoint.observe(self.pid,self.ns)
        def denied(path):
            if path==Path(f'/proc/{self.pid}/fd'):raise PermissionError('fixture FD table EACCES')
            return original_iterdir(path)
        with patch.object(Path,'iterdir',denied):
            with self.assertRaises(PermissionError):self.endpoint.observe(self.pid,self.ns)

    def test_pid_reuse_and_final_socket_loss_fail_closed(self):
        real=receiver.birth(self.pid);calls=0
        def reused(pid):
            nonlocal calls
            calls+=1
            return real if calls==1 else real+'changed'
        with patch.object(receiver,'birth',side_effect=reused):
            with self.assertRaisesRegex(PermissionError,'owner_changed'):self.endpoint.observe(self.pid,self.ns)
        original=receiver.owns_socket;hits=0
        def lost(proc,inode):
            nonlocal hits
            value=original(proc,inode)
            if value:hits+=1
            return value and hits==1
        with patch.object(receiver,'owns_socket',side_effect=lost):
            with self.assertRaisesRegex(PermissionError,'owner_changed'):self.endpoint.observe(self.pid,self.ns)

if __name__=='__main__':unittest.main()
