"""Run on Linux as root in isolated scratch: publication/crash/protocol fixtures only."""
import json
import os
import socket
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from garage_audio import ma_enrollment_installer as daemon
from garage_audio.ma_enrollment import EnrollmentFailure,SCOPES

TOKEN='isolated-synthetic-token-not-production-12345'
class MA:
    def call(self,command,args=None,bearer=None):
        if command=='auth/me':return {'role':'admin'}
        if command=='auth/scopes':return {'admin':list(SCOPES)}
        return []

@unittest.skipUnless(os.geteuid()==0,'root scratch publication only')
class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1])
        previous=os.umask(0o077)
        try:self.worker=daemon.Installer(self.temp.name,MA())
        finally:os.umask(previous)
        self.payload={'op':'install','enrollment_id':'fixture-request-0001','bearer':TOKEN}
    def tearDown(self):self.worker.close();self.temp.cleanup()
    def test_two_private_readers_repeated_install(self):
        self.assertEqual(self.worker.handle(self.payload)['state'],'ready')
        before={n:os.stat('token',dir_fd=fd).st_ino for n,fd in self.worker.targets.items()}
        self.assertEqual(self.worker.handle(self.payload)['state'],'ready')
        for name,uid in daemon.TARGETS:
            s=os.stat('token',dir_fd=self.worker.targets[name]);self.assertEqual(s.st_uid,uid)
            self.assertEqual(s.st_mode&0o777,0o600);self.assertEqual(s.st_nlink,1);self.assertEqual(s.st_ino,before[name])
    def test_crash_between_publications_recovers_no_false_ready(self):
        original=daemon.write
        def fail(fd,name,data,uid=0):
            if uid==1001:raise OSError('fixture interruption')
            original(fd,name,data,uid)
        with patch.object(daemon,'write',fail):
            with self.assertRaises(OSError):self.worker.handle(self.payload)
        self.assertEqual(self.worker.transaction()['phase'],'prepared')
        self.assertEqual(self.worker.status()['state'],'ready')
        self.assertEqual(self.worker.transaction()['phase'],'committed')
    def test_expiry_and_scope_loss_never_ready(self):
        self.worker.handle(self.payload)
        class Expired:
            def call(self,*args,**kwargs):raise EnrollmentFailure('ma_authentication_failed')
        self.worker.ma=Expired();result=self.worker.status()
        self.assertEqual(result['state'],'setup_required');self.assertFalse(result['scope_verified'])
        class Missing(MA):
            def call(self,command,args=None,bearer=None):return {'admin':[]} if command=='auth/scopes' else super().call(command,args,bearer)
        self.worker.ma=Missing();self.assertEqual(self.worker.status()['code'],'ma_scope_required')
    def test_symlink_hardlink_and_caller_path_rejected(self):
        fd=self.worker.targets['app'];os.symlink('/etc/passwd','token',dir_fd=fd)
        with self.assertRaises((OSError,ValueError)):self.worker.handle(self.payload)
        os.unlink('token',dir_fd=fd)
        with self.assertRaises(EnrollmentFailure):self.worker.handle({**self.payload,'path':'/etc/passwd'})
        self.worker.handle(self.payload)
        os.link('token','alias',src_dir_fd=fd,dst_dir_fd=fd)
        with self.assertRaises(ValueError):daemon.read(fd,'token',1000)
    def test_peer_uid_not_authority(self):
        a,b=socket.socketpair()
        try:
            with self.assertRaises(PermissionError):daemon.authorize(a,{'uid':1000})
        finally:a.close();b.close()
    def test_real_uid1000_peer_without_exact_process_admission_denied(self):
        os.chmod(self.temp.name,0o711)
        path=self.temp.name+'/probe.sock'
        listener=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM);listener.bind(path)
        os.chown(path,0,1000);os.chmod(path,0o660);listener.listen(1);listener.settimeout(3)
        pid=os.fork()
        if pid==0:
            try:
                os.setgid(1000);os.setuid(1000)
                with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as peer:
                    peer.connect(path);peer.recv(1)
                os._exit(0)
            except Exception:os._exit(1)
        try:
            connection,_=listener.accept()
            with connection:
                with self.assertRaises(PermissionError):daemon.authorize(connection,{'uid':1000,'pid':0})
            self.assertEqual(os.waitpid(pid,0)[1],0)
        finally:listener.close()

if __name__=='__main__':unittest.main()
