import test from 'node:test';
import assert from 'node:assert/strict';
import {agentConfigArgs} from '../setup.js';
test('agent configuration passes only bounded configuration and the server-owned credential reference',()=>{
 const form=new Map([['model','  gpt-4.1-mini  '],['max_turns','4'],['timeout_seconds','20'],['token','not transmitted'],['inference_enabled','true']]);
 assert.deepEqual(agentConfigArgs(form),{provider:'openai',model:'gpt-4.1-mini',credential_ref:'application-agent',max_turns:4,timeout_seconds:20});
});
