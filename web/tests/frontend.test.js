import {test} from 'node:test';
import assert from 'node:assert/strict';
import {GarageAPI,ApiError,validateFile,readTextFile,MAX_TEXT} from '../api.js';
import {plotGeometry} from '../plot.js';
test('cookie login binds CSRF without storage or credential URL',async()=>{
  const calls=[];
  const api=new GarageAPI(async(path,options)=>{calls.push({path,options});return {ok:true,status:200,json:async()=>({ok:true,result:path==='/api/login'?{csrf:'test-csrf'}:{available:false}})};});
  await api.login('synthetic-token');await api.operation('dsp.mute');
  assert.equal(calls[0].path,'/api/login');assert.equal(calls[0].options.credentials,'same-origin');
  assert.equal(calls[1].options.headers['X-CSRF-Token'],'test-csrf');
  assert.deepEqual(JSON.parse(calls[1].options.body),{operation:'dsp.mute',args:{}});
  assert.ok(!calls[1].options.body.includes('synthetic-token'));
});
test('structured prerequisites survive failed operations',async()=>{
  const api=new GarageAPI(async()=>({ok:false,status:409,json:async()=>({ok:false,error:{code:'attended_authority_required',message:'Locked',requirements:['Separate authority required']}})}));
  await assert.rejects(api.operation('playback.play',{player_id:'test'}),e=>e instanceof ApiError&&e.code==='attended_authority_required'&&e.requirements[0]==='Separate authority required');
});
test('unreadable responses and network loss never become success',async()=>{
  const api=new GarageAPI(async()=>{throw Error('untrusted private exception');});
  await assert.rejects(api.status(),e=>e.code==='connection_unavailable'&&!e.message.includes('untrusted private exception'));
  const invalid=new GarageAPI(async()=>({ok:true,status:200,json:async()=>{throw Error();}}));
  await assert.rejects(invalid.status(),e=>e.code==='invalid_response');
});
test('file limits, path names and UTF-8 encoding are bounded',async()=>{
  assert.throws(()=>validateFile({name:'response.txt',size:MAX_TEXT+1}),ApiError);
  assert.throws(()=>validateFile({name:'../response.txt',size:10}),ApiError);
  validateFile({name:'Left response.txt',size:MAX_TEXT});
  const file=(bytes)=>({name:'response.txt',size:bytes.length,arrayBuffer:async()=>new Uint8Array(bytes).buffer});
  await assert.rejects(readTextFile(file([255,255])),e=>e.code==='file_encoding');
  await assert.rejects(readTextFile(file([48,0,49])),e=>e.code==='file_limit');
  assert.equal(await readTextFile(file([...new TextEncoder().encode('20 75\n100 72')])),'20 75\n100 72');
});
test('plots use only finite supplied measured points and log frequency',()=>{
  assert.equal(plotGeometry([]),null);
  assert.equal(plotGeometry([{points:[{frequency_hz:-1,db:10},{frequency_hz:20,db:NaN}]}]),null);
  const g=plotGeometry([{label:'real',points:[{frequency_hz:20,db:72},{frequency_hz:200,db:75},{frequency_hz:2000,db:74}]}]);
  assert.equal(g.clean[0].points.length,3);assert.ok(Math.abs((g.x(200)-g.x(20))-(g.x(2000)-g.x(200)))<.00001);
  assert.ok(Number.isFinite(g.y(74)));
});
test('UTF-8 BOM is retained exactly for original-byte provenance',async()=>{
  const original=new Uint8Array([0xef,0xbb,0xbf,...new TextEncoder().encode('20 70\n100 75\n')]);
  const file={name:'response.txt',size:original.length,arrayBuffer:async()=>original.buffer};
  const text=await readTextFile(file);
  assert.equal(text,'\ufeff20 70\n100 75\n');
  assert.deepEqual(new TextEncoder().encode(text),original);
});
