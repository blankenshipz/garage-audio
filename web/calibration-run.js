// Backend-authorized lifecycle. No client consent, capture promotion or fake success.
export function sourceLabel(source){return source==='synthetic'?'SYNTHETIC · no acoustic proof':source==='measured'?'Measured source · inspect acquisition provenance':'Source not verified';}
export function lifecyclePermissions(state){
  if(state?.runtime?.configuration_readback_available===false)return {apply:false,rollback:false,verify:false};
  const tx=state?.transaction,runtime=state?.runtime,owned=tx&&runtime?.configuration&&JSON.stringify(runtime.configuration)===JSON.stringify(tx.configuration)&&runtime.revision===(tx.accepted_revision??tx.base_revision+1);
  return {apply:runtime?.binding?.kind==='file'&&tx?.state==='prepared'&&state.inputs_current===true&&runtime.revision===tx.base_revision,
    rollback:!!owned,verify:tx?.state==='accepted_for_next_play'&&state.inputs_current===true};
}
export class CalibrationRunPanel {
  constructor(api,{host,document=globalThis.document,storage=globalThis.sessionStorage}={}){
    Object.assign(this,{api,host,doc:document,storage});this.authenticated=false;this.capabilities=null;this.pending=false;this.generation=0;this.sessions=[];this.jobs=[];this.state=null;
    this.beforeID=storage?.getItem('garage-correction-before')||'';this.afterID=storage?.getItem('garage-correction-after')||'';
    host.id='calibration-lifecycle';host.className='card calibration-run';host.setAttribute('aria-labelledby','correction-title');
    this.el=(tag,text)=>{const el=document.createElement(tag);if(text!==undefined)el.textContent=text;return el;};
    host.append(this.el('p','ACQUIRE → RECOMMEND → ACCEPT → VERIFY'),Object.assign(this.el('h2','Correction & paired verification'),{id:'correction-title'}),this.el('p','Choose a before session and a separate matching after session. Accepting a FILE correction is for next Play only: it does not start sound or prove the room improved. Hardware apply still needs an independent authorized implementation.'));
    this.feedback=this.el('p','Unlock to inspect correction state.');this.feedback.id='correction-feedback';this.feedback.setAttribute('role','status');
    const choices=this.el('div');choices.className='correction-choices';
    this.before=this.select('correction-before','Before / fitting session',choices);this.after=this.select('correction-after','After / verification session',choices);
    this.before.onchange=()=>{this.beforeID=this.before.value;this.storage?.setItem('garage-correction-before',this.beforeID);this.state=null;this.run(()=>this.readSelected());};
    this.after.onchange=()=>{this.afterID=this.after.value;this.storage?.setItem('garage-correction-after',this.afterID);this.run(()=>this.readAfter());};
    this.actions=this.el('div');this.actions.className='correction-actions';this.buttons={};
    for(const [key,label] of [['refresh','Refresh / recover'],['analyze','Analyze before'],['propose','Recommend cuts'],['prepare','Prepare exact correction'],['apply','Accept FILE correction'],['verify','Compare paired captures'],['rollback','Rollback owned correction']]){
      const button=this.el('button',label);button.type='button';button.id='correction-'+key;button.onclick=()=>this.run(()=>this.action(key));this.buttons[key]=button;this.actions.append(button);
    }
    this.buttons.apply.className='primary';this.buttons.rollback.className='danger-button';
    this.summary=this.el('div');this.summary.id='correction-state';this.provenance=this.el('div');this.provenance.className='correction-provenance';
    const capture=this.el('details');capture.id='correction-acquisition';capture.append(this.el('summary','Bring a completed hardware capture into its plan'));
    this.capture=this.select('correction-capture','Completed job',capture);this.importButton=this.el('button','Import qualified capture');this.importButton.id='correction-import';this.importButton.onclick=()=>this.run(()=>this.importCapture());capture.append(this.importButton,this.el('p','Only completed measured hardware jobs with retained raw data and passing QC can be imported. Synthetic FILE jobs cannot be promoted to microphone measurements.'));
    this.report=this.el('div');this.report.id='correction-report';this.report.setAttribute('aria-live','polite');
    host.append(this.feedback,choices,this.actions,this.summary,this.provenance,capture,this.report);this.controls();
  }
  select(id,title,host){const label=this.el('label',title),select=this.el('select');select.id=id;label.htmlFor=id;label.append(select);host.append(label);return select;}
  supports(op){return this.authenticated&&!!this.capabilities?.operations?.[op];}
  setContext({authenticated,capabilities}){
    const wasSupported=this.supports('calibration.correction.get');this.authenticated=authenticated;this.capabilities=capabilities;
    if(!authenticated){this.generation++;this.state=null;this.beforeSession=null;this.afterSession=null;this.sessions=[];this.jobs=[];this.summary.replaceChildren();this.report.replaceChildren();this.provenance.replaceChildren();this.feedback.textContent='Unlock to inspect correction state.';}
    this.controls();if(!wasSupported&&this.supports('calibration.correction.get'))this.run(()=>this.refresh());
  }
  options(select,values,id,label,selected){select.replaceChildren(this.el('option','Choose…'));select.firstChild.value='';for(const value of values){const option=this.el('option',label(value));option.value=id(value);select.append(option);}select.value=selected;}
  async refresh(){
    const generation=this.generation;const [sessions,jobs]=await Promise.all([this.api.operation('sessions.list'),this.supports('jobs.list')?this.api.operation('jobs.list'):[]]);if(generation!==this.generation)return;
    this.sessions=sessions.filter(s=>s.plan);this.jobs=jobs;
    this.options(this.before,this.sessions,s=>s.id,s=>s.name,this.beforeID);this.options(this.after,this.sessions,s=>s.id,s=>s.name,this.afterID);
    this.options(this.capture,jobs.filter(j=>j.state==='completed'),j=>j.id,j=>`${j.manifest?.position||'Position unknown'} / ${j.manifest?.channel||'channel unknown'} · ${sourceLabel(j.evidence?.source)} · ${j.adapter}`,this.capture.value);
    this.capture.onchange=()=>this.controls();await this.readSelected();await this.readAfter();
  }
  async readSelected(){
    const generation=this.generation,id=this.beforeID;this.state=null;this.beforeSession=null;
    if(id&&this.sessions.some(s=>s.id===id)){
      const session=await this.api.operation('sessions.get',{id});if(generation!==this.generation||id!==this.beforeID)return;this.beforeSession=session;
      if(this.supports('calibration.correction.get')){const state=await this.api.operation('calibration.correction.get',{id});if(generation!==this.generation||id!==this.beforeID)return;this.state=state;}
    }
    this.render();
  }
  async readAfter(){const generation=this.generation,id=this.afterID;this.afterSession=null;if(id&&this.sessions.some(s=>s.id===id)){const session=await this.api.operation('sessions.get',{id});if(generation!==this.generation||id!==this.afterID)return;this.afterSession=session;}this.render();}
  async run(fn){if(this.pending||!this.authenticated)return;this.pending=true;const generation=this.generation;this.controls();try{await fn();if(generation===this.generation)this.feedback.textContent='Server readback current. No sound started by this panel.';}catch(error){if(generation===this.generation){this.state=null;this.render();this.feedback.textContent=[error.message,...(error.requirements||[]),'Refresh to reconcile; no action is automatically replayed.'].join(' ');}}finally{this.pending=false;this.controls();}}
  async action(key){
    if(key==='refresh')return this.refresh();
    const generation=this.generation,id=this.beforeID,op=key==='analyze'||key==='propose'?`calibration.${key}`:`calibration.correction.${key}`;
    const result=await this.api.operation(op,{id,...(key==='verify'?{after_id:this.afterID}:{})});if(generation!==this.generation)return;await this.readSelected();
    if(generation!==this.generation)return;
    if(key==='verify')this.renderReport(result);
  }
  async importCapture(){const generation=this.generation,job=this.jobs.find(j=>j.id===this.capture.value);if(!job)return;await this.api.operation('calibration.capture.import',{job_id:job.id});if(generation!==this.generation)return;this.beforeID=job.session_id;this.storage?.setItem('garage-correction-before',this.beforeID);await this.refresh();}
  controls(){
    const disabled=!this.authenticated||this.pending,p=lifecyclePermissions(this.state),selected=!!this.beforeSession;
    for(const select of [this.before,this.after,this.capture])select.disabled=disabled;
    for(const [key,button] of Object.entries(this.buttons)){
      const op=key==='refresh'?'sessions.list':key==='analyze'||key==='propose'?`calibration.${key}`:`calibration.correction.${key}`;
      let allowed=this.supports(op)&&(key==='refresh'||selected);
      if(key==='apply'||key==='rollback')allowed&&=p[key];
      if(key==='prepare')allowed&&=!!this.beforeSession?.proposal&&this.state?.runtime?.binding?.kind==='file'&&this.state.runtime.configuration_readback_available!==false;
      if(key==='analyze'||key==='propose')allowed&&=this.beforeSession?.state!=='cancelled';
      if(key==='verify')allowed&&=p.verify&&!!this.afterSession&&this.afterID!==this.beforeID;
      button.disabled=disabled||!allowed;
    }
    const job=this.jobs.find(j=>j.id===this.capture.value);
    this.importButton.disabled=disabled||!this.supports('calibration.capture.import')||job?.adapter!=='hardware'||job?.evidence?.source!=='measured'||job?.evidence?.supported_magnitude!==true;
  }
  render(){
    this.summary.replaceChildren();this.provenance.replaceChildren();this.report.replaceChildren();const tx=this.state?.transaction,runtime=this.state?.runtime;
    this.summary.append(this.el('p',tx?`Transaction: ${tx.state.replaceAll('_',' ')} · ${sourceLabel(tx.source)}`:'No correction transaction in the selected session.'));
    if(runtime?.configuration_readback_available===false)this.summary.append(this.el('p',runtime.unavailable_reason),this.el('p',`Status only: ${runtime.observed_status?.device_mode||'unknown'} / ${runtime.observed_status?.state||'unknown'}. No configuration inferred.`));
    else if(runtime)this.summary.append(this.el('p',`Runtime: ${runtime.binding.kind} · accepted revision ${runtime.revision} · loaded ${runtime.loaded?'reported (process launch only)':'not running / not loaded'}`));
    if(runtime?.binding?.kind==='hardware')this.summary.append(this.el('p','Hardware correction apply unavailable: independent exact-proposal hardware authority is not implemented. This panel cannot grant it.'));
    if(tx&&!this.state.inputs_current)this.summary.append(this.el('p','Inputs changed. Apply/compare blocked; rollback remains available only while this transaction still owns the runtime revision.'));
    for(const [name,session] of [['Before',this.beforeSession],['After',this.afterSession]]){
      const detail=this.el('details');detail.append(this.el('summary',`${name} source, QC & provenance`));
      for(const file of session?.files||[]){detail.append(this.el('p',`${file.metadata?.position||'position unknown'} / ${file.metadata?.channel||'channel unknown'} · ${sourceLabel(file.metadata?.source)} · calibration ${file.metadata?.calibration_applied?'reported applied':'not applied'}`));const pre=this.el('pre',JSON.stringify({sha256:file.sha256,metadata:file.metadata,capture_provenance:file.capture_provenance||'External import: acquisition/QC not independently verified'},null,2));detail.append(pre);}
      if(!session?.files?.length)detail.append(this.el('p','No responses in this selection.'));this.provenance.append(detail);
    }
    if(tx?.verification)this.renderReport(tx.verification);this.controls();
  }
  renderReport(report){this.report.replaceChildren(this.el('h3','Paired magnitude report'),this.el('p',sourceLabel(report.source)),this.el('p',report.acoustic_improvement_verified===true?'Qualified measured pairs support relative magnitude improvement at these positions.':'No acoustic improvement verified.'));
    const rows=this.el('div');rows.className='correction-results';for(const slot of report.slots||[]){const row=this.el('div');row.append(this.el('strong',`${slot.position} / ${slot.channel}`),this.el('p',`RMS ${Number(slot.before_rms_db).toFixed(2)} → ${Number(slot.after_rms_db).toFixed(2)} dB · ${slot.nonworsening?'nonworsening':'worsened'}`));rows.append(row);}this.report.append(rows,this.el('p',report.next_action||'Inspect paired QC.'));for(const warning of report.warnings||[])this.report.append(this.el('p',warning));}
}
