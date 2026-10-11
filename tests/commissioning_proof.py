"""Recorded numerical fixtures ONLY, even when testing claimed-measured import paths.

Never physical capture or acoustic-benefit evidence. HTTP and MCP use real transports.
"""
import asyncio
import copy
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.request
import http.cookiejar
from concurrent.futures import ThreadPoolExecutor
from garage_audio.application import Application
from garage_audio.profile import DEFAULT
from garage_audio.webserver import Server

CHAIN={'microphone_serial':'recorded-fixture-only','calibration_sha256':'a'*64,
       'sample_rate':48000,'orientation':'90','setup_id':'fixture-not-hardware',
       'hardware_gain_db':0,'transport_normalization':'off','timing_reference':'fixture-magnitude-only'}

def build(call):
    room=call('rooms.create',name='Fixture room')
    annotations=[dict(kind=kind,label=str(i),x=.1*i,y=.5,mic_height_m=1,orientation='90')
                 for i,kind in enumerate(['fit','fit','verify','verify','verify'])]
    room=call('rooms.annotate',id=room['id'],expected_version=room['version'],annotations=annotations)
    room=call('rooms.confirm',id=room['id'],expected_version=room['version'])
    fits=[a['id'] for a in room['annotations'] if a['kind']=='fit']
    holds=[a['id'] for a in room['annotations'] if a['kind']=='verify']
    plan=call('listening_profiles.create',room_id=room['id'],name='Area fixture',kind='area',
              fit_positions=[dict(position_id=p,weight=1) for p in fits],verify_positions=holds)
    fit=call('calibration.plan',name='Fit fixture',positions=fits,channels=['left'])
    sources=[]
    for p in fits:
        f=import_file(call,fit['id'],p,'left',[65,75,65])
        sources.append(dict(session_id=fit['id'],file_id=f['id']))
    call('calibration.analyze',id=fit['id']); call('calibration.propose',id=fit['id'])
    baseline=call('profiles.save',profile=copy.deepcopy(DEFAULT))
    guided=call('profiles.save',profile=copy.deepcopy(DEFAULT))
    bs=call('sessions.create',name='Baseline fixture'); gs=call('sessions.create',name='Guided fixture')
    spec=dict(listening_profile_id=plan['id'],baseline_profile_id=baseline['id'],guided_profile_id=guided['id'],
              proposal_session_id=fit['id'],fit_sources=sources,baseline_session_id=bs['id'],guided_session_id=gs['id'],
              channels=['combined'],grid_hz=[30,100,200],target_db=70,reference_offset_db=0,chain=CHAIN,
              minimum_improvement_db=1,maximum_worsening_db=2,maximum_level_change_db=2,
              minimum_noise_margin_db=20,capture_budget=8,rollback_profile_id=baseline['id'])
    return spec,holds,room

def import_file(call,sid,pos,channel,values,source='measured',**overrides):
    metadata={k:CHAIN[k] for k in ('microphone_serial','calibration_sha256','sample_rate','orientation','timing_reference')}
    metadata.update(source=source,position=pos,channel=channel,calibration_applied=True,timing_quality='unknown')
    metadata.update(overrides)
    # This explicit label survives originals/provenance: never disguise fixture as actual capture.
    text='* RECORDED NUMERICAL FIXTURE NOT PHYSICAL CAPTURE\n'+'\n'.join(f'{f} {v}' for f,v in zip([30,100,200],values))
    return call('measurements.import',session_id=sid,filename='recorded-fixture.txt',format='rew-response',text=text,metadata=metadata)

def pairs(call,spec,holds,guided=(70,70,70),baseline=(65,75,65),source='measured'):
    result=[]
    for pos in holds:
        for c in spec['channels']:
            pair=dict(position=pos,channel=c)
            for arm,values in [('baseline',baseline),('guided',guided)]:
                f=import_file(call,spec[arm+'_session_id'],pos,c,values,source)
                pair[arm]=dict(session_id=spec[arm+'_session_id'],file_id=f['id'])
                pair[arm+'_qc']=dict(provenance='operator-export-attestation',clipped=False,
                                    noise_margin_db=30,level_chain_known=True,chain=copy.deepcopy(CHAIN))
            result.append(pair)
    return result

class Tests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(dir='/private/tmp/opencode')
        self.app=Application(self.tmp.name)
        self.spec,self.holds,self.room=build(self.call)
    def tearDown(self): self.tmp.cleanup()
    def call(self,op,**args):
        response=self.app.execute(op,args)
        self.assertTrue(response['ok'],response['error'])
        return response['result']
    def freeze(self): return self.call('commissioning_proof.freeze',spec=self.spec)['id']
    def analyze(self,id,p): return self.call('commissioning_proof.analyze',id=id,pairs=p)
    def error(self,op,code,**args):
        r=self.app.execute(op,args); self.assertFalse(r['ok']); self.assertEqual(r['error']['code'],code)
    def test_useful_persistence_and_immutable_concurrent_finalize(self):
        id=self.freeze(); p=pairs(self.call,self.spec,self.holds)
        with ThreadPoolExecutor(2) as ex:
            results=list(ex.map(lambda _:self.app.execute('commissioning_proof.analyze',dict(id=id,pairs=p)),range(2)))
        self.assertTrue(all(r['ok'] for r in results))
        self.assertEqual(results[0]['result']['receipt_sha256'],results[1]['result']['receipt_sha256'])
        r=Application(self.tmp.name).execute('commissioning_proof.get',dict(id=id))['result']
        self.assertEqual(r['report']['recommendation'],'keep'); self.assertFalse(r['verified_acoustically'])
        code="import json,sys; from garage_audio.application import Application; print(json.dumps(Application(sys.argv[1]).execute('commissioning_proof.get',{'id':sys.argv[2]})))"
        fresh=json.loads(subprocess.check_output([sys.executable,'-c',code,self.tmp.name,id],text=True))
        self.assertEqual(fresh['result']['receipt_sha256'],r['receipt_sha256'])
        p[0]['guided_qc']['clipped']=True
        self.error('commissioning_proof.analyze','proof_immutable',id=id,pairs=p)
    def test_no_change(self):
        id=self.freeze(); r=self.analyze(id,pairs(self.call,self.spec,self.holds,guided=(65,75,65)))
        self.assertEqual(r['report']['recommendation'],'reject')
    def test_worse(self):
        id=self.freeze(); r=self.analyze(id,pairs(self.call,self.spec,self.holds,guided=(40,50,40)))
        self.assertEqual(r['report']['recommendation'],'reject')
    def test_uniform_attenuation(self):
        id=self.freeze(); r=self.analyze(id,pairs(self.call,self.spec,self.holds,baseline=(80,80,80),guided=(70,70,70)))
        self.assertEqual(r['report']['metrics'][0]['attenuation_db'],10)
        self.assertEqual(r['report']['recommendation'],'reject')
    def test_branch_only(self):
        self.spec['channels']=['left']; id=self.freeze()
        r=self.analyze(id,pairs(self.call,self.spec,self.holds))
        self.assertFalse(r['report']['combined_system_imports_complete'])
        self.assertEqual(r['report']['recommendation'],'inconclusive')
    def test_bad_qc(self):
        id=self.freeze(); p=pairs(self.call,self.spec,self.holds)
        p[0]['guided_qc'].update(provenance='unknown',clipped=True,noise_margin_db=0,level_chain_known=False)
        self.assertEqual(self.analyze(id,p)['report']['recommendation'],'inconclusive')
    def test_synthetic_and_unverified(self):
        id=self.freeze(); p=pairs(self.call,self.spec,self.holds,source='synthetic')
        self.error('commissioning_proof.analyze','source_invalid',id=id,pairs=p)
        s=self.app.read('sessions',self.spec['baseline_session_id']); s['files'][0]['metadata'].pop('source')
        self.app.save('sessions',s)
        self.error('commissioning_proof.analyze','source_invalid',id=id,pairs=p)
    def test_chain_mismatch(self):
        id=self.freeze(); p=pairs(self.call,self.spec,self.holds)
        for k,v in [('hardware_gain_db',1),('microphone_serial','other'),('calibration_sha256','b'*64),('orientation','0'),('sample_rate',44100),('setup_id','other')]:
            changed=copy.deepcopy(p); changed[0]['guided_qc']['chain'][k]=v
            self.error('commissioning_proof.analyze','source_chain_mismatch',id=id,pairs=changed)
        original=self.app.read('sessions',self.spec['guided_session_id'])
        for key,value in [('orientation','0'),('sample_rate',44100),('microphone_serial','other'),('calibration_sha256','b'*64),('timing_reference','other')]:
            s=copy.deepcopy(original); s['files'][0]['metadata'][key]=value; self.app.save('sessions',s)
            self.error('commissioning_proof.analyze','source_chain_mismatch',id=id,pairs=p)
        self.app.save('sessions',original)
    def test_unknown_and_failed_qc_individually(self):
        for change in [dict(provenance='unknown'),dict(clipped='unknown'),dict(clipped=True),dict(noise_margin_db=0),dict(level_chain_known=False)]:
            # Each is a separate immutable trial, never a reanalysis of a finalized pass.
            self.spec['baseline_session_id']=self.call('sessions.create',name='Baseline QC fixture')['id']
            self.spec['guided_session_id']=self.call('sessions.create',name='Guided QC fixture')['id']
            id=self.freeze(); p=pairs(self.call,self.spec,self.holds); p[0]['guided_qc'].update(change)
            self.assertEqual(self.analyze(id,p)['report']['recommendation'],'inconclusive')
    def test_extra_capture_and_import_timestamp(self):
        id=self.freeze(); p=pairs(self.call,self.spec,self.holds)
        sid=self.spec['guided_session_id']; original=self.app.read('sessions',sid)
        s=copy.deepcopy(original); s['files'][0]['imported_at']=0; self.app.save('sessions',s)
        self.error('commissioning_proof.analyze','protocol_order',id=id,pairs=p)
        self.app.save('sessions',original)
        import_file(self.call,sid,self.holds[0],'left',[70,70,70])
        self.error('commissioning_proof.analyze','budget_exceeded',id=id,pairs=p)
    def test_incomplete_duplicate_fit_and_missing_band(self):
        id=self.freeze(); p=pairs(self.call,self.spec,self.holds)
        self.error('commissioning_proof.analyze','pairs_incomplete',id=id,pairs=p[:-1])
        changed=copy.deepcopy(p); changed[1]['guided']=changed[0]['guided']
        self.error('commissioning_proof.analyze','source_invalid',id=id,pairs=changed)
        changed=copy.deepcopy(p); changed[0]['guided']=self.spec['fit_sources'][0]
        self.error('commissioning_proof.analyze','source_invalid',id=id,pairs=changed)
        s=self.app.read('sessions',self.spec['guided_session_id']); s['files'][0]['points']=s['files'][0]['points'][1:]; self.app.save('sessions',s)
        self.error('commissioning_proof.analyze','band_missing',id=id,pairs=p)
    def test_import_before_freeze(self):
        pairs(self.call,self.spec,self.holds)
        self.error('commissioning_proof.freeze','protocol_order',spec=self.spec)
    def test_stale_sources_map_profiles_and_proposal(self):
        id=self.freeze(); p=pairs(self.call,self.spec,self.holds); self.analyze(id,p)
        targets=[('sessions',self.spec['guided_session_id']),('profiles',self.spec['baseline_profile_id']),('sessions',self.spec['proposal_session_id'])]
        for collection,key in targets:
            original=self.app.read(collection,key); changed=copy.deepcopy(original); changed['name']='changed'
            self.app.save(collection,changed)
            r=self.call('commissioning_proof.get',id=id); self.assertTrue(r['stale']); self.assertIsNone(r['report'])
            self.app.save(collection,original)
        room=self.app.room_profiles._read('rooms',self.room['id']); room['annotations_confirmed']=False
        self.app.room_profiles._save('rooms',room)
        self.assertTrue(self.call('commissioning_proof.get',id=id)['stale'])
    def test_strict_schema_and_budget(self):
        s=copy.deepcopy(self.spec); s['grid_hz']=[100,30]
        self.error('commissioning_proof.freeze','invalid_arguments',spec=s)
        s=copy.deepcopy(self.spec); s['capture_budget']=1
        self.error('commissioning_proof.freeze','budget_exceeded',spec=s)
        s=copy.deepcopy(self.spec); s['target_db']=float('nan')
        self.error('commissioning_proof.freeze','invalid_arguments',spec=s)
    def test_typed_candidate_preserves_alignment_and_current_provenance(self):
        base=self.app.read('profiles',self.spec['baseline_profile_id'])
        base['profile']['channels']['left'].update(delay_ms=3,invert=True,gain=-2)
        self.app.save('profiles',base)
        candidate=self.call('candidates.save',baseline_id=base['id'],proposal_session_id=self.spec['proposal_session_id'],name='Fixture candidate')
        self.assertEqual(candidate['profile']['channels']['left']['delay_ms'],3)
        self.assertTrue(candidate['profile']['channels']['left']['invert'])
        self.assertEqual(candidate['profile']['channels']['left']['gain'],-2)
        self.assertEqual(candidate['profile']['crossover_hz'],base['profile']['crossover_hz'])
        self.assertFalse(candidate['candidate_stale']); self.assertFalse(candidate['applied'])
        s=self.app.read('sessions',self.spec['proposal_session_id']); s['state']='cancelled'; self.app.save('sessions',s)
        self.assertTrue(self.call('profiles.get',id=candidate['id'])['candidate_stale'])
        self.error('candidates.save','candidate_source_invalid',baseline_id=base['id'],proposal_session_id=s['id'],name='Blocked')
    def test_candidate_invalid_baseline_and_proof_list(self):
        base=self.app.read('profiles',self.spec['baseline_profile_id']); base['profile']['peq_bypass']=True; self.app.save('profiles',base)
        self.error('candidates.save','candidate_source_invalid',baseline_id=base['id'],proposal_session_id=self.spec['proposal_session_id'],name='Bypass blocked')
        id=self.freeze(); records=self.call('commissioning_proof.list',listening_profile_id=self.spec['listening_profile_id'])
        self.assertEqual(records[0]['id'],id); self.assertIsNone(records[0]['recommendation'])

class TransportTests(unittest.TestCase):
    def test_real_http_and_sdk_mcp_recorded_fixture(self):
        from mcp import ClientSession,StdioServerParameters
        from mcp.client.stdio import stdio_client
        with tempfile.TemporaryDirectory(dir='/private/tmp/opencode') as tmp:
            app=Application(tmp); token=secrets.token_urlsafe(32)
            tokenfile=Path(tmp)/'credential'; tokenfile.write_text(token); tokenfile.chmod(0o600)
            server=Server(('127.0.0.1',0),app,token,Path(__file__).resolve().parents[1]/'web')
            origin='http://127.0.0.1:'+str(server.server_address[1])
            thread=threading.Thread(target=server.serve_forever,daemon=True); thread.start()
            opener=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
            def post(path,body,csrf=''):
                req=urllib.request.Request(origin+path,json.dumps(body).encode(),{'Content-Type':'application/json','Origin':origin,'X-CSRF-Token':csrf})
                with opener.open(req) as response:return json.load(response)
            try:
                csrf=post('/api/login',{'token':token})['result']['csrf']
                def call(op,**args):
                    r=post('/api/operations',{'operation':op,'args':args},csrf)
                    self.assertTrue(r['ok'],r['error']); return r['result']
                spec,holds,_=build(call)
                async def run():
                    env=dict(os.environ,GARAGE_APP_URL=origin,GARAGE_APP_TOKEN_FILE=str(tokenfile))
                    async with stdio_client(StdioServerParameters(command=sys.executable,args=['-m','garage_audio.mcp_server'],env=env)) as (r,w):
                        async with ClientSession(r,w) as client:
                            await client.initialize()
                            async def operation(op,**args):
                                result=await client.call_tool('garage_operation',{'operation':op,'args':args})
                                return json.loads(next(c.text for c in result.content if c.type=='text'))
                            candidate=await operation('candidates.save',baseline_id=spec['baseline_profile_id'],proposal_session_id=spec['proposal_session_id'],name='Recorded transport candidate')
                            self.assertTrue(candidate['ok'],candidate['error']); spec['guided_profile_id']=candidate['result']['id']
                            frozen=await operation('commissioning_proof.freeze',spec=spec)
                            self.assertTrue(frozen['ok'],frozen['error']); id=frozen['result']['id']
                            p=pairs(call,spec,holds)
                            bad=await operation('commissioning_proof.analyze',id=id,pairs=p[:-1])
                            self.assertEqual(bad['error']['code'],'pairs_incomplete')
                            result=await operation('commissioning_proof.analyze',id=id,pairs=p)
                            self.assertTrue(result['ok'],result['error'])
                            read=await operation('commissioning_proof.get',id=id)
                            http=call('commissioning_proof.get',id=id)
                            self.assertEqual(read['result']['receipt_sha256'],http['receipt_sha256'])
                            self.assertEqual(http['report']['recommendation'],'keep')
                            self.assertFalse(http['verified_acoustically'])
                            listed=await operation('commissioning_proof.list',listening_profile_id=spec['listening_profile_id'])
                            self.assertEqual(listed['result'][0]['id'],id)
                            blocked=await operation('dsp.unmute')
                            self.assertFalse(blocked['ok'])
                asyncio.run(run())
            finally:
                server.shutdown(); thread.join(); server.server_close()

if __name__=='__main__': unittest.main()
