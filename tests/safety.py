"""Reviewer regressions: real official-client FILE DSP plus isolated injections."""
import copy
import importlib.util
import io
import json
import os
from pathlib import Path
import socket
import subprocess
import tarfile
import tempfile
import threading
import time
import unittest
from unittest.mock import patch, Mock
import yaml
from camilladsp import CamillaClient
from garage_audio.control import apply_graph, atomic, store, prove_mute, connect_guarded, ROOT
from garage_audio.broker import Policy, serve
from garage_audio.ipc import request
from garage_audio.profile import DEFAULT, compile_profile

ENV=dict(os.environ,LD_LIBRARY_PATH=str(ROOT/'tools/libs/usr/lib/x86_64-linux-gnu'))

class RealDSP(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(dir=ROOT/'state');self.state=Path(self.tmp.name)
        self.config=compile_profile(DEFAULT,{'type':'Stdin','channels':2,'format':'F32_LE'},
            {'type':'File','channels':20,'format':'F32_LE','filename':'/dev/null'})
        path=self.state/'file.yaml';path.write_text(yaml.safe_dump(self.config))
        self.proc=subprocess.Popen([str(ROOT/'tools/camilladsp'),'-a','127.0.0.1','-p','1235','--gain=-60','--mute',str(path)],env=ENV,stdin=subprocess.PIPE,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        self.done=threading.Event()
        def feed():
            try:
                while not self.done.is_set():self.proc.stdin.write(b'\0'*8192);self.proc.stdin.flush();time.sleep(.0213)
            except (OSError,ValueError):pass
        self.feeder=threading.Thread(target=feed,daemon=True);self.feeder.start()
        self.c=CamillaClient('127.0.0.1',1235)
        for _ in range(30):
            try:self.c.connect();break
            except OSError:time.sleep(.1)
        atomic(self.state/'profile-store.json',{'active':copy.deepcopy(DEFAULT),'known_good':copy.deepcopy(DEFAULT)})
        self.epoch=[str(self.proc.pid)]
        self.factory=lambda p:compile_profile(p,self.config['devices']['capture'],self.config['devices']['playback'])
    def tearDown(self):
        self.c.disconnect();self.done.set();self.feeder.join(timeout=1)
        try:self.proc.stdin.close()
        except OSError:pass
        if self.proc.poll() is None:self.proc.terminate()
        try:self.proc.wait(timeout=3)
        except subprocess.TimeoutExpired:self.proc.kill();self.proc.wait(timeout=3)
        self.tmp.cleanup()
    def candidate(self):
        p=copy.deepcopy(DEFAULT);p['channels']['left']['gain']=-3;return p
    def test_persistence_failure_never_unmutes_and_rolls_back_muted_first(self):
        self.c.volume.set_main_volume(-40);self.c.volume.set_main_mute(False)
        observed=[];original=self.c.config.set_active
        def reload(graph):
            observed.append((self.c.volume.main_mute(),self.c.volume.main_volume()));original(graph)
        def fail(*_):
            self.assertTrue(self.c.volume.main_mute());self.assertEqual(self.c.volume.main_volume(),-60)
            raise OSError('injected disk commit failure')
        with patch.object(self.c.config,'set_active',side_effect=reload),patch('garage_audio.control.atomic',side_effect=fail):
            with self.assertRaises(OSError):apply_graph(self.c,self.candidate(),self.state,self.factory(self.candidate()),authorized_unmuted=True,generation=lambda:self.epoch[0])
        self.assertTrue(all(m and v==-60 for m,v in observed))
        self.assertTrue(self.c.volume.main_mute());self.assertEqual(self.c.volume.main_volume(),-60)
        self.assertEqual(store(self.state)['active'],DEFAULT)
        self.assertEqual(self.c.config.active()['filters']['left_gain']['parameters']['gain'],0)
    def test_generation_change_after_commit_never_restores_unmute(self):
        self.c.volume.set_main_volume(-40);self.c.volume.set_main_mute(False)
        def commit(path,value):
            atomic(path,value)
            if value['active']!=DEFAULT:self.epoch[0]='restarted-generation'
        with patch('garage_audio.control.atomic',side_effect=commit):
            with self.assertRaises(RuntimeError):apply_graph(self.c,self.candidate(),self.state,self.factory(self.candidate()),authorized_unmuted=True,generation=lambda:self.epoch[0])
        self.assertTrue(self.c.volume.main_mute());self.assertEqual(self.c.volume.main_volume(),-60)
        self.assertEqual(store(self.state)['active'],DEFAULT)
    def test_failure_after_rename_restores_authoritative_store_muted(self):
        self.c.volume.set_main_volume(-40);self.c.volume.set_main_mute(False)
        def uncertain(path,value):
            atomic(path,value)
            if value['active']!=DEFAULT:raise OSError('injected post-rename fsync failure')
        with patch('garage_audio.control.atomic',side_effect=uncertain):
            with self.assertRaises(OSError):apply_graph(self.c,self.candidate(),self.state,self.factory(self.candidate()),authorized_unmuted=True,generation=lambda:self.epoch[0])
        self.assertTrue(self.c.volume.main_mute());self.assertEqual(self.c.volume.main_volume(),-60)
        self.assertEqual(store(self.state)['active'],DEFAULT)
    def test_broker_serializes_lifecycle_and_rejects_stale_epoch(self):
        policy=Policy(self.state,lambda:CamillaClient('127.0.0.1',1235),self.factory,self.proc)
        before=policy.execute({'cmd':'status'})['generation']
        self.c.volume.set_main_volume(-40);self.c.volume.set_main_mute(False)
        policy.execute({'cmd':'initialize','generation':before})
        after=policy.execute({'cmd':'status'})['generation'];self.assertNotEqual(before,after)
        with self.assertRaises(ValueError):policy.execute({'cmd':'profile','profile':self.candidate(),'generation':before,'attended_profile':'PRESENT-GAINS-LOW'})
        self.assertTrue(self.c.volume.main_mute());self.assertEqual(self.c.volume.main_volume(),-60)
        with self.assertRaises(ValueError):policy.execute({'cmd':'SetConfig','generation':after,'config':{'devices':{'volume_limit':50}}})
        self.assertEqual(self.c.config.active()['devices']['volume_limit'],-20)
    def test_concurrent_initialize_cannot_be_followed_by_old_unmute(self):
        policy=Policy(self.state,lambda:CamillaClient('127.0.0.1',1235),self.factory,self.proc)
        epoch=policy.execute({'cmd':'status'})['generation']
        self.c.volume.set_main_volume(-60);self.c.volume.set_main_mute(True)
        writing=threading.Event();release=threading.Event();errors=[]
        def delayed(path,value):
            if value['active']!=DEFAULT:writing.set();release.wait(timeout=3)
            atomic(path,value)
        def invoke(payload):
            try:policy.execute(payload)
            except Exception as error:errors.append(error)
        with patch('garage_audio.control.atomic',side_effect=delayed):
            profile=threading.Thread(target=invoke,args=({'cmd':'profile','profile':self.candidate(),'generation':epoch},))
            profile.start();self.assertTrue(writing.wait(timeout=3))
            lifecycle=threading.Thread(target=invoke,args=({'cmd':'initialize','generation':epoch},));lifecycle.start()
            release.set();profile.join(timeout=4);lifecycle.join(timeout=4)
        self.assertFalse(errors);self.assertFalse(profile.is_alive());self.assertFalse(lifecycle.is_alive())
        self.assertTrue(self.c.volume.main_mute());self.assertEqual(self.c.volume.main_volume(),-60)
        self.assertNotEqual(policy.generation(),epoch)
    def test_legacy_commission_string_cannot_grant_sound(self):
        policy=Policy(self.state,lambda:CamillaClient('127.0.0.1',1235),self.factory,self.proc)
        epoch=policy.execute({'cmd':'status'})['generation']
        with self.assertRaises(ValueError):policy.execute({'cmd':'commission-window','channel':'left','seconds':1,'attended':'PRESENT-GAINS-LOW','generation':epoch})
        self.assertTrue(self.c.volume.main_mute());self.assertIsNone(self.proc.poll())
        self.assertEqual(store(self.state)['active'],DEFAULT)
    def test_unreachable_official_client_stops_only_owned_producer(self):
        stopped=Mock()
        with self.assertRaises(OSError):connect_guarded(CamillaClient('127.0.0.1',1237),stopped)
        stopped.assert_called_once_with()
    def test_unix_peer_credentials_reject_wrong_uid(self):
        policy=Policy(self.state,lambda:CamillaClient('127.0.0.1',1235),self.factory,self.proc)
        path=self.state/'control.sock'
        server=threading.Thread(target=serve,args=(policy,path,os.getuid()+1),daemon=True);server.start()
        for _ in range(20):
            if path.exists():break
            time.sleep(.02)
        with self.assertRaisesRegex(RuntimeError,'peer UID'):request({'cmd':'status'},str(path))
        self.assertTrue(self.c.volume.main_mute())
    def test_unreachable_ipc_cli_stops_project_producer(self):
        from garage_audio import control
        with patch('sys.argv',['control','status']),patch('garage_audio.ipc.request',side_effect=ConnectionRefusedError),patch.object(control,'stop_project_producer') as stopped:
            with self.assertRaises(ConnectionRefusedError):control.main()
        stopped.assert_called_once_with()

class SnapshotTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec=importlib.util.spec_from_file_location('provision',ROOT/'scripts/provision.py')
        cls.module=importlib.util.module_from_spec(spec);spec.loader.exec_module(cls.module)
    def test_snapshot_rejects_symlink_and_mutated_source_digest(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'state') as d:
            root=Path(d);source=root/'source';source.write_bytes(b'approved')
            import hashlib
            expected=hashlib.sha256(b'approved').hexdigest()
            link=root/'link';link.symlink_to(source)
            with self.assertRaises(OSError):self.module.snapshot(link,root/'private',expected)
            source.write_bytes(b'tampered')
            with self.assertRaises(ValueError):self.module.snapshot(source,root/'private2',expected)
            source.write_bytes(b'approved')
            self.module.snapshot(source,root/'verified-copy',expected)
            source.unlink();source.write_bytes(b'replaced after verification')
            self.assertEqual((root/'verified-copy').read_bytes(),b'approved')
    def test_tar_rejects_traversal_symlinks_and_untrusted_root_asset(self):
        for name,kind in [('garage_audio/broker.py',tarfile.SYMTYPE),('../escape',tarfile.REGTYPE),('scripts/arbitrary-root-code',tarfile.REGTYPE)]:
            with tempfile.TemporaryDirectory(dir=ROOT/'state') as d:
                root=Path(d);archive=root/'bad.tar.gz';dest=root/'verified';dest.mkdir()
                with tarfile.open(archive,'w:gz') as t:
                    item=tarfile.TarInfo(name);item.type=kind;item.linkname='/etc/passwd'
                    if kind==tarfile.REGTYPE:item.size=1;t.addfile(item,io.BytesIO(b'x'))
                    else:t.addfile(item)
                with self.assertRaises(ValueError):self.module.extract_release(archive,dest)

if __name__=='__main__':unittest.main(verbosity=2)
