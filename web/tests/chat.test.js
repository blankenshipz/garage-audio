import test from 'node:test';
import assert from 'node:assert/strict';
import {conversationArgs,conversationItems,CONVERSATION_KEY} from '../chat.js';
test('saved message uses current server version, not invented authority',()=>{
  assert.deepEqual(conversationArgs({id:'abc',version:7},'  My room  '),{id:'abc',expected_version:7,message:'My room'});
});
test('conversation renders only actual server messages, not status as an assistant reply',()=>{
  assert.deepEqual(conversationItems({items:[{role:'user',content:'Hello'},{role:'system',content:'Disabled'},{role:'assistant',content:'Retained actual response'},{role:'assistant',content:{}}],last_result:{state:'inference_disabled'}}),[{role:'user',content:'Hello'},{role:'assistant',content:'Retained actual response'}]);
  assert.deepEqual(conversationItems(null),[]);
  assert.equal(CONVERSATION_KEY,'garage-chat-session');
});
