import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {normalizedPoint,nextPositionLabel,validatePlan,photoBlob,photoDimensions,preparePhoto,MAX_PHOTO_BASE64} from '../room.js';
test('coordinates use displayed photo rectangle, not page or original dimensions',()=>{
  assert.deepEqual(normalizedPoint(205,130,{left:10,top:30,width:390,height:200}),{x:.5,y:.5});
  assert.deepEqual(normalizedPoint(-10,500,{left:10,top:30,width:390,height:200}),{x:0,y:1});
  assert.throws(()=>normalizedPoint(0,0,{width:0,height:0}));
});
test('numbered position labels fill gaps without changing existing stable IDs',()=>{
  const annotations=[{id:'a',label:'P1'},{id:'b',label:'P3'},{id:'c',label:'V1'}];
  assert.equal(nextPositionLabel(annotations,'fit'),'P2');assert.equal(nextPositionLabel(annotations,'verify'),'V2');assert.equal(annotations[0].id,'a');
});
test('seat / whole-room plans have explicit separate coverage and positive weights',()=>{
  const fit=[{position_id:'p1',weight:1},{position_id:'p2',weight:2}];
  assert.deepEqual(validatePlan('whole-room',fit,['v1']),{fit_positions:fit,verify_positions:['v1']});
  assert.throws(()=>validatePlan('whole-room',fit.slice(0,1),['v1']),/two/);
  assert.throws(()=>validatePlan('seat',fit,['v1']),/exactly one/);
  assert.throws(()=>validatePlan('custom',fit,['p1']),/separate/);
  assert.throws(()=>validatePlan('area',[{position_id:'p1',weight:0}],['v1']),/weight/);
  assert.throws(()=>validatePlan('seat',fit.slice(0,1),[]),/verification/);
});
test('authenticated photo data creates a bounded safe blob, never arbitrary URLs',async()=>{
  const blob=photoBlob({mime_type:'image/jpeg',data_base64:btoa('private pixels')});assert.equal(blob.type,'image/jpeg');assert.equal(await blob.text(),'private pixels');
  assert.throws(()=>photoBlob({mime_type:'image/svg+xml',data_base64:btoa('<script/>')}));
  assert.throws(()=>photoBlob({mime_type:'image/jpeg',data_base64:'https://external.invalid/image'}));
  assert.throws(()=>photoBlob({mime_type:'image/jpeg',data_base64:'A'.repeat(MAX_PHOTO_BASE64+1)}));
});
function png(width,height){const bytes=new Uint8Array(24);bytes.set([137,80,78,71,13,10,26,10]);bytes.set([73,72,68,82],12);new DataView(bytes.buffer).setUint32(16,width);new DataView(bytes.buffer).setUint32(20,height);return bytes;}
test('dimension metadata preflight rejects decompression bombs before decode',async()=>{
  assert.deepEqual(photoDimensions(png(4032,3024)),{width:4032,height:3024});
  const bomb=png(30000,30000);await assert.rejects(preparePhoto({type:'image/png',size:bomb.length,arrayBuffer:async()=>bomb.buffer}),/24 megapixel/);
  await assert.rejects(preparePhoto({type:'image/heic',size:20}),/export it as JPEG/);
  assert.deepEqual(photoDimensions(new Uint8Array([255,216,255,192,0,7,8,1,44,2,88])),{width:600,height:300});
  assert.throws(()=>photoDimensions(new Uint8Array([1,2,3])),/Cannot read/);
});
test('phone JPEG is EXIF-oriented and compressed below backend bound locally',async()=>{
  const oldBitmap=globalThis.createImageBitmap,oldDoc=globalThis.document;let closed=false,orientation,drawSize;
  globalThis.createImageBitmap=async(_file,options)=>{orientation=options.imageOrientation;return {width:4032,height:3024,close(){closed=true;}};};
  globalThis.document={createElement:()=>({getContext:()=>({fillRect(){},drawImage(_im,_x,_y,w,h){drawSize=[w,h];}}),toBlob(callback,_type,quality){callback(new Blob([new Uint8Array(quality>.7?600000:400000)]));}})};
  try{const bytes=png(4032,3024);const encoded=await preparePhoto({type:'image/jpeg',size:2000000,arrayBuffer:async()=>bytes.buffer});assert.ok(encoded.length<=MAX_PHOTO_BASE64);assert.equal(orientation,'from-image');assert.deepEqual(drawSize,[1600,1200]);assert.ok(closed);}finally{globalThis.createImageBitmap=oldBitmap;globalThis.document=oldDoc;}
});
test('room source has no fake chat, raw HTML, private photo/token storage, remote route or apply',async()=>{
  const source=await readFile(new URL('../room.js',import.meta.url),'utf8');
  assert.ok(!/innerHTML|outerHTML|insertAdjacentHTML|localStorage|https?:\/\//.test(source));
  assert.ok(source.includes("sessionStorage.setItem('garage-room',room.id)"));assert.ok(source.includes("sessionStorage.removeItem('garage-room')"));assert.ok(!/sessionStorage.setItem\([^\n]*(?:data_base64|token|photo)/.test(source));
  assert.ok(source.includes("'rooms.photo_get'"));assert.ok(source.includes('expected_version:room.version'));assert.ok(source.includes('Deterministic guidance · live AI off'));assert.ok(!source.includes("'profiles.apply'"));
});
