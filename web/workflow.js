// Readiness is reported evidence, never inferred from a successful login.
export const MCP_CONFIG = {mcpServers:{'garage-audio':{command:'ssh',args:['-T','-o','BatchMode=yes','zach@192.168.68.74','docker','exec','-i','garage-audio-dashboard','python','-m','garage_audio.mcp_server']}}};
export function qualifiedSlots(session){
  if(!session?.plan)return {complete:false,count:0,total:0};
  const expected=session.plan.positions.flatMap(p=>session.plan.channels.map(c=>`${p}/${c}`));
  const slots=(session.files||[]).filter(f=>f.format==='rew-response'&&['measured','synthetic'].includes(f.metadata?.source)&&f.metadata?.calibration_applied===true&&/^[a-f0-9]{64}$/.test(f.metadata?.calibration_sha256||'')&&f.metadata?.sample_rate===session.plan.sample_rate&&f.metadata?.orientation===session.plan.orientation&&f.metadata?.timing_reference?.trim()).map(f=>`${f.metadata.position}/${f.metadata.channel}`);
  return {count:expected.filter(k=>slots.filter(s=>s===k).length===1).length,total:expected.length,complete:slots.length===expected.length&&expected.every(k=>slots.filter(s=>s===k).length===1)};
}
export function nextStep({authenticated=false,room=null,profile=null,session=null,proof=null,job=null}={}){
  const slots=qualifiedSlots(session),limitations=['Deterministic guidance · live AI off','Nothing applied · imported evidence is self-reported'];
  let stage='check',title='Choose your room',reason='Prepare a listening goal without hardware.',primaryAction='Choose my room';
  if(!authenticated){stage='connect';title='Unlock your private workspace';reason='Rooms and imports stay on this service.';primaryAction='Unlock dashboard';}
  else if(session?.state==='cancelled'){stage='calibration';title='Create another fitting plan';reason='This plan is cancelled; originals are retained.';primaryAction='Review room plan';}
  else if(session?.derived_results_superseded||session?.recompute_required){stage='improve';title='Reanalyze superseded fitting results';reason='Previous predictions are no longer current. Retained imports are not lost.';primaryAction='Review fitting evidence';}
  else if(session?.analysis?.proposal_eligibility?.eligible===false){stage='improve';title='Review measurement provenance';reason='Proposal blocked by known clock drift. Import qualified replacement evidence; no drift compensation is implemented.';primaryAction='Review fitting evidence';}
  else if(!room){/* room selection above */}
  else if(!room.annotations_confirmed||!profile){title='Save your map and listening goal';reason='Use separate fitting and held-out positions; a photo is optional.';primaryAction='Review room plan';}
  else if(!session?.plan||session.plan.positions.length!==profile.fit_positions.length||!profile.fit_positions.every(p=>session.plan.positions.includes(p.position_id))||session.plan.positions.some(p=>profile.verify_positions.includes(p))){stage='calibration';title='Prepare fitting imports';reason='Create a fitting-only plan from your listening goal. Verification stays separate.';primaryAction='Prepare fitting imports';}
  else if(session.state==='cancelled'){stage='calibration';title='Create another fitting plan';reason='This plan is cancelled; its originals are retained.';primaryAction='Review room plan';}
  else if(!slots.complete){stage='calibration';title='Bring in your measurements';reason=slots.count?`${slots.count}/${slots.total} qualified fitting slots. Import the missing position/channel exports.`:'Your plan is saved; return when you have REW exports.';primaryAction='Import REW exports';}
  else if(session.derived_results_superseded||!session.analysis){stage='improve';title='Analyze fitting imports';reason='The fitting checklist is complete. Held-out data is not used.';primaryAction='Review fitting evidence';}
  else if(!session.proposal){stage='improve';title='Prepare a recommendation';reason='Magnitude-only, conservative cut-only EQ. Leaving unchanged is valid.';primaryAction='Review recommendation';}
  else {stage='verify';title=proof?.stale?'Inputs changed':proof?.report?'Review imported evidence':'Prepare paired verification';reason=proof?.stale?'This result is no longer current. Retain it and freeze a new comparison.':proof?.report?'A report recommendation is not applied correction or independent acoustic proof.':'Save an offline candidate, then freeze comparison rules before importing held-out pairs.';primaryAction='Review verification';}
  if(job?.state==='running')reason+=' A file-only job is running. Cancel remains available; no hardware or sound is enabled.';
  return {stage,title,reason,primaryAction,evidenceSummary:`${room?.name||'No room selected'} · ${profile?.name||'No listening goal'} · ${slots.count}/${slots.total} qualified fit slots`,limitations};
}
export function workflowState(context={}) {
  const {authenticated=false,status=null,capabilities=null,session=null}=context,guide=nextStep(context);
  const measured=(session?.files||[]).filter(f=>f.format==='rew-response');
  return {
    connection:authenticated?'Dashboard session unlocked · agent connection not reported':'Unlock the dashboard to read private state',
    guide,next:`${guide.title}. ${guide.reason}`,
    checks:[['Dashboard',authenticated?'Authenticated session':'Locked'],['Native DSP',status?.dsp?.available===true?'Available · inspect readback':'Not verified / unavailable'],['Microphone & output','Not verified by dashboard login'],['Sound authority','No sound authorized by this workflow'],['Agent','Bring your own MCP agent · no connection telemetry']],
    measurement:measured.length?`${measured.length} imported response(s) · source provenance below`:'No imported response in the selected session',
    operations:Object.keys(capabilities?.operations||{})
  };
}
export function optionalClockDrift(value) {
  if(value===null||value===undefined||String(value).trim()==='')return {};
  const text=String(value).trim();
  if(!/^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$/.test(text))throw new RangeError('Clock drift must be a finite number between −1000 and 1000 ppm, or blank for unknown.');
  const number=Number(text);
  if(!Number.isFinite(number)||number < -1000||number > 1000)throw new RangeError('Clock drift must be a finite number between −1000 and 1000 ppm, or blank for unknown.');
  return {clock_drift_ppm:number};
}
export function canPropose(session) {
  return !!session?.analysis && !session.derived_results_superseded && !session.recompute_required && session.state!=='cancelled' && session.analysis.proposal_eligibility?.eligible===true;
}
export function fitSummary(data) {
  const f=data?.target_fit,format=value=>typeof value==='number'&&Number.isFinite(value)?value.toFixed(3):'unknown';
  const lines=[data?.selection_message||(data?.useful_cut_available===false?'No useful conservative cut available; leave unchanged':'Target-fit outcome not reported')];
  if(!f)return [...lines,'Target-fit validation not reported; electrical validation alone is not target-fit proof.'];
  lines.push(`Predicted aggregate RMS: ${format(f.before?.aggregate_rms_db)} → ${format(f.after?.aggregate_rms_db)} dB; aggregate improved: ${f.aggregate_improved===true?'yes':'no'}.`);
  lines.push(`Nonworsening at every imported position: ${f.nonworsening_each_position===true?'reported yes':'not reported yes'}. Prediction only, not measured improvement.`);
  for(const after of f.after?.positions||[]){const before=f.before?.positions?.find(p=>p.file_id===after.file_id);lines.push(`${after.position}: grid RMS ${format(before?.grid_rms_db)} → ${format(after.grid_rms_db)} dB; native-band RMS ${format(before?.import_rms_db)} → ${format(after.import_rms_db)} dB.`);}
  return lines;
}
export function evidenceLabel(record) {
  if(record?.synthetic===true||record?.metadata?.synthetic===true||record?.metadata?.source==='synthetic'||record?.source==='synthetic'||record?.data_kind==='synthetic'||record?.source_kind==='synthetic')return 'SYNTHETIC · not garage measurements';
  if(record?.interpretation==='electrical-only'||record?.data_kind==='electrical')return 'ELECTRICAL PREDICTION · not acoustic verification';
  return 'IMPORTED DATA · provenance is not independent acoustic verification';
}
