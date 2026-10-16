// Dedicated human enrollment only. Never route login credentials through operations/chat.
const KEY='garage.ma.pending-request';
const VALID_ID=/^[A-Za-z0-9_-]{16,64}$/;
export function enrollmentRequestID(){return Array.from(globalThis.crypto.getRandomValues(new Uint8Array(16)),byte=>byte.toString(16).padStart(2,'0')).join('');}
const REASONS={ma_credential_required:'Music Assistant is not connected yet.',ma_installer_unavailable:'Private credential installation is unavailable. Setup must be prepared before connecting.',ma_enrollment_origin_required:'Connect is available only on the configured trusted home Wi-Fi dashboard.',ma_enrollment_session_required:'Unlock the dashboard normally before connecting.',csrf_rejected:'Your dashboard session needs refreshing before connecting.',ma_authentication_failed:'Music Assistant did not accept that username and password.',ma_scope_required:'That account lacks the required Music Assistant permissions.',ma_unavailable:'Music Assistant is unavailable.',ma_enrollment_busy:'Another connection request is in progress. Check connection status before trying again.',ma_enrollment_rate_limited:'Too many connection attempts. Wait before trying again.',invalid_enrollment_request:'Check the username and password lengths before connecting.'};
export function enrollmentReady(status){return status?.state==='ready'&&status.scope_verified===true&&VALID_ID.test(status.enrollment_id||'');}
export function enrollmentValidation(username,password){
  if(!username)return {field:'ma-username',message:'Enter your existing Music Assistant username before connecting.'};
  if(username.length>100||/[\x00-\x1f\x7f]/.test(username))return {field:'ma-username',message:'Use a Music Assistant username of 1–100 characters without control characters.'};
  if(!password)return {field:'ma-password',message:'Enter your Music Assistant password before connecting.'};
  if(password.length>1024)return {field:'ma-password',message:'Use a Music Assistant password of 1–1024 characters.'};
  return null;
}
export function revealEnrollmentElement(element,{focus=false}={}){
  if(!element?.getBoundingClientRect)return;
  const doc=element.ownerDocument,win=doc.defaultView,viewport=win.visualViewport;
  const viewportTop=viewport?.offsetTop||0,viewportHeight=viewport?.height||win.innerHeight;
  const stickyBottom=Math.max(viewportTop,...['.speaker-stop-bar','.sidebar'].map(selector=>{
    const e=doc.querySelector(selector);return e&&win.getComputedStyle(e).position==='sticky'?e.getBoundingClientRect().bottom:0;
  }));
  const rect=element.getBoundingClientRect();
  if(rect.top<stickyBottom+20||rect.bottom>viewportTop+viewportHeight-20){
    const desired=stickyBottom+Math.max(20,(viewportTop+viewportHeight-stickyBottom-rect.height)/2);
    win.scrollTo({top:Math.max(0,win.scrollY+rect.top-desired),behavior:'instant'});
  }
  if(focus)element.focus({preventScroll:true});
}
export function enrollmentText(status,uncertain=false){
  if(uncertain||status?.code==='ma_enrollment_interrupted')return 'Connection interrupted · issuance is uncertain. Reconciliation is required before another Connect request. Check connection status; do not retry with a new request.';
  if(enrollmentReady(status))return 'Music Assistant connected · both private credential copies and account permissions verified. No playback or sound authority granted.';
  return REASONS[status?.code]||'Connection status unavailable. No verified connection is assumed.';
}
export class MAEnrollmentPanel {
  constructor(api,{host,storage=globalThis.sessionStorage,onReadback=async()=>{}}){
    this.api=api;this.host=host;this.storage=storage;this.onReadback=onReadback;this.authenticated=false;this.pending=false;this.status=null;
    try{const saved=storage.getItem(KEY);this.requestID=VALID_ID.test(saved||'')?saved:null;}catch{this.requestID=null;}
    host.innerHTML='<div class="card-heading"><h3>Connect Music Assistant</h3><button id="ma-status-refresh" type="button">Check connection status</button></div><p class="hint">Use your existing Music Assistant account. No token copying or separate Music Assistant page is needed.</p><p id="ma-enrollment-status" role="status" aria-live="polite"></p><form id="ma-enrollment-form" autocomplete="on"><label>Music Assistant username<input id="ma-username" name="username" autocomplete="username" autocapitalize="none" spellcheck="false" minlength="1" maxlength="100" required></label><label>Music Assistant password<input id="ma-password" name="password" type="password" autocomplete="current-password" minlength="1" maxlength="1024" required></label><p class="hint" id="ma-connect-disclosure">Connect signs into your existing Music Assistant account and creates one garage-service integration token with your account’s existing roles, permissions and player/provider filters. It is stored privately for the garage app and observer. This trusted-LAN HTTP connection is not encrypted: use trusted home Wi-Fi only. It does not accept Spotify/Soloist terms, start music, change volume, grant measurement authority or enable AI.</p><div class="form-actions"><button id="ma-enrollment-connect" class="primary" type="submit" aria-describedby="ma-connect-disclosure">Connect Music Assistant</button><button id="ma-enrollment-cancel" type="button">Cancel · clear login</button></div></form>';
    this.el=id=>host.querySelector('#'+id);
    // Own validation feedback: native required-field bubbles can hide beneath sticky navigation.
    this.el('ma-enrollment-form').noValidate=true;
    for(const id of ['ma-username','ma-password']){
      this.el(id).insertAdjacentHTML('afterend',`<span id="${id}-error" class="ma-field-error"></span>`);
      this.el(id).setAttribute('aria-describedby',`${id}-error`);
      this.el(id).oninput=()=>{this.validation=null;this.render();};
    }
    this.el('ma-connect-disclosure').insertAdjacentHTML('afterend','<p id="ma-enrollment-feedback" role="status" aria-live="polite" tabindex="-1"></p>');
    this.el('ma-enrollment-form').onsubmit=e=>{e.preventDefault();this.connect();};
    this.el('ma-status-refresh').onclick=()=>this.refresh();
    this.el('ma-enrollment-cancel').onclick=()=>{this.clearCredentials();this.feedbackActive=false;this.render();};
    // Journey navigation uses hidden regions/replaceState, not necessarily hashchange.
    const stage=host.closest('[data-stage]');if(stage){this.navigationObserver=new MutationObserver(()=>{if(stage.hidden)this.clearCredentials();});this.navigationObserver.observe(stage,{attributes:true,attributeFilter:['hidden']});}
    globalThis.addEventListener?.('pagehide',()=>this.clearCredentials());
    this.render();
  }
   clearCredentials(){this.el('ma-username').value='';this.el('ma-password').value='';this.validation=null;}
  remember(id){this.requestID=id;try{if(id)this.storage.setItem(KEY,id);else this.storage.removeItem(KEY);}catch{/* Memory remains conservative for this session. */}}
  setAuthenticated(value){this.authenticated=value;if(!value){this.clearCredentials();this.status=null;}this.render();if(value)this.refresh();}
  acceptStatus(status){this.status=status;if(enrollmentReady(status))this.remember(null);this.render();}
  render(){
    const blocked=!this.authenticated||this.pending||!!this.requestID||enrollmentReady(this.status)||this.status?.code==='ma_enrollment_interrupted'||this.status?.state!=='setup_required';
    for(const id of ['ma-username','ma-password','ma-enrollment-connect'])this.el(id).disabled=blocked;
    this.el('ma-status-refresh').disabled=!this.authenticated||this.pending;
    this.el('ma-enrollment-cancel').disabled=!this.authenticated;
    const message=!this.authenticated?'Unlock to read connection status.':this.validation?.message|| (this.pending?'Connecting · do not submit another request. Cancel clears login fields; it cannot undo a request already sent.':enrollmentText(this.status,!!this.requestID));
    this.el('ma-enrollment-status').textContent=message;
    this.el('ma-enrollment-feedback').textContent=this.feedbackActive?message:'';
    for(const id of ['ma-username','ma-password']){this.el(id).setAttribute('aria-invalid',String(this.validation?.field===id));this.el(id+'-error').textContent=this.validation?.field===id?this.validation.message:'';}
  }
  async refresh(){if(!this.authenticated||this.pending)return;this.pending=true;this.render();try{this.acceptStatus(await this.api.request('/api/setup/ma/status'));}catch{this.status={state:'unavailable'};}finally{this.pending=false;this.render();}}
  async connect(){
    if(!this.authenticated||this.pending||this.requestID||this.status?.state!=='setup_required'||this.status?.code==='ma_enrollment_interrupted')return;
    let username=this.el('ma-username').value,password=this.el('ma-password').value;
    const validation=enrollmentValidation(username,password);this.feedbackActive=true;
    if(validation){username='';password='';this.el('ma-password').value='';this.validation=validation;this.render();revealEnrollmentElement(this.el(validation.field),{focus:true});return;}
    const request_id=enrollmentRequestID();this.remember(request_id);this.pending=true;this.clearCredentials();this.render();
    revealEnrollmentElement(this.el('ma-enrollment-feedback'));
    try{
      // No automatic retry, even if issuance succeeds but a reply is lost.
      await this.api.request('/api/setup/ma/enroll',{username,password,request_id,confirm:true});
      username='';password='';
      this.acceptStatus(await this.api.request('/api/setup/ma/status'));
    }catch(e){
      const code=e.code;
      if(Object.hasOwn(REASONS,code)&&!['ma_enrollment_busy'].includes(code)){this.remember(null);this.status={state:code==='ma_installer_unavailable'?'unavailable':'setup_required',code};}
      else this.status={state:'unavailable',code:'ma_enrollment_interrupted'};
    }finally{username='';password='';this.clearCredentials();this.pending=false;this.render();if(this.authenticated)revealEnrollmentElement(this.el('ma-enrollment-feedback'));try{await this.onReadback();}catch{/* Do not relabel enrollment from an unrelated setup failure. */}}
  }
}
