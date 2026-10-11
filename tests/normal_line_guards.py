"""Actual native FILE normal-line fault guards; synthetic peer/input only."""
import array,time,unittest
from native_playback import NativeFileTests

class LineGuards(NativeFileTests):
    def feed(self):
        self.level=.25
        try:
            while self.process.poll() is None:
                self.process.stdin.write(array.array('f',[self.level,self.level*.5]*1024).tobytes())
                self.process.stdin.flush();time.sleep(.01)
        except (OSError,ValueError):pass
    def open_line(self):
        self.peer.realm='phone'
        def execute(cmd,**fields):return self.policy.execute({'cmd':cmd,'generation':self.policy.generation(),**fields},self.peer)
        execute('phone-start');execute('initialize');execute('phone-prepare')
        execute('normal-open',hours=2,policy='fosi-line-v1')
        return execute
    def test_line_clip_growth_closes_owned_dsp(self):
        execute=self.open_line();execute('normal-state',state='playing')
        c=self.client();c.connect();self.assertEqual(c.status.clipped_samples(),0);c.disconnect()
        self.level=4.0 # deliberate abnormal >full-scale upstream FILE input
        self.process.wait(timeout=3);self.assertTrue(self.policy.lease.revoked)
        print('PASS actual Camilla FILE abnormal overload→clip counter guard→owned DSP closed')
    def test_old_gain_schema_does_not_open_line(self):
        self.peer.realm='phone'
        with self.assertRaises((ValueError,KeyError,PermissionError)):
            self.policy.execute({'cmd':'normal-open','generation':self.policy.generation(),'hours':2,'ceiling':-20},self.peer)
        self.assertIsNone(self.policy.lease)

if __name__=='__main__':
    suite=unittest.TestSuite(LineGuards(name) for name in ('test_line_clip_growth_closes_owned_dsp','test_old_gain_schema_does_not_open_line'))
    result=unittest.TextTestRunner(verbosity=2).run(suite)
    raise SystemExit(0 if result.wasSuccessful() else 1)
