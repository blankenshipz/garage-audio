import test from 'node:test';
import assert from 'node:assert/strict';
import {flowSubmitArgs,flowExpired,flowTerminal,retainedFlow,volumePolicyState} from '../spotify-setup.js';
test('backend submits only owned flow and reviewed step; never provider fields',()=>{
 assert.deepEqual(flowSubmitArgs({id:'owned',step:'backend'},{provider:'other',player:'other'}),{id:'owned',step:'backend'});
});
test('terms default false and require explicit boolean true',()=>{
 const flow={id:'owned',step:'soloist_terms'};
 assert.deepEqual(flowSubmitArgs(flow),{id:'owned',step:'soloist_terms',accept_terms:false});
 assert.equal(flowSubmitArgs(flow,{acceptTerms:'yes'}).accept_terms,false);
 assert.equal(flowSubmitArgs(flow,{acceptTerms:true}).accept_terms,true);
});
test('key submission is only fixed private reference or explicitly available retained key',()=>{
 const flow={id:'owned',step:'soloist_api_key'};
 assert.deepEqual(flowSubmitArgs(flow,{key:'not sent',credential_ref:'arbitrary'}),{id:'owned',step:'soloist_api_key',credential_ref:'spotify-soloist'});
 assert.throws(()=>flowSubmitArgs(flow,{keepStoredKey:true}),/No stored key/);
 assert.deepEqual(flowSubmitArgs({...flow,fields:[{name:'keep_stored_key',available:true}]},{keepStoredKey:true}),{id:'owned',step:'soloist_api_key',keep_stored_key:true});
 assert.throws(()=>flowSubmitArgs({id:'owned',step:'unreviewed'}),/reviewed setup step/);
});
test('expiry blocks only an active setup form, not terminal readback',()=>{
 assert.equal(flowExpired({type:'form',expires_at:1},2000),true);
 assert.equal(flowExpired({type:'finish',expires_at:1},2000),false);
 assert.equal(flowExpired({type:'abort',expires_at:1},2000),false);
 assert.equal(flowExpired({type:'form',expires_at:3},2000),false);
});
test('interrupted setup is terminal, cannot submit old consent/key and permits fresh state selection',()=>{
 const interrupted={id:'old',type:'interrupted',step:'interrupted',expires_at:1,restart_required:true};
 assert.equal(flowTerminal(interrupted),true);assert.equal(flowExpired(interrupted,2000),false);
 assert.throws(()=>flowSubmitArgs(interrupted,{acceptTerms:true,keepStoredKey:true}),/cannot be continued/);
 assert.equal(retainedFlow({active_flow:null,last_flow:interrupted}),interrupted);
 const fresh={id:'new',type:'form',step:'soloist_terms'};
 assert.equal(retainedFlow({active_flow:fresh,last_flow:interrupted}),fresh);
 assert.equal(flowSubmitArgs(fresh).accept_terms,false);
});
test('offline does not reinterpret a cached active flow as interrupted or complete',()=>{
 const cached={id:'old',type:'form',step:'soloist_terms'};
 assert.equal(retainedFlow(null,cached),cached);assert.equal(flowTerminal(cached),false);
});
test('policy states distinguish unavailable, mismatched and stored-but-unqualified without fake readiness',()=>{
 assert.equal(volumePolicyState(null).code,'status_unavailable');
 assert.equal(volumePolicyState({configured:false}).code,'provider_setup_required');
 assert.equal(volumePolicyState({configured:true,volume_policy_available:false}).code,'required_volume_policy_unavailable');
 const stored={configured:true,backend:'soloist',required_volume_mode:'spotify_authoritative',configured_volume_mode:'player_only',volume_policy_available:true,volume_policy_configured:false};
 assert.equal(volumePolicyState(stored).code,'required_volume_policy_mismatch');
 const result=volumePolicyState({...stored,configured_volume_mode:'spotify_authoritative',volume_policy_configured:true});
 assert.equal(result.code,'runtime_qualification_required');assert.match(result.text,/not qualified/);
});
