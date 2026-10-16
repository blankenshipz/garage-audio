import {test} from 'node:test';
import assert from 'node:assert/strict';
import {lifecyclePermissions,sourceLabel} from '../calibration-run.js';
import {jobPermissions,jobSummary} from '../jobs.js';
test('lifecycle accepts only current prepared FILE revision, owned rollback survives changed inputs',()=>{
 const config={layout:'stereo'},tx={state:'prepared',base_revision:0,configuration:config};
 const state={transaction:tx,runtime:{binding:{kind:'file'},revision:0,configuration:config},inputs_current:true};
 assert.equal(lifecyclePermissions(state).apply,true);state.runtime.binding.kind='hardware';assert.equal(lifecyclePermissions(state).apply,false);
 state.transaction={...tx,state:'accepted_for_next_play',accepted_revision:1};state.runtime.revision=1;state.inputs_current=false;
 assert.equal(lifecyclePermissions(state).rollback,true);assert.equal(lifecyclePermissions(state).verify,false);
 state.runtime.revision=2;assert.equal(lifecyclePermissions(state).rollback,false);
 assert.match(sourceLabel('synthetic'),/no acoustic proof/);
 state.runtime.configuration_readback_available=false;assert.deepEqual(lifecyclePermissions(state),{apply:false,rollback:false,verify:false});
});
test('hardware execution needs server exact approval, v4 binding and explicit available human channel',()=>{
 const hash='a'.repeat(64),job={adapter:'hardware',state:'approved',hardware_enabled:true,sound_disabled:false,human_approval_available:true,manifest_sha256:hash,manifest:{version:4,correction_render:{binding:{binding:{kind:'hardware'}}}},approval:{approved:true,expires_at:200,manifest_sha256:hash}};
 assert.equal(jobPermissions(job,null,100).execute,true);
 for(const changed of [{human_approval_available:false},{sound_disabled:true},{manifest:{version:3}},{hardware_enabled:false}])assert.equal(jobPermissions({...job,...changed},null,100).execute,false);
 assert.match(jobSummary(job).flat().join(' '),/microphone acquisition/);assert.doesNotMatch(jobSummary(job).flat().join(' '),/Synthetic file/);
});
