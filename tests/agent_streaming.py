"""Synthetic streamed Models, actual SDK/tool boundary; no live inference or network."""
import asyncio
import json
import socket
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from agents.models.interface import Model
from openai.types.responses import (Response, ResponseCompletedEvent, ResponseTextDeltaEvent,
    ResponseOutputMessage, ResponseOutputText, ResponseFunctionToolCall)
from garage_audio.application import Application
from garage_audio.agent_runtime import stream_loop, run_configured, TOOL_OPERATIONS
from garage_audio.agent_harness import AgentFailure


class StreamingModel(Model):
    def __init__(self,tool=False,delay=0): self.tool,self.delay,self.calls = tool,delay,0
    async def get_response(self,*args,**kwargs): raise AssertionError('Must stream')
    async def stream_response(self,*args,**kwargs):
        self.calls += 1
        await asyncio.sleep(self.delay)
        if self.tool and self.calls == 1:
            output = [ResponseFunctionToolCall(id='t1',call_id='c1',type='function_call',
                name='garage_operation',arguments=json.dumps({'operation':'rooms.list','arguments_json':'{}'}))]
        else:
            yield ResponseTextDeltaEvent(type='response.output_text.delta',delta='Synthetic fixture',
                content_index=0,output_index=0,item_id='m1',sequence_number=1,logprobs=[])
            output = [ResponseOutputMessage(id='m1',role='assistant',status='completed',type='message',
                content=[ResponseOutputText(type='output_text',text='Synthetic fixture',annotations=[])])]
        response = Response.model_construct(id='r1',created_at=0,model='scripted',object='response',
            output=output,parallel_tool_calls=False,tools=[],tool_choice='auto',usage=None)
        yield ResponseCompletedEvent(type='response.completed',sequence_number=2,response=response)


class StreamingTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir='/private/tmp/opencode')
        self.app = Application(self.temp.name,broker=lambda _: {'mute':True})
        self.network = patch.object(socket.socket,'connect',side_effect=AssertionError('No network'))
        self.network.start()
    def tearDown(self):
        self.network.stop(); self.app.agent_harness.close(); self.temp.cleanup()

    async def test_configured_entry_denies_before_credentials_and_runner(self):
        config = self.app.execute('agent.config.set',{'provider':'openai','model':'gpt-4.1-mini',
            'credential_ref':'application-agent','max_turns':2,'timeout_seconds':2})
        self.assertTrue(config['ok'],config)
        self.assertFalse(config['result']['inference_enabled'])
        with patch('garage_audio.agent_runtime.Runner.run_streamed',side_effect=AssertionError('No runner')):
            with self.assertRaises(AgentFailure):
                await run_configured(self.app,config['result'],lambda _: self.fail('No credential read'),'test')
        self.assertFalse(self.app.execute('agent.config.set',{'enabled':True})['ok'])

    async def test_actual_stream_and_tool_shared_application(self):
        room = self.app.execute('rooms.create',{'name':'retained real local record'})['result']
        model = StreamingModel(tool=True)
        events = []
        async def event(item): events.append(item)
        result = await stream_loop(self.app,model,'Read rooms',on_event=event)
        self.assertEqual(result['state'],'completed')
        self.assertEqual(model.calls,2)
        self.assertTrue(any(e['type']=='text_delta' for e in events))
        self.assertTrue(any(e['type']=='tool_progress' for e in events))
        self.assertEqual(self.app.execute('rooms.get',{'id':room['id']})['result']['version'],1)
        self.assertNotIn('playback.play',TOOL_OPERATIONS)
        self.assertNotIn('dsp.volume',TOOL_OPERATIONS)
        self.assertNotIn('rooms.photos.get',TOOL_OPERATIONS)

    async def test_timeout_and_cancel(self):
        with self.assertRaises(asyncio.TimeoutError):
            await stream_loop(self.app,StreamingModel(delay=1),'test',timeout_seconds=.01)
        cancel = asyncio.Event()
        task = asyncio.create_task(stream_loop(self.app,StreamingModel(delay=1),'test',cancel_event=cancel))
        await asyncio.sleep(.01); cancel.set()
        with self.assertRaises(asyncio.CancelledError): await task

    async def test_persisted_disabled_runs_list_reload_and_setup(self):
        session = self.app.execute('agent.session.create',{})['result']
        run = self.app.execute('agent.run',{'id':session['id'],'expected_version':1,'message':'hello'})['result']
        self.assertEqual(run['items'],[{'role':'user','content':'hello'}])
        self.assertFalse(run['runs'][0]['provider_called'])
        read = self.app.execute('agent.session.list',{})['result'][0]
        self.assertEqual(read,run)
        self.assertTrue(self.app.execute('agent.cancel',{'id':run['id'],'expected_version':2})['ok'])
        with patch.dict('os.environ',{'MA_TOKEN_FILE':'/not-a-secret-reference'},clear=True):
            setup = self.app.execute('setup.status',{})['result']
        self.assertEqual(setup['credentials']['music_assistant']['code'],'credential_invalid')
        self.assertNotIn('/not-a-secret-reference',json.dumps(setup))
        self.assertFalse(setup['inference_enabled'])
        self.assertTrue(self.app.execute('setup.config.set',{'display_name':'Workshop'})['ok'])
        self.assertEqual(self.app.execute('setup.config.get',{})['result']['display_name'],'Workshop')


if __name__ == '__main__': unittest.main(verbosity=2)
