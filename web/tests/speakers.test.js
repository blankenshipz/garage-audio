import test from 'node:test';
import assert from 'node:assert/strict';
import {audioSummary,audioReadiness,SpeakersPanel} from '../speakers.js';
test('FILE/off readback never claims physical sound or silence',()=>{
 assert.match(audioSummary({device_mode:'file',state:'off',device_open:false}),/FILE worker · no physical sound claim/);
 assert.match(audioSummary({device_mode:'file',state:'off',device_open:false}),/Physical silence is not inferred/);
 assert.match(audioSummary(null),/Stop and physical silence are not verified/);
});

test('readiness stages keep visibility, auth, software playback and audibility separate',()=>{
 for(const [readiness,expected] of Object.entries({standby:/listening is unavailable/,network_unavailable:/network admission unavailable/,observation_unavailable:/Fresh receiver observation unavailable/,receiver_unavailable:/authentication or active-device/,awaiting_source:/waiting for source corroboration/,waiting_for_play:/waiting for a fresh Spotify Play/,playing:/audibility are not verified/,paused:/output paused/})){
  const text=audioReadiness({readiness});assert.match(text,expected);assert.match(text,/Phone discovery visibility is not verified/);
 }
 assert.match(audioReadiness(null),/readiness has not been verified/);
 assert.match(audioReadiness({device_mode:'standby',readiness:'playing'}),/listening is unavailable/);
 assert.match(audioSummary({device_mode:'standby',state:'off',device_open:false}),/Discovery-only standby/);
});
test('Docker panel gives first-play volume guidance and distinguishes standby',()=>{
 const nodes=new Map();const panel=Object.create(SpeakersPanel.prototype);
 panel.el=id=>{if(!nodes.has(id))nodes.set(id,{});return nodes.get(id);};
 panel.mode='docker';panel.authenticated=true;
 panel.state={device_mode:'hardware',state:'off',readiness:'waiting_for_play'};panel.render();
 assert.match(nodes.get('music-player-status').textContent,/Fosi is the main volume control/);
 assert.match(nodes.get('music-player-status').textContent,/Turn Fosi fully down/);
 assert.match(nodes.get('music-source-status').textContent,/waiting for a fresh Spotify Play/);
 panel.state={device_mode:'standby',state:'off',readiness:'standby'};panel.render();
 assert.match(nodes.get('music-player-status').textContent,/no audio authority/);
 assert.equal(nodes.get('speakers-stop').disabled,false);
 panel.state=null;panel.render();assert.match(nodes.get('music-source-status').textContent,/not been verified/);
});
