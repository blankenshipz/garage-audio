import {GarageAPI,ApiError} from './api.js';
import {sourceLabel} from './calibration-run.js';
const terminal=new Set(['completed','failed','cancelled','revoked','expired','interrupted','invalidated']);
export function confidentialOrigin(hostname){return hostname==='127.0.0.1'||hostname==='localhost';}
export function humanReadiness(status){return {human_approval_available:status?.human_approval_available===true,reason:typeof status?.human_approval_unavailable_reason==='string'?status.human_approval_unavailable_reason:null};}
export function fileBackendReadiness(status){return {available:typeof status?.camilla_file_available==='boolean'?status.camilla_file_available:null,reason:typeof status?.camilla_file_unavailable_reason==='string'?status.camilla_file_unavailable_reason:null};}
export function fileBackendHelp(readiness){return readiness?.available===true?'CamillaDSP FILE backend available · disposable offline render only. Availability is not a completed render or isolation result. Capture remains synthetic; hardware has separate admission and authority.':readiness?.available===false?`CamillaDSP FILE backend unavailable${readiness.reason?`: ${readiness.reason}`:''}. No legacy or fake FILE fallback; hardware readiness is separate.`:'CamillaDSP FILE readiness not reported. No render/isolation success is assumed and no legacy or fake FILE fallback is permitted. Hardware admission remains separate.';}
export function nativeFileSummary(job){
  const route=job?.manifest?.native_file_route,e=job?.evidence?.native_file_render;
  if(!route&&!e)return ['Camilla FILE route/result not reported; no DSP render or isolation proof.'];
  const lines=[];if(e){const verified=e.native_child_exit_verified===true&&e.exit_status===0;lines.push(`Disposable Camilla FILE branch render: ${verified?'child exit verified (0)':e.native_child_exit_verified===false?'child exit not verified':'successful child exit not verified'} · unrequested outputs ${e.all_other_outputs_exact_zero===true?'reported exactly zero':e.all_other_outputs_exact_zero===false?'NOT all zero':'isolation not reported'}. Synthetic capture · acoustically unverified.`);}if(route)lines.push(`Bound FILE route: ${route.binary_version||'version unknown'} · software ${route.software_index??'unknown'} → physical test index ${route.physical_index??'unknown'} · ${route.physical_channels??'unknown'} physical + ${route.software_probe_channels??'unknown'} software probes. Test indices are not garage commissioning.`);
  if(!e)return [...lines,'No observed FILE render evidence yet. A manifest or available binary is not execution.'];
  const format=v=>typeof v==='number'&&Number.isFinite(v)?v.toExponential(3):'unknown';
  lines.push(`Observed FILE child exit: ${e.exit_status??'unknown'} · frames ${e.input_frames??'unknown'} in / ${e.output_frames??'unknown'} out.`);
  lines.push(`Other outputs exactly zero: ${e.all_other_outputs_exact_zero===true?'reported yes':e.all_other_outputs_exact_zero===false?'reported no':'not reported'}. Requested physical peak/RMS ${format(e.physical_peaks?.[e.requested_physical_index])} / ${format(e.physical_rms?.[e.requested_physical_index])}; software peak/RMS ${format(e.software_peaks?.[e.requested_software_index])} / ${format(e.software_rms?.[e.requested_software_index])}.`);
  lines.push(`Output/config/input SHA256: ${e.output_sha256||'not reported'} / ${e.config_sha256||'not reported'} / ${e.input_sha256||'not reported'}.`);
  lines.push(`Approved manifest SHA256: ${job.evidence.approved_manifest_sha256||'not reported'}. Binary/library/route SHA256: ${route?.binary_sha256||'not reported'} / ${route?.library_sha256||'not reported'} / ${route?.route_sha256||'not reported'}.`);
  lines.push(`Owned group children reaped: ${job.descendant_reaping?.owned_group_children_reaped===true?'reported yes':job.descendant_reaping?.owned_group_children_reaped===false?'reported no':'not reported'}. Policy master/headroom/ceiling: ${route?.master_db??'unknown'} / ${route?.headroom_db??'unknown'} / ${route?.volume_ceiling_db??'unknown'} dB.`);
  lines.push(`Synthetic virtual microphone reference normalization: ${e.synthetic_reference_normalization_db??'not reported'} dB; not calibration correction or acoustic authority.`);
  lines.push('Real disposable DSP file rendering is separate from synthetic capture. No physical device, production-service isolation or garage acoustic success is demonstrated.');return lines;
}
export function humanAccessAllowed(readiness,hostname){return readiness?.human_approval_available===true&&confidentialOrigin(hostname);}
export function humanAccessHelp(readiness){return readiness?.human_approval_available===true?'Server reports native local human approval availability. Separate provisioned credentials, fresh authentication and exact manifest review are still required; microphone and commissioned-output admission is independent.':`Human approval unavailable${readiness?.reason?`: ${readiness.reason}`:': readiness is unknown; no credentials will be requested or sent.'} Shared/container dashboard approval stays disabled, even through a localhost SSH tunnel. Only a separately verified native local server can offer human login. No hardware authority is inferred.`;}
export function jobPermissions(job,human,now=Date.now()/1000){
  const active=!!job&&!terminal.has(job.state),fresh=human?.role==='human'&&human.fresh_until>now&&human.expires_at>now;
  const admittedAdapter=job?.adapter==='file-null'&&job.hardware_enabled===false&&job.sound_disabled===true||job?.adapter==='hardware'&&job.hardware_enabled===true&&job.sound_disabled===false&&job.manifest?.version===4&&job.manifest?.correction_render?.binding?.binding?.kind==='hardware'&&job.human_approval_available===true;
  return {cancel:active,revoke:active&&human?.role==='human'&&human.expires_at>now,
    approve:active&&job.state==='awaiting_human'&&fresh&&human.realm===job.manifest?.realm&&/^[a-f0-9]{64}$/.test(job.manifest_sha256||''),
    execute:job?.state==='approved'&&job.approval?.approved===true&&job.approval.expires_at>now&&job.approval.manifest_sha256===job.manifest_sha256&&admittedAdapter};
}
export function jobSummary(job){
  const m=job?.manifest||{},s=m.signal||{},b=m.budgets||{};
  if(job?.adapter==='hardware')return [['Action','Bounded microphone acquisition through exact correction graph'],['Output',`Commissioned ${m.channel||'unknown'} · OUT${m.hardware_admission?.policy?.outputs?.[m.channel]??'unknown'}`],['Position / channel',`${m.position||'unknown'} / ${m.channel||'unknown'}`],['Peak / band',`${m.policy?.output_peak_dbfs??'unknown'} dBFS bound · ${s.band_hz?.join('–')||'unknown'} Hz`],['Total deadline',`${b.total_seconds??'unknown'} seconds (all stages)`],['Safety boundary','Requires calibrated mic, commissioned route and separate exact-manifest attended approval'],['Correction revision',String(m.correction_render?.binding?.revision??'unknown')]];
  const actual=typeof s.peak_dbfs==='number'&&Number.isFinite(s.peak_dbfs)?`≈ ${s.peak_dbfs.toFixed(2)} dBFS`:'Unknown peak';const peak=typeof s.maximum_peak_dbfs==='number'?`≤ ${s.maximum_peak_dbfs} dBFS (${actual})`:actual;
  return [['Action',m.native_file_route?'Pinned CamillaDSP FILE render + synthetic capture':'Synthetic file/null excitation, capture & evidence'],['Output',m.devices?.output||'Not reported'],['Position / channel',`${m.position||'Not reported'} / ${m.channel||'Not reported'}`],['Peak / band',`${peak} · ${s.band_hz?.join('–')||'Unknown'} Hz`],['Total deadline',`${b.total_seconds??'Unknown'} seconds (all stages)`],['Signal / sweep',`${s.signal_seconds??'Unknown'}s total / ${s.sweep_seconds??'Unknown'}s sweep`],['Pre / post / fade',`${s.pre_seconds??'Unknown'}s / ${s.post_seconds??'Unknown'}s / ${s.fade_seconds??'Unknown'}s`],['Format',`${s.encoding||'Unknown'} · ${s.sample_rate??'Unknown'} Hz · ${s.sample_frames??'Unknown'} frames`],['Safety boundary','Hardware disabled · no audio device · no sound'],['Authority','One-use, exact manifest · no acoustic approval']];
}
export function jobStatus(state){return {awaiting_human:['Waiting for a separate human','No execution. A manually provisioned human must review this exact synthetic manifest.'],approved:['Approved, not yet executed','One-use authority is time-limited. Refresh before execution; hardware stays disabled.'],running:['Running file/null job','Server reports execution. Cancel stops the owned job; no audio device opens.'],completed:['Synthetic evidence ready','File/null completion is not microphone, room correction or garage proof.'],cancelled:['Cancelled','No further execution authority. Request a fresh job if needed.'],revoked:['Authority revoked','Human or control-context revocation invalidated this job.'],expired:['Approval expired','Do not reuse old authority. Request a fresh job.'],interrupted:['Interrupted by restart','Execution authority is invalid. Inspect retained history.'],invalidated:['Invalidated by restart or context change','No authority resumes. Inspect history and request a fresh job.'],failed:['Job failed','Do not infer success from partial files. Inspect history and evidence.']}[state]||['State not verified','Refresh to read the actual server state.'];}
export class JobsPanel {
  constructor(api,{document=globalThis.document,hostname=globalThis.location.hostname,onState=()=>{}}={}){
    this.onState=onState;this.hardwareReadiness=null;
    this.api=api;this.humanAPI=new GarageAPI(api.fetcher);this.doc=document;this.hostname=hostname;this.readiness=null;this.fileReadiness=null;this.jobs=[];this.job=null;this.human=null;this.pending=new Set();this.stale=true;this.generation=0;this.pollTimer=null;this.pollUntil=null;
    this.$=id=>document.getElementById(id);this.$('jobs-refresh').onclick=()=>this.refresh();this.render();
  }
  el(tag,cls,text){const el=this.doc.createElement(tag);if(cls)el.className=cls;if(text!==undefined)el.textContent=text;return el;}
  setContext({authenticated,capabilities,session,busy,status}){
    const previousStatus=this.context?.status;this.context={authenticated,capabilities,session,busy,status};
    if(status!==previousStatus)this.fileReadiness=status?fileBackendReadiness(status):null;
    if(!authenticated||!status)this.fileReadiness=null;
    if(status!==previousStatus)this.readiness=status?humanReadiness(status):null;
    if(!status){this.readiness=null;this.human=null;this.humanAPI.csrf=null;}
    if(!authenticated){this.generation++;clearTimeout(this.pollTimer);this.pollUntil=null;this.jobs=[];this.job=null;this.stale=true;this.readiness=null;this.hardwareReadiness=null;this.human=null;this.humanAPI.csrf=null;}
    this.render();
  }
  supported(op){return this.context?.authenticated&&!!this.context.capabilities?.operations?.[op];}
  humanAllowed(){return !this.stale&&humanAccessAllowed(this.readiness,this.hostname);}
  async refresh(){
    if(!this.supported('jobs.list')||this.pending.has('refresh'))return;
    const generation=this.generation;this.pending.add('refresh');this.render();
    try{const status=await this.api.status();if(generation!==this.generation)return;this.readiness=humanReadiness(status);this.fileReadiness=fileBackendReadiness(status);const jobs=await this.api.operation('jobs.list');if(generation!==this.generation)return;const selected=this.job?.id,job=selected?await this.api.operation('jobs.get',{id:selected}):null;if(generation!==this.generation)return;this.jobs=jobs;if(selected)this.job=job;this.stale=false;this.error=null;
      if(this.supported('measurement.hardware.status')){try{const hardware=await this.api.operation('measurement.hardware.status');if(generation!==this.generation)return;this.hardwareReadiness=hardware;}catch(e){this.hardwareReadiness={hardware_capture_available:false,requirements:[e.message]};}}
      if(this.humanAllowed()){try{const human=await this.humanAPI.request('/api/human/session');if(generation!==this.generation)return;this.human=human;this.humanAPI.csrf=human.csrf;}catch(e){if(generation!==this.generation)return;this.human=null;this.humanAPI.csrf=null;if(e.status!==401)this.error=e.message;}}else{this.human=null;this.humanAPI.csrf=null;}
    }catch(e){if(generation===this.generation){this.stale=true;this.readiness=null;this.fileReadiness=null;this.human=null;this.humanAPI.csrf=null;this.error=e.message;}}
    finally{this.pending.delete('refresh');this.render();this.scheduleRunningReadback();}
  }
  scheduleRunningReadback(){
    clearTimeout(this.pollTimer);
    if(this.job?.state!=='running'||this.stale||!this.context?.authenticated){this.pollUntil=null;return;}
    if(!this.pollUntil)this.pollUntil=Date.now()+Math.min(60,(this.job.manifest?.budgets?.total_seconds||20)+10)*1000;
    if(Date.now()>=this.pollUntil){this.stale=true;this.error='Automatic readback window ended. Refresh manually; no completion is assumed.';this.render();return;}
    this.pollTimer=setTimeout(()=>this.refresh(),1000);
  }
  async select(id){const generation=++this.generation;clearTimeout(this.pollTimer);this.pollUntil=null;this.job=null;this.stale=true;this.render();try{const status=await this.api.status();if(generation!==this.generation)return;this.readiness=humanReadiness(status);this.fileReadiness=fileBackendReadiness(status);if(!humanAccessAllowed(this.readiness,this.hostname)){this.human=null;this.humanAPI.csrf=null;}const job=await this.api.operation('jobs.get',{id});if(generation!==this.generation)return;this.job=job;this.stale=false;this.error=null;}catch(e){if(generation===this.generation){this.stale=true;this.readiness=null;this.fileReadiness=null;this.human=null;this.humanAPI.csrf=null;this.error=e.message;}}finally{this.render();this.scheduleRunningReadback();}}
  async action(key,fn){
    this.doc?.getElementById('action-status')?.setAttribute('hidden','');
    if(['human-login','human-logout','approve','revoke'].includes(key)&&!this.humanAllowed()){this.error='Human approval readiness unavailable or unknown. No human credential request was sent.';this.render();return;}
    if(this.pending.has(key))return;this.pending.add(key);this.error=null;this.render();
    const generation=this.generation;
    try{const job=await fn();if(generation!==this.generation)return;if(job?.id)this.job=job;if(key==='request')this.doc?.getElementById('job-request-details')?.removeAttribute('open');this.stale=false;await this.refresh();}
    catch(e){if(generation===this.generation){this.error=[e.message,...(e.requirements||[])].join('\n');this.stale=true;}}
    finally{this.pending.delete(key);this.render();}
  }
  button(label,key,enabled,fn){const b=this.el('button',key==='cancel'||key==='revoke'?'danger-button':'',label);b.dataset.jobAction=key;b.disabled=!enabled||this.pending.has(key);b.onclick=()=>this.action(key,fn);return b;}
  render(){
    this.onState?.();
    const c=this.context||{},host=this.$('job-activity'),history=this.$('job-history'),request=this.$('job-request');host.replaceChildren();history.replaceChildren();request.replaceChildren();
    this.$('jobs-refresh').disabled=!this.supported('jobs.list')||this.pending.has('refresh');this.$('jobs-state').textContent=this.stale?'Readback needed':this.job?.state||'History available';
    this.renderHuman();
    if(!c.authenticated){host.append(this.el('p','empty','Unlock to read private job state. Nothing is approved by dashboard access.'));history.append(this.el('p','empty','Unlock to read job history.'));return;}
    if(!this.supported('jobs.request')){host.append(this.el('p','empty','This server has not reported executable job capabilities. Existing offline plans/imports remain available.'));return;}
    const fileStatus=this.el('p','hint',fileBackendHelp(this.stale?null:this.fileReadiness));fileStatus.id='job-file-readiness';host.append(fileStatus);
    this.renderRequest(request);
    if(this.error)host.append(this.el('p','job-error',this.error));
    if(this.pending.size)host.append(this.el('p','hint',`Request in flight: ${[...this.pending].join(', ')}. This is not server completion; Refresh jobs reads actual state.`));
    if(this.stale)host.append(this.el('p','job-error','Job readback is not current. Execute and approval stay disabled until refreshed.'));
    if(this.job)this.renderJob(host);else host.append(this.el('p','empty','Select a job to inspect its server-owned action manifest, state and evidence.'));
    if(!this.jobs.length)history.append(this.el('p','empty','No jobs in the current readback.'));
    for(const job of this.jobs){const row=this.el('div','job-history-row'),body=this.el('div');body.append(this.el('strong','',`${job.manifest?.channel||'Job'} · ${job.manifest?.position||job.id}`),this.el('p','',`${job.state} · ${job.adapter==='hardware'?(job.evidence?sourceLabel(job.evidence.source):'Hardware request · no completed capture'):'SYNTHETIC file/null'} · ${job.id}`));const b=this.el('button','',job.id===this.job?.id?'Selected':'Review job');b.disabled=this.pending.has('refresh');b.onclick=()=>this.select(job.id);row.append(body,b);history.append(row);}
  }
  renderRequest(host){
    const session=this.context.session,p=session?.plan;
    if(!p||session.state==='cancelled'){host.append(this.el('p','hint','Select an active versioned measurement plan below to prepare a bounded synthetic job.'));return;}
    const form=this.el('form','job-request-form');form.id='job-request-form';form.append(this.el('p','eyebrow','PREPARE WITHOUT SOUND'));
    for(const [name,values] of [['position',p.positions],['channel',p.channels]]){const label=this.el('label','',name==='position'?'Planned position':'Planned channel'),select=this.el('select');select.name=name;for(const value of values){const option=this.el('option','',value);option.value=value;select.append(option);}label.append(select);form.append(label);}
    const adapterLabel=this.el('label','','Acquisition adapter'),adapter=this.el('select');adapter.name='adapter';
    for(const [value,text] of [['file-null','Synthetic FILE / null'],['hardware','Calibrated hardware capture']]){const option=this.el('option','',text);option.value=value;option.disabled=value==='hardware'&&this.hardwareReadiness?.hardware_capture_available!==true;adapter.append(option);}adapterLabel.append(adapter);form.append(adapterLabel);
    form.append(this.el('p','hint',this.hardwareReadiness?.hardware_capture_available===true?'Hardware metadata admitted. Preparing is not consent; separate exact-manifest attended approval remains required.':`Hardware capture unavailable: ${(this.hardwareReadiness?.requirements||['Refresh to read microphone and commissioned-output admission']).join('; ')}. FILE jobs remain synthetic.`));
    const b=this.el('button','primary','Prepare synthetic file/null job');b.disabled=this.pending.has('request')||this.context.busy;b.dataset.jobAction='request';form.append(b);
    adapter.onchange=()=>{b.textContent=adapter.value==='hardware'?'Prepare attended hardware manifest':'Prepare synthetic file/null job';};
    form.onsubmit=e=>{e.preventDefault();if(b.disabled)return;const data=new FormData(form),id=session.id;this.action('request',()=>this.api.operation('jobs.request',{session_id:id,position:data.get('position'),channel:data.get('channel'),adapter:data.get('adapter')}));};host.append(form);
  }
  renderJob(host){
    if(this.job.adapter==='hardware')return this.renderHardwareJob(host);
    const job=this.job,[title,description]=jobStatus(job.state),banner=this.el('div',`job-banner ${job.state==='running'?'running':'blocked'}`);banner.append(this.el('h3','',title),this.el('p','',description));host.append(banner,this.el('span','badge evidence-label','SYNTHETIC · not garage measurements'));
    host.append(this.el('p','hint','SERVER-OWNED ACTION MANIFEST · not an agent-authored explanation'));
    const list=this.el('dl','job-manifest'),extra=this.el('dl','job-manifest'),core=new Set(['Action','Output','Position / channel','Peak / band','Total deadline','Safety boundary']);const cells=[...jobSummary(job),['Immutable policy',`Ceiling ${job.manifest?.policy?.volume_ceiling_db??'unknown'} dB · startup ${job.manifest?.policy?.startup_master_db??'unknown'} dB · muted ${job.manifest?.policy?.muted===true?'yes':'not verified'}`],['Production DSP','Not exercised by disposable file jobs. FILE rendering and isolation require separate observed evidence.']];for(const [label,value] of cells){const cell=this.el('div');cell.append(this.el('dt','',label),this.el('dd','',value));(core.has(label)?list:extra).append(cell);}const bounds=this.el('details');bounds.id='job-bounds-details';bounds.append(this.el('summary','','Format, timing & immutable policy bounds'),extra);host.append(list,bounds);
    host.append(this.el('p','hint','Execution prerequisites: separately provisioned human realm, fresh human authentication, exact manifest review, unchanged session/control context and current one-use approval. Hardware and calibrated microphone remain unavailable; this job cannot enable them.'));
    const binding=this.el('details');binding.id='job-manifest-binding';binding.append(this.el('summary','','Exact approved manifest binding'),this.el('p','eyebrow','EXACT MANIFEST SHA256'),this.el('p','job-hash',job.manifest_sha256));host.append(binding,this.el('p','hint',`Server next action: ${job.next_action}`));
    const native=nativeFileSummary(job);host.append(this.el('p','hint',native[0]));if(native.length>1){const report=this.el('details');report.id='native-file-report';report.append(this.el('summary','','FILE render counts, isolation & hashes'));for(const line of native.slice(1))report.append(this.el('p','hint job-hash',line));host.append(report);}
    const permission=jobPermissions(job,this.human),actions=this.el('div','job-actions');
    actions.append(this.button('Cancel job','cancel',permission.cancel&&this.supported('jobs.cancel'),()=>this.api.operation('jobs.cancel',{id:job.id})),this.button('Execute file/null job','execute',!this.stale&&permission.execute&&this.supported('jobs.execute'),async()=>{const fresh=await this.api.operation('jobs.get',{id:job.id});if(fresh.manifest_sha256!==job.manifest_sha256||!jobPermissions(fresh,null).execute)throw new ApiError({code:'approval_changed',message:'Approval or exact manifest is no longer current. Refresh and review again.'});return this.api.operation('jobs.execute',{id:job.id});}));
    if(this.humanAllowed()&&this.human?.role==='human'){
      const review=this.el('label','job-review-check'),check=this.el('input');check.type='checkbox';check.id='job-manifest-reviewed';review.append(check,this.doc.createTextNode(' I reviewed this exact hash and all bounds. This authorizes only this synthetic file/null action.'));host.append(review);
      const approve=this.button('Approve exact synthetic manifest','approve',false,()=>this.humanAPI.request('/api/human/approve',{id:job.id,manifest_sha256:job.manifest_sha256}));check.onchange=()=>{approve.disabled=!check.checked||this.stale||!jobPermissions(job,this.human).approve||this.pending.has('approve');};actions.append(approve,this.button('Revoke authority','revoke',permission.revoke,()=>this.humanAPI.request('/api/human/revoke',{id:job.id})));
    }
    host.append(actions);
    if(job.approval?.expires_at)host.append(this.el('p','hint',`Approval expires ${new Date(job.approval.expires_at*1000).toLocaleTimeString()}; server revalidates realm, inputs and one-use authority.`));
    const timeline=this.el('ol','job-timeline');for(const event of job.history||[]){const row=this.el('li','',`${event.state} · ${event.reason}`);row.append(this.el('small','',new Date(event.at*1000).toLocaleString()));timeline.append(row);}const events=this.el('details');events.append(this.el('summary','','Event history'),timeline);if(job.history?.length)host.append(this.el('p','hint',`Latest: ${job.history.at(-1).reason}`));host.append(events);
    if(job.evidence){const box=this.el('div','job-result');box.append(this.el('h3','','Synthetic evidence pack'),this.el('p','hint','Owned file-only execution is not measured garage response or clock verification.'),this.el('p','hint',job.evidence.supported_ir===true?'Synthetic band-limited transfer estimate available; not calibrated acoustic IR.':'Supported IR estimate not reported; inspect limitations.'));const quality=this.el('details');quality.append(this.el('summary','','Quality checks & warnings'));for(const [name,value] of Object.entries(job.evidence.checks||{})){const line=this.el('p','',`${name.replaceAll('_',' ')}: ${value===true?'passed':value===false?'failed':String(value)}`);quality.append(line);if(value===false)box.append(this.el('p','job-error',`Failed: ${name.replaceAll('_',' ')}`));}for(const warning of job.evidence.warnings||[])quality.append(this.el('p','hint',warning));box.append(quality);const details=this.el('details');details.append(this.el('summary','','Inspect server evidence JSON & hashes'),this.el('pre','',JSON.stringify(job.evidence,null,2)));box.append(details);host.append(box);}
    const details=this.el('details');details.append(this.el('summary','','Inspect canonical manifest'),this.el('pre','',JSON.stringify(job.manifest,null,2)));host.append(details);
  }
  renderHardwareJob(host){
    const job=this.job,m=job.manifest||{},e=job.evidence,permission=jobPermissions(job,this.human);
    const banner=this.el('div','job-banner');banner.append(this.el('h3','',`Hardware acquisition · ${job.state.replaceAll('_',' ')}`),this.el('p','',e?sourceLabel(e.source):'Prepared hardware request, not a completed measurement or sound approval.'));host.append(banner);
    const list=this.el('dl','job-manifest');for(const [label,value] of jobSummary(job)){const row=this.el('div');row.append(this.el('dt','',label),this.el('dd','',value));list.append(row);}host.append(list);
    host.append(this.el('p','hint','Execution can emit a bounded test signal. Stay at the commissioned speakers with low amplifier gains. The server must independently validate microphone, correction revision, exact approval and deadline; this screen grants none of those prerequisites.'));
    const exact=this.el('details');exact.append(this.el('summary','','Exact manifest, mic, graph & revision'),this.el('p','job-hash',job.manifest_sha256),this.el('pre','',JSON.stringify(m,null,2)));host.append(exact);
    const actions=this.el('div','job-actions');actions.append(this.button('Cancel hardware job','cancel',permission.cancel&&this.supported('jobs.cancel'),()=>this.api.operation('jobs.cancel',{id:job.id})),this.button('Execute approved hardware capture','execute',!this.stale&&permission.execute&&this.supported('jobs.execute'),async()=>{const fresh=await this.api.operation('jobs.get',{id:job.id});if(fresh.manifest_sha256!==job.manifest_sha256||!jobPermissions(fresh,null).execute)throw new ApiError({code:'approval_changed',message:'Exact hardware approval changed; refresh and review again.'});return this.api.operation('jobs.execute',{id:job.id});}));
    if(this.humanAllowed()&&this.human?.role==='human'){
      const review=this.el('label','job-review-check'),check=this.el('input');check.type='checkbox';review.append(check,this.doc.createTextNode(' I am attending this bounded hardware test and reviewed the exact mic, route, level, correction revision and hash.'));
      const approve=this.button('Approve exact attended hardware manifest','approve',false,()=>this.humanAPI.request('/api/human/approve',{id:job.id,manifest_sha256:job.manifest_sha256}));check.onchange=()=>{approve.disabled=!check.checked||this.stale||!jobPermissions(job,this.human).approve||this.pending.has('approve');};host.append(review);actions.append(approve,this.button('Revoke authority','revoke',permission.revoke,()=>this.humanAPI.request('/api/human/revoke',{id:job.id})));
    }
    host.append(actions,this.el('p','hint',`Next action: ${job.next_action||'Refresh server readback'}`));
    if(e){const result=this.el('div','job-result');result.append(this.el('h3','',`${sourceLabel(e.source)} · acquisition evidence`),this.el('p','hint',`Magnitude QC: ${e.supported_magnitude===true?'passed':'not qualified'} · raw SHA256 ${e.capture_sha256||'not reported'}`));for(const [key,value] of Object.entries(e.checks||{}))result.append(this.el('p',value===false?'job-error':'hint',`${key.replaceAll('_',' ')}: ${value===true?'passed':value===false?'failed':'unknown'}`));for(const warning of e.warnings||[])result.append(this.el('p','hint',warning));const details=this.el('details');details.append(this.el('summary','','Capture provenance & raw evidence hashes'),this.el('pre','',JSON.stringify(e,null,2)));result.append(details);const link=this.el('a','browse-link','Open correction & paired verification →');link.href='#calibration-lifecycle';result.append(link);host.append(result);}
    const timeline=this.el('details');timeline.append(this.el('summary','','Authority, cancellation & execution history'));for(const event of job.history||[])timeline.append(this.el('p','hint',`${event.state}: ${event.reason}`));host.append(timeline);
  }
  renderHuman(){
    const host=this.$('human-access');host.replaceChildren();
    this.$('human-role').textContent=this.human?.role==='human'?`Human verifier · ${this.human.user}`:this.context?.authenticated?'Automation access only':'Identity not verified';
    this.$('human-role-help').textContent=humanAccessHelp(this.readiness);
    if(!this.context?.authenticated||!this.supported('jobs.request')||!this.humanAllowed())return;
    const form=this.el('form');form.id='human-login-form';for(const [name,title,type] of [['user','Human verifier username','text'],['password','Independent human password','password']]){const label=this.el('label','',title),input=this.el('input');input.name=name;input.type=type;input.required=true;input.maxLength=256;input.autocomplete='off';label.append(input);form.append(label);}
    const button=this.el('button','',this.human?'Re-authenticate · invalidates prior grants':'Sign in as separate human');button.disabled=this.pending.has('human-login');form.append(button);form.onsubmit=e=>{e.preventDefault();if(button.disabled||!this.humanAllowed()){form.reset();return;}const data=new FormData(form),body={user:data.get('user'),password:data.get('password')},generation=this.generation;form.reset();this.action('human-login',async()=>{const human=await this.humanAPI.request(this.human?'/api/human/reauth':'/api/human/login',body);if(generation!==this.generation)return;this.human=human;this.humanAPI.csrf=human.csrf;});};host.append(form);
    if(this.human){host.append(this.el('p','hint',`Fresh until ${new Date(this.human.fresh_until*1000).toLocaleTimeString()}; expires ${new Date(this.human.expires_at*1000).toLocaleTimeString()}.`),this.button('Sign out human & revoke grants','human-logout',true,async()=>{await this.humanAPI.request('/api/human/logout',{});this.human=null;this.humanAPI.csrf=null;}));}
  }
}
