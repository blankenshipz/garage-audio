import test from 'node:test';
import assert from 'node:assert/strict';
import {audioSummary} from '../speakers.js';
test('FILE/off readback never claims physical sound or silence',()=>{
 assert.match(audioSummary({device_mode:'file',state:'off',device_open:false}),/FILE worker · no physical sound claim/);
 assert.match(audioSummary({device_mode:'file',state:'off',device_open:false}),/Physical silence is not inferred/);
 assert.match(audioSummary(null),/Stop and physical silence are not verified/);
});
