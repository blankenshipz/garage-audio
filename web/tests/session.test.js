import {test} from 'node:test';
import assert from 'node:assert/strict';
import {SessionSelection} from '../session.js';
const deferred=()=>{let resolve,reject;const promise=new Promise((yes,no)=>{resolve=yes;reject=no;});return {promise,resolve,reject};};
test('failed B selection clears A before loading and cannot silently import into A',async()=>{
  const b=deferred(),states=[];
  const selection=new SessionSelection(id=>id==='A'?Promise.resolve({id,metadata:{},files:[]}):b.promise,state=>states.push(state));
  await selection.select('A');assert.equal(selection.forImport('A').id,'A');
  const loading=selection.select('B');assert.equal(selection.session,null);assert.equal(selection.pending,true);
  assert.throws(()=>selection.forImport('B'));assert.throws(()=>selection.forImport('A'));
  b.reject(Error('readback failed'));await assert.rejects(loading,/readback failed/);
  assert.equal(selection.id,'B');assert.equal(selection.session,null);assert.equal(selection.pending,false);
  assert.throws(()=>selection.forImport('B'));assert.throws(()=>selection.forImport('A'));
  assert.equal(states.at(-1).session,null);
});
test('fast A/B selection ignores stale A response after newer B success',async()=>{
  const a=deferred(),b=deferred(),selection=new SessionSelection(id=>id==='A'?a.promise:b.promise);
  const first=selection.select('A'),second=selection.select('B');b.resolve({id:'B'});assert.equal(await second,true);
  a.resolve({id:'A'});assert.equal(await first,false);assert.equal(selection.forImport('B').id,'B');
  assert.throws(()=>selection.forImport('A'));
});
test('late errors and lock invalidation cannot overwrite latest selection',async()=>{
  const a=deferred(),selection=new SessionSelection(id=>id==='A'?a.promise:Promise.resolve({id}));
  const first=selection.select('A');await selection.select('B');a.reject(Error('stale failure'));assert.equal(await first,false);assert.equal(selection.session.id,'B');
  const c=deferred();selection.fetchSession=()=>c.promise;const loading=selection.select('C');selection.clear();c.resolve({id:'C'});assert.equal(await loading,false);assert.equal(selection.session,null);assert.throws(()=>selection.forImport('C'));
});
test('mismatched server ID never becomes importable',async()=>{
  const selection=new SessionSelection(()=>Promise.resolve({id:'wrong'}));
  await assert.rejects(selection.select('requested'),e=>e.code==='session_mismatch');assert.throws(()=>selection.forImport('requested'));
});
