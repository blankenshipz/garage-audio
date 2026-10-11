import {test} from 'node:test';
import assert from 'node:assert/strict';
import {prepareChannel,preparePEQ,profileEditArgs} from '../profile.js';
import {readFileSync} from 'node:fs';
// Isolated synthetic profile fixture; never used as dashboard runtime data.
const source=()=>({name:'fixture',channels:Object.fromEntries(['left','right','sub'].map(c=>[c,{gain:-2,mute:false,delay_ms:0,invert:false,peq:[{freq:80,gain:-3,q:1}]}])),crossover_hz:80,sub_trim:-3,peq_bypass:false});
const fields={channel:'left',gain:'-4.5',delay_ms:'1.25',invert:true,mute:true,crossover_hz:'90',sub_trim:'-6',peq_bypass:true};
test('channel preparation preserves strict profile shape and untouched channels immutably',()=>{
  const p=source(),before=structuredClone(p),next=prepareChannel(p,fields);
  assert.deepEqual(p,before);assert.deepEqual(next.channels.right,p.channels.right);assert.deepEqual(next.channels.sub,p.channels.sub);
  assert.deepEqual(next.channels.left,{gain:-4.5,delay_ms:1.25,invert:true,mute:true,peq:p.channels.left.peq});
  assert.equal(next.crossover_hz,90);assert.equal(next.sub_trim,-6);assert.equal(next.peq_bypass,true);
  assert.deepEqual(Object.keys(next).sort(),Object.keys(p).sort());
});
test('channel bounds, booleans and cut-only gains fail before API submission',()=>{
  for(const patch of [{channel:'arbitrary'},{gain:'1'},{delay_ms:'51'},{gain:''},{gain:' '},{mute:'true'},{invert:1},{peq_bypass:'false'},{crossover_hz:'39'},{sub_trim:'1'}])assert.throws(()=>prepareChannel(source(),{...fields,...patch}));
});
test('PEQ appends, replaces and removes exact selected slot without altering source',()=>{
  const p=source(),base={channel:'sub',index:'',freq:'100',gain:'-5',q:'2'};
  const appended=preparePEQ(p,base);assert.equal(appended.channels.sub.peq.length,2);assert.equal(p.channels.sub.peq.length,1);
  const replaced=preparePEQ(p,{...base,index:'0'});assert.deepEqual(replaced.channels.sub.peq,[{freq:100,gain:-5,q:2}]);
  const removed=preparePEQ(p,{channel:'sub',index:'0'},true);assert.deepEqual(removed.channels.sub.peq,[]);
  assert.deepEqual(p.channels.sub.peq,[{freq:80,gain:-3,q:1}]);
});
test('PEQ rejects boost, bad frequency/Q/index and an eleventh band',()=>{
  const fields={channel:'left',index:'',freq:'100',gain:'-4',q:'1'};
  for(const patch of [{gain:'1'},{gain:'-21'},{freq:'19'},{q:'0'},{index:'2'},{index:null},{index:'-1'}])assert.throws(()=>preparePEQ(source(),{...fields,...patch}));
  assert.throws(()=>preparePEQ(source(),fields,true));
  const full=source();full.channels.left.peq=Array.from({length:10},()=>({freq:100,gain:-3,q:1}));assert.throws(()=>preparePEQ(full,fields));
});
test('typed profile edit matches published narrow args, preserving source ID without raw profile transport',()=>{
  const record={id:'source-id',profile:source()};
  const args=profileEditArgs(record,fields);
  assert.deepEqual(args,{id:'source-id',channel:'left',gain:-4.5,delay_ms:1.25,invert:true,mute:true,crossover_hz:90,sub_trim:-6,peq_bypass:true});
  const peq=profileEditArgs(record,{channel:'right',index:'0',freq:'120',gain:'-6',q:'2'},true);
  assert.deepEqual(peq,{id:'source-id',channel:'right',peq:[{freq:120,gain:-6,q:2}]});
  assert.deepEqual(profileEditArgs(record,{channel:'sub',index:'0'},true,true),{id:'source-id',channel:'sub',peq:[]});
  assert.throws(()=>profileEditArgs({profile:source()},fields));
  assert.equal(record.profile.channels.left.gain,-2);
});
test('Q 1.41 and arbitrary API precision survive gain-only and selected-band edits unrounded',()=>{
  const p=source();p.channels.left.peq=[{freq:123.456,gain:-3.14159,q:1.41}];
  const prepared=prepareChannel(p,{...fields,gain:'-4.123456',delay_ms:'1.234567',crossover_hz:'80.123456',sub_trim:'-6.123456'});
  assert.deepEqual(prepared.channels.left.peq,p.channels.left.peq);
  assert.equal(prepared.channels.left.gain,-4.123456);assert.equal(prepared.channels.left.delay_ms,1.234567);
  assert.equal(prepared.crossover_hz,80.123456);assert.equal(prepared.sub_trim,-6.123456);
  const args=profileEditArgs({id:'source',profile:p},{channel:'left',index:'0',freq:'123.456',gain:'-4.123456',q:'1.41'},true);
  assert.deepEqual(args.peq,[{freq:123.456,gain:-4.123456,q:1.41}]);
  const html=readFileSync(new URL('../index.html',import.meta.url),'utf8');
  for(const tag of html.match(/<input name="(?:gain|delay_ms|crossover_hz|sub_trim|freq|q)"[^>]+>/g))assert.match(tag,/step="any"/);
});
