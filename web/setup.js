export function agentConfigArgs(form){return {provider:'openai',model:form.get('model').trim(),credential_ref:'application-agent',max_turns:Number(form.get('max_turns')),timeout_seconds:Number(form.get('timeout_seconds'))};}
export function spotifyReadinessText(state){return (state?.spotify_readiness?.code||'status_unavailable').replaceAll('_',' ')+' · runtime playback not qualified';}
export class SetupPanel {
  constructor(api,{host,onStatus=()=>{}}){
    this.api=api;this.host=host;this.onStatus=onStatus;this.authenticated=false;this.pending=false;
    host.innerHTML='<div class="section-heading compact"><div><p class="eyebrow">ONE-TIME SETUP</p><h2>Make this room yours</h2><p class="subtitle">Direct controls work without an AI conversation. No setup action starts playback.</p></div></div><div class="card"><div class="card-heading"><h3>Connection & readiness</h3><button id="setup-refresh" type="button">Check setup</button></div><ul id="setup-readiness" class="setup-readiness"><li>Unlock to read actual setup state.</li></ul><p id="setup-feedback" class="hint" role="status"></p><form id="setup-name-form"><label>Room display name<input name="display_name" maxlength="80" required placeholder="Garage audio"></label><button class="primary" type="submit">Save display name</button></form></div><details class="stage-disclosure"><summary>Agent configuration · inference stays off</summary><p class="hint">Saving configuration cannot enable AI or call a provider. Credentials are operator-provisioned private server references; do not paste keys here.</p><form id="agent-config-form"><label>Provider<select name="provider"><option value="openai">OpenAI</option></select></label><label>Model<input name="model" maxlength="80" pattern="[a-zA-Z0-9][a-zA-Z0-9._\\-]{0,79}" required></label><div class="form-grid"><label>Maximum turns<input name="max_turns" type="number" min="1" max="8" required></label><label>Timeout (seconds)<input name="timeout_seconds" type="number" min="1" max="30" required></label></div><p class="hint">Credential reference: application-agent · AI calls remain disabled, even when credentials are present.</p><button type="submit">Save disabled agent configuration</button></form></details>';
    this.el=id=>host.querySelector('#'+id);this.el('setup-refresh').onclick=()=>this.refresh();
    const spotifyHost=document.createElement('section');spotifyHost.className='card spotify-setup';spotifyHost.id='spotify-setup';host.querySelector('.section-heading').after(spotifyHost);this.spotify=new SpotifySetupPanel(api,{host:spotifyHost,onReadback:()=>this.refreshReadiness()});
    const maHost=document.createElement('section');maHost.className='card';maHost.id='ma-enrollment';spotifyHost.before(maHost);this.enrollment=new MAEnrollmentPanel(api,{host:maHost,onReadback:()=>this.refreshReadiness()});
    this.el('setup-name-form').onsubmit=e=>{e.preventDefault();const args={display_name:new FormData(e.target).get('display_name').trim()};this.action(async()=>{await api.operation('setup.config.set',args);await this.read();this.feedback('Display name saved. No speaker or provider action.');});};
    this.el('agent-config-form').onsubmit=e=>{e.preventDefault();const args=agentConfigArgs(new FormData(e.target));this.action(async()=>{await api.operation('agent.config.set',args);await this.read();this.feedback('Agent configuration saved. Inference remains disabled.');});};this.render();
  }
  feedback(value){this.el('setup-feedback').textContent=value;}
   setAuthenticated(value){if(value===this.authenticated)return;this.authenticated=value;this.spotify.setAuthenticated(value);this.enrollment.setAuthenticated(value);if(value)this.refresh();else{this.el('setup-readiness').replaceChildren();this.feedback('Unlock to read setup. No readiness is assumed.');this.onStatus(null);this.render();}}
   render(){this.host.querySelectorAll('button,input,select').forEach(el=>{if(!el.closest('#spotify-setup,#ma-enrollment'))el.disabled=!this.authenticated||this.pending;});}
  async action(fn){if(!this.authenticated||this.pending)return;this.pending=true;this.render();try{await fn();}catch(e){this.feedback([e.message,...(e.requirements||[])].join(' '));this.onStatus(null);}finally{this.pending=false;this.render();}}
  async read(){
    const state=await this.api.operation('setup.status');this.onStatus(state);this.renderReadiness(state);
    await this.spotify.acceptStatus(state.spotify_setup);
    this.el('setup-name-form').elements.display_name.value=state.config?.display_name||'';
    const config=await this.api.operation('agent.config.get');for(const name of ['model','max_turns','timeout_seconds'])this.el('agent-config-form').elements[name].value=config[name];
  }
  renderReadiness(state){
    const list=this.el('setup-readiness');list.replaceChildren();
    const rows=[['Audio worker',state.audio?.available?(state.audio.readback?.device_mode==='file'?'FILE readiness only · no physical sound claim':'Worker readback available · not a listening test'):state.audio?.error?.code||'Not verified'],['Audio device',state.device?.verified?'Identity verified':state.device?.code||'Not verified'],['Playback backend credential',state.credentials?.music_assistant?.available?'Private reference ready':state.credentials?.music_assistant?.code||'Not verified'],['Agent credential',state.credentials?.agent?.available?'Private reference ready · inference off':state.credentials?.agent?.code||'Not configured · inference off'],['Ordinary playback','Spotify → Garage Speakers; full-range stereo OUT3/4, no subwoofer path'],['AI replies','Disabled by server policy']];
    for(const [label,value] of rows){const row=document.createElement('li'),strong=document.createElement('strong'),detail=document.createElement('span');strong.textContent=label;detail.textContent=value.replaceAll('_',' ');row.append(strong,detail);list.append(row);}
    const value=Object.assign(document.createElement('span'),{id:'spotify-readiness-value',textContent:spotifyReadinessText(state)});value.dataset.code=state.spotify_readiness?.code||'status_unavailable';
    const readiness=document.createElement('li');readiness.append(Object.assign(document.createElement('strong'),{textContent:'Spotify readiness'}),value);list.append(readiness);
  }
  async refreshReadiness(){
    if(!this.authenticated)return;
    try{const state=await this.api.operation('setup.status');if(this.authenticated){this.renderReadiness(state);this.onStatus(state);}}
    catch(e){if(this.authenticated){const value=this.el('spotify-readiness-value');if(value){value.textContent='Readback unavailable · runtime playback not qualified';value.dataset.code='status_unavailable';}this.feedback(e.message);this.onStatus(null);}}
  }
  async refresh(){await this.action(async()=>{await this.read();this.feedback('Setup read from server. Missing device or credential references require operator provisioning; no readiness is inferred.');});}
}
import {SpotifySetupPanel} from './spotify-setup.js';
import {MAEnrollmentPanel} from './ma-enrollment.js';
