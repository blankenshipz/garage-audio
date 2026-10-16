// Shared UI/MCP/agent operations. A button is NOT hardware authorization.
const node=(tag,text)=>{const el=document.createElement(tag);if(text!==undefined)el.textContent=text;return el;};
export class AudioConfigPanel {
  constructor(api,{host}) {
    this.api=api;this.authenticated=false;this.pending=false;this.proposal=null;this.state=null;
    host.className='card audio-config-panel';host.id='audio-configuration';
    host.append(node('h2','Audio configuration'),node('p','Inspect routing and propose a crossover. Apply is available only on an explicitly declared virtual FILE binding. Hardware changes require verified wiring and independent exact-proposal human authority; chat or this form cannot grant it.'));
    this.feedback=node('p','Unlock to inspect configuration.');this.feedback.setAttribute('role','status');
    this.readback=node('div');this.readback.className='metadata';
    this.form=node('form');this.form.className='audio-config-fields';
    this.inputs={};
    for(const [key,title] of [['layout','Layout'],['left','Left output'],['right','Right output'],['sub','Powered sub output'],['crossover_hz','Crossover (Hz)'],['headroom_db','Headroom (dB)']]) {
      const label=node('label',title),input=node(key==='layout'?'select':'input');input.name=key;
      if(key==='layout'){for(const [value,text] of [['stereo','Stereo fullrange'],['powered-sub-2.1','Stereo + powered sub (2.1)']]){const option=node('option',text);option.value=value;input.append(option);}}
      else {input.type='number';input.step=key==='headroom_db'?'0.5':'1';input.min=key==='headroom_db'?-60:key==='crossover_hz'?40:1;input.max=key==='headroom_db'?-6:key==='crossover_hz'?160:12;}
      input.id='audio-config-'+key;label.htmlFor=input.id;label.append(input);this.form.append(label);this.inputs[key]=input;
    }
    this.inputs.layout.onchange=()=>{this.inputs.sub.disabled=this.inputs.layout.value==='stereo';};
    this.propose=node('button','Create exact proposal');this.propose.type='submit';this.form.append(this.propose);
    this.form.onsubmit=e=>{e.preventDefault();this.run(async()=>{const configuration={};for(const [key,input] of Object.entries(this.inputs))configuration[key]=key==='layout'?input.value:key==='sub'&&this.inputs.layout.value==='stereo'?null:Number(input.value);this.proposal=await api.operation('audio.config.propose',{configuration});this.showProposal();});};
    this.card=node('section');this.card.className='audio-config-proposal';this.card.hidden=true;
    this.diff=node('dl');this.identity=node('p');this.identity.className='hint';this.validation=node('p');
    this.validate=node('button','Validate exact proposal');this.apply=node('button','Accept for next Play');
    this.validate.onclick=()=>this.run(async()=>{await api.operation('audio.config.validate',{proposal_id:this.proposal.proposal_id});this.proposal.validated=true;this.showProposal();});
    this.apply.onclick=()=>this.run(async()=>{await api.operation(this.proposal.rollback?'audio.config.rollback':'audio.config.apply',{proposal_id:this.proposal.proposal_id});this.proposal=null;await this.refresh();});
    this.card.append(node('h3','Exact configuration proposal'),this.identity,this.diff,this.validation,this.validate,this.apply);
    this.reload=node('button','Read configuration');this.reload.onclick=()=>this.run(()=>this.refresh());
    this.rollback=node('button','Propose rollback');this.rollback.onclick=()=>this.run(async()=>{this.proposal=await api.operation('audio.config.propose',{configuration:this.state.previous});this.proposal.rollback=true;this.showProposal();});
    host.append(this.feedback,this.reload,this.readback,this.form,this.card,this.rollback,node('p','Accepting a FILE graph never starts playback. Loaded state is separate from acceptance; no physical or acoustic verification is inferred. Stop speaker output remains available in the top bar, independently of this panel and chat. AI inference remains OFF.'));
    this.controls();
  }
  setAuthenticated(value){const changed=value!==this.authenticated;this.authenticated=value;if(!value){this.state=null;this.proposal=null;this.readback.replaceChildren();this.card.hidden=true;}this.controls();if(changed&&value)this.run(()=>this.refresh());}
  async run(action){if(this.pending||!this.authenticated)return;this.pending=true;this.controls();try{await action();this.feedback.textContent='Configuration readback current. No playback started.';}catch(error){this.feedback.textContent=error.message+' No configuration success is inferred. Reload if the proposal is stale.';}finally{this.pending=false;this.controls();}}
  async refresh(){this.state=await this.api.operation('audio.config.get');const state=this.state;this.readback.replaceChildren();if(state.configuration_readback_available===false){this.proposal=null;this.card.hidden=true;this.readback.append(node('strong','Configuration unknown'),node('p',state.unavailable_reason),node('p',`Observed status only: ${state.observed_status?.device_mode||'unknown'} / ${state.observed_status?.state||'unknown'}. No desired, accepted or loaded graph is assumed.`));for(const input of Object.values(this.inputs))input.value='';this.controls();return;}for(const [label,value] of [['Binding',`${state.binding.kind} · ${state.binding.wiring}`],['Accepted revision',state.revision],['Desired / accepted for next Play',JSON.stringify(state.accepted_for_next_play)],['Loaded',state.loaded?JSON.stringify(state.loaded):'Not running'],['Load evidence',state.load_verification],['Physically verified','No']]){this.readback.append(node('strong',label),node('p',String(value)));}for(const [key,input] of Object.entries(this.inputs))input.value=state.configuration[key]??'';this.inputs.layout.onchange();this.showProposal();}
  showProposal(){this.card.hidden=!this.proposal;if(!this.proposal)return;const p=this.proposal;this.identity.textContent=`Proposal ${p.proposal_id} · base revision ${p.base_revision}`;this.diff.replaceChildren();for(const [key,value] of Object.entries(p.diff)){this.diff.append(node('dt',key.replaceAll('_',' ')),node('dd',`${value.before??'none'} → ${value.after??'none'}`));}this.validation.textContent=`Validated: ${p.validated?'yes (Camilla software only)':'no'} · Authority: ${p.approval_requirement}`;this.controls();}
  controls(){const disabled=!this.authenticated||this.pending,configurationDisabled=disabled||this.state?.configuration_readback_available===false;for(const control of this.form.elements)control.disabled=configurationDisabled;this.inputs.sub.disabled=configurationDisabled||this.inputs.layout.value==='stereo';this.reload.disabled=disabled;this.validate.disabled=configurationDisabled||!this.proposal;this.apply.disabled=configurationDisabled||!this.proposal?.validated||this.state?.binding.kind!=='file';this.rollback.disabled=configurationDisabled||!this.state?.previous;}
}
