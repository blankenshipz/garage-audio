"""Local persistence proof, no provider, sound or deployed state."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from garage_audio.application import Application
from garage_audio import private_records


class Durability(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir='/private/tmp/opencode')
        self.root = Path(self.temp.name)
        self.app = Application(self.root, broker=lambda _: {'mute': True})
        self.record = self.app.execute('sessions.create', {'name': 'retained'})['result']

    def tearDown(self):
        self.app.agent_harness.close()
        self.temp.cleanup()

    def test_directory_symlink_cannot_read_write_or_count(self):
        directory = self.root/'sessions'
        directory.rename(self.root/'original')
        outside = self.root/'outside'
        outside.mkdir(mode=0o700)
        directory.symlink_to(outside)
        self.assertFalse(self.app.execute('sessions.get', {'id':self.record['id']})['ok'])
        self.assertFalse(self.app.execute('sessions.create', {'name':'escape'})['ok'])
        self.assertEqual(list(outside.iterdir()), [])

    def test_ancestor_symlink_cannot_create_application_state(self):
        target = self.root/'ancestor-target'; target.mkdir(mode=0o700)
        alias = self.root/'ancestor-alias'; alias.symlink_to(target)
        with self.assertRaises(OSError):
            Application(alias/'new-root',broker=lambda _: {})
        self.assertEqual(list(target.iterdir()),[])

    def test_file_symlink_and_hardlink_refused_without_changes(self):
        path = self.root/'sessions'/(self.record['id']+'.json')
        original = path.read_bytes()
        outside = self.root/'outside.json'
        path.rename(outside)
        path.symlink_to(outside)
        self.assertFalse(self.app.execute('sessions.get', {'id':self.record['id']})['ok'])
        with self.assertRaises(ValueError): self.app.write_atomic(path,b'changed')
        self.assertEqual(outside.read_bytes(), original)
        path.unlink()
        os.link(outside,path)
        self.assertFalse(self.app.execute('sessions.get', {'id':self.record['id']})['ok'])
        with self.assertRaises(ValueError): self.app.write_atomic(path,b'changed')
        self.assertEqual(outside.read_bytes(),original)

    def test_failed_replace_preserves_original_and_cleans_unique_temp(self):
        path = self.root/'sessions'/(self.record['id']+'.json')
        original = path.read_bytes()
        with patch.object(private_records.os,'replace',side_effect=OSError('fixture crash')):
            with self.assertRaises(OSError): self.app.write_atomic(path,b'changed')
        self.assertEqual(path.read_bytes(),original)
        self.assertEqual(list(path.parent.glob('*.tmp')),[])

    def test_directory_fsync_and_stale_manifest_recovery(self):
        native = os.fsync
        calls = []
        with patch.object(private_records.os,'fsync',side_effect=lambda fd: (calls.append(fd),native(fd))[1]):
            record = dict(self.record,name='new authoritative value')
            self.app.save('sessions',record)
        self.assertGreaterEqual(len(calls),4)
        manifest = self.root/'session-manifests'/(record['id']+'.json')
        stale = json.loads(manifest.read_text()); stale['name']='stale'
        stale.pop('_record_identity')  # Legacy or interrupted commit lacks current source identity.
        manifest.write_text(json.dumps(stale))
        os.utime(manifest,(2000000000,2000000000))
        listed = self.app.execute('sessions.list',{})
        self.assertTrue(listed['ok'],listed)
        self.assertEqual(listed['result'][0]['name'],'new authoritative value')

    def test_unsafe_mode_and_lock_symlink_do_not_mutate(self):
        path = self.root/'sessions'/(self.record['id']+'.json')
        path.chmod(0o644)
        self.assertFalse(self.app.execute('sessions.get',{'id':self.record['id']})['ok'])
        lock = self.root/'application.lock'; lock.unlink()
        outside = self.root/'outside'; outside.write_text('unchanged')
        lock.symlink_to(outside)
        self.assertFalse(self.app.execute('sessions.create',{'name':'denied'})['ok'])
        self.assertEqual(outside.read_text(),'unchanged')


if __name__ == '__main__': unittest.main(verbosity=2)
