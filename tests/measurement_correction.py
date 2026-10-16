"""Actual Camilla correction/isolation render with SYNTHETIC sweeps; no devices."""
import copy
import math
import json
import struct
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from garage_audio import measurement_correction as correction
from garage_audio.measurement_signal import excitation
from garage_audio.measurement_verification import compare
from measurement_hardware import SNAPSHOT, BOUND, CAL, FixtureApplication, provision


class Correction(unittest.TestCase):
    def test_device_owner_can_only_read_public_binding_not_mutate(self):
        from garage_audio.audio_container import Worker
        class Client:
            def __init__(self,value): self.data=json.dumps(value).encode()+b'\n'; self.reply=None
            def __enter__(self): return self
            def __exit__(self,*args): pass
            def settimeout(self,value): pass
            def getsockopt(self,*args): return struct.pack('3i',123,1002,1003)
            def recv(self,size): data,self.data=self.data,b''; return data
            def sendall(self,data): self.reply=json.loads(data)
        with tempfile.TemporaryDirectory() as tmp:
            worker=Worker(tmp,Path(tmp)/'evidence')
            try:
                for value,allowed in [({'operation':'audio.status'},True),({'operation':'audio.config.get','arguments':{}},True),
                    ({'operation':'audio.stop'},False),({'operation':'audio.config.propose','arguments':{'configuration':SNAPSHOT['configuration']}},False)]:
                    client=Client(value)
                    with patch('garage_audio.audio_container.os.getuid',return_value=1002): worker.handle_client(client,'public',1000)
                    self.assertEqual(client.reply['ok'],allowed,client.reply)
                self.assertEqual(worker.configurations.state['revision'],0)
            finally: worker.closed.set(); worker.halt('test_exit')

    def test_actual_render_cut_and_exact_graph_isolation(self):
        with tempfile.TemporaryDirectory() as tmp:
            parent=Path(tmp)/'measurement-jobs'; parent.mkdir(mode=0o700)
            amplitudes=[]
            for index in range(2):
                directory=parent/(str(index)*32); directory.mkdir(mode=0o700)
                spec=excitation(directory/'excitation.wav',48000,'left'); binding=copy.deepcopy(SNAPSHOT)
                if index:
                    binding['revision']=1; binding['configuration']['correction']={'left':[{'freq':80,'q':2,'gain':-6}],'right':[],'sub':[]}
                route=correction.prepare(directory,binding,'left',3)
                manifest={'signal':spec,'hardware_admission':BOUND,'correction_render':route,'channel':'left'}
                reference,raw,evidence=correction.render(directory,manifest,lambda:None)
                self.assertTrue(evidence['unused_outputs_zero']); self.assertEqual(evidence['native_exit_status'],0)
                self.assertEqual(evidence['binding'],binding)
                self.assertTrue(all(raw[i]==0 for i in range(len(raw)) if i%36 not in (6,7,8)))
                values=[int.from_bytes(raw[i+6:i+9],'little',signed=True)/8388608 for i in range(0,len(raw),36)]
                amplitudes.append(abs(sum(v*complex(math.cos(2*math.pi*80*n/48000),math.sin(2*math.pi*80*n/48000)) for n,v in enumerate(values))))
            self.assertAlmostEqual(20*math.log10(amplitudes[1]/amplitudes[0]),-6,delta=.1)

    def test_correction_revision_change_revokes_approved_job(self):
        from garage_audio import measurement_hardware as hardware
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); verifier,password=provision(root/'human'); app=FixtureApplication(root/'state',verifier)
            with patch.object(hardware,'admission',return_value=BOUND), patch.object(hardware,'immutable',return_value=CAL), patch.object(correction,'snapshot',return_value=SNAPSHOT):
                job=app.jobs.request(app.session['id'],'seat','left','hardware')
                identity=app.humans.authenticate('operator',password,'synthetic-revision-change')
                app.jobs.approve(job['id'],job['manifest_sha256'],identity)
                changed=dict(SNAPSHOT,revision=1)
                with patch.object(correction,'snapshot',return_value=changed):
                    self.assertFalse(app.execute('jobs.execute',{'id':job['id']})['ok'])
                    self.assertEqual(app.jobs.get(job['id'])['state'],'expired')
                self.assertEqual(app.jobs.children,{})

    def test_paired_verification_never_promotes_synthetic_or_mismatched_binding(self):
        from measurement_workflow import fixture
        from garage_audio.calibration import parse
        f=fixture(); record=parse(f['text'],'rew-response'); record.update(id='b'*32,metadata=f['metadata'])
        base=copy.deepcopy(SNAPSHOT); accepted=copy.deepcopy(SNAPSHOT); accepted['revision']=1
        accepted['configuration']['correction']={'left':[{'freq':80,'q':2,'gain':-6}],'right':[],'sub':[]}
        def provenance(binding): return {'hardware_sha256':'d'*64,'correction_render':{'binding':binding,'native_exit_status':0,'unused_outputs_zero':True,'emitted_sha256':'a'*64,'input_sha256':'b'*64,'binary_sha256':'c'*64}}
        record['capture_provenance']=provenance(base)
        repeated=copy.deepcopy(record); repeated['id']='c'*32; repeated['capture_provenance']=provenance(accepted)
        repeated['points']=[dict(p,db=70) for p in record['points']]
        plan={'positions':['seat'],'channels':['left']}
        before={'plan':plan,'files':[record]}; after={'plan':plan,'files':[repeated]}
        transaction={'state':'accepted_for_next_play','accepted_revision':1,'base_revision':0,'previous_configuration':base['configuration'],'configuration':accepted['configuration']}
        runtime={'revision':1,'configuration':accepted['configuration'],'generation':accepted['generation']}
        report=compare(before,after,transaction,runtime)
        self.assertTrue(report['improved']); self.assertEqual(report['source'],'synthetic')
        self.assertFalse(report['acoustic_improvement_verified'])
        repeated['capture_provenance']['correction_render']['binding']['revision']=2
        with self.assertRaises(ValueError): compare(before,after,transaction,runtime)


if __name__=='__main__': unittest.main()
