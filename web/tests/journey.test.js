import {test} from 'node:test';
import assert from 'node:assert/strict';
import {STAGES,stageForTarget,stageGuidance} from '../journey.js';
test('chat-first stages retain deep links and separate setup from advanced controls',()=>{
  assert.deepEqual(STAGES,['connect','setup','check','calibration','improve','verify','advanced']);assert.equal(stageForTarget('jobs'),'calibration');assert.equal(stageForTarget('profiles'),'improve');assert.equal(stageForTarget('diagnostics'),'advanced');assert.equal(stageForTarget('arbitrary'),null);
});
test('room and import deep links reveal the correct existing stage',()=>{assert.equal(stageForTarget('room-workspace'),'check');assert.equal(stageForTarget('measurement-import'),'calibration');});
test('stage guidance never equates copying or navigation with agent connection',()=>{
  assert.match(stageGuidance('connect',{authenticated:true}),/without hardware/);assert.match(stageGuidance('calibration',{authenticated:true}),/Choose your room/);assert.match(stageGuidance('connect'),/Unlock/);assert.doesNotMatch(stageGuidance('connect',{authenticated:true}),/agent connected|approved/);
});
test('running cancellation is explained from every stage without sound authority',()=>{
  for(const stage of STAGES){const guidance=stageGuidance(stage,{authenticated:true,job:{state:'running'}});assert.match(guidance,/Cancel remains available/);assert.match(guidance,/no hardware or sound/);}
});
test('superseded and predicted results are not current acoustic success',()=>{
  assert.match(stageGuidance('improve',{authenticated:true,session:{derived_results_superseded:true}}),/superseded/);assert.doesNotMatch(stageGuidance('improve',{authenticated:true}),/verified acoustically|success/);assert.match(stageGuidance('verify',{authenticated:true}),/without hardware/);
});
