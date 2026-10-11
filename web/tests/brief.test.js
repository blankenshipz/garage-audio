import {test} from 'node:test';
import assert from 'node:assert/strict';
import {AGENT_TASK_BRIEF} from '../brief.js';
test('copyable agent brief grants no human or sound authority',()=>{
  assert.match(AGENT_TASK_BRIEF,/grants no sound authority/);assert.match(AGENT_TASK_BRIEF,/approve as automation/);assert.match(AGENT_TASK_BRIEF,/Unknown stays unknown/);assert.match(AGENT_TASK_BRIEF,/paid API inference/);
});
test('brief separates synthetic/electrical evidence from garage proof',()=>{
  assert.match(AGENT_TASK_BRIEF,/synthetic file\/null/);assert.match(AGENT_TASK_BRIEF,/electrical predictions/);assert.match(AGENT_TASK_BRIEF,/no-useful-cut/);assert.ok(!AGENT_TASK_BRIEF.includes('192.168'));assert.ok(!AGENT_TASK_BRIEF.includes('dashboard-token'));
});
test('FILE brief requires actual output/isolation evidence and forbids legacy render fallback',()=>{
  assert.match(AGENT_TASK_BRIEF,/Never substitute a legacy\/fake FILE fallback/);assert.match(AGENT_TASK_BRIEF,/Availability or a manifest is not execution or DSP isolation proof/);assert.match(AGENT_TASK_BRIEF,/actual child-exit/);assert.match(AGENT_TASK_BRIEF,/still uses synthetic excitation\/capture/);
});
