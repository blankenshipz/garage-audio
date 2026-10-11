import {test} from 'node:test';
import assert from 'node:assert/strict';
import {JobsPanel,jobPermissions,jobSummary,jobStatus,confidentialOrigin,humanAccessAllowed,humanAccessHelp,humanReadiness,fileBackendReadiness,fileBackendHelp,nativeFileSummary} from '../jobs.js';
import {GarageAPI} from '../api.js';
const hash='a'.repeat(64),job={state:'awaiting_human',manifest_sha256:hash,manifest:{realm:'test',signal:{peak_dbfs:-40,band_hz:[20,20000],signal_seconds:4,sweep_seconds:2},budgets:{total_seconds:20}},adapter:'file-null',hardware_enabled:false,sound_disabled:true};
const human={role:'human',realm:'test',fresh_until:200,expires_at:300};
test('separate human review requires fresh matching realm and exact manifest',()=>{
  assert.equal(jobPermissions(job,null,100).approve,false);assert.equal(jobPermissions(job,{role:'automation'},100).approve,false);
  assert.equal(jobPermissions(job,human,100).approve,true);assert.equal(jobPermissions(job,human,250).approve,false);assert.equal(jobPermissions(job,{...human,realm:'other'},100).approve,false);assert.equal(jobPermissions({...job,manifest_sha256:'bad'},human,100).approve,false);
});
test('execute is gated by bounded exact public approval and file/null safety boundary',()=>{
  const approved={...job,state:'approved',approval:{approved:true,manifest_sha256:hash,expires_at:200}};
  assert.equal(jobPermissions(approved,null,100).execute,true);assert.equal(jobPermissions(approved,null,201).execute,false);
  for(const patch of [{hardware_enabled:true},{sound_disabled:false},{adapter:'hardware'},{state:'running'},{approval:{approved:true,manifest_sha256:'b'.repeat(64),expires_at:200}}])assert.equal(jobPermissions({...approved,...patch},null,100).execute,false);
});
test('cancel and revoke remain possible during running; terminal jobs cannot reuse authority',()=>{
  assert.equal(jobPermissions({...job,state:'running'},human,100).cancel,true);assert.equal(jobPermissions({...job,state:'running'},human,100).revoke,true);
  for(const state of ['completed','failed','cancelled','revoked','expired','interrupted','invalidated']){assert.equal(jobPermissions({...job,state},human,100).cancel,false);assert.equal(jobPermissions({...job,state},human,100).approve,false);}
});
test('human endpoints are only offered on exact loopback hostnames',()=>{
  for(const host of ['localhost','127.0.0.1'])assert.equal(confidentialOrigin(host),true);
  for(const host of ['192.168.68.74','localhost.evil','[::1]'])assert.equal(confidentialOrigin(host),false);
});
test('localhost is insufficient: false or unknown backend readiness hides human entry',()=>{
  for(const readiness of [null,undefined,{}, {human_approval_available:false},{human_approval_available:'true'}])assert.equal(humanAccessAllowed(readiness,'localhost'),false);
  assert.equal(humanAccessAllowed({human_approval_available:true},'127.0.0.1'),true);assert.equal(humanAccessAllowed({human_approval_available:true},'192.168.68.74'),false);
  assert.match(humanAccessHelp(null),/unknown/);assert.match(humanAccessHelp({human_approval_available:false,reason:'shared listener'}),/shared listener/);assert.match(humanAccessHelp(null),/even through a localhost SSH tunnel/);
});
test('readiness uses actual authenticated status fields, never a URL or inferred permission',()=>{
  assert.deepEqual(humanReadiness({human_approval_available:true,human_approval_unavailable_reason:null}),{human_approval_available:true,reason:null});
  const falseStatus=humanReadiness({human_approval_available:false,human_approval_unavailable_reason:'shared listener'});assert.equal(humanAccessAllowed(falseStatus,'localhost'),false);assert.match(humanAccessHelp(falseStatus),/shared listener/);
  for(const missing of [null,{}, {human_approval_available:'true'}])assert.equal(humanAccessAllowed(humanReadiness(missing),'localhost'),false);
});
test('FILE readiness is explicit, tri-state and never falls back or implies isolation proof',()=>{
  for(const status of [null,{}, {camilla_file_available:'true'}]){assert.equal(fileBackendReadiness(status).available,null);assert.match(fileBackendHelp(fileBackendReadiness(status)),/not reported/);}
  const missing=fileBackendReadiness({camilla_file_available:false,camilla_file_unavailable_reason:'Pinned binary missing'});assert.match(fileBackendHelp(missing),/Pinned binary missing/);assert.match(fileBackendHelp(missing),/No legacy or fake FILE fallback/);
  assert.match(fileBackendHelp(fileBackendReadiness({camilla_file_available:true})),/not a completed render or isolation result/);assert.match(fileBackendHelp({available:true}),/Capture remains synthetic/);
});
test('native FILE report distinguishes a bound route from observed execution',()=>{
  assert.match(nativeFileSummary({}).join('\n'),/no DSP render or isolation proof/);
  const manifest={native_file_route:{binary_version:'CamillaDSP 4.1.3',software_index:0,physical_index:2,physical_channels:20,software_probe_channels:3}};
  assert.match(nativeFileSummary({manifest}).join('\n'),/No observed FILE render evidence yet/);
  const e={exit_status:0,input_frames:84000,output_frames:84992,requested_physical_index:2,requested_software_index:0,physical_peaks:[0,0,.0001],physical_rms:[0,0,.00005],software_peaks:[.0001,0,0],software_rms:[.00005,0,0],all_other_outputs_exact_zero:true,input_sha256:'input',output_sha256:'output',config_sha256:'config'};
  const report=nativeFileSummary({manifest,evidence:{native_file_render:e}}).join('\n');assert.match(report,/child exit: 0/);assert.match(report,/84000 in \/ 84992 out/);assert.match(report,/reported yes/);assert.match(report,/No physical device/);assert.match(report,/production-service isolation/);
  assert.match(nativeFileSummary({manifest,evidence:{native_file_render:{exit_status:17,all_other_outputs_exact_zero:false}}}).join('\n'),/reported no/);
});
test('FILE result headline requires observed verified exit and retains synthetic boundary',()=>{
  const job={manifest:{native_file_route:{physical_index:2,software_index:0,master_db:-60,headroom_db:-6,volume_ceiling_db:-20}},descendant_reaping:{owned_group_children_reaped:true},evidence:{approved_manifest_sha256:'approved',native_file_render:{synthetic_reference_normalization_db:66,exit_status:0,native_child_exit_verified:true,all_other_outputs_exact_zero:true}}};
  const report=nativeFileSummary(job);assert.match(report[0],/child exit verified \(0\)/);assert.match(report[0],/reported exactly zero/);assert.match(report[0],/Synthetic capture · acoustically unverified/);assert.match(report.join('\n'),/physical test index 2/);assert.match(report.join('\n'),/not garage commissioning/);assert.match(report.join('\n'),/normalization: 66 dB; not calibration/);assert.match(report.join('\n'),/children reaped: reported yes/);assert.match(report.join('\n'),/Approved manifest SHA256: approved/);
  for(const e of [{exit_status:0},{exit_status:0,native_child_exit_verified:false},{exit_status:17,native_child_exit_verified:true}])assert.match(nativeFileSummary({evidence:{native_file_render:e}})[0],/not verified/);
  job.evidence.native_file_render.all_other_outputs_exact_zero=false;assert.match(nativeFileSummary(job)[0],/NOT all zero/);delete job.evidence.native_file_render.all_other_outputs_exact_zero;assert.match(nativeFileSummary(job)[0],/isolation not reported/);
});
test('selecting a job refreshes actual boundary readiness and discards unavailable human state',async()=>{
  const calls=[],owner={generation:0,pollTimer:null,hostname:'localhost',human:{role:'human'},humanAPI:{csrf:'synthetic-human'},render(){},scheduleRunningReadback(){},api:{async status(){calls.push('status');return {human_approval_available:false,human_approval_unavailable_reason:'shared listener'};},async operation(op,args){calls.push(op);return {id:args.id,state:'awaiting_human'};}}};
  await JobsPanel.prototype.select.call(owner,'synthetic-id');assert.deepEqual(calls,['status','jobs.get']);assert.equal(humanAccessAllowed(owner.readiness,'localhost'),false);assert.equal(owner.human,null);assert.equal(owner.humanAPI.csrf,null);
});
test('unavailable readiness rejects human actions before any credential callback',async()=>{
  let sent=0;const owner={humanAllowed:()=>false,render(){}};
  for(const key of ['human-login','approve','revoke','human-logout'])await JobsPanel.prototype.action.call(owner,key,async()=>sent++);
  assert.equal(sent,0);assert.match(owner.error,/No human credential request was sent/);
});
test('manifest review separates total deadline, peak, band and file/null output from garage proof',()=>{
  const text=jobSummary(job).flat().join(' ');assert.match(text,/20 seconds \(all stages\)/);assert.match(text,/-40\.00 dBFS/);assert.match(text,/20–20000 Hz/);assert.match(text,/no audio device/);assert.match(jobStatus('completed')[1],/not microphone/);assert.match(jobStatus('interrupted')[0],/Interrupted/);
});
test('action single-flight prevents double execute while separate cancellation remains usable',async()=>{
  let release,count=0;const pending=new Promise(resolve=>release=resolve),owner={pending:new Set(),generation:1,render(){},async refresh(){}};
  const first=JobsPanel.prototype.action.call(owner,'execute',async()=>{count++;await pending;});
  await JobsPanel.prototype.action.call(owner,'execute',async()=>count++);
  await JobsPanel.prototype.action.call(owner,'cancel',async()=>count++);
  assert.equal(count,2);release();await first;assert.equal(owner.pending.size,0);
});
test('late action readback cannot overwrite a new selection or locked view',async()=>{
  let release;const pending=new Promise(resolve=>release=resolve),owner={pending:new Set(),generation:1,render(){},async refresh(){},job:null};
  const action=JobsPanel.prototype.action.call(owner,'request',async()=>{await pending;return {id:'old'};});owner.generation++;release();await action;assert.equal(owner.job,null);
});
test('bounded running readback never invents completion after its deadline',()=>{
  const owner={pollTimer:null,pollUntil:Date.now()-1,job:{state:'running'},context:{authenticated:true},stale:false,render(){}};
  JobsPanel.prototype.scheduleRunningReadback.call(owner);assert.equal(owner.stale,true);assert.equal(owner.job.state,'running');assert.match(owner.error,/no completion is assumed/);
});
test('separate human API client never reuses automation CSRF',async()=>{
  const calls=[],fetcher=async(path,options)=>{calls.push({path,options});return {ok:true,json:async()=>({ok:true,result:{}})};};
  const automation=new GarageAPI(fetcher),humanAPI=new GarageAPI(fetcher);automation.csrf='synthetic-automation';humanAPI.csrf='synthetic-human';
  await automation.operation('jobs.cancel',{id:'test'});await humanAPI.request('/api/human/revoke',{id:'test'});
  assert.equal(calls[0].options.headers['X-CSRF-Token'],'synthetic-automation');assert.equal(calls[1].options.headers['X-CSRF-Token'],'synthetic-human');assert.equal(calls[1].options.credentials,'same-origin');
});
