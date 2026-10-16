"""Synthetic SDK adapter tests only; no provider clients or model calls."""
import asyncio
import json
import unittest
import tempfile
from unittest.mock import patch
from garage_audio.agent_runtime import configuration_tools, require_inference_authorization
from garage_audio.agent_harness import AgentFailure
from garage_audio.audio_config import OPERATION_SCHEMAS


class ToolsTests(unittest.TestCase):
    def test_exact_schemas_and_shared_dispatch(self):
        class Application:
            def execute(self, operation, args): return {'operation':operation,'args':args}
        tools = configuration_tools(Application())
        self.assertEqual(len(tools),5)
        for tool in tools:
            operation=tool.name.replace('_','.',2)  # audio_config_get -> audio.config.get
            self.assertEqual(tool.params_json_schema,OPERATION_SCHEMAS[operation])
            args={} if operation.endswith('get') else {'proposal_id':'a'*64}
            if operation.endswith('propose'): args={'configuration':{}}
            result=json.loads(asyncio.run(tool.on_invoke_tool(None,json.dumps(args))))
            self.assertEqual(result,{'operation':operation,'args':args})

    def test_inference_stays_off(self):
        with self.assertRaises(AgentFailure): require_inference_authorization()

    def test_run_enters_guarded_runner_outside_application_lock(self):
        from garage_audio.application import Application
        with tempfile.TemporaryDirectory(dir='/private/tmp/opencode') as root:
            application=Application(root)
            session=application.execute('agent.session.create',{})['result']
            async def guarded(app,*args,**kwargs):
                self.assertFalse(app.lock._is_owned())
                raise AgentFailure('inference_disabled','Provider not authorized')
            with patch('garage_audio.agent_runtime.run_configured',side_effect=guarded) as runner:
                result=application.execute('agent.run',{'id':session['id'],'expected_version':session['version'],'message':'Inspect configuration'})
            self.assertTrue(result['ok'],result)
            self.assertEqual(result['result']['runner'],{'state':'inference_disabled','provider_called':False})
            runner.assert_called_once()


if __name__=='__main__': unittest.main()
