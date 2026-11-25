"""Synthetic: scripts/container-transport socket handling against fake pipewire (no Docker/PipeWire)."""
import os,socket,stat,subprocess,tempfile,unittest
from pathlib import Path

SCRIPT=Path(__file__).parents[1]/'scripts/container-transport'
PIPEWIRE='''#!/usr/bin/env python3
import os,socket,sys,time
d=os.environ['PIPEWIRE_RUNTIME_DIR'];sock=d+'/garage-pipewire'
mode=os.environ['FAKE_PW_MODE']
open(d+'/launch-state','w').write('stale-present' if os.path.exists(sock) or os.path.exists(sock+'.lock') else 'clean')
if mode=='die':sys.exit(3)
time.sleep(.4)  # PipeWire unlinks/recreates its socket shortly after start
s=socket.socket(socket.AF_UNIX);s.bind(sock);os.chmod(sock,0o755)
open(d+'/new-inode','w').write(str(os.stat(sock).st_ino))
time.sleep(30)
'''
CHMOD='''#!/bin/sh
# Simulates a chmod that does not take effect on the socket.
case "$2" in */garage-pipewire) exec /bin/chmod 0755 "$2";; esac
exec /bin/chmod "$@"
'''

class Transport(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.run_dir=self.root/'run';self.shims=self.root/'bin'
        self.run_dir.mkdir();self.shims.mkdir()
        for name in ('wireplumber','squeezelite'):self.shim(name,'#!/bin/sh\nexit 0\n')
        self.shim('dbus-run-session','#!/bin/sh\nshift 2\nexec "$@"\n')
        self.shim('pipewire',PIPEWIRE)
    def shim(self,name,text):
        path=self.shims/name;path.write_text(text);path.chmod(0o755)
    def stale(self):
        sock=self.run_dir/'garage-pipewire'
        s=socket.socket(socket.AF_UNIX);s.bind(str(sock));s.close()  # leftover socket inode
        os.chmod(sock,0o660);(self.run_dir/'garage-pipewire.lock').write_text('')
        return os.stat(sock).st_ino
    def run_script(self,mode='ok'):
        env=dict(os.environ,PATH=f'{self.shims}:{os.environ["PATH"]}',GARAGE_TRANSPORT_RUNTIME_DIR=str(self.run_dir),
            GARAGE_TRANSPORT_CONTROL_GID=str(os.getgid()),FAKE_PW_MODE=mode)
        return subprocess.run(['sh',str(SCRIPT)],env=env,capture_output=True,text=True,timeout=40)
    def test_stale_socket_and_lock_removed_before_pipewire_and_new_socket_verified(self):
        stale=self.stale()
        result=self.run_script()
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual((self.run_dir/'launch-state').read_text(),'clean')
        sock=self.run_dir/'garage-pipewire';info=os.stat(sock)
        self.assertTrue(stat.S_ISSOCK(info.st_mode))
        self.assertEqual(str(info.st_ino),(self.run_dir/'new-inode').read_text())
        self.assertEqual((stat.S_IMODE(info.st_mode),info.st_gid),(0o660,os.getgid()))
        self.assertFalse((self.run_dir/'garage-pipewire.lock').exists())
    def test_wrong_final_mode_fails_loudly(self):
        self.shim('chmod',CHMOD)
        result=self.run_script()
        self.assertEqual(result.returncode,1)
        self.assertIn('mode 0660',result.stderr)
    def test_pipewire_exit_before_socket_fails_loudly_even_with_stale_socket(self):
        self.stale()
        result=self.run_script('die')
        self.assertEqual(result.returncode,1)
        self.assertIn('exited before creating',result.stderr)

if __name__=='__main__':
    unittest.main()
