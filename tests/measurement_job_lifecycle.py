"""Actual supervisor death and restart proof; isolated credentials/file-null children."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from garage_audio.application import Application
from measurement_jobs import provision
from measurement_job_receipts import retain_jobs

SUPERVISOR = '''import json,sys,time
from pathlib import Path
from garage_audio.application import Application
root=Path(sys.argv[1])
app=Application(root/'state',human_reference=str(root/'human'/'verifier.json'),job_options={'test_behavior':'hang'})
app.human_approval_available=True # isolated native fixture context
sid=app.execute('calibration.plan',{'name':'isolated parent-loss test','channels':['left']})['result']['id']
job=app.execute('jobs.request',{'session_id':sid,'position':'seat','channel':'left'})['result']
identity=app.humans.authenticate('operator',(root/'fixture-password').read_text(),'isolated-supervisor-human')
app.jobs.approve(job['id'],job['manifest_sha256'],identity)
assert app.execute('jobs.execute',{'id':job['id']})['ok']
print(json.dumps({'id':job['id']}),flush=True)
time.sleep(60)
'''

class Lifecycle(unittest.TestCase):
    def run_parent_loss(self, before_admission=False):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); verifier,password = provision(root/'human')
            fixture = root/'fixture-password'; fixture.write_text(password); fixture.chmod(0o600)
            script = SUPERVISOR.replace("'test_behavior':'hang'","'test_behavior':'pre-admission'") if before_admission else SUPERVISOR
            parent = subprocess.Popen([sys.executable,'-c',script,str(root)],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
            try:
                line = parent.stdout.readline()
                self.assertTrue(line,'supervisor failed before creating own child')
                jid = json.loads(line)['id']
                directory = root/'state'/'measurement-jobs'/jid
                if not before_admission: time.sleep(.2)
                parent.kill(); parent.wait(timeout=3)
                until = time.monotonic()+5
                while not (directory/'guardian-exit.json').exists() and time.monotonic()<until: time.sleep(.02)
                receipt = json.loads((directory/'guardian-exit.json').read_text())
                self.assertTrue(receipt['parent_lost'])
                self.assertTrue(receipt['owned_worker_exit_verified'])
                if before_admission: self.assertFalse(receipt['worker_spawned'])
                else: self.assertNotEqual(receipt['worker_exit_status'],0)
                self.assertFalse((directory/'capture.wav').exists())
                restarted = Application(root/'state',human_reference=str(verifier))
                job = restarted.execute('jobs.get',{'id':jid})['result']
                self.assertEqual(job['state'],'invalidated')
                self.assertFalse(restarted.execute('jobs.execute',{'id':jid})['ok'])
                retain_jobs(restarted,'actual-supervisor-parent-loss')
            finally:
                if parent.poll() is None: parent.kill()
                parent.communicate(timeout=3)
    def test_actual_parent_loss_stops_owned_worker_and_restart_never_resumes(self):
        self.run_parent_loss()
    def test_immediate_owner_death_before_admission_repeated(self):
        for _ in range(3): self.run_parent_loss(True)

if __name__ == '__main__': unittest.main()
