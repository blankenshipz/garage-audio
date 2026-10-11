import {nextStep} from './workflow.js';
export const STAGES=['connect','check','calibration','improve','verify'];
export function stageForTarget(target){return STAGES.includes(target)?target:['jobs','measurement-workspace','measurement-import'].includes(target)?'calibration':['diagnostics','profiles','overview','room-workspace'].includes(target)?'check':null;}
export function stageGuidance(stage,context={}){const guide=nextStep(context);return `${guide.title}. ${guide.reason}`;}
export class Journey {
  constructor({document=globalThis.document,window=globalThis.window,onCancel=()=>{}}={}){
    this.doc=document;this.win=window;this.onCancel=onCancel;this.current='connect';this.context={};this.groups=new Map();
    const main=document.querySelector('main'),ids={connect:['connect'],check:['check','advanced-controls'],calibration:['measurement-workspace','jobs'],improve:['improve'],verify:['verify']};
    for(const stage of STAGES){const group=document.createElement('div');group.id=`stage-${stage}`;group.className='journey-stage';group.dataset.stage=stage;group.setAttribute('role','region');group.setAttribute('aria-label',stage==='calibration'?'Measure':stage[0].toUpperCase()+stage.slice(1));main.append(group);for(const id of ids[stage]){const node=document.getElementById(id);if(node)group.append(node);}this.groups.set(stage,group);}
    const footer=main.querySelector('footer');if(footer)main.append(footer);
    const bar=document.createElement('section');bar.className='journey-summary';bar.id='journey-summary';bar.setAttribute('aria-label','Current step and safety');
    const make=(tag,id,text)=>{const el=document.createElement(tag);if(id)el.id=id;if(text)el.textContent=text;return el;};
    const safety=make('div','journey-safety');safety.className='journey-safety';safety.append(make('strong',null,'Quiet preparation · No audio'),make('span','journey-human','Deterministic guidance · Live AI off'),make('span','journey-file','Hardware is needed later, not to prepare'));
    const head=make('div','journey-summary-heading');head.className='journey-summary-heading';head.append(make('strong','journey-current','01 / Connect'),make('a',null,'Room & listening goal'));head.lastChild.href='#check';
    this.next=make('p','journey-next');this.action=make('button','journey-action','Continue to Check');this.action.className='primary';this.action.onclick=()=>this.primaryAction();
    this.cancel=make('button','journey-cancel-job','Cancel active job');this.cancel.className='danger-button';this.cancel.hidden=true;this.cancel.onclick=()=>this.onCancel(this.context.job);
    const actions=make('div','journey-actions');actions.className='journey-actions';actions.append(this.action,this.cancel);bar.append(safety,head,this.next,actions);document.getElementById('login-panel').after(bar);
    this.links=[...document.querySelectorAll('.sidebar nav a')];for(const link of this.links){const stage=stageForTarget(link.hash.slice(1));link.setAttribute('aria-controls',`stage-${stage}`);link.onkeydown=e=>{const i=STAGES.indexOf(stage);let target;if(e.key==='ArrowRight'||e.key==='ArrowDown')target=STAGES[(i+1)%5];if(e.key==='ArrowLeft'||e.key==='ArrowUp')target=STAGES[(i+4)%5];if(e.key==='Home')target='connect';if(e.key==='End')target='verify';if(target){e.preventDefault();this.activate(target,{focus:'nav'});}};}
    document.addEventListener('click',e=>{const link=e.target.closest('a[href^="#"]');if(!link)return;const target=link.getAttribute('href').slice(1),stage=stageForTarget(target);if(!stage)return;e.preventDefault();this.activate(stage,{target,reveal:!link.closest('.sidebar'),focus:'heading'});});
    window.addEventListener('hashchange',()=>{const target=window.location.hash.slice(1),stage=stageForTarget(target);if(stage)this.activate(stage,{target,focus:'heading'});});
    this.activate(stageForTarget(window.location.hash.slice(1))||'connect');
  }
  activate(stage,{target,focus,reveal}={}){
    if(!STAGES.includes(stage))return;this.current=stage;
    const oldToast=this.doc.getElementById('action-status');if(oldToast)oldToast.hidden=true;
    for(const [name,group] of this.groups)group.hidden=name!==stage;
    for(const link of this.links){const selected=stageForTarget(link.hash.slice(1))===stage;link.classList.toggle('active',selected);if(selected)link.setAttribute('aria-current','step');else link.removeAttribute('aria-current');}
    if(target&&(target!==stage||reveal)){let ancestor=this.doc.getElementById(target);while(ancestor){if(ancestor.tagName==='DETAILS')ancestor.open=true;ancestor=ancestor.parentElement;}}
    this.win.history.replaceState(null,'',`#${target||stage}`);this.update(this.context);this.win.scrollTo({top:0,behavior:'instant'});
    if(focus==='nav')this.links.find(a=>stageForTarget(a.hash.slice(1))===stage)?.focus({preventScroll:true});
    if(focus==='heading'){const heading=[...this.groups.get(stage).querySelectorAll('h1,h2,h3')].find(h=>h.getClientRects().length);if(heading){heading.tabIndex=-1;heading.focus({preventScroll:true});}}
  }
  update(context){
    this.context=context;const labels=['Connect','Check','Measure','Improve','Verify'],i=STAGES.indexOf(this.current);this.doc.getElementById('journey-current').textContent=`0${i+1} / ${labels[i]}`;this.next.textContent=stageGuidance(this.current,context);
    const guide=nextStep(context);this.doc.getElementById('journey-file').textContent=guide.evidenceSummary;
    const job=context.job;this.cancel.hidden=!context.authenticated||!job||['completed','failed','cancelled','revoked','expired','invalidated','interrupted'].includes(job.state);this.cancel.disabled=!!context.cancelPending;
    this.action.textContent=guide.primaryAction;
    this.action.disabled=!context.authenticated;
  }
  primaryAction(){
    const guide=nextStep(this.context);this.activate(guide.stage,{focus:'heading'});
    if(guide.stage==='calibration'){if(!this.context.session?.plan)this.activate('check',{target:'room-workspace',focus:'heading'});else{const d=this.doc.getElementById('measurement-import');if(d)d.open=true;}}
  }
}
