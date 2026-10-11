import {GarageAPI, ApiError, readTextFile} from './api.js';
import {renderPlot} from './plot.js';
import {profileEditArgs} from './profile.js';
import {SessionSelection} from './session.js';
import {MCP_CONFIG, workflowState, evidenceLabel, optionalClockDrift, canPropose, fitSummary, qualifiedSlots} from './workflow.js';
import {AGENT_TASK_BRIEF} from './brief.js';
import {JobsPanel} from './jobs.js';
import {Journey} from './journey.js';
import {RoomPanel} from './room.js';
import {ProofPanel} from './proof.js';
import {SpeakersPanel} from './speakers.js';
const $=id=>document.getElementById(id), api=new GarageAPI();
const speakersPanel=new SpeakersPanel(api);
const trustedLAN=document.body.dataset.trustedLan==='true';
let journey=null;
let proofPanel=null,roomContext={room:null,profile:null};
const jobsPanel=new JobsPanel(api,{onState:updateJourney});
function updateJourney(){if(!journey)return;const fresh=jobsPanel.stale?null:{...status,human_approval_available:jobsPanel.readiness?.human_approval_available,camilla_file_available:jobsPanel.fileReadiness?.available};journey.update({authenticated,status:fresh,session,...roomContext,proof:proofPanel?.proof,job:jobsPanel.job,cancelPending:jobsPanel.pending.has('cancel')});}
let authenticated=false, busy=false, status=null, profiles=[], sessions=[], session=null, selectedProfile=null;
let playbackError=null, actionError=null, players=[],maAuthenticated=false;
let capabilities=null, capabilitiesError=null;
const text=(id,value)=>{$(id).textContent=value;};
const finite=value=>typeof value==='number'&&Number.isFinite(value);
const make=(tag,className,value)=>{const el=document.createElement(tag);if(className)el.className=className;if(value!==undefined)el.textContent=value;return el;};
function toast(message,error=false){const box=$('action-status');box.textContent=message;box.classList.toggle('error',error);box.hidden=false;}
function errorText(e){return [e.message,...(e.requirements||[])].join('\n');}
function lock(){authenticated=false;status=null;profiles=[];sessions=[];session=null;sessionSelection.clear();players=[];maAuthenticated=false;selectedProfile=null;playbackError=null;actionError=null;document.querySelectorAll('form').forEach(form=>form.reset());$('login-panel').hidden=trustedLAN;$('sign-out').hidden=true;$('token').value='';$('sub-trim').value='';$('connection-dot').classList.remove('online');text('connection-label',trustedLAN?'LAN session unavailable':'Locked');text('status-json','No status available.');text('last-read','No readback yet');renderStatus();renderProfiles();renderSessions();renderSession();renderPlayers();renderPlot($('electrical-plot'),[],'No profile loaded');}
function setControls(){
  speakersPanel.setAuthenticated(authenticated);
  if(!authenticated){sessionStorage.removeItem('garage-fit-session');roomContext={room:null,profile:null};}
  roomPanel.setContext({authenticated,capabilities,busy});
  proofPanel?.setContext({authenticated,session,...roomContext});
  renderCandidateForm();
  jobsPanel.setContext({authenticated,capabilities,session,busy,status});
  const dsp=authenticated&&!busy&&status?.dsp?.available===true;
  ['volume','volume-submit','mute-button','sub-trim','sub-submit'].forEach(id=>$(id).disabled=!dsp);
  ['refresh','session-submit','session-select'].forEach(id=>$(id).disabled=!authenticated||busy);
  ['before-select','after-select'].forEach(id=>$(id).disabled=!authenticated||busy||!session);
  const filterImport=$('import-form').elements.kind.value==='filters';
  $('import-submit').disabled=!authenticated||busy||(filterImport?!selectedProfile:!session);
  $('import-form').elements.position.required=!filterImport;
  $('import-form').elements.position.disabled=filterImport;
  ['profile-edit-submit','peq-submit'].forEach(id=>$(id).disabled=!authenticated||busy||!selectedProfile);
  ['channel-fields','peq-fields'].forEach(id=>$(id).disabled=!authenticated||busy||!selectedProfile);
  $('peq-remove').disabled=!authenticated||busy||!selectedProfile||$('peq-form').elements.index.value==='';
  document.querySelectorAll('#profile-list button').forEach(button=>{button.disabled=!authenticated||busy||(button.dataset.apply==='true'&&!status?.dsp?.available);});
   if(typeof profileButton!=='undefined')profileButton.disabled=!authenticated||busy;
   document.querySelectorAll('[data-workflow-op]').forEach(button=>{const op=button.dataset.workflowOp;button.disabled=!authenticated||busy||!capabilities?.operations?.[op]||(op!=='calibration.plan'&&(!session?.workflow_version||session?.state==='cancelled'))||(op==='calibration.propose'&&!canPropose(session))||(op==='calibration.analyze'&&!qualifiedSlots(session).complete);});
  $('player-select').disabled=!authenticated||busy||!players.length;
  document.querySelectorAll('[data-playback]').forEach(button=>{button.disabled=!authenticated||busy||button.dataset.playback!=='pause'||!$('player-select').value;button.title=button.dataset.playback==='pause'?'Pause selected player; server verifies garage target':'Locked: trusted attended authority is not implemented';});
}
function renderStatus(){
  if(!authenticated){capabilities=null;capabilitiesError=null;}
  renderWorkflow();
  const dsp=status?.dsp, read=dsp?.readback?.result || dsp?.readback;
  text('volume-value',dsp?.available&&finite(read?.volume_db)?read.volume_db.toFixed(1):'—');
  text('dsp-state',dsp?.available?'Available':'Not verified');
  text('mute-state',dsp?.available&&typeof read?.muted==='boolean'?(read.muted?'Output muted · verified readback':'Output unmuted · verified readback'):'Mute state unknown · not a silence guarantee');
  text('master-requirement',dsp?.available?'Master ceiling −20 dB. Actions require server validation and readback; no automatic unmute.':dsp?.error?errorText(dsp.error):'Unlock to read actual DSP state. Unmute is never automatic.');
  text('health-dsp',dsp?.available?'Readback available':dsp?.error?'Unavailable':'Unknown');
  text('health-hardware','Not reported');text('health-mic','Not reported');
  text('health-ma',maAuthenticated?(players.length?'Authenticated · players listed':'Authenticated · no players'):playbackError?.code==='ma_credential_required'?'Credential required':status?.playback?.configured?'Configured · not verified':'Not verified');
  text('playback-state',players.length?'Players listed':'Not ready');
  text('playback-requirement',`${playbackError?errorText(playbackError):'Player listing is not a playback-ready guarantee.'} Play, media URL playback and next remain locked: independent trusted attended authority and physical commissioning are required.`);
  text('track-title','Now-playing not reported');text('track-artist','No track or transport state has been verified.');
  if(dsp?.available&&finite(read?.volume_db)&&!busy){$('volume').value=read.volume_db;text('volume-draft',`${read.volume_db} dB target`);}
  if(dsp?.available&&finite(read?.sub_trim))$('sub-trim').value=read.sub_trim;else $('sub-trim').value='';
  document.querySelector('.sub-row p').textContent=dsp?.available&&finite(read?.sub_trim)?`Actual trim ${read.sub_trim} dB`:'Actual trim not reported · input is a target';
  $('ma-link').hidden=true;
  renderDiagnostics();setControls();
}
function renderDiagnostics(){
  const list=$('diagnostic-list');list.replaceChildren();
  const entries=[status?.dsp?.error,playbackError,actionError].filter(Boolean);
  if(!authenticated)entries.push({code:'locked',message:'Unlock to inspect private status.',requirements:[]});
  if(authenticated)entries.push({code:'uncalibrated',message:'Acoustic performance has not been verified.',requirements:['Interface and microphone discovery, measured alignment and attended commissioning remain prerequisites.']},{code:'playback_unverified',message:'Track state and a tunneled Music Assistant browsing URL are not provided by this API.',requirements:['Continue music browsing in your separately configured Music Assistant UI. Player listing does not verify playback readiness.']});
  for(const entry of entries){const row=make('div','diagnostic'),body=make('div');row.append(make('span','code',entry.code));body.append(make('strong','',entry.message));for(const requirement of entry.requirements||[])body.append(make('p','',requirement));row.append(body);list.append(row);}
}
async function readback(){
  let next;try{next=await api.status();}catch(e){status=null;text('volume-value','—');text('status-json','Current state unavailable; previous readback is stale.');text('connection-label','Readback unavailable');$('connection-dot').classList.remove('online');renderStatus();throw e;}status=next;text('status-json',JSON.stringify(next,null,2));text('last-read',`Readback ${new Date().toLocaleTimeString()}`);
  text('connection-label',trustedLAN?'Home LAN':'Private session');$('connection-dot').classList.add('online');renderStatus();
}
async function refreshAll(){
  await readback();
  try{capabilities=await api.operation('capabilities');capabilitiesError=null;}catch(e){capabilities=null;capabilitiesError=e;}
  [profiles,sessions]=await Promise.all([api.operation('profiles.list'),api.operation('sessions.list')]);
  try{players=await api.operation('playback.status');maAuthenticated=true;playbackError=null;}catch(e){players=[];maAuthenticated=false;playbackError=e;}
  renderProfiles();renderSessions();renderPlayers();renderStatus();
  if(session)await loadSession(session.id);
  await jobsPanel.refresh();
  await roomPanel.refresh();
  const saved=sessionStorage.getItem('garage-fit-session');if(!session&&saved&&sessions.some(s=>s.id===saved))await loadSession(saved);
}
async function run(label,fn){
  if(busy)return;busy=true;actionError=null;setControls();toast(`${label}…`);
  try{await fn();if(authenticated)await readback();toast(authenticated?`${label}: server acknowledged; fresh status received.`:'Dashboard locked.');}
  catch(e){if(e.status===401){lock();if(trustedLAN){await init();toast('LAN session renewed. Retry your action; nothing was automatically repeated.',true);}else toast('Session expired. Unlock to continue.',true);}else{actionError=e;if(authenticated){try{await readback();}catch{status=null;renderStatus();}}toast(errorText(e),true);renderDiagnostics();}}
  finally{busy=false;setControls();}
}
function renderProfiles(){
  const list=$('profile-list');list.replaceChildren();
  if(!profiles.length){list.append(make('p','empty',authenticated?'No saved profiles. Import a validated profile JSON to start; no settings are assumed active.':'Unlock to read the profile library. No profile is assumed active.'));selectedProfile=null;}
  for(const p of profiles){const row=make('div','profile-item'),body=make('div');body.append(make('h3','',p.profile.name),make('p','',`${p.profile.crossover_hz} Hz crossover · offline preparation`),make('span','badge','ACOUSTICALLY UNVERIFIED'));const select=make('button','',p.id===selectedProfile?.id?'Selected':'Inspect & preview');select.onclick=()=>run('Load profile',async()=>{selectedProfile=await api.operation('profiles.get',{id:p.id});const preview=await api.operation('profiles.preview',{id:p.id});renderPlot($('electrical-plot'),Object.entries(preview.channels).map(([label,c])=>({label,points:c.points})),'Electrical-only filter magnitude; not acoustic response');renderProfiles();});row.append(body,select);list.append(row);}
  if(selectedProfile){const apply=make('button','','Apply selected profile');apply.dataset.apply='true';apply.disabled=!status?.dsp?.available||busy;apply.onclick=()=>run('Apply profile',async()=>{await api.operation('profiles.apply',{id:selectedProfile.id});});list.append(apply);}
  text('profile-readback',selectedProfile?JSON.stringify(selectedProfile.profile,null,2):'Select a profile to inspect its actual channel settings.');
  populateEditor();
  setControls();
}
function renderSessions(){
  const select=$('session-select'),current=sessionSelection.id;select.replaceChildren(new Option('Select a session',''));
  sessions.forEach(s=>select.add(new Option(s.name,s.id)));if(current&&sessions.some(s=>s.id===current))select.value=current;setControls();
}
const sessionSelection=new SessionSelection(id=>api.operation('sessions.get',{id}),state=>{session=state.session;if(session?.plan)sessionStorage.setItem('garage-fit-session',session.id);if([...$('session-select').options].some(option=>option.value===state.id))$('session-select').value=state.id;renderSession();if(state.pending)text('session-metadata','Loading selected session… Import disabled until readback succeeds.');});
async function loadSession(id){return sessionSelection.select(id);}
function renderSession(){
   renderWorkflow();
   const checklist=$('fit-slot-list');if(checklist){checklist.replaceChildren();if(session?.plan){for(const p of session.plan.positions)for(const c of session.plan.channels){const found=session.files.filter(f=>f.metadata.position===p&&f.metadata.channel===c);checklist.append(make('p','',`${roomContext.room?.annotations?.find(a=>a.id===p)?.label||p} · ${c} · ${found.length===1?'Imported — provenance checked when analyzed':found.length?'Duplicate slot — review':'Waiting for export'}`));}if(!session.files.length)checklist.append(make('p','hint','Your plan is saved; return when you have REW exports.'));}}
   let pos=$('import-form').elements.position;if(pos&&((session?.plan&&pos.tagName!=='SELECT')||(!session?.plan&&pos.tagName==='SELECT'))){const n=make(session?.plan?'select':'input');n.name='position';n.required=true;n.value=pos.value;pos.replaceWith(n);pos=n;}if(pos?.tagName==='SELECT'){const current=pos.value;pos.replaceChildren(new Option('Choose a fitting position',''));for(const p of session?.plan?.positions||[])pos.add(new Option(roomContext.room?.annotations?.find(a=>a.id===p)?.label||p,p));if([...pos.options].some(o=>o.value===current))pos.value=current;}
  text('session-metadata',session?Object.entries(session.metadata).map(([k,v])=>`${k.replaceAll('_',' ')}: ${v}`).join('\n'):'');
  const before=$('before-select'),after=$('after-select'),previous=[before.value,after.value];
  [before,after].forEach((select,i)=>{select.replaceChildren(new Option('Select a measurement',''));(session?.files||[]).filter(f=>f.format==='rew-response').forEach(f=>select.add(new Option(`${f.filename} · ${f.metadata.channel||'unknown'} · ${f.metadata.position||'position not recorded'}`,f.id)));if([...select.options].some(o=>o.value===previous[i]))select.value=previous[i];});
  const list=$('measurement-list');list.replaceChildren();
    for(const file of session?.files||[]){const entry=make('div');entry.append(make('strong','',file.filename),make('span','badge evidence-label',evidenceLabel(file)),make('p','',`${file.interpretation} · ${file.points.length} points`));const technical=make('details');technical.append(make('summary','','Import provenance & hash'),make('p','',`SHA256 ${file.sha256}`),make('p','',`Calibration applied: ${file.metadata.calibration_applied?'yes':'no'} · ${JSON.stringify(file.metadata)}`));entry.append(technical);list.append(entry);}
  if(!before.value&&!after.value){$('measured-plot').replaceChildren(make('p','empty','No measurement selected. Import a real response to see its curve.'));}setControls();
}
function renderPlayers(){const select=$('player-select');const current=select.value;select.replaceChildren(new Option('Choose a player',''));players.forEach(p=>select.add(new Option(`${p.name} · ${p.available?'available':'unavailable'}`,p.id)));if(players.some(p=>p.id===current))select.value=current;setControls();}
async function compare(){
  const before=$('before-select').value,after=$('after-select').value;
   if(before&&after){await run('Compare measurements',async()=>{const comparison=await api.operation('measurements.compare',{session_id:session.id,before_id:before,after_id:after});renderPlot($('measured-plot'),[{label:`Before · ${evidenceLabel(comparison.before)}`,points:comparison.before.points},{label:`After · ${evidenceLabel(comparison.after)}`,points:comparison.after.points}],'Imported before/after magnitude responses · source labels apply');if(!comparison.comparable_metadata)actionError=new ApiError({code:'setup_mismatch',message:'Measurement metadata differs. The curves are not a controlled before/after proof.',requirements:[comparison.warning]});renderDiagnostics();});}
   else{const file=session?.files.find(f=>f.id===(before||after));renderPlot($('measured-plot'),file?[{label:`${before?'Before':'After'} · ${evidenceLabel(file)}`,points:file.points}]:[],file?evidenceLabel(file):'No imported response selected');}
}
$('login-form').onsubmit=async event=>{event.preventDefault();if(busy)return;busy=true;text('login-error','');const input=$('token'),token=input.value;input.value='';event.target.querySelector('button').disabled=true;try{await api.login(token);authenticated=true;$('login-panel').hidden=true;$('sign-out').hidden=false;await refreshAll();}catch(e){if(!authenticated||e.status===401){lock();text('login-error',errorText(e));}else{actionError=e;renderDiagnostics();toast(errorText(e),true);}}finally{busy=false;event.target.querySelector('button').disabled=false;setControls();}};
$('sign-out').onclick=()=>run('Lock dashboard',async()=>{await api.logout();lock();});
$('refresh').onclick=()=>run('Refresh',refreshAll);
$('volume').oninput=()=>text('volume-draft',`${$('volume').value} dB target`);
$('volume-form').onsubmit=event=>{event.preventDefault();run('Set master level',()=>api.operation('dsp.volume',{db:Number($('volume').value)}));};
$('mute-button').onclick=()=>run('Mute output',()=>api.operation('dsp.mute'));
$('sub-trim').min='-30';
$('sub-trim').required=true;
// Match API precision; range/number widgets must not reject valid readback values.
document.querySelectorAll('input[type=number],input[type=range]').forEach(input=>input.step='any');
$('sub-form').onsubmit=event=>{event.preventDefault();run('Set subwoofer trim',()=>api.operation('dsp.sub_trim',{db:Number($('sub-trim').value)}));};
$('player-select').onchange=setControls;
document.querySelectorAll('[data-playback]').forEach(button=>button.onclick=()=>run('Pause playback',async()=>{await api.operation('playback.pause',{player_id:$('player-select').value});players=await api.operation('playback.status');renderPlayers();}));
$('session-select').onchange=async()=>{try{if(await loadSession($('session-select').value)){actionError=null;toast(session?'Selected session read back from the server.':'No session selected.');renderDiagnostics();}}catch(e){if(e.status===401)lock();else{actionError=e;renderDiagnostics();}toast(errorText(e),true);}};
$('session-form').onsubmit=event=>{event.preventDefault();const values=Object.fromEntries(new FormData(event.target));run('Create session',async()=>{const created=await api.operation('sessions.create',{name:values.name,metadata:{position:values.position,notes:values.notes,orientation:values.orientation,timing_reference:values.timing_reference,channel:values.channel,profile:values.profile}});sessions=await api.operation('sessions.list');await loadSession(created.id);renderSessions();});};
text('import-limit','UTF-8 text · up to 256 KiB / 20,000 numeric rows. Originals and SHA256 provenance are preserved.');
$('import-form').elements.kind.onchange=setControls;
$('import-form').onsubmit=event=>{event.preventDefault();const form=new FormData(event.target);run('Validate & import',async()=>{
  const provenance=importProvenance(form);
  const target=form.get('kind')==='filters'?null:sessionSelection.forImport($('session-select').value),file=form.get('file'),source=await readTextFile(file);
  if(form.get('kind')==='filters'){
    if(!selectedProfile)throw new ApiError({code:'profile_required',message:'Select a saved profile before importing cut-only REW filters.'});
    selectedProfile=await api.operation('profiles.import_rew',{id:selectedProfile.id,channel:form.get('channel'),text:source});profiles=await api.operation('profiles.list');renderProfiles();
    const preview=await api.operation('profiles.preview',{id:selectedProfile.id});renderPlot($('electrical-plot'),Object.entries(preview.channels).map(([label,c])=>({label,points:c.points})),'Electrical-only imported filter response');
  }else{
    if(sessionSelection.forImport($('session-select').value).id!==target.id)throw new ApiError({code:'session_changed',message:'Session changed during file read. Nothing was imported.'});
    await api.operation('measurements.import',{session_id:target.id,filename:file.name,format:form.get('kind')==='response'?'rew-response':'mic-calibration',text:source,metadata:{...target.metadata,...provenance,channel:form.get('channel'),position:form.get('position')}});
    if(sessionSelection.id===target.id)await loadSession(target.id);
  }
});};
$('before-select').onchange=compare;$('after-select').onchange=compare;
function populateChannel(){const fields=$('profile-edit').elements,p=selectedProfile?.profile,c=p?.channels[fields.channel.value];for(const key of ['gain','delay_ms'])fields[key].value=c?.[key]??'';for(const key of ['crossover_hz','sub_trim'])fields[key].value=p?.[key]??'';for(const key of ['invert','mute'])fields[key].checked=c?.[key]===true;fields.peq_bypass.checked=p?.peq_bypass===true;}
function populateBand(){const fields=$('peq-form').elements,bands=selectedProfile?.profile.channels[fields.channel.value]?.peq||[],band=fields.index.value===''?null:bands[Number(fields.index.value)];for(const key of ['freq','gain','q'])fields[key].value=band?.[key]??'';setControls();}
function populatePEQ(){const fields=$('peq-form').elements,bands=selectedProfile?.profile.channels[fields.channel.value]?.peq||[],previous=fields.index.value;fields.index.replaceChildren(new Option('Append a filter',''));bands.forEach((band,i)=>fields.index.add(new Option(`${i+1} · ${band.freq} Hz / ${band.gain} dB / Q ${band.q}`,String(i))));if([...fields.index.options].some(o=>o.value===previous))fields.index.value=previous;text('peq-summary',selectedProfile?`${bands.length}/10 slots · profile PEQ ${selectedProfile.profile.peq_bypass?'bypassed':'enabled'}. This is offline preparation.`:'No filters loaded.');populateBand();}
function populateEditor(){text('editor-profile',selectedProfile?`Preparing “${selectedProfile.profile.name}” · saved source ${selectedProfile.id}. Not assumed active.`:'Select a profile to prepare its settings.');populateChannel();populatePEQ();}
async function saveEdit(form,peq=false,remove=false){if(!selectedProfile)return;const keys=peq?['channel','index','freq','gain','q']:['channel','gain','delay_ms','crossover_hz','sub_trim'],fields=Object.fromEntries(keys.map(key=>[key,form.elements[key].value]));if(!peq)for(const key of ['invert','mute','peq_bypass'])fields[key]=form.elements[key].checked;selectedProfile=await api.operation('profiles.edit',profileEditArgs(selectedProfile,fields,peq,remove));profiles=await api.operation('profiles.list');renderProfiles();const preview=await api.operation('profiles.preview',{id:selectedProfile.id});renderPlot($('electrical-plot'),Object.entries(preview.channels).map(([label,c])=>({label,points:c.points})),'Electrical-only offline prepared profile response');}
$('profile-edit').onsubmit=event=>{event.preventDefault();run('Save profile preparation',()=>saveEdit(event.target));};
$('peq-form').onsubmit=event=>{event.preventDefault();run('Prepare filter',()=>saveEdit(event.target,true));};
$('profile-edit').elements.channel.onchange=populateChannel;
$('peq-form').elements.channel.onchange=()=>{$('peq-form').elements.index.value='';populatePEQ();};
$('peq-form').elements.index.onchange=populateBand;
$('peq-remove').onclick=()=>run('Remove offline filter',()=>saveEdit($('peq-form'),true,true));
// Add profile import without inventing an active or measured profile.
const profileImport=make('form'),profileLabel=make('label','','Import validated profile JSON'),profileFile=make('input');profileFile.type='file';profileFile.accept='.json';profileFile.required=true;profileFile.name='file';profileLabel.append(profileFile);const profileButton=make('button','','Save offline profile');profileButton.type='submit';profileImport.append(profileLabel,profileButton,make('p','hint','Existing strict garage 2.1 schema only. Import does not apply to speakers.'));$('profile-list').after(profileImport);
profileImport.onsubmit=event=>{event.preventDefault();if(!authenticated)return;run('Save offline profile',async()=>{const source=await readTextFile(profileFile.files[0]);let profile;try{profile=JSON.parse(source);}catch{throw new ApiError({code:'invalid_json',message:'Select a valid profile JSON document.'});}selectedProfile=await api.operation('profiles.save',{profile});profiles=await api.operation('profiles.list');renderProfiles();const preview=await api.operation('profiles.preview',{id:selectedProfile.id});renderPlot($('electrical-plot'),Object.entries(preview.channels).map(([label,c])=>({label,points:c.points})),'Electrical-only imported profile response');});};
// Preserve explicit setup metadata, including timing/orientation, in the session.
for(const [name,label,options] of [['orientation','Microphone orientation',['unknown','0','90']],['channel','Channel',['unknown','left','right','sub','combined']]]){const wrap=make('label','',label),select=make('select');select.name=name;options.forEach(value=>select.add(new Option(value,value)));wrap.append(select);$('session-submit').before(wrap);}
for(const [name,label] of [['timing_reference','Timing reference (write “none” if absent)'],['profile','Profile / setup reference']]){const wrap=make('label','',label),input=make('input');input.name=name;input.maxLength=120;wrap.append(input);$('session-submit').before(wrap);}
document.querySelectorAll('.sidebar nav a').forEach(a=>a.onclick=()=>{document.querySelectorAll('.sidebar nav a').forEach(link=>link.classList.toggle('active',link===a));});
function renderWorkflow(){
  const state=workflowState({authenticated,status,capabilities,session,...roomContext,proof:proofPanel?.proof});
  text('agent-connection',state.connection);text('workflow-next',state.next);text('workflow-measurement',state.measurement);
  const checks=$('workflow-checks');checks.replaceChildren();for(const [label,value] of state.checks){const row=make('div');row.append(make('strong','',label),make('p','',value));checks.append(row);}
  const operations=$('workflow-operations');operations.replaceChildren();for(const name of state.operations)operations.append(make('li','',name));if(!state.operations.length)operations.append(make('li','','No capability readback available.'));
  text('capabilities-error',capabilitiesError?errorText(capabilitiesError):'');
    renderWorkflowRecords();
    renderCandidateForm();
  updateJourney();
}
text('mcp-config',JSON.stringify(MCP_CONFIG,null,2));
const configDetails=make('details','mcp-setup-details');configDetails.append(make('summary','','Review MCP configuration'));$('mcp-config').before(configDetails);configDetails.append($('mcp-config'));configDetails.before($('copy-mcp'));
$('copy-mcp').onclick=async()=>{try{await navigator.clipboard.writeText(JSON.stringify(MCP_CONFIG,null,2));text('copy-result','Configuration copied. Agent connection has not been verified.');}catch{configDetails.open=true;const selection=window.getSelection(),range=document.createRange();range.selectNodeContents($('mcp-config'));selection.removeAllRanges();selection.addRange(range);text('copy-result','Clipboard unavailable on this HTTP browser. Configuration selected: use your device’s Copy action.');}};
const advanced=make('details','advanced-shell'),advancedSummary=make('summary','','Advanced controls & profile library');advanced.id='advanced-controls';advancedSummary.append(make('span','','Output · playback · manual offline preparation'));advanced.append(advancedSummary);$('overview').before(advanced);advanced.append($('overview'),$('profiles'));$('diagnostics').after(advanced);
$('open-profiles').onclick=()=>{advanced.open=true;};
document.querySelector('.brand').href='#connect';
const briefCard=make('div','card agent-brief'),briefTitle=make('h3','','Give your agent a safe starting brief');briefCard.append(make('p','eyebrow','YOU SET THE GOAL'),briefTitle,make('p','','Copy this task into your own connected MCP agent. It asks for preparation and evidence, not permission to make sound.'));
const briefText=make('details'),briefSummary=make('summary','','Review the task brief'),briefPre=make('pre','',AGENT_TASK_BRIEF);briefPre.id='agent-task-brief';briefText.append(briefSummary,briefPre);const briefCopy=make('button','','Copy safe task brief');briefCopy.id='copy-task-brief';const briefResult=make('p','hint');briefResult.id='brief-copy-result';briefResult.setAttribute('role','status');briefCard.append(briefText,briefCopy,briefResult);$('connect').append(briefCard);
briefCopy.onclick=async()=>{try{await navigator.clipboard.writeText(AGENT_TASK_BRIEF);briefResult.textContent='Brief copied. No agent connection or sound approval is implied.';}catch{briefText.open=true;const selection=window.getSelection(),range=document.createRange();range.selectNodeContents(briefPre);selection.removeAllRanges();selection.addRange(range);briefResult.textContent='Clipboard unavailable here. Brief selected: use your device’s Copy action.';}};
// Backend contract v1: these are published shared operations, not sound actions.
const planForm=make('form'),planHeading=make('h3','','Create a quiet measurement plan');planForm.append(planHeading);
function field(form,name,label,values=null,value=''){const wrap=make('label','',label),input=make(values?'select':'input');input.name=name;if(values)for(const option of values)input.add(new Option(option,option,option===value,option===value));else{input.type='text';input.defaultValue=value;}input.value=value;wrap.append(input);form.append(wrap);return input;}
field(planForm,'name','Plan name',null,'Listening position');field(planForm,'positions','Positions · comma separated',null,'seat');field(planForm,'channels','Channels · comma separated',null,'left,right,sub');field(planForm,'target','Import workflow',['laptop-rew','server-umik-umc'],'laptop-rew');field(planForm,'sample_rate','Sample rate',['48000','44100','96000'],'48000');field(planForm,'orientation','Calibration orientation',['90','0'],'90');
const planButton=make('button','primary','Save plan · no sound');planButton.dataset.workflowOp='calibration.plan';planButton.disabled=true;planForm.append(planButton,make('p','hint','A plan specifies bounded capture requirements. Capture and human sound authority are not implemented; import existing REW data instead.'));$('session-select').before(planForm);
planForm.onsubmit=event=>{event.preventDefault();run('Create quiet plan',async()=>{const v=Object.fromEntries(new FormData(planForm));const created=await api.operation('calibration.plan',{name:v.name,positions:v.positions.split(',').map(p=>p.trim()),channels:v.channels.split(',').map(p=>p.trim()),target:v.target,sample_rate:Number(v.sample_rate),orientation:v.orientation});sessions=await api.operation('sessions.list');renderSessions();await loadSession(created.id);$('plan-preparation')?.removeAttribute('open');});};
const provenance=make('details'),provenanceTitle=make('summary','','Analysis provenance · required for versioned plans');provenance.append(provenanceTitle);const provenanceFields=make('div');provenance.append(provenanceFields);$('import-submit').before(provenance);
field(provenanceFields,'source','Data source',['measured','synthetic'],'measured');field(provenanceFields,'sample_rate','Response sample rate',['48000','44100','96000'],'48000');field(provenanceFields,'orientation','Calibration orientation',['90','0'],'90');field(provenanceFields,'calibration_sha256','Applied calibration SHA256');field(provenanceFields,'calibration_applied','Calibration already applied to response?',['no','yes'],'no');field(provenanceFields,'timing_reference','Timing reference · write none if absent',null,'none');field(provenanceFields,'timing_quality','Timing quality',['unknown','unverified','verified'],'unknown');field(provenanceFields,'microphone_serial','Microphone serial · optional');
// Inputs inherit the import form; calibration files alone are never treated as applied.
const clockDrift=field(provenanceFields,'clock_drift_ppm','Clock drift · ppm (optional; blank means unknown)');clockDrift.type='number';clockDrift.min='-1000';clockDrift.max='1000';clockDrift.step='any';provenanceFields.append(make('p','hint','Supplied drift is not independently verified. Known absolute drift >100 ppm blocks proposals; unknown remains provisional magnitude-only. No compensation is implemented.'));
function importProvenance(form){if(form.get('kind')!=='response')return {};if(!/^[a-f0-9]{64}$/.test(form.get('calibration_sha256')||'')){provenance.open=true;provenanceFields.querySelector('[name="calibration_sha256"]').focus();throw new ApiError({code:'provenance_required',message:'Enter the 64-character calibration hash from your export. Nothing imported; your file and fields are retained.'});}const values={source:form.get('source'),sample_rate:Number(form.get('sample_rate')),orientation:form.get('orientation'),calibration_sha256:form.get('calibration_sha256'),calibration_applied:form.get('calibration_applied')==='yes',timing_reference:form.get('timing_reference'),timing_quality:form.get('timing_quality')};if(form.get('microphone_serial'))values.microphone_serial=form.get('microphone_serial');try{Object.assign(values,optionalClockDrift(form.get('clock_drift_ppm')));}catch(e){throw new ApiError({code:'invalid_arguments',message:e.message});}return values;}
function renderWorkflowRecords(){
  const host=$('workflow-records');host.replaceChildren();
  if(!authenticated){host.append(make('p','hint','Unlock to read plans and correction proposals.'));return;}
  if(!session?.workflow_version){host.append(make('p','hint','Create or select a versioned quiet plan in Measure. Legacy measurement sessions remain supported for import and comparison.'));return;}
  const p=session.plan;host.append(make('h3','',session.name),make('p','metadata',`Workflow v${session.workflow_version} · ${session.state} · no sound / no capture`));
  if(session.derived_results_superseded){host.append(make('p','error-text',session.derived_results_warning||'Previous derived results are superseded; no old validation is current.'));for(const requirement of session.derived_results_requirements||[])host.append(make('p','hint',requirement));host.append(make('p','hint',session.state==='cancelled'?'Cancelled workflow remains terminal; create a new plan.':'Recompute required: analyze retained imports, then prepare a new proposal.'));}
  const eligibility=session.analysis?.proposal_eligibility;
  if(!session.derived_results_superseded&&eligibility){host.append(make('p',eligibility.eligible===true?'hint':'error-text',eligibility.eligible===true?'Eligible for provisional magnitude-only proposal; not verified timing or acoustic success.':`Proposal blocked: known absolute clock drift exceeds ${eligibility.maximum_known_absolute_drift_ppm} ppm; acquire new qualified measurements. No compensation supported.`));for(const [id,drift] of Object.entries(session.analysis.timing?.clock_drift_ppm_by_file||{}))host.append(make('p','hint',`${id}: clock drift ${drift===null?'unknown · provisional':`${drift} ppm · supplied provenance only`}`));}
  host.append(make('p','',`${p.target} · ${p.sample_rate} Hz · ${p.orientation}° calibration · ${p.positions.join(', ')} · ${p.channels.join(', ')}`));
  host.append(make('p','hint',`Specification only: ${p.sweep.duration_seconds}s sweep at ${p.sweep.level_dbfs} dBFS; ≤${p.maximum_total_seconds}s total. Digital level is not calibrated acoustic SPL.`));
   const later=make('details');later.append(make('summary','','Capture requirements · needed later for sound'));const prerequisites=make('ul');for(const requirement of p.prerequisites)prerequisites.append(make('li','',requirement));later.append(prerequisites);host.append(later);
   const actions=make('div','control-row');for(const [op,label] of [['calibration.analyze','Analyze fitting imports'],['calibration.propose','Prepare recommendation'],['calibration.cancel','Cancel plan · retain originals']]){const button=make('button','',label);button.dataset.workflowOp=op;button.disabled=busy||!capabilities?.operations?.[op]||session.state==='cancelled'||(op==='calibration.propose'&&!canPropose(session))||(op==='calibration.analyze'&&!qualifiedSlots(session).complete);button.onclick=()=>{if(op==='calibration.cancel'&&!confirm('Cancel this plan? Originals are retained; cancellation cannot be undone.'))return;run(label,async()=>{const id=session.id;await api.operation(op,{id});await loadSession(id);});};actions.append(button);}host.append(actions);
  for(const [kind,record] of [['Analysis',session.analysis],['Proposal',session.proposal]]){if(!record||session.derived_results_superseded)continue;host.append(make('h3','workflow-result-title',kind),make('span','badge evidence-label',evidenceLabel(record)));if(record.method)host.append(make('p','hint',record.method));if(record.warnings?.length){host.append(make('p','hint',record.warnings[0]));const warnings=make('details');warnings.append(make('summary','',`All limitations (${record.warnings.length})`));for(const warning of record.warnings)warnings.append(make('p','hint',warning));host.append(warnings);}
    if(kind==='Proposal')for(const [channel,data] of Object.entries(record.channels)){host.append(make('h3','workflow-result-title',`${channel} · predicted target fit`));const lines=fitSummary(data);for(const line of lines.slice(0,2))host.append(make('p','hint',line));const fit=make('details');fit.append(make('summary','','Per-position fit & validation'));for(const line of lines.slice(2))fit.append(make('p','hint',line));host.append(fit);}
    for(const [channel,data] of Object.entries(record.channels)){const box=make('details','workflow-result');box.append(make('summary','',`${channel} · ${kind==='Analysis'?'normalized magnitude details':'offline filters & electrical prediction'}`));if(kind==='Analysis'){box.append(make('p','hint',`Positions: ${data.positions.join(', ')} · normalized magnitude, not calibrated SPL`));const plot=make('div','plot');box.append(plot);renderPlot(plot,[{label:`${record.source} · normalized ${channel}`,points:data.points}],`${record.source==='synthetic'?'SYNTHETIC':'Imported'} magnitude analysis · no delay or phase inference`);}else{box.append(make('p','hint',`${data.peq.length} cut-only filters · ${record.reserved_headroom_db} dB reserved headroom · ${data.validation_passed?'electrical validation passed':'electrical validation not passed'} · NOT APPLIED`));const filters=make('ul');for(const eq of data.peq)filters.append(make('li','',`${eq.freq} Hz · ${eq.gain} dB · Q ${eq.q}`));box.append(filters);const plot=make('div','plot');box.append(plot);renderPlot(plot,[{label:'Electrical PEQ + reserved headroom',points:data.electrical_points}],'Electrical prediction only · not measured room response');}host.append(box);}
  }
}
// Existing nodes move once. Navigation never rebuilds forms or changes server state.
function disclose(node,id,label){if(!node)return;const details=make('details','stage-disclosure');details.id=id;details.append(make('summary','',label));node.before(details);details.append(node);return details;}
disclose(planForm,'plan-preparation','Create a quiet plan · no sound');disclose($('import-form'),'measurement-import','Import REW data & provenance');
disclose($('measured-plot').closest('.card'),'measurement-comparison','Compare response & electrical previews');
disclose($('diagnostics'),'technical-diagnostics','Diagnostics & technical readback');
disclose($('job-request'),'job-request-details','Prepare a file-only job');
disclose(document.querySelector('.authority-card'),'job-authority-details','Separate human authority · review availability');
disclose(document.querySelector('.job-history-card'),'job-history-details','Browse job history');
const measurementWorkspace=disclose($('calibration'),'measurement-workspace','Measurement plan & import workspace');measurementWorkspace.open=true;
const roomHost=make('section','card room-workspace');roomHost.id='room-workspace';$('check').append(roomHost);
const roomPanel=new RoomPanel(api,{host:roomHost,onUnauthorized:()=>lock(),onImport:({annotation,channel})=>{const f=$('import-form');f.elements.position.value=annotation.id;f.elements.orientation.value=annotation.orientation;if(channel)f.elements.channel.value=channel;f.elements.kind.value='response';f.elements.source.value='measured';$('measurement-import').open=true;journey.activate('calibration',{target:'measurement-import',reveal:true,focus:'heading'});toast(`${annotation.label}: stable ID set for import. ${annotation.mic_height_m} m / ${annotation.orientation}°. Select an existing session; calibration provenance remains required.`);}});
roomPanel.onChange=context=>{roomContext=context;proofPanel?.setContext({authenticated,session,...roomContext});updateJourney();};
roomPanel.onPlan=({name,positions,orientation})=>run('Create profile measurement plan',async()=>{const created=await api.operation('calibration.plan',{name:name.slice(0,120),positions,orientation,channels:['left','right','sub'],target:'laptop-rew',sample_rate:48000});sessions=await api.operation('sessions.list');renderSessions();await loadSession(created.id);journey.activate('calibration',{focus:'heading'});});
const quickCopies=make('div','agent-quick-actions');quickCopies.append($('copy-mcp'),$('copy-task-brief'));document.querySelector('#connect .section-heading').after(quickCopies);
journey=new Journey({onCancel:job=>{if(job)jobsPanel.action('cancel',()=>api.operation('jobs.cancel',{id:job.id}));}});
$('stage-check').append($('technical-diagnostics'));
// Quiet preparation is the main product; hardware and external-agent setup are secondary.
text('connect-title','Prepare your listening room');document.querySelector('#connect .subtitle').textContent='Choose a goal. Bring in REW exports. Review a recommendation and its evidence — without making sound.';
const choose=make('a','primary browse-link','Choose my room →');choose.href='#check';document.querySelector('#connect .section-heading').after(choose);
const mcpCard=$('mcp-config').closest('.card'),external=make('details','stage-disclosure');external.append(make('summary','','Use my own agent · SSH MCP'));mcpCard.before(external);external.append(mcpCard,briefCard,quickCopies);
text('check-title','Your room & listening goal');const readiness=make('details','stage-disclosure');readiness.append(make('summary','','System status — needed later for sound'));const checkCard=$('workflow-checks').closest('.card');checkCard.before(readiness);readiness.append(checkCard);$('check').insertBefore(roomHost,readiness);
text('calibration-title','Bring in your measurements');text('improve-title','Review your recommendation');text('verify-title','Compare independent imported evidence');
const importPosition=$('import-form').elements.position,positionSelect=make('select');positionSelect.name='position';positionSelect.required=true;importPosition.replaceWith(positionSelect);
const slotsHost=make('div');slotsHost.id='fit-slot-list';$('session-metadata').after(slotsHost);
const importCTA=make('button','primary','Import REW exports');importCTA.type='button';importCTA.onclick=()=>{$('measurement-import').open=true;$('import-form').scrollIntoView({block:'start'});};slotsHost.before(importCTA);
provenance.querySelector('summary').textContent='Source & calibration · required for analysis';provenance.open=true;
const candidateHost=make('div','card');candidateHost.id='candidate-workspace';$('improve').append(candidateHost);
function renderCandidateForm(){
  if(!document.getElementById('candidate-workspace'))return;
  const host=$('candidate-workspace'),old=host.querySelector('select')?.value;host.replaceChildren(make('h3','','Save an offline candidate'),make('p','hint','Nothing is applied. Use an explicitly selected fixed baseline; preserve crossover, delay and polarity. Existing -6 dB compiler headroom is not added twice.'));
  const baselineSelect=field(host,'candidate-baseline','Fixed baseline',[''],'');baselineSelect.replaceChildren(new Option('Choose a saved fixed baseline',''));for(const p of profiles.filter(p=>!p.candidate_provenance))baselineSelect.add(new Option(p.profile.name,p.id));if([...baselineSelect.options].some(o=>o.value===old))baselineSelect.value=old;
  const starter=make('button','','Save fixed starter baseline · offline only');starter.disabled=!authenticated||busy;starter.onclick=()=>run('Save fixed baseline',async()=>{const profile={name:'Fixed starter · 80 Hz / no EQ',channels:Object.fromEntries(['left','right','sub'].map(c=>[c,{gain:0,mute:false,delay_ms:0,invert:false,peq:[]}])),sub_trim:0,peq_bypass:false,crossover_hz:80};await api.operation('profiles.save',{profile});profiles=await api.operation('profiles.list');renderProfiles();renderCandidateForm();});host.append(starter);
  const save=make('button','primary','Save recommendation as offline candidate');save.disabled=!authenticated||busy||!session?.proposal_available||!baselineSelect.value;baselineSelect.onchange=()=>save.disabled=!session?.proposal_available||busy||!baselineSelect.value;
  save.onclick=()=>run('Save offline candidate',async()=>{await api.operation('candidates.save',{baseline_id:baselineSelect.value,proposal_session_id:session.id,name:`${session.name.slice(0,80)} · candidate`});profiles=await api.operation('profiles.list');renderProfiles();renderCandidateForm();await proofPanel?.refresh();});host.append(save,make('p','hint','No useful cut? Leaving the fixed baseline unchanged is a valid outcome. Candidate predictions are not measured benefit.'));
}
const verifyContent=$('verify').querySelector('.card');verifyContent.replaceChildren();verifyContent.id='proof-workspace';proofPanel=new ProofPanel(api,{host:verifyContent,onChange:()=>updateJourney(),onUnauthorized:()=>lock()});
const fileOnly=disclose($('jobs'),'file-only-experiments','Advanced FILE-only experiments · synthetic, optional');fileOnly.open=false;
renderCandidateForm();
async function init(){renderWorkflow();try{await api.resume();authenticated=true;$('login-panel').hidden=true;$('sign-out').hidden=trustedLAN;await refreshAll();}catch(e){if(e.status===401||e.status===403)lock();else{lock();text('login-error',errorText(e));}}}
init();
