"""Actual stdio MCP -> authenticated HTTP -> Application/FILE configuration.

Synthetic response inputs; in-process runtime bridge, real Camilla --check.
Not a Unix socket, physical-device, browser or acoustic acceptance test.
"""
import asyncio
import http.cookiejar
import json
import os
from pathlib import Path
import tempfile
import threading
from unittest.mock import patch
from urllib.request import Request, build_opener, HTTPCookieProcessor, ProxyHandler
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from garage_audio.application import Application
from garage_audio.audio_container import Worker
from garage_audio.webserver import Server
from garage_audio import audio_public
from measurement_workflow import fixture


async def journey():
    with tempfile.TemporaryDirectory() as tmp:
        root=Path(tmp); token=root/'token'; token.write_text(os.urandom(32).hex()); token.chmod(0o600)
        app=Application(root/'app'); worker=Worker(root/'worker',root/'evidence')
        server=Server(('127.0.0.1',0),app,token.read_text(),Path(__file__).resolve().parents[1]/'web')
        origin=f'http://127.0.0.1:{server.server_address[1]}'
        thread=threading.Thread(target=server.serve_forever,daemon=True); thread.start()
        opener=build_opener(ProxyHandler({}),HTTPCookieProcessor(http.cookiejar.CookieJar()))
        def post(path,body,csrf=''):
            req=Request(origin+path,json.dumps(body).encode(),{'Origin':origin,'Content-Type':'application/json','X-CSRF-Token':csrf})
            with opener.open(req,timeout=25) as response: return json.load(response)
        login=post('/api/login',{'token':token.read_text()}); csrf=login['result']['csrf']
        def runtime(op,args=None):
            if op in ('audio.status','audio.stop','audio.pause'):
                if op!='audio.status': worker.halt('test_stop')
                return worker.status()
            return worker.configurations.execute(op,args or {})
        try:
            with patch.object(audio_public,'request',side_effect=runtime):
                params=StdioServerParameters(command=os.sys.executable,args=['-m','garage_audio.mcp_server'],env={**os.environ,'GARAGE_APP_URL':origin,'GARAGE_APP_TOKEN_FILE':str(token)})
                async with stdio_client(params) as (read,write):
                    async with ClientSession(read,write) as client:
                        await client.initialize()
                        tools=await client.list_tools(); names={tool.name for tool in tools.tools}
                        assert {'calibration_import_capture','calibration_correction_prepare','calibration_correction_get','calibration_correction_apply','calibration_correction_rollback','calibration_correction_verify'} <= names
                        async def call(name,args):
                            result=json.loads((await client.call_tool(name,args)).content[0].text)
                            assert result['ok'],result
                            return result['result']
                        session=await call('calibration_create_plan',{'name':'SYNTHETIC MCP correction journey','channels':['left']}); sid=session['id']
                        await call('calibration_import_response',dict(fixture(),session_id=sid))
                        await call('calibration_analyze',{'id':sid}); await call('calibration_propose',{'id':sid})
                        prepared=await call('calibration_correction_prepare',{'id':sid}); assert prepared['source']=='synthetic'
                        accepted=await call('calibration_correction_apply',{'id':sid}); assert accepted['runtime']['revision']==1
                        actual=post('/api/operations',{'operation':'calibration.correction.get','args':{'id':sid}},csrf)
                        assert actual['ok'] and actual['result']['transaction']['accepted_revision']==1
                        assert actual['result']['runtime']['loaded'] is None
                        rolled=await call('calibration_correction_rollback',{'id':sid}); assert rolled['runtime']['revision']==2
                        readback=await call('calibration_correction_get',{'id':sid}); assert readback['transaction']['state']=='rolled_back'
                        assert (await client.list_resources()).resources
                        result={'transport':'real stdio MCP and authenticated loopback HTTP','source':'synthetic',
                            'runtime_bridge':'in-process FILE worker; real pinned validator','accepted_revision':1,'rollback_revision':2,
                            'loaded':None,'acoustic_improvement_verified':False,'tool_count':len(names)}
                        receipt=os.environ.get('GARAGE_TEST_RECEIPT_DIR')
                        if receipt:
                            target=Path(receipt); target.mkdir(parents=True,exist_ok=True)
                            (target/'calibration-transport.json').write_text(json.dumps(result,indent=2))
                        print(json.dumps(result),flush=True)
        finally:
            server.shutdown(); server.server_close(); worker.closed.set(); worker.halt('test_exit')


if __name__=='__main__': asyncio.run(journey())
