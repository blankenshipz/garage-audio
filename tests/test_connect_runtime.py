"""Synthetic native IPC/MA fixtures; no real receiver/phone acceptance."""
import asyncio,importlib.util,os,sys,tempfile,unittest
from pathlib import Path
from types import ModuleType,SimpleNamespace as NS
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
pkg=ModuleType('garage_runtime_fixture');pkg.__path__=[str(ROOT/'ma_provider/garage_speaker_control')];sys.modules[pkg.__name__]=pkg
spec=importlib.util.spec_from_file_location(pkg.__name__+'.connect_runtime',Path(pkg.__path__[0])/'connect_runtime.py')
runtime=importlib.util.module_from_spec(spec);sys.modules[spec.name]=runtime;spec.loader.exec_module(runtime)
spec=importlib.util.spec_from_file_location('connect_config_fixture',ROOT/'scripts/prepare-spotify-connect.py')
config=importlib.util.module_from_spec(spec);spec.loader.exec_module(config)

class ReservationEOF(unittest.TestCase):
    def test_partial_reply_eof_fails_once_and_happy_reply_unchanged(self):
        import json
        class Socket:
            def __init__(self,raw):self.raw=raw;self.calls=0;self.closed=False
            def settimeout(self,value):pass
            def connect(self,path):pass
            def sendall(self,value):pass
            def recv(self,size):
                self.calls+=1
                if self.calls>len(self.raw)+1:raise AssertionError('retried an EOF instead of failing closed')
                return self.raw[self.calls-1:self.calls]
            def close(self):self.closed=True
        broken=Socket(b'{"ok":')
        with patch.object(runtime.socket,'socket',return_value=broken):
            with self.assertRaisesRegex(PermissionError,'native reservation disconnected'):runtime.NativeBridge(None,None,None)
        self.assertTrue(broken.closed);self.assertEqual(broken.calls,len(broken.raw)+1)
        reply=Socket((json.dumps({'ok':True,'result':{'port':54321,'stop_handle':'synthetic','initial_volume':27,'armed':False}})+'\n').encode())
        with patch.object(runtime.socket,'socket',return_value=reply):value=runtime.NativeBridge(None,None,None)
        self.assertEqual(value.port,54321);self.assertEqual(value.initial_volume,27);self.assertEqual(value.status,'disabled');self.assertFalse(reply.closed)

class Tests(unittest.IsolatedAsyncioTestCase):
    def bridge(self,armed=True):
        b=runtime.NativeBridge.__new__(runtime.NativeBridge)
        b.paused_source_session=None
        b.armed=armed;b.stop_task=None;b.cancel_generation=0;b.epoch=1;b.status='waiting_for_spotify_play';b.graph=None;b.started=None
        self.volumes=[]
        async def volume(target,value):self.volumes.append((target,value))
        directory=tempfile.TemporaryDirectory(dir='/private/tmp/opencode');self.addCleanup(directory.cleanup)
        fifo=Path(directory.name)/'source.pcm';os.mkfifo(fifo)
        writer=os.open(fifo,os.O_RDWR|os.O_NONBLOCK);self.addCleanup(os.close,writer)
        os.write(writer,b'old-source-fixture')
        b.backend=NS(_garage_receiver_volume=27,_sink=NS(fifo_path=fifo))
        b.daemon=NS(active_session_id=None)
        players=NS(cmd_volume_set=volume,get_player_state=lambda _:NS(volume_level=27))
        b.provider=NS(mass=NS(players=players));return b
    async def test_one_bind_only_and_actual_receiver_volume_not_MA100(self):
        b=self.bridge();calls=[];states=iter(('prepared','prepared','listening'))
        async def request(command,**values):
            calls.append((command,values))
            return {'state':next(states),'generation':'zero-1','player_registered':True}
        b.request=request
        result=await b.prepare(NS(sequence=2))
        self.assertEqual(result,'zero-1');self.assertEqual(sum(c[0]=='connect-bind' for c in calls),1)
        self.assertEqual(self.volumes,[(runtime.TARGET,27)])
        self.assertEqual(b.discarded_capture_bytes,len(b'old-source-fixture'))
    async def test_disabled_cannot_prepare_or_write_volume(self):
        b=self.bridge(False)
        with self.assertRaises(PermissionError):await b.prepare(NS(sequence=2))
        self.assertEqual(self.volumes,[])
    async def test_resume_already_muted_graph_never_resets_volume(self):
        b=self.bridge()
        async def request(command,**values):return {'state':'listening','generation':'same-zero-1'}
        b.request=request
        self.assertEqual(await b.prepare(NS(sequence=3)),'same-zero-1');self.assertEqual(self.volumes,[])
    async def test_unknown_receiver_volume_cannot_bind(self):
        b=self.bridge();b.backend._garage_receiver_volume=None
        async def request(command,**values):return {'state':'prepared','generation':'zero','player_registered':True}
        b.request=request
        with self.assertRaises(PermissionError):await b.prepare(NS(sequence=2))
        self.assertEqual(self.volumes,[])
    async def test_old_source_consumer_denies_handoff(self):
        b=self.bridge();b.daemon.active_session_id='old-source'
        async def request(command,**values):return {'state':'listening','generation':'zero'}
        b.request=request
        with self.assertRaises(PermissionError):await b.prepare(NS(sequence=2))
    async def test_capture_regular_file_is_not_current_pcm_boundary(self):
        b=self.bridge();file=b.backend._sink.fifo_path.with_suffix('.regular');file.write_bytes(b'not-fifo')
        with self.assertRaises(PermissionError):runtime.discard_capture(file)

class Migration(unittest.TestCase):
    def test_only_volume_mode_changes_and_original_is_unchanged(self):
        value={'providers':{'connect':{'domain':'spotify_connect','enabled':True,'values':{'connected_players':[runtime.TARGET],'volume_mode':'player_only','unrelated':'preserved'}}},'dashboard':'preserved'}
        result=config.migrate(value)
        self.assertEqual(value['providers']['connect']['values']['volume_mode'],'player_only')
        expected={'providers':{'connect':{'domain':'spotify_connect','enabled':True,'values':{'connected_players':[runtime.TARGET],'volume_mode':'sync_spotify','unrelated':'preserved'}}},'dashboard':'preserved'}
        self.assertEqual(result,expected)
    def test_other_target_denied(self):
        with self.assertRaises(PermissionError):config.migrate({'providers':[{'domain':'spotify_connect','enabled':True,'values':{'connected_players':['other'],'volume_mode':'player_only'}}]})

if __name__=='__main__':unittest.main()
