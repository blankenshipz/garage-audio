// No authority in localStorage, role strings, public code or event.isTrusted.
// HTTP trusted-home-LAN threat model: interception/modification is not defended.
export class SpeakersPanel {
  constructor(api) {
    this.api=api;this.csrf=null;this.authenticated=false;this.state=null;this.pending=false;this.mode=null;
    this.el=id=>document.getElementById(id);
    this.el('speakers-setup').onclick=()=>this.perform('setup');
    this.el('speakers-enable').onclick=()=>this.perform('enable');
    this.el('speakers-stop').onclick=()=>this.perform('stop');
    setInterval(()=>{if(this.authenticated&&!this.polling)this.refresh();},1000);
    this.render();
  }
  setAuthenticated(value) {
    if(value===this.authenticated)return;
    this.authenticated=value;
    if(value)this.connect();else {this.csrf=null;this.state=null;this.mode=null;this.render();this.feedback('Unlock to request Stop. No silence is assumed.');}
  }
  async connect() {
    try {const setup=await this.api.operation('setup.status');this.mode=setup.deployment_mode;if(this.mode==='legacy-native'){const result=await this.api.request('/api/speakers/session',{});this.csrf=result.csrf;}await this.refresh();}
    catch(e){this.el('speakers-status').textContent=e.message;this.render();}
  }
  async refresh() {
    this.polling=true;
    try {this.state=this.mode==='legacy-native'?await this.api.request('/api/speakers/status'):await this.api.operation('audio.status');this.render();}
    catch(e){this.state=null;this.render();this.el('speakers-status').textContent=e.message;}
    finally {this.polling=false;}
  }
  async perform(command) {
    // Stop is never gated by setup's pending request or dashboard busy lock.
    if(command!=='stop'&&this.pending)return;
    this.pending=true;this.render();
    try {
      if(command==='stop'&&this.mode!=='legacy-native'){
        const result=await this.api.operation('audio.stop');this.state=result;this.render();this.feedback(audioSummary(result));return;
      }
      const headers={'X-Speaker-CSRF':this.csrf};let body={};
      if(command==='enable') {
        const action=await this.api.request('/api/speakers/action',{},headers);
        body={action:action.action};
      }
      await this.api.request('/api/speakers/'+command,body,headers);await this.refresh();this.feedback('Stop request acknowledged. Inspect fresh output readback; physical silence is not inferred.');
    } catch(e) {this.el('speakers-status').textContent=e.message;this.feedback(`${e.message} Stop is not confirmed; pause Spotify and turn the amplifier down if needed.`);}
    finally {this.pending=false;this.renderButtons();}
  }
  feedback(message){const el=this.el('speaker-stop-feedback');if(el)el.textContent=message;}
  renderButtons() {
    this.el('speakers-setup').disabled=!this.authenticated||!this.csrf||this.pending||!['stopped',undefined].includes(this.state?.state);
    this.el('speakers-enable').disabled=!this.authenticated||this.pending||this.state?.state!=='prepared'||!this.state?.music?.human_bridge_available||this.state?.player_registered!==true;
    this.el('speakers-stop').disabled=!this.authenticated;
    this.el('speakers-legacy-details').hidden=this.mode!=='legacy-native';
  }
  render() {
    this.renderButtons();
    const s=this.state;
    if(this.mode!=='legacy-native'){
      this.el('music-source-status').textContent='Ordinary playback starts in Spotify → Garage Speakers. No chat, manual enable or listening session is required.';
      this.el('music-player-status').textContent='Full-range stereo · UMC1820 OUT3/4 · fixed −6 dB headroom · no subwoofer path.';
      this.el('speakers-human-status').textContent=s?.device_mode==='file'?'FILE worker only: no physical audio device or sound verified.':s?.physical_verified===true?'Physical device identity reported by worker. Listening and acoustic results are not inferred.':'Physical device identity not verified.';
      this.el('speakers-status').textContent=audioSummary(s);return;
    }
    this.el('music-source-status').textContent=s?.music?.spotify_connect_configured?`Official Spotify Connect · ${s?.spotify_connect?.state||'runtime not verified'}. Phone music delivery is not inferred from saved configuration.`:s?.music?.spotify_configured===true?'MA Spotify library configuration saved · runtime login and music delivery not verified.':'Spotify Connect configuration not verified.';
    this.el('music-player-status').textContent=s?.player_registered===true?'Garage Speakers playback connection verified.':'Garage Speakers playback connection not currently verified.';
    this.el('speakers-human-status').textContent=s?.music?.spotify_connect_configured?
      (s?.spotify_connect?.armed&&s?.spotify_connect?.receiver_verified?'Use Spotify only → Garage Speakers → Play. No MA session, code or periodic approval. Fosi is the main volume control; Spotify is digital attenuation. Turn Fosi fully down for first playback, then raise slowly.':`Spotify Connect automatic sound is ${s?.spotify_connect?.state||'not verified'}. Not ready for playback yet; no sound approval is restored. Manual controls are separate diagnostics/other-source fallback.`):
      'Spotify Connect is not configured/verified. Optional manual controls remain separate from the Spotify-only path.';
    this.el('speakers-status').textContent=!s?'Speaker output status unavailable.':
      s.state==='prepared'?(s.mode==='spotify_connect'?'Spotify output preparing · exact zero output, muted; waiting for current stream.':'Speaker check prepared · zero output, muted. No sound authorized.'):
      s.state==='listening'?(s.mode==='spotify_connect'?`Spotify Connect · ${s.transport==='playing'?'playing':s.transport==='paused'?'paused / native muted':'waiting for current Spotify stream'} · fixed stereo / −6 dB headroom · Fosi main volume · Spotify ${s.ma_volume_percent}% digital.`:s.mode==='normal'?`Optional manual listening session · ${s.transport==='playing'?'playing':s.transport==='paused'?'paused / native muted':'ready / waiting for your Play'} · ${s.seconds_remaining}s remaining · fixed line output / −6 dB headroom · Fosi main volume · MA ${s.ma_volume_percent}% (digital attenuation).`:`Optional speaker check active · ${s.seconds_remaining}s remaining · fixed −60 dB.`):
      s.state==='setting_up'?(s.mode==='spotify_connect'?'Preparing protected Spotify output silently.':'Preparing a silent speaker check.'):s.state==='stopping'?'Closing speaker output · waiting for verified exit.':
      s.state==='stopped'?'Speaker output stopped · no sound.':'Output stop not verified — do not start music.';
  }
}
export function audioSummary(s){if(!s)return 'Audio worker unavailable. Output Stop and physical silence are not verified.';return `${s.device_mode==='file'?'FILE worker · no physical sound claim':'Audio worker'} · ${s.state} · ${s.device_open?'device open':'device closed'}${s.reason?` · ${s.reason.replaceAll('_',' ')}`:''}. Physical silence is not inferred from software readback.`;}
