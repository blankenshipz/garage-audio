"""Real owned local child/file effects; fixture human verifier and synthetic PCM only."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from garage_audio.application import Application
from measurement_job_receipts import retain_jobs

def provision(directory):
    # Isolated test credential only, never production enrollment.
    directory.mkdir(mode=0o700)
    password = 'isolated-human-fixture-'+os.urandom(16).hex()
    salt = os.urandom(16).hex()
    value = {'user':'operator','realm':'isolated-test','version':1,'salt':salt,'iterations':200000,
             'password_hash':hashlib.pbkdf2_hmac('sha256',password.encode(),bytes.fromhex(salt),200000).hex()}
    path = directory/'verifier.json'; path.write_text(json.dumps(value)); path.chmod(0o600)
    return path,password

class JobsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.root = Path(self.tmp.name)
        self.verifier,self.password = provision(self.root/'human')
        self.apps = []
        self.app = self.make()
        self.sid = self.app.execute('calibration.plan',{'name':'synthetic job','channels':['left','sub']})['result']['id']
    def make(self, **options):
        def forbidden(_): raise AssertionError('file/null must not contact native DSP/hardware')
        app = Application(self.root/'state',forbidden,human_reference=str(self.verifier),job_options=options)
        app.human_approval_available = True  # Trusted isolated native fixture context, never HTTP arguments.
        self.apps.append(app); return app
    def tearDown(self):
        for app in self.apps:
            for job in app.jobs.list():
                try: app.jobs.cancel(job['id'])
                except Exception: pass
        for app in self.apps:
            until = time.monotonic()+4
            while app.jobs.children and time.monotonic()<until: time.sleep(.02)
        retain_jobs(self.app,self.id().replace('.','_'))
        self.tmp.cleanup()
    def request(self, channel='left'):
        result = self.app.execute('jobs.request',{'session_id':self.sid,'position':'seat','channel':channel})
        self.assertTrue(result['ok'],result); return result['result']
    def approve(self, job):
        identity = self.app.humans.authenticate('operator',self.password,'human-session-fixture-'+os.urandom(8).hex())
        return self.app.jobs.approve(job['id'],job['manifest_sha256'],identity)
    def wait(self, identifier):
        until = time.monotonic()+25
        while time.monotonic()<until:
            result = self.app.execute('jobs.get',{'id':identifier})
            self.assertTrue(result['ok'],result)
            if result['result']['state'] in ('completed','failed','cancelled','revoked','expired','invalidated') and identifier not in self.app.jobs.children:
                return result['result']
            time.sleep(.02)
        self.fail('owned child did not reach terminal state')
    def test_real_file_null_exact_manifest_one_use_evidence(self):
        job = self.request('sub')
        self.assertEqual(job['manifest']['signal']['band_hz'],[20,200])
        self.assertEqual(job['manifest']['policy']['volume_ceiling_db'],-20)
        self.assertFalse(self.app.execute('jobs.execute',{'id':job['id']})['ok'])
        self.assertFalse(self.app.execute('jobs.approve',{'id':job['id'],'consent':'human'})['ok'])
        self.approve(job)
        running = self.app.execute('jobs.execute',{'id':job['id']})
        self.assertTrue(running['ok'],running)
        self.assertFalse(self.app.execute('jobs.execute',{'id':job['id']})['ok'])
        done = self.wait(job['id'])
        self.assertEqual(done['state'],'completed',done)
        self.assertTrue(done['owned_child_exit_verified']); self.assertEqual(done['child_exit_status'],0)
        self.assertTrue(done['evidence']['supported_ir'])
        self.assertEqual(done['evidence']['source'],'synthetic')
        self.assertFalse(done['evidence']['hardware_enabled'])
        self.assertNotIn('_grant',done)
        self.assertTrue((self.app.jobs.directory(job['id'])/'capture.wav').is_file())
        reopened = Application(self.root/'state',human_reference=str(self.verifier)).execute('jobs.get',{'id':job['id']})['result']
        self.assertEqual(reopened['manifest'],done['manifest'])
        self.assertEqual(reopened['evidence'],done['evidence'])
        self.assertEqual(reopened['state'],done['state'])
        self.assertFalse(reopened['human_authority']['human_approval_available'])  # no listener context after standalone restart
    def test_changed_hash_mutation_credential_expiry_restart_denials(self):
        job = self.request()
        identity = self.app.humans.authenticate('operator',self.password,'test')
        with self.assertRaises(Exception): self.app.jobs.approve(job['id'],'0'*64,identity)
        self.approve(job)
        directory = self.app.jobs.directory(job['id'])
        with (directory/'excitation.wav').open('ab') as f: f.write(b'changed')
        self.assertFalse(self.app.execute('jobs.execute',{'id':job['id']})['ok'])
        job = self.request(); self.approve(job)
        private = self.app.jobs._read(job['id']); private['_grant']['expires_at'] = time.time()-1
        with self.app.jobs.transaction(): self.app.jobs._save(private)
        self.assertEqual(self.app.execute('jobs.get',{'id':job['id']})['result']['state'],'expired')
        job = self.request(); self.approve(job)
        value = json.loads(self.verifier.read_text()); value['version'] += 1; self.verifier.write_text(json.dumps(value))
        self.assertFalse(self.app.execute('jobs.execute',{'id':job['id']})['ok'])
        job = self.request(); self.approve(job)
        s = self.app.read('sessions',self.sid); s['name'] = 'changed'; self.app.save('sessions',s)
        self.assertEqual(self.app.execute('jobs.get',{'id':job['id']})['result']['state'],'revoked')
        job = self.request(); self.approve(job)
        restarted = self.make()
        self.assertEqual(restarted.execute('jobs.get',{'id':job['id']})['result']['state'],'invalidated')
        self.assertFalse(restarted.execute('jobs.execute',{'id':job['id']})['ok'])
    def test_actual_cancel_child_and_control_loss(self):
        self.app.jobs.behavior = 'hang'
        for stop in ('cancel','input-change','logout'):
            job = self.request(); self.approve(job)
            self.assertTrue(self.app.execute('jobs.execute',{'id':job['id']})['ok'])
            child = self.app.jobs.children[job['id']]
            if stop == 'cancel': self.app.execute('jobs.cancel',{'id':job['id']})
            elif stop == 'input-change':
                with (self.app.jobs.directory(job['id'])/'excitation.wav').open('ab') as f: f.write(b'changed')
            else: self.app.jobs.revoke_identity(self.app.jobs._read(job['id'])['_grant']['identity']['session'])
            done = self.wait(job['id'])
            self.assertIn(done['state'],('cancelled','revoked'))
            self.assertIsNotNone(child.poll())
            self.assertTrue(done['owned_child_exit_verified'])
    def test_actual_child_crash_and_deadline(self):
        for behavior in ('crash','hang'):
            self.app.jobs.behavior = behavior
            self.app.jobs.test_deadline = .2
            job = self.request(); self.approve(job)
            self.assertTrue(self.app.execute('jobs.execute',{'id':job['id']})['ok'])
            done = self.wait(job['id'])
            self.assertEqual(done['state'],'failed')
            self.assertTrue(done['owned_child_exit_verified']); self.assertNotEqual(done['child_exit_status'],0)
            self.assertFalse(self.app.execute('jobs.execute',{'id':job['id']})['ok'])
    def test_concurrent_approve_cancel_and_global_serialization(self):
        job = self.request()
        with ThreadPoolExecutor(2) as pool:
            tasks = [pool.submit(self.approve,job),pool.submit(self.app.jobs.cancel,job['id'])]
            for task in tasks:
                try: task.result()
                except Exception: pass
        self.assertEqual(self.app.execute('jobs.get',{'id':job['id']})['result']['state'],'cancelled')
        self.app.jobs.behavior = 'hang'
        first,second = self.request(),self.request()
        self.approve(first); self.approve(second)
        self.assertTrue(self.app.execute('jobs.execute',{'id':first['id']})['ok'])
        self.assertEqual(self.app.execute('jobs.execute',{'id':second['id']})['error']['code'],'execution_busy')
        self.app.execute('jobs.cancel',{'id':first['id']}); self.wait(first['id'])
        self.assertTrue(self.app.execute('jobs.execute',{'id':second['id']})['ok'])
        self.app.execute('jobs.cancel',{'id':second['id']}); self.wait(second['id'])
    def test_no_hardware_paths_commands_or_agent_identity(self):
        base = {'session_id':self.sid,'position':'seat','channel':'left'}
        self.assertEqual(self.app.execute('jobs.request',base|{'adapter':'hardware'})['error']['code'],'hardware_prerequisites_required')
        for extra in ({'path':'/dev/audio'},{'command':'play'},{'consent':'human'},{'identity':{'role':'human'}}):
            self.assertFalse(self.app.execute('jobs.request',base|extra)['ok'])
    def test_running_restart_policy_mutation_and_capture_clipping(self):
        self.app.jobs.behavior = 'hang'
        job = self.request(); self.approve(job)
        self.assertTrue(self.app.execute('jobs.execute',{'id':job['id']})['ok'])
        restarted = self.make()
        done = self.wait(job['id'])
        self.assertEqual(done['state'],'invalidated')
        self.assertTrue(done['owned_child_exit_verified'])
        self.app = restarted
        self.app.jobs.behavior = 'hang'
        job = self.request(); self.approve(job)
        self.assertTrue(self.app.execute('jobs.execute',{'id':job['id']})['ok'])
        # Even broker failure cannot preserve authority after policy mutation.
        self.app.execute('dsp.volume',{'db':-40})
        self.assertEqual(self.wait(job['id'])['state'],'revoked')
        self.app.jobs.behavior = 'clip'
        job = self.request(); self.approve(job)
        self.assertTrue(self.app.execute('jobs.execute',{'id':job['id']})['ok'])
        done = self.wait(job['id'])
        self.assertEqual(done['state'],'failed')
        self.assertFalse(done['evidence']['checks']['clipping_free'])
        self.assertFalse(done['evidence']['supported_ir'])
    def test_forged_identity_is_not_server_authenticated(self):
        from dataclasses import replace
        job = self.request()
        identity = self.app.humans.authenticate('operator',self.password,'legitimate')
        forged = replace(identity,session='agent-authored-session')
        with self.assertRaises(Exception): self.app.jobs.approve(job['id'],job['manifest_sha256'],forged)
    def test_cancel_journal_failure_cannot_skip_owned_child_stop_or_revive_grant(self):
        from unittest.mock import patch
        self.app.jobs.behavior = 'hang'
        job = self.request(); self.approve(job)
        self.assertTrue(self.app.execute('jobs.execute',{'id':job['id']})['ok'])
        child = self.app.jobs.children[job['id']]
        with patch.object(self.app.jobs,'_save',side_effect=OSError('isolated journal failure')):
            with self.assertRaises(OSError): self.app.jobs.cancel(job['id'])
            self.assertIsNotNone(child.wait(timeout=4))
            self.assertFalse(self.app.jobs._grant_current(self.app.jobs._read(job['id'])))
            self.assertFalse(self.app.execute('jobs.execute',{'id':job['id']})['ok'])
        self.app.jobs.cancel(job['id'])  # explicit successful journal retry, no new authority

if __name__ == '__main__': unittest.main()
