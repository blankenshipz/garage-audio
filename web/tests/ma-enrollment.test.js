// Synthetic unit boundary only: no real login, issuance, private installer or rendered acceptance.
import test from 'node:test';
import assert from 'node:assert/strict';
import {MAEnrollmentPanel,enrollmentReady,enrollmentText,enrollmentRequestID} from '../ma-enrollment.js';
import {GarageAPI} from '../api.js';
const id='fixture_request_123456';
test('request ID works with getRandomValues only, as on trusted LAN HTTP',()=>{const id=enrollmentRequestID();assert.match(id,/^[a-f0-9]{32}$/);assert.notEqual(id,enrollmentRequestID());});
function fixture(request){
  const fields=Object.fromEntries(['ma-username','ma-password','ma-enrollment-connect','ma-status-refresh','ma-enrollment-cancel','ma-enrollment-status','ma-enrollment-feedback','ma-username-error','ma-password-error'].map(k=>[k,{value:'',textContent:'',disabled:false,setAttribute(){}}]));
  const saved=new Map(),calls=[];
  const panel=Object.assign(Object.create(MAEnrollmentPanel.prototype),{authenticated:true,pending:false,requestID:null,status:{state:'setup_required',code:'ma_credential_required'},el:k=>fields[k],storage:{setItem:(k,v)=>saved.set(k,v),removeItem:k=>saved.delete(k)},onReadback:async()=>{},api:{request:async(path,body)=>{calls.push({path,keys:body?Object.keys(body):[],id:body?.request_id,confirm:body?.confirm});return request(path,body);}}});
  const fill=()=>{fields['ma-username'].value='synthetic-unit-user';fields['ma-password'].value='synthetic-unit-password';};
  return {panel,fields,saved,calls,fill};
}
test('ready requires strict named server state, verified scope and enrollment identity',()=>{
  assert.equal(enrollmentReady({state:'ready',scope_verified:true,enrollment_id:id}),true);
  for(const state of [{state:'connected',scope_verified:true,enrollment_id:id},{state:'ready',scope_verified:false,enrollment_id:id},{state:'ready',scope_verified:true}])assert.equal(enrollmentReady(state),false);
  assert.match(enrollmentText({state:'unavailable',code:'ma_installer_unavailable'}),/unavailable/);
});
test('empty Connect gives field-specific visible status without a request; missing password keeps autofill username',async()=>{
  const f=fixture(async()=>{throw new Error('No request expected');});await f.panel.connect();assert.equal(f.calls.length,0);assert.match(f.fields['ma-enrollment-status'].textContent,/Enter your existing Music Assistant username/);assert.equal(f.fields['ma-username-error'].textContent,f.fields['ma-enrollment-feedback'].textContent);
  f.fields['ma-username'].value='synthetic-unit-user';await f.panel.connect();assert.equal(f.calls.length,0);assert.match(f.fields['ma-password-error'].textContent,/Enter your Music Assistant password/);assert.equal(f.fields['ma-username'].value,'synthetic-unit-user');
  f.fields['ma-username'].value='';f.fields['ma-password'].value='synthetic-unit-password';await f.panel.connect();assert.equal(f.calls.length,0);assert.equal(f.fields['ma-password'].value,'');
});
test('Connect uses dedicated POST then authoritative status; clears inputs immediately and persists only request ID',async()=>{
  let f;f=fixture(async(path,body)=>{
    if(body){assert.equal(f.fields['ma-password'].value,'');assert.equal(f.fields['ma-username'].value,'');assert.equal(f.saved.size,1);assert.equal([...f.saved.values()][0],body.request_id);assert.equal(body.confirm,true);return {state:'ready',scope_verified:true,enrollment_id:body.request_id};}
    return {state:'ready',scope_verified:true,enrollment_id:id};
  });f.fill();await f.panel.connect();
  assert.deepEqual(f.calls.map(c=>c.path),['/api/setup/ma/enroll','/api/setup/ma/status']);assert.deepEqual(f.calls[0].keys,['username','password','request_id','confirm']);
  assert.equal(f.saved.size,0);assert.equal(enrollmentReady(f.panel.status),true);assert.equal(f.fields['ma-enrollment-connect'].disabled,true);assert.match(f.fields['ma-enrollment-status'].textContent,/both private credential copies/);
});
test('uncertain issuance never retries; reload-like retained ID requires reconciliation until verified ready',async()=>{
  const f=fixture(async()=>{throw {code:'connection_unavailable',message:'must not echo synthetic credentials'};});f.fill();await f.panel.connect();await f.panel.connect();
  assert.equal(f.calls.length,1);assert.equal(f.saved.size,1);assert.match(f.fields['ma-enrollment-status'].textContent,/Reconciliation is required/);assert.doesNotMatch(f.fields['ma-enrollment-status'].textContent,/synthetic/);
  f.panel.api.request=async()=>({state:'setup_required',code:'ma_credential_required'});await f.panel.refresh();assert.ok(f.panel.requestID);assert.equal(f.fields['ma-enrollment-connect'].disabled,true);
  f.panel.api.request=async()=>({state:'ready',scope_verified:true,enrollment_id:id});await f.panel.refresh();assert.equal(f.panel.requestID,null);
});
test('authentication failure allows deliberate new submission, clears credentials, no automatic retry',async()=>{
  const f=fixture(async()=>{throw {code:'ma_authentication_failed',message:'unsafe upstream text'};});f.fill();await f.panel.connect();assert.equal(f.calls.length,1);assert.equal(f.saved.size,0);assert.equal(f.fields['ma-password'].value,'');assert.equal(f.fields['ma-enrollment-connect'].disabled,false);assert.doesNotMatch(f.fields['ma-enrollment-status'].textContent,/unsafe/);
});
test('cancel while pending clears inputs but cannot undo issuance or enable a duplicate Connect',async()=>{
  let finish;const wait=new Promise(r=>finish=r);const f=fixture(async(path,body)=>body?wait:{state:'ready',scope_verified:true,enrollment_id:id});f.fill();const running=f.panel.connect();assert.equal(f.panel.pending,true);assert.equal(f.fields['ma-enrollment-connect'].disabled,true);f.panel.clearCredentials();await f.panel.connect();assert.equal(f.calls.length,1);assert.ok(f.panel.requestID);finish({state:'ready'});await running;assert.equal(f.fields['ma-password'].value,'');
});
test('unavailable installer disables form, no optimistic ready and malformed ready is denied',()=>{
  const f=fixture(async()=>{});f.panel.acceptStatus({state:'unavailable',code:'ma_installer_unavailable'});assert.equal(f.fields['ma-enrollment-connect'].disabled,true);assert.match(f.fields['ma-enrollment-status'].textContent,/installation is unavailable/);
  f.panel.acceptStatus({state:'ready',scope_verified:false,enrollment_id:id});assert.equal(f.fields['ma-enrollment-connect'].disabled,true);assert.doesNotMatch(f.fields['ma-enrollment-status'].textContent,/Music Assistant connected/);
});
test('dedicated request keeps same-origin session/CSRF without credential URLs or general operation dispatch',async()=>{
  let received;const api=new GarageAPI(async(path,options)=>{received={path,options};return {ok:true,json:async()=>({ok:true,result:{state:'setup_required'}})};});api.csrf='synthetic-csrf';await api.request('/api/setup/ma/enroll',{username:'synthetic-unit-user',password:'synthetic-unit-password',request_id:id,confirm:true});assert.equal(received.path,'/api/setup/ma/enroll');assert.equal(received.options.credentials,'same-origin');assert.equal(received.options.headers['X-CSRF-Token'],'synthetic-csrf');assert.equal(received.options.method,'POST');
});
