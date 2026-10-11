import {test} from 'node:test';
import assert from 'node:assert/strict';
import {MCP_CONFIG,workflowState,evidenceLabel,optionalClockDrift,canPropose,fitSummary,nextStep,qualifiedSlots} from '../workflow.js';
test('SSH MCP config contains no browser token, TTY or paid service',()=>{
  const c=MCP_CONFIG.mcpServers['garage-audio'];assert.equal(c.command,'ssh');assert.ok(c.args.includes('-T'));assert.ok(c.args.includes('garage_audio.mcp_server'));assert.ok(!JSON.stringify(c).includes('token'));
});
test('superseded results direct fresh analysis instead of implying a current proposal',()=>{
  const session={derived_results_superseded:true,recompute_required:true,state:'imported',analysis:{proposal_eligibility:{eligible:true}},files:[{format:'rew-response'}]};
  assert.match(workflowState({authenticated:true,status:{},session}).next,/Reanalyze/);assert.equal(canPropose(session),false);
  assert.match(workflowState({authenticated:true,status:{},session:{...session,state:'cancelled'}}).next,/cancelled/);
});
test('proposal eligibility fails closed for absent, blocked or stale analysis',()=>{
  assert.equal(canPropose({analysis:{}}),false);assert.equal(canPropose({analysis:{proposal_eligibility:{eligible:false}}}),false);
  const session={state:'analyzed',analysis:{proposal_eligibility:{eligible:true}}};assert.equal(canPropose(session),true);assert.equal(canPropose({...session,recompute_required:true}),false);
  assert.match(workflowState({authenticated:true,status:{},session:{analysis:{proposal_eligibility:{eligible:false}}}}).next,/known clock drift/);
});
test('optional clock drift preserves unknown rather than inventing zero and validates exact numeric bounds',()=>{
  for(const blank of ['', ' ',null,undefined])assert.deepEqual(optionalClockDrift(blank),{});
  for(const value of [-1000,0,1000,'-12.5','1e2'])assert.equal(optionalClockDrift(value).clock_drift_ppm,Number(value));
  for(const invalid of ['NaN','Infinity','0x10',true,1000.1,-1000.1,'10 ppm'])assert.throws(()=>optionalClockDrift(invalid),RangeError);
});
test('fit validation renders aggregate and both per-position RMS measures as predictions',()=>{
  const summary=fitSummary({selection_message:'Predicted fit improved',target_fit:{before:{aggregate_rms_db:3,positions:[{file_id:'f',position:'seat',grid_rms_db:3,import_rms_db:4}]},after:{aggregate_rms_db:2,positions:[{file_id:'f',position:'seat',grid_rms_db:2,import_rms_db:3}]},aggregate_improved:true,nonworsening_each_position:true}}).join('\n');
  assert.match(summary,/3\.000 → 2\.000/);assert.match(summary,/native-band RMS 4\.000 → 3\.000/);assert.match(summary,/not measured improvement/);
});
test('no useful cut is an honest unchanged outcome, not electrical-only proof of fit',()=>{
  assert.match(fitSummary({useful_cut_available:false}).join('\n'),/leave unchanged/);assert.match(fitSummary({validation_passed:true}).join('\n'),/electrical validation alone is not target-fit proof/);
});
test('login is neither agent connection nor microphone readiness',()=>{
  const s=workflowState({authenticated:true,status:{dsp:{available:false}}});assert.match(s.connection,/agent connection not reported/);assert.match(s.checks.find(c=>c[0]==='Microphone & output')[1],/Not verified/);assert.match(s.next,/Choose your room/);
});
test('stale status and absent capabilities never imply readiness',()=>{
  assert.match(workflowState({authenticated:true}).next,/without hardware/);assert.deepEqual(workflowState().operations,[]);assert.doesNotMatch(workflowState({authenticated:true}).connection,/agent connected/);
});
test('guidance counts qualified unique slots, not file count, and never fits holds',()=>{
 const session={plan:{positions:['p'],channels:['left'],sample_rate:48000,orientation:'90'},files:[]};
 const context={authenticated:true,room:{name:'Room',annotations_confirmed:true},profile:{name:'Goal',fit_positions:[{position_id:'p'}],verify_positions:['v']},session};
 assert.match(nextStep(context).reason,/plan is saved/);session.files=[{format:'rew-response',metadata:{source:'measured',calibration_applied:true,calibration_sha256:'a'.repeat(64),sample_rate:48000,orientation:'90',timing_reference:'none',position:'p',channel:'left'}}];assert.equal(qualifiedSlots(session).complete,true);assert.equal(nextStep(context).stage,'improve');session.files.push(session.files[0]);assert.equal(qualifiedSlots(session).complete,false);session.plan.positions=['v'];assert.match(nextStep(context).reason,/fitting-only/);
});
test('only server published capabilities are advertised',()=>{
  assert.deepEqual(workflowState({capabilities:{operations:{'sessions.list':{}}}}).operations,['sessions.list']);
});
test('evidence types stay explicitly distinct',()=>{
  assert.match(evidenceLabel({synthetic:true}),/SYNTHETIC/);assert.match(evidenceLabel({data_kind:'electrical'}),/ELECTRICAL/);assert.match(evidenceLabel({format:'rew-response'}),/not independent/);
  assert.match(evidenceLabel({metadata:{source:'synthetic'}}),/SYNTHETIC/);assert.match(evidenceLabel({source:'synthetic'}),/SYNTHETIC/);
});
