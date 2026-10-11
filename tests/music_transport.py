"""Lifecycle boundaries without hardware; owned capture must exit, transport stays."""
import json,subprocess,tempfile,time,unittest
from pathlib import Path
from unittest.mock import patch
from garage_audio import producer
from garage_audio.speaker_controller import NativeChain,Controller

class TransportTests(unittest.TestCase):
    def test_output_failclose_manager_never_stops_player(self):
        with tempfile.TemporaryDirectory() as d,patch.object(producer,'atomic'),patch.object(producer.subprocess,'run') as run:
            run.return_value.returncode=0
            producer.stop(Path(d))
            self.assertEqual(run.call_args.args[0],('systemctl','--user','stop','garage-dsp'))
    def test_owned_capture_exit_leaves_independent_transport_alive(self):
        # Real owned OS children, not a physical or MA registration assertion.
        transport=subprocess.Popen(['sleep','30'])
        with tempfile.TemporaryDirectory() as d:
            state=Path(d)
            with patch.object(producer,'ROOT',state):
                capture,record=producer.register(state,['sleep','30'],subprocess.DEVNULL)
            try:
                result=producer.stop(state,manager=('true',))
                self.assertEqual(result['producer'],'exit_verified');capture.wait(timeout=3)
                self.assertIsNone(transport.poll());self.assertTrue((state/'producer-stop.json').exists())
            finally:
                if capture.poll() is None:capture.kill();capture.wait()
                transport.terminate();transport.wait()
    def test_units_no_hardware_or_output_lifetime_dependency(self):
        root=Path(__file__).resolve().parents[1]
        unit=(root/'config/garage-player.service').read_text()
        self.assertNotIn('garage-dsp.service',unit);self.assertNotIn('ExecStartPre=',unit)
        self.assertIn('Requires=garage-wireplumber.service',unit);self.assertIn('-o garage_music',unit)
        self.assertNotIn(' -V ',unit);self.assertNotIn(' -U ',unit)
        graph=(root/'config/pipewire.conf').read_text();self.assertNotIn('api.alsa',graph)
    def test_stop_retains_observed_transport_not_output_grant(self):
        class Fixture:
            def stop(self):pass
        c=Controller(Fixture());c.registered=True;c.generation='ISOLATED'
        s=c.stop();self.assertEqual(s['state'],'stopped');self.assertTrue(s['player_registered'])
        self.assertIsNone(s['generation']);self.assertFalse(s['music']['human_bridge_available'])
    def test_stale_audio_barrier_uses_stop_not_pause_and_reads_mode(self):
        class Reply:
            status=200
            def __init__(self,v):self.v=v
            def read(self,n):return json.dumps(self.v).encode()
        calls=[]
        class Connection:
            def __init__(self,*a,**k):pass
            def request(self,method,path,body,headers):calls.append(json.loads(body)['params'])
            def getresponse(self):return Reply({'result':None if len(calls)==1 else {'mode':'stop'}})
            def close(self):pass
        with patch('garage_audio.speaker_controller.http.client.HTTPConnection',Connection):NativeChain().stop_pending_music()
        self.assertEqual(calls,[['02:00:00:00:21:01',['stop']],['02:00:00:00:21:01',['status',0,1]]])
        calls.clear()
        with patch('garage_audio.speaker_controller.http.client.HTTPConnection',Connection),patch.object(Reply,'read',return_value=b'{"result":{"mode":"play"}}'):
            with self.assertRaises(RuntimeError):NativeChain().stop_pending_music()

if __name__=='__main__':unittest.main()
