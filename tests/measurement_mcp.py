"""Real LOCAL SDK stdio -> authenticated HTTP -> disk; synthetic data, no sound."""
import asyncio
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from garage_audio.application import Application
from garage_audio.webserver import Server
from measurement_workflow import fixture

async def journey():
    with tempfile.TemporaryDirectory() as tmp:
        token = Path(tmp)/'token'
        token.write_text('local-test-only-'+'x'*40); token.chmod(0o600)
        state = Path(tmp)/'state'
        http = Server(('127.0.0.1',0),Application(state),token.read_text(),tmp)
        threading.Thread(target=http.serve_forever,daemon=True).start()
        params = StdioServerParameters(command=sys.executable,args=['-m','garage_audio.mcp_server'],
            env={**os.environ,'GARAGE_APP_URL':f'http://127.0.0.1:{http.server_address[1]}','GARAGE_APP_TOKEN_FILE':str(token)})
        try:
            async with stdio_client(params) as (read,write):
                async with ClientSession(read,write) as client:
                    await client.initialize()
                    names = {t.name for t in (await client.list_tools()).tools}
                    assert {'calibration_create_plan','calibration_import_response','calibration_analyze',
                            'calibration_propose','calibration_readback','calibration_cancel'} <= names
                    async def call(name,args):
                        result = json.loads((await client.call_tool(name,args)).content[0].text)
                        assert result['ok'],result
                        return result['result']
                    plan = await call('calibration_create_plan',{'name':'LOCAL synthetic acceptance','channels':['left']})
                    sid = plan['id']
                    imported = await call('calibration_import_response',{'session_id':sid,**fixture()})
                    assert imported['metadata']['source'] == 'synthetic'
                    assert 'synthetic' in imported['interpretation']
                    await call('calibration_analyze',{'id':sid})
                    proposed = await call('calibration_propose',{'id':sid})
                    assert proposed['proposal']['source'] == 'synthetic'
                    assert proposed['proposal']['channels']['left']['peq']
                    assert await call('calibration_readback',{'id':sid}) == proposed
                    assert Application(state).execute('calibration.get',{'id':sid})['result'] == proposed
                    bad = await client.call_tool('calibration_create_plan',{'name':'bad','attended':'human'})
                    assert json.loads(bad.content[0].text)['error']['code'] == 'invalid_arguments'
                    denied = await client.call_tool('garage_operation',{'operation':'dsp.unmute','args':{}})
                    assert json.loads(denied.content[0].text)['error']['code'] == 'attended_authority_required'
                    await call('calibration_cancel',{'id':sid})
                    print('PASS LOCAL real MCP initialize/discover/create/import/analyze/propose/readback/cancel; authenticated HTTP and restart disk readback; explicitly synthetic, no sound')
        finally:
            http.shutdown(); http.server_close()

if __name__ == '__main__': asyncio.run(journey())
