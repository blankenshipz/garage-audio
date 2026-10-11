import {ApiError} from './api.js';
const fail=message=>{throw new ApiError({code:'invalid_profile_preparation',message});};
function number(value,min,max,label){const n=typeof value==='number'?value:Number(value);if((typeof value!=='number'&&typeof value!=='string')||(typeof value==='string'&&!value.trim())||!Number.isFinite(n)||n<min||n>max)fail(`${label} must be within ${min}–${max}.`);return n;}
function clone(profile,channel){if(!['left','right','sub'].includes(channel)||!profile?.channels?.[channel])fail('Select a saved profile and a valid channel.');return structuredClone(profile);}
export function prepareChannel(profile, fields){
  const p=clone(profile,fields.channel),c=p.channels[fields.channel];
  for(const key of ['mute','invert','peq_bypass'])if(typeof fields[key]!=='boolean')fail(`${key} must be a boolean.`);
  c.gain=number(fields.gain,-60,0,'Channel gain');c.delay_ms=number(fields.delay_ms,0,50,'Delay');c.invert=fields.invert;c.mute=fields.mute;
  p.crossover_hz=number(fields.crossover_hz,40,160,'Crossover');p.sub_trim=number(fields.sub_trim,-30,0,'Subwoofer trim');p.peq_bypass=fields.peq_bypass;
  return p;
}
export function preparePEQ(profile,fields,remove=false){
  const p=clone(profile,fields.channel),bands=p.channels[fields.channel].peq;
  if(typeof fields.index!=='string'||!/^\d*$/.test(fields.index))fail('Select a valid filter slot.');
  const append=fields.index==='';const index=append?bands.length:Number(fields.index);
  if(!Number.isInteger(index)||index<0||index>=bands.length+(append?1:0))fail('Select an existing filter or append a new filter.');
  if(remove){if(append)fail('Select a filter to remove.');bands.splice(index,1);return p;}
  if(append&&bands.length>=10)fail('A channel supports at most ten PEQ filters.');
  bands[index]={freq:number(fields.freq,20,20000,'Frequency'),gain:number(fields.gain,-20,0,'PEQ gain'),q:number(fields.q,0.1,20,'Q')};
  return p;
}
export function profileEditArgs(record,fields,peq=false,remove=false){
  if(!record?.id)fail('Select a saved source profile.');
  const p=peq?preparePEQ(record.profile,fields,remove):prepareChannel(record.profile,fields),c=p.channels[fields.channel];
  return peq?{id:record.id,channel:fields.channel,peq:c.peq}:{id:record.id,channel:fields.channel,gain:c.gain,delay_ms:c.delay_ms,invert:c.invert,mute:c.mute,crossover_hz:p.crossover_hz,sub_trim:p.sub_trim,peq_bypass:p.peq_bypass};
}
