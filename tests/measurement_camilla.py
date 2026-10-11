"""Actual pinned CamillaDSP job abnormal exits; no hardware, mocks only negative pin seams."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
import signal
import fcntl
from unittest.mock import patch
from garage_audio.application import Application
from garage_audio import measurement_route as route
from measurement_jobs import provision
from measurement_job_receipts import retain_jobs

class Camilla(unittest.TestCase):
    def test_unsafe_artifacts_rejected_before_write_or_spawn(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); app = Application(root/'state')
            sid = app.execute('calibration.plan',{'name':'unsafe path fixture','channels':['left']})['result']['id']
            job = app.execute('jobs.request',{'session_id':sid,'position':'seat','channel':'left'})['result']
            directory = app.jobs.directory(job['id'])
            canary = root/'outside-canary'; canary.write_bytes(b'outside must remain unchanged'); canary.chmod(0o600)
            original = {name:(directory/name).read_bytes() for name in route.RENDER_EXISTING}
            rows = []
            for name in route.RENDER_FRESH+route.RENDER_EXISTING:
                for kind in ('symlink','hardlink','fifo','directory','existing'):
                    path = directory/name
                    if path.exists(): path.unlink()
                    if kind == 'symlink': path.symlink_to(canary)
                    elif kind == 'hardlink': os.link(canary,path)
                    elif kind == 'fifo': os.mkfifo(path,0o600)
                    elif kind == 'directory': path.mkdir(mode=0o700)
                    else:
                        path.write_bytes(b'preexisting'); path.chmod(0o600)
                    before = time.monotonic()
                    with patch.object(route.subprocess,'Popen') as spawn:
                        with self.assertRaises((ValueError,OSError)): route.render(directory,job['manifest'])
                        spawn.assert_not_called()
                    self.assertLess(time.monotonic()-before,1,'FIFO must never be opened')
                    self.assertEqual(canary.read_bytes(),b'outside must remain unchanged')
                    rows.append({'path':name,'kind':kind,'no_spawn':True,'canary_unchanged':True})
                    if path.is_dir(): path.rmdir()
                    else: path.unlink()
                    if name in original:
                        path.write_bytes(original[name]); path.chmod(0o600)
            # Config and version logs also deny overwrite before prepare's pin probe.
            import uuid
            for name in ('native-config.json','native-version.stdout','native-version.stderr'):
                for kind in ('symlink','hardlink','fifo','directory','existing'):
                    fresh = directory.parent/uuid.uuid4().hex; fresh.mkdir(mode=0o700)
                    (fresh/'excitation.wav').write_bytes(original['excitation.wav']); (fresh/'excitation.wav').chmod(0o600)
                    path = fresh/name
                    if kind == 'symlink': path.symlink_to(canary)
                    elif kind == 'hardlink': os.link(canary,path)
                    elif kind == 'fifo': os.mkfifo(path,0o600)
                    elif kind == 'directory': path.mkdir(mode=0o700)
                    else: path.write_bytes(b'preexisting'); path.chmod(0o600)
                    with patch.object(route.subprocess,'Popen') as spawn:
                        with self.assertRaises(ValueError): route.prepare(fresh,'left',job['manifest']['signal'])
                        spawn.assert_not_called()
                    self.assertEqual(canary.read_bytes(),b'outside must remain unchanged')
                    rows.append({'phase':'prepare','path':name,'kind':kind,'no_spawn':True,'canary_unchanged':True})
                    if path.is_dir(): path.rmdir()
                    else: path.unlink()
                    (fresh/'excitation.wav').unlink(); fresh.rmdir()
            directory.chmod(0o755)
            with self.assertRaises(ValueError): route.render(directory,job['manifest'])
            directory.chmod(0o700)
            moved = directory.with_name('saved-job'); directory.rename(moved); directory.symlink_to(moved,target_is_directory=True)
            with self.assertRaises((ValueError,OSError)): route.render(directory,job['manifest'])
            directory.unlink(); moved.rename(directory)
            parent = directory.parent; parent.chmod(0o755)
            with self.assertRaises(ValueError): route.render(directory,job['manifest'])
            parent.chmod(0o700)
            saved = parent.with_name('saved-jobs'); parent.rename(saved); parent.symlink_to(saved,target_is_directory=True)
            with self.assertRaises(OSError): route.render(directory,job['manifest'])
            parent.unlink(); saved.rename(parent)
            self.assertEqual(canary.read_bytes(),b'outside must remain unchanged')
            if os.environ.get('GARAGE_TEST_RECEIPT_DIR'):
                Path(os.environ['GARAGE_TEST_RECEIPT_DIR'],'unsafe-path-proof.json').write_text(json.dumps(rows))

    def test_emergency_native_guardian_group_and_journal_failures_rescue_reap(self):
        proof = []
        for mode in ('guardian-sigkill','group-sigkill','journal-failure'):
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp); verifier,password = provision(root/'human')
                app = Application(root/'state',human_reference=str(verifier),job_options={'test_behavior':'native-hang'})
                app.human_approval_available=True
                sid = app.execute('calibration.plan',{'name':'rescue reaper fixture','channels':['left']})['result']['id']
                job = app.execute('jobs.request',{'session_id':sid,'position':'seat','channel':'left'})['result']
                identity = app.humans.authenticate('operator',password,'rescue-human'); app.jobs.approve(job['id'],job['manifest_sha256'],identity)
                unrelated = subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)'],start_new_session=True)
                leases = []; original_wait = os.waitpid
                def observed_wait(pid,options):
                    if pid < -1:
                        with (app.jobs.root/'execution.lock').open('a') as lease:
                            try: fcntl.flock(lease,fcntl.LOCK_EX|fcntl.LOCK_NB)
                            except BlockingIOError: leases.append(True)
                            else: leases.append(False)
                    return original_wait(pid,options)
                try:
                    with patch('garage_audio.measurement_jobs.os.waitpid',side_effect=observed_wait):
                        self.assertTrue(app.execute('jobs.execute',{'id':job['id']})['ok'])
                        directory = app.jobs.directory(job['id']); until = time.monotonic()+5
                        while not (directory/'native-process.json').exists() and time.monotonic()<until: time.sleep(.01)
                        native = json.loads((directory/'native-process.json').read_text())
                        guardian = app.jobs.children[job['id']].pid
                        pids = [guardian,native['parent_pid'],native['pid']]
                        identities = [{'pid':pid,'proc_stat':(Path('/proc')/str(pid)/'stat').read_text()} for pid in pids]
                        self.assertTrue(all(os.getpgid(pid) == guardian for pid in pids))
                        if mode == 'guardian-sigkill': os.kill(guardian,signal.SIGKILL)
                        elif mode == 'group-sigkill': os.killpg(guardian,signal.SIGKILL)
                        else:
                            with patch.object(app.jobs,'_save',side_effect=OSError('private journal failure fixture')):
                                app.jobs.revoked.add(job['id'])
                                until = time.monotonic()+5
                                while job['id'] in app.jobs.children and time.monotonic()<until: time.sleep(.01)
                        until = time.monotonic()+5
                        while job['id'] in app.jobs.children and time.monotonic()<until: time.sleep(.01)
                        self.assertNotIn(job['id'],app.jobs.children)
                    self.assertTrue(leases and all(leases),'lease held through every owned-group wait')
                    self.assertTrue(all(not (Path('/proc')/str(pid)).exists() for pid in pids))
                    receipt = json.loads((directory/'parent-reap.json').read_text())
                    self.assertTrue(receipt['owned_group_children_reaped'])
                    self.assertEqual({r['pid'] for r in receipt['reaped_descendants']},set(pids[1:]))
                    self.assertIsNone(unrelated.poll(),'unrelated subprocess must not be signaled or reaped')
                    self.assertFalse(app.execute('jobs.execute',{'id':job['id']})['ok'],'consumed authority cannot resume after journal failure')
                    with (app.jobs.root/'execution.lock').open('a') as lease: fcntl.flock(lease,fcntl.LOCK_EX|fcntl.LOCK_NB)
                    proof.append({'mode':mode,'identities':identities,'receipt':receipt,'lease_held_until_reap':leases,'all_proc_entries_gone':True,'unrelated_alive':True})
                    retain_jobs(app,'native-rescue-'+mode)
                finally:
                    unrelated.terminate(); unrelated.wait(timeout=3)
        if os.environ.get('GARAGE_TEST_RECEIPT_DIR'):
            Path(os.environ['GARAGE_TEST_RECEIPT_DIR'],'failstop-proof.json').write_text(json.dumps({'pid1_cmdline':Path('/proc/1/cmdline').read_bytes().decode().replace('\0',' '),'cases':proof}))

    def test_actual_native_cancel_timeout_failure_all_owned_pids_exit(self):
        for mode in ('cancel','timeout','failure'):
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp); verifier,password = provision(root/'human')
                app = Application(root/'state',human_reference=str(verifier),job_options={'test_behavior':'native-failure' if mode == 'failure' else 'native-hang',
                                  'test_deadline':2 if mode == 'timeout' else None})
                app.human_approval_available = True  # trusted isolated native fixture only
                sid = app.execute('calibration.plan',{'name':'native abnormal fixture','channels':['left']})['result']['id']
                job = app.execute('jobs.request',{'session_id':sid,'position':'seat','channel':'left'})['result']
                self.assertIsNotNone(job)
                identity = app.humans.authenticate('operator',password,'isolated-human')
                app.jobs.approve(job['id'],job['manifest_sha256'],identity)
                self.assertTrue(app.execute('jobs.execute',{'id':job['id']})['ok'])
                directory = app.jobs.directory(job['id']); end = time.monotonic()+5
                while not (directory/'native-process.json').exists() and time.monotonic()<end: time.sleep(.01)
                pid = json.loads((directory/'native-process.json').read_text())['pid']
                if mode == 'cancel': app.execute('jobs.cancel',{'id':job['id']})
                end = time.monotonic()+6
                while job['id'] in app.jobs.children and time.monotonic()<end: time.sleep(.02)
                observed = app.execute('jobs.get',{'id':job['id']})['result']
                self.assertIn(observed['state'],('cancelled','failed'))
                self.assertTrue(observed['owned_child_exit_verified'])
                self.assertFalse((Path('/proc')/str(pid)).exists(),'actual Camilla PID must be reaped, not orphaned')
                self.assertFalse(app.execute('jobs.execute',{'id':job['id']})['ok'])
                retain_jobs(app,'actual-native-'+mode)

    def test_actual_native_owner_death_reaped_and_cannot_resume(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); verifier,password = provision(root/'human')
            secret = root/'fixture-password'; secret.write_text(password); secret.chmod(0o600)
            script = '''import json,time,sys
from pathlib import Path
from garage_audio.application import Application
r=Path(sys.argv[1]);a=Application(r/'state',human_reference=str(r/'human'/'verifier.json'),job_options={'test_behavior':'native-hang'})
a.human_approval_available=True
s=a.execute('calibration.plan',{'name':'native owner loss','channels':['sub']})['result']['id']
j=a.execute('jobs.request',{'session_id':s,'position':'seat','channel':'sub'})['result']
i=a.humans.authenticate('operator',(r/'fixture-password').read_text(),'owner-human');a.jobs.approve(j['id'],j['manifest_sha256'],i)
assert a.execute('jobs.execute',{'id':j['id']})['ok']
p=a.jobs.directory(j['id'])/'native-process.json'
while not p.exists():time.sleep(.01)
print(json.dumps({'id':j['id'],'pid':json.loads(p.read_text())['pid']}),flush=True)
time.sleep(60)
'''
            parent = subprocess.Popen([sys.executable,'-c',script,str(root)],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
            try:
                info = json.loads(parent.stdout.readline()); parent.kill(); parent.wait(timeout=3)
                directory = root/'state'/'measurement-jobs'/info['id']; end = time.monotonic()+5
                while not (directory/'guardian-exit.json').exists() and time.monotonic()<end: time.sleep(.02)
                guardian = json.loads((directory/'guardian-exit.json').read_text())
                restarted = Application(root/'state',human_reference=str(verifier)); restarted.human_approval_available=True
                retain_jobs(restarted,'actual-native-owner-loss')
                self.assertTrue(guardian['parent_lost'])
                self.assertTrue(guardian['owned_worker_exit_verified'])
                # Either the cooperative worker's finally reaps Camilla, or the
                # guardian adopts/reaps it after forcibly stopping the worker.
                self.assertTrue(any(d['pid'] == info['pid'] for d in guardian['reaped_descendants']) or (directory/'native.exit').exists())
                self.assertFalse((Path('/proc')/str(info['pid'])).exists())
                self.assertEqual(restarted.execute('jobs.get',{'id':info['id']})['result']['state'],'invalidated')
                self.assertFalse(restarted.execute('jobs.execute',{'id':info['id']})['ok'])
            finally:
                if parent.poll() is None: parent.kill()
                parent.communicate(timeout=3)

    def test_missing_binary_version_hash_fail_closed_no_fallback(self):
        self.assertTrue(route.readiness()['camilla_file_available'])
        with tempfile.TemporaryDirectory() as tmp:
            app = Application(Path(tmp)/'state')
            sid = app.execute('calibration.plan',{'name':'pin failure test','channels':['left']})['result']['id']
            for name,value in (('BINARY',Path('/tmp/isolated-nonexistent-camilla')),('VERSION','invalid-version'),('BINARY_SHA256','0'*64)):
                with patch.object(route,name,value):
                    self.assertFalse(route.readiness()['camilla_file_available'])
                    with self.assertRaises((ValueError,OSError,subprocess.SubprocessError)): route.pinned_binary()
                    rejected = app.execute('jobs.request',{'session_id':sid,'position':'seat','channel':'left'})
                    self.assertFalse(rejected['ok']); self.assertEqual(rejected['error']['code'],'camilla_file_unavailable')

if __name__ == '__main__': unittest.main()
