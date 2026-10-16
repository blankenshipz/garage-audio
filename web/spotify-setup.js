const FLOW_KEY='garage-spotify-setup-flow';
const OFFICIAL_LINKS={documentation:'https://developer.spotify.com/documentation/soloist',policy:'https://developer.spotify.com/policy',key_page:'https://developer.spotify.com/dashboard/soloist'};
export function flowSubmitArgs(flow,{acceptTerms=false,keepStoredKey=false}={}){
  if(flowTerminal(flow)||flowExpired(flow))throw new Error('Start or review a new setup; the retained step cannot be continued.');
  const args={id:flow.id,step:flow.step};
  if(flow.step==='backend')return args;
  if(flow.step==='soloist_terms')return {...args,accept_terms:acceptTerms===true};
  if(flow.step==='soloist_api_key'){
    if(keepStoredKey){if(!flow.fields?.some(f=>f.name==='keep_stored_key'&&f.available===true))throw new Error('No stored key is reported available.');return {...args,keep_stored_key:true};}
    return {...args,credential_ref:'spotify-soloist'};
  }
  throw new Error('Reload the current reviewed setup step before submitting.');
}
export function flowTerminal(flow){return !!flow&&['finish','abort','interrupted'].includes(flow.type);}
export function flowExpired(flow,now=Date.now()){return !!flow&&Number.isFinite(flow.expires_at)&&flow.expires_at*1000<=now&&!flowTerminal(flow);}
export function retainedFlow(status,current=null){return status?.active_flow||status?.last_flow||current;}
export function volumePolicyState(status){
  if(!status)return {code:'status_unavailable',text:'Required: Soloist with Spotify-authoritative volume. Policy status is unavailable; playback is not qualified.'};
  if(!status.configured)return {code:'provider_setup_required',text:'Required: Soloist with Spotify-authoritative volume. Complete official provider setup first; policy availability and runtime playback are not yet verified.'};
  if(status.volume_policy_available!==true)return {code:'required_volume_policy_unavailable',text:'Required Spotify-authoritative volume policy is unavailable in the playback backend. A matching patched backend is needed; setup is not ready.'};
  if(status.backend!=='soloist'||status.configured_volume_mode!==status.required_volume_mode||status.volume_policy_configured!==true)return {code:'required_volume_policy_mismatch',text:'Stored backend or volume policy does not match required Soloist / Spotify-authoritative volume. Review setup; no fallback or runtime qualification is inferred.'};
  return {code:'runtime_qualification_required',text:'Required Spotify-authoritative volume policy is stored. Runtime login, discovery and playback are not qualified yet.'};
}
export class SpotifySetupPanel {
  constructor(api,{host,storage=globalThis.localStorage,onReadback=async()=>{}}){
    this.api=api;this.host=host;this.storage=storage;this.onReadback=onReadback;this.authenticated=false;this.pending=false;this.status=null;this.flow=null;this.error=null;
    host.innerHTML='<div class="card-heading"><h3>Spotify Connect setup</h3><button id="spotify-refresh" type="button">Check Spotify setup</button></div><p class="hint">One-time official Soloist setup, separate from everyday Spotify playback. No setup action starts music or grants sound authority.</p><p id="spotify-setup-status" role="status" class="setup-message"></p><p id="spotify-setup-error" role="alert" class="error-text"></p><div class="control-row"><button id="spotify-begin" type="button" class="primary">Start official setup</button><button id="spotify-reconfigure" type="button">Review existing setup</button></div><div id="spotify-flow"></div><form id="spotify-name-form"><label>Spotify device name<input name="advertised_name" value="Garage Speakers" maxlength="60" pattern="[A-Za-z0-9][A-Za-z0-9 ._\\-]*" required aria-describedby="spotify-name-help"></label><p id="spotify-name-help" class="hint">Available after the reviewed official setup completes. Binds only the server-owned garage player; no keys, passwords or player IDs belong here.</p><button type="submit">Save Spotify device name</button></form><p class="hint">Setup expiry or cancellation does not disable stored everyday receiver credentials. Runtime login, phone discovery and physical playback still require separate verification.</p>';
    this.el=id=>host.querySelector('#'+id);this.el('spotify-refresh').onclick=()=>this.refresh();
    const policy=document.createElement('p');policy.id='spotify-volume-policy';policy.className='setup-message';policy.setAttribute('role','status');this.el('spotify-setup-error').before(policy);
    const values=document.createElement('p');values.id='spotify-policy-values';values.className='hint';policy.after(values);
    this.el('spotify-begin').onclick=()=>this.mutate('setup.spotify.begin');this.el('spotify-reconfigure').onclick=()=>this.mutate('setup.spotify.reconfigure');
    this.el('spotify-name-form').onsubmit=e=>{e.preventDefault();const args={advertised_name:new FormData(e.target).get('advertised_name').trim()};this.mutate('setup.spotify.configure',args);};this.render();
  }
  storedID(){try{return this.storage.getItem(FLOW_KEY);}catch{return null;}}
  remember(id){try{this.storage.setItem(FLOW_KEY,id);}catch{/* Server status remains authoritative. */}}
  setAuthenticated(value){this.authenticated=value;if(!value){this.status=null;this.flow=null;this.error=null;}this.render();}
  async acceptStatus(envelope){this.status=envelope?.ok?envelope.result:null;this.error=envelope?.ok?null:envelope?.error||null;if(this.authenticated&&this.status){const flow=retainedFlow(this.status),id=flow?.id||this.storedID();if(flow){this.flow=flow;this.remember(id);}if(id){try{this.flow=await this.api.operation('setup.spotify.flow.get',{id});}catch(e){this.error=e;}}}this.render();}
  async refresh(){await this.action(async()=>{const status=await this.api.operation('setup.spotify.status');await this.acceptStatus({ok:true,result:status});});}
  async action(fn){if(!this.authenticated||this.pending)return;this.pending=true;this.error=null;this.render();try{await fn();}catch(e){this.error=e;if(['setup_step_changed','setup_restart_required'].includes(e.code)&&this.flow){try{this.flow=await this.api.operation('setup.spotify.flow.get',{id:this.flow.id});}catch{/* Keep the original structured failure; never replay a submit. */}}}finally{if(this.authenticated){try{await this.onReadback();}catch(e){this.error=this.error||e;}}if(!this.authenticated){this.status=null;this.flow=null;this.error=null;}this.pending=false;this.render();}}
  async mutate(operation,args={}){await this.action(async()=>{const result=await this.api.operation(operation,args);if(operation.startsWith('setup.spotify.flow.')||['setup.spotify.begin','setup.spotify.reconfigure'].includes(operation)){this.flow=result;this.remember(result.id);}if(operation==='setup.spotify.configure')this.el('spotify-name-form').elements.advertised_name.value=result.advertised_name;const status=await this.api.operation('setup.spotify.status');this.status=status;this.flow=retainedFlow(status,this.flow);if(this.flow)this.remember(this.flow.id);});}
  render(){
    const ready=this.authenticated&&!this.pending&&!!this.status,active=this.flow&&!flowTerminal(this.flow)&&!flowExpired(this.flow);
    this.el('spotify-setup-status').textContent=!this.authenticated?'Unlock to inspect private Spotify setup.':!this.status?'Spotify setup has not been verified. The existing private playback-backend credential is a genuine prerequisite.':this.status.setup_restart_required?'Setup was interrupted by the backend. Start or review a new official setup; earlier consent and key steps are not continued.':this.status.official_setup_completed?'Reviewed provider setup is retained. Stored settings do not prove qualified runtime playback.':this.status.configured?'Existing garage provider found. Review its official Soloist setup without deleting accounts or stored keys.':'No garage Spotify Connect provider is configured. Complete official one-time setup here.';
    const policy=volumePolicyState(this.status);this.el('spotify-volume-policy').textContent=this.authenticated?policy.text:'Required volume policy is server-owned; unlock to read it.';this.el('spotify-volume-policy').dataset.code=policy.code;
    this.el('spotify-policy-values').textContent=this.authenticated&&this.status?`Required policy: ${this.status.required_volume_mode||'not reported'} · stored backend: ${this.status.backend||'not configured'} · stored volume policy: ${this.status.configured_volume_mode||'not configured'}${this.status.volume_policy_error?` · ${this.status.volume_policy_error.code}`:''}`:'';
    this.el('spotify-setup-error').textContent=this.error?[this.error.code,this.error.message,...(this.error.requirements||[])].filter(Boolean).join(' · '):'';
    this.el('spotify-refresh').disabled=!this.authenticated||this.pending;this.el('spotify-begin').disabled=!ready||this.status?.configured||!!active;this.el('spotify-reconfigure').disabled=!ready||!this.status?.configured||!!active;
    this.el('spotify-name-form').querySelector('button').disabled=!ready||this.status?.official_setup_completed!==true||this.status?.volume_policy_available!==true||this.status?.backend!=='soloist';this.el('spotify-name-form').elements.advertised_name.disabled=!this.authenticated||this.pending;
    const host=this.el('spotify-flow');host.replaceChildren();if(!this.authenticated||!this.flow)return;
    const add=(tag,text,className)=>{const node=document.createElement(tag);node.textContent=text;if(className)node.className=className;host.append(node);return node;};
    add('h4',this.flow.type==='interrupted'?'Retained interrupted setup':'Official setup progress');add('p',flowExpired(this.flow)?'Setup flow expired. Existing receiver credentials and everyday playback remain unchanged.':`Current step: ${this.flow.step.replaceAll('_',' ')}${this.flow.validation_failed?' · choice needs correction':''}`,'hint');
    if(this.flow.notice)add('p',this.flow.notice,'setup-message');
    for(const [key,url] of Object.entries(this.flow.links||{}))if(OFFICIAL_LINKS[key]===url){const link=add('a',`Official Spotify ${key.replaceAll('_',' ')} ↗`,'browse-link');link.href=url;link.target='_blank';link.rel='noopener noreferrer';}
    const button=(label,fn)=>{const node=add('button',label);node.type='button';node.disabled=!ready||!active;node.onclick=fn;return node;};
    if(active&&this.flow.type==='form'){
      if(this.flow.step==='backend'){add('p','Supported backend: Soloist only. No community fallback.','hint');button('Continue with Soloist',()=>this.mutate('setup.spotify.flow.submit',flowSubmitArgs(this.flow)));}
      if(this.flow.step==='soloist_terms'){
        const label=add('label','','setup-terms'),checkbox=document.createElement('input');checkbox.type='checkbox';checkbox.id='spotify-accept-terms';checkbox.checked=false;checkbox.disabled=!ready;label.append(checkbox,document.createTextNode('I accept download, running, retention and automatic refresh of Soloist, understanding Spotify Premium is required and this may violate Spotify terms. This is not sound approval.'));
        const accept=button('Accept terms & continue',()=>this.mutate('setup.spotify.flow.submit',flowSubmitArgs(this.flow,{acceptTerms:checkbox.checked})));accept.disabled=true;checkbox.onchange=()=>accept.disabled=!ready||!checkbox.checked;
        button('Decline terms',()=>this.mutate('setup.spotify.flow.submit',flowSubmitArgs(this.flow,{acceptTerms:false})));
      }
      if(this.flow.step==='soloist_api_key'){
        add('p','Ask the operator to provision the fixed private spotify-soloist server reference. Never enter or send a key in this app.','hint');button('Use private server key reference',()=>this.mutate('setup.spotify.flow.submit',flowSubmitArgs(this.flow)));
        if(this.flow.fields?.some(f=>f.name==='keep_stored_key'&&f.available===true))button('Keep existing stored key',()=>this.mutate('setup.spotify.flow.submit',flowSubmitArgs(this.flow,{keepStoredKey:true})));
      }
    }
    if(active){button('Refresh owned setup step',()=>this.mutate('setup.spotify.flow.get',{id:this.flow.id}));button('Cancel setup · keep stored credentials',()=>this.mutate('setup.spotify.flow.abort',{id:this.flow.id}));}
  }
}
