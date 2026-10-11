"""Real local SDK + HTTP independent-human fixture + actual owned file/null child."""
import asyncio
import http.cookiejar
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
from urllib.error import HTTPError
from urllib.request import Request, build_opener, HTTPCookieProcessor, ProxyHandler
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from garage_audio.application import Application
from garage_audio.webserver import Server
from measurement_jobs import provision
from measurement_job_receipts import retain_jobs

def post(opener, origin, path, data, csrf='', host=None):
    req = Request(origin+path,json.dumps(data).encode(),{'Origin':'http://'+host if host else origin,'Content-Type':'application/json','X-CSRF-Token':csrf})
    if host: req.add_header('Host',host)
    try:
        with opener.open(req,timeout=25) as response: return response.status,json.load(response)
    except HTTPError as response: return response.code,json.load(response)

async def journey():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp); reference,password = provision(root/'human')
        auth = root/'automation'; auth.mkdir(mode=0o700)
        token = auth/'token'; token.write_text('isolated-automation-'+os.urandom(24).hex()); token.chmod(0o600)
        app = Application(root/'state',human_reference=str(reference))
        http_server = Server(('127.0.0.1',0),app,token.read_text(),tmp)
        origin = f'http://127.0.0.1:{http_server.server_address[1]}'
        lan_host = f'192.168.68.74:{http_server.server_address[1]}'
        http_server.authorities.add(lan_host)
        thread = threading.Thread(target=http_server.serve_forever,daemon=True); thread.start()
        human = build_opener(ProxyHandler({}),HTTPCookieProcessor(http.cookiejar.CookieJar()))
        automation = build_opener(ProxyHandler({}),HTTPCookieProcessor(http.cookiejar.CookieJar()))
        params = StdioServerParameters(command=sys.executable,args=['-m','garage_audio.mcp_server'],env={**os.environ,
            'GARAGE_APP_URL':origin,'GARAGE_APP_TOKEN_FILE':str(token)})
        try:
            async with stdio_client(params) as (read,write):
                async with ClientSession(read,write) as client:
                    await client.initialize()
                    names = {t.name for t in (await client.list_tools()).tools}
                    assert {'measurement_request','measurement_read','measurement_execute','measurement_cancel','measurement_history'} <= names
                    prompts = await client.list_prompts(); assert {p.name for p in prompts.prompts} == {'measurement_next_action'}
                    async def call(name,args):
                        result = json.loads((await client.call_tool(name,args)).content[0].text)
                        assert result['ok'],result
                        return result['result']
                    plan = await call('calibration_create_plan',{'name':'LOCAL file-null human-fixture journey','channels':['left','right','sub']})
                    job = await call('measurement_request',{'session_id':plan['id'],'position':'seat','channel':'left'})
                    jid = job['id']; approval = {'id':jid,'manifest_sha256':job['manifest_sha256']}
                    status,login = post(automation,origin,'/api/login',{'token':token.read_text()}); assert status == 200
                    assert post(automation,origin,'/api/human/approve',approval,login['result']['csrf'])[0] == 401
                    assert post(human,origin,'/api/human/login',{'user':'operator','password':token.read_text()})[0] == 401
                    status,denied = post(human,origin,'/api/human/login',{'user':'operator','password':password},host=lan_host)
                    assert status == 403 and denied['error']['code'] == 'confidential_origin_required'
                    status,login = post(human,origin,'/api/human/login',{'user':'operator','password':password}); assert status == 200
                    csrf = login['result']['csrf']
                    assert post(human,origin,'/api/human/approve',approval,'wrong')[0] == 403
                    assert post(human,origin,'/api/human/enroll',{},csrf)[0] == 404
                    status,approved = post(human,origin,'/api/human/approve',approval,csrf)
                    assert status == 200 and approved['result']['state'] == 'approved'
                    assert '_grant' not in approved['result']
                    assert 'grant' not in approved['result']['approval']
                    await call('measurement_execute',{'id':jid})
                    until = time.monotonic()+25
                    while time.monotonic()<until:
                        done = await call('measurement_read',{'id':jid})
                        if done['state'] == 'completed': break
                        assert done['state'] == 'running',done
                        await asyncio.sleep(.05)
                    assert done['state'] == 'completed' and done['owned_child_exit_verified']
                    assert done['evidence']['source'] == 'synthetic' and done['evidence']['supported_ir']
                    native = done['evidence']['native_file_render']
                    assert native['all_other_outputs_exact_zero'] and native['native_child_exit_verified']
                    assert native['master_db'] == -60 and native['requested_physical_index'] == 2
                    assert app.execute('jobs.get',{'id':jid})['result'] == done
                    resource = await client.read_resource('garage://measurement-jobs')
                    assert jid in resource.contents[0].text
                    prompt = await client.get_prompt('measurement_next_action',{'id':jid})
                    assert 'synthetic' in str(prompt.messages).lower()
                    replay = await client.call_tool('measurement_execute',{'id':jid})
                    assert not json.loads(replay.content[0].text)['ok']
                    # Reauthentication refreshes a separate session and invalidates old authority.
                    status,reauth = post(human,origin,'/api/human/reauth',{'user':'operator','password':password},csrf)
                    assert status == 200; csrf = reauth['result']['csrf']
                    app.jobs.behavior = 'hang'
                    second = await call('measurement_request',{'session_id':plan['id'],'position':'seat','channel':'left'})
                    assert post(human,origin,'/api/human/approve',{'id':second['id'],'manifest_sha256':second['manifest_sha256']},csrf)[0] == 200
                    await call('measurement_execute',{'id':second['id']})
                    assert post(human,origin,'/api/human/logout',{},csrf)[0] == 200
                    until = time.monotonic()+4
                    while second['id'] in app.jobs.children and time.monotonic()<until: await asyncio.sleep(.02)
                    cancelled = await call('measurement_read',{'id':second['id']})
                    assert cancelled['state'] == 'revoked' and cancelled['owned_child_exit_verified']
                    app.jobs.behavior = 'normal'
                    status,login = post(human,origin,'/api/human/login',{'user':'operator','password':password})
                    assert status == 200; csrf = login['result']['csrf']
                    for channel,index in (('right',1),('sub',2)):
                        routed = await call('measurement_request',{'session_id':plan['id'],'position':'seat','channel':channel})
                        assert routed['manifest']['version'] == 2
                        assert post(human,origin,'/api/human/approve',{'id':routed['id'],'manifest_sha256':routed['manifest_sha256']},csrf)[0] == 200
                        await call('measurement_execute',{'id':routed['id']})
                        until = time.monotonic()+25
                        while time.monotonic()<until:
                            observed = await call('measurement_read',{'id':routed['id']})
                            if observed['state'] == 'completed': break
                            assert observed['state'] == 'running',observed
                            await asyncio.sleep(.05)
                        assert observed['state'] == 'completed'
                        native = observed['evidence']['native_file_render']
                        assert native['requested_physical_index'] == 2+index
                        assert native['physical_peaks'][2+index] > 0 and native['software_peaks'][index] > 0
                        assert all(v == 0 for i,v in enumerate(native['physical_peaks']) if i != 2+index)
                        assert all(v == 0 for i,v in enumerate(native['software_peaks']) if i != index)
                        assert native['output_frames'] >= native['input_frames'] == 84000
                        assert native['native_child_exit_verified']
                    print('PASS real LOCAL MCP17 tools/resource/prompt → request → separate fixture human HTTP exact-manifest approval → actual owned file/null child → supported synthetic IR/evidence → shared read/history; escalation/LAN/CSRF/enrollment/replay rejected, reauth/logout stop verified; no hardware sound')
        finally:
            for job in app.jobs.list(): app.jobs.cancel(job['id'])
            retain_jobs(app,'real-local-http-mcp')
            http_server.shutdown(); http_server.server_close(); thread.join()

if __name__ == '__main__': asyncio.run(journey())
