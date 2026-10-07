"""Real own FILE/null child effects; no root, hardware or fake termination."""
import json
import os
from pathlib import Path
import signal
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
from garage_audio.control import ROOT,atomic
from garage_audio.daemon import create_capture_fifo
from garage_audio.producer import register,stop,identity

DUMMY="import os,time; b=b'\\0'*8192\nwhile True: os.write(1,b); time.sleep(.01)"

class ProducerTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(dir=ROOT/'state');self.state=Path(self.tmp.name)/'state';self.state.mkdir()
        self.children=[];self.files=[]
    def tearDown(self):
        for child in self.children:
            if child.poll() is None:child.kill()
            child.wait(timeout=3)
        for file in self.files:file.close()
        self.tmp.cleanup()
    def launch(self):
        output=(self.state/'pcm.raw').open('wb');self.files.append(output)
        child,record=register(self.state,[sys.executable,'-c',DUMMY],output);self.children.append(child)
        for _ in range(30):
            if (self.state/'pcm.raw').stat().st_size:break
            time.sleep(.01)
        self.assertGreater((self.state/'pcm.raw').stat().st_size,0)
        return child,record
    def verify_stopped(self,child):
        self.assertEqual(child.wait(timeout=3),-signal.SIGKILL)
        size=(self.state/'pcm.raw').stat().st_size;time.sleep(.06)
        self.assertEqual((self.state/'pcm.raw').stat().st_size,size)
        with self.assertRaises(RuntimeError):self.launch()
    def test_failed_manager_independent_verified_pcm_stop(self):
        child,_=self.launch()
        result=stop(self.state,manager=['/bin/sh','-c','exit 7'])
        self.assertEqual(result['manager'],'exit7');self.assertEqual(result['producer'],'exit_verified')
        self.verify_stopped(child)
    def test_timeout_manager_independent_verified_pcm_stop(self):
        child,_=self.launch()
        result=stop(self.state,manager=[sys.executable,'-c','import time;time.sleep(10)'],timeout=.1)
        self.assertEqual(result['manager'],'timeout');self.assertEqual(result['producer'],'exit_verified')
        self.verify_stopped(child)
    def test_absent_manager_independent_verified_pcm_stop(self):
        child,_=self.launch()
        result=stop(self.state,manager=[str(self.state/'absent-manager')])
        self.assertEqual(result['manager'],'unavailable');self.verify_stopped(child)
    def test_actual_unreachable_ipc_cli_and_user_manager_stop_real_pcm(self):
        from garage_audio import control
        from garage_audio.ipc import request as real_request
        child,_=self.launch()
        # Path/environment seams force real missing Unix sockets, not a mocked
        # shutdown helper. No real host service manager receives this request.
        def unavailable(payload):return real_request(payload,path=str(self.state/'absent-policy.sock'),timeout=.1)
        with patch.object(control,'ROOT',self.state.parent),patch('sys.argv',['control','status']),patch('garage_audio.ipc.request',unavailable),patch.dict(os.environ,{'DBUS_SESSION_BUS_ADDRESS':'unix:path='+str(self.state/'absent-bus.sock'),'XDG_RUNTIME_DIR':str(self.state)}):
            with self.assertRaises(FileNotFoundError):control.main()
        self.verify_stopped(child)
    def test_wrong_starttime_or_uid_never_kills_unrelated_child(self):
        child,record=self.launch()
        for field,value in [('starttime','0'),('uid',os.getuid()+1),('marker','wrong-instance')]:
            forged=dict(record,**{field:value});atomic(self.state/'producer.json',forged)
            with self.assertRaises(PermissionError):stop(self.state,manager=['/bin/true'])
            self.assertIsNone(child.poll())
        atomic(self.state/'producer.json',record)
        stop(self.state,manager=['/bin/true']);self.verify_stopped(child)
    def test_fifo_exact0620_despite0022_umask(self):
        old=os.umask(0o022)
        try:create_capture_fifo(self.state/'capture.fifo',os.geteuid(),os.getegid())
        finally:os.umask(old)
        info=(self.state/'capture.fifo').stat()
        self.assertTrue(stat.S_ISFIFO(info.st_mode));self.assertEqual(stat.S_IMODE(info.st_mode),0o620)
    def supervisor(self):
        code='''from pathlib import Path
import sys
from garage_audio.producer import supervise
state=Path(sys.argv[1])
count=[0]
def heartbeat():
 count[0]+=1
 if count[0]>2:raise ConnectionRefusedError('actual control loss test')
 return {'generation':'file-test'}
with (state/'pcm.raw').open('wb') as output:
 supervise(state,[sys.executable,'-c',sys.argv[2]],output,initialize=lambda:None,heartbeat=heartbeat,interval=.05)
'''
        parent=subprocess.Popen([sys.executable,'-c',code,str(self.state),DUMMY],cwd=ROOT,stderr=subprocess.DEVNULL)
        self.children.append(parent);return parent
    def test_control_loss_watchdog_reaps_real_pcm_child(self):
        parent=self.supervisor()
        self.assertNotEqual(parent.wait(timeout=4),0)
        record=json.loads((self.state/'producer.json').read_text())
        with self.assertRaises((FileNotFoundError,ProcessLookupError)):identity(record['pid'],record['marker'])
        self.assertTrue((self.state/'producer-stop.json').exists())
        size=(self.state/'pcm.raw').stat().st_size;time.sleep(.1)
        self.assertGreater(size,0);self.assertEqual((self.state/'pcm.raw').stat().st_size,size)
    def test_supervisor_crash_kernel_closes_pcm_child(self):
        # Uncatchable supervisor SIGKILL exercises actual Linux PDEATHSIG.
        code='''from pathlib import Path
import sys,time
from garage_audio.producer import register
state=Path(sys.argv[1])
with (state/'pcm.raw').open('wb') as output:
 register(state,[sys.executable,'-c',sys.argv[2]],output)
 time.sleep(20)
'''
        parent=subprocess.Popen([sys.executable,'-c',code,str(self.state),DUMMY],cwd=ROOT)
        self.children.append(parent)
        for _ in range(100):
            if (self.state/'producer.json').exists() and (self.state/'pcm.raw').stat().st_size:break
            time.sleep(.01)
        record=json.loads((self.state/'producer.json').read_text());fd=os.pidfd_open(record['pid'])
        try:
            parent.kill();parent.wait(timeout=3)
            import select
            self.assertTrue(select.select([fd],[],[],3)[0])
            size=(self.state/'pcm.raw').stat().st_size;time.sleep(.1)
            self.assertEqual((self.state/'pcm.raw').stat().st_size,size)
        finally:os.close(fd)

if __name__=='__main__':unittest.main(verbosity=2)
