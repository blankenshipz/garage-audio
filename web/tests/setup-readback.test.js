// Isolated API-boundary regression; not live MA/browser evidence.
import test from 'node:test';
import assert from 'node:assert/strict';
import {SetupPanel,spotifyReadinessText} from '../setup.js';
import {SpotifySetupPanel} from '../spotify-setup.js';
function fixture(){
 let flow={id:'old',type:'interrupted',step:'interrupted'},restart=true,code='setup_restart_required',offline=false;
 const calls=[],rows=[];
 const api={async operation(op){calls.push(op);if(op==='setup.status')return {spotify_readiness:{code:offline?'ma_setup_unavailable':code}};if(offline)throw Object.assign(new Error('MA unavailable'),{code:'ma_setup_unavailable'});if(op==='setup.spotify.begin'){flow={id:'fresh',type:'form',step:'backend'};restart=false;code='ma_provider_setup_required';return flow;}if(op==='setup.spotify.flow.abort'){flow={...flow,type:'abort',step:'abort'};return flow;}if(op==='setup.spotify.status')return {configured:false,setup_restart_required:restart,active_flow:flow.type==='form'?flow:null,last_flow:flow.type==='form'?null:flow};if(op==='setup.spotify.flow.get')return flow;throw new Error('Unexpected operation '+op);}};
 const parent=Object.assign(Object.create(SetupPanel.prototype),{api,authenticated:true,renderReadiness:state=>rows.push(spotifyReadinessText(state)),onStatus:()=>{},feedback:()=>{}});
 const child=Object.assign(Object.create(SpotifySetupPanel.prototype),{api,authenticated:true,pending:false,status:{setup_restart_required:true},flow,render:()=>{},remember:()=>{},storedID:()=>null,onReadback:()=>parent.refreshReadiness()});
 return {child,calls,rows,setOffline:value=>offline=value};
}
test('interrupted → Start clears secondary restart notice only after authoritative setup.status readback',async()=>{
 const {child,calls,rows}=fixture();await child.mutate('setup.spotify.begin');
 assert.equal(child.status.setup_restart_required,false);assert.equal(child.flow.id,'fresh');
 assert.deepEqual(calls,['setup.spotify.begin','setup.spotify.status','setup.status']);
 assert.deepEqual(rows,['ma provider setup required · runtime playback not qualified']);
 await child.mutate('setup.spotify.flow.abort',{id:'fresh'});assert.equal(child.flow.type,'abort');assert.equal(calls.at(-1),'setup.status');assert.equal(rows.at(-1),'ma provider setup required · runtime playback not qualified');
});
test('refresh during offline failure preserves cached active flow while secondary code uses server failure readback',async()=>{
 const f=fixture();await f.child.mutate('setup.spotify.begin');const cached=f.child.flow;f.setOffline(true);await f.child.refresh();
 assert.equal(f.child.flow,cached);assert.equal(f.child.flow.type,'form');assert.equal(f.child.error.code,'ma_setup_unavailable');
 assert.equal(f.calls.at(-1),'setup.status');assert.equal(f.rows.at(-1),'ma setup unavailable · runtime playback not qualified');
});
