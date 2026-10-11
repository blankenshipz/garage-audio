"""Real local HTTP and official MCP transport, isolated state; no model/hardware.

The generated photo and imported response below are synthetic test fixtures.
This executable journey is not deployed LAN, physical-phone or LLM acceptance.
"""
import asyncio
import base64
import http.cookiejar
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, build_opener, HTTPCookieProcessor, ProxyHandler
from PIL import Image
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from garage_audio.application import Application, OPERATION_SCHEMAS
from garage_audio.mcp_server import ROOM_TOOLS
from garage_audio.webserver import Server


def post(opener, origin, path, body, csrf='', headers=None):
    request = Request(origin+path,json.dumps(body,allow_nan=False).encode(),
        {'Content-Type':'application/json','Origin':origin,'X-CSRF-Token':csrf,**(headers or {})})
    try:
        with opener.open(request,timeout=20) as response:
            return response.status,json.load(response),dict(response.headers)
    except HTTPError as response:
        return response.code,json.load(response),dict(response.headers)


def photo_fixture():
    image = Image.new('RGB',(48,32),(90,110,130))
    exif = Image.Exif(); exif[315] = 'PRIVATE SYNTHETIC EXIF CANARY'
    stream = io.BytesIO(); image.save(stream,format='JPEG',exif=exif)
    return base64.b64encode(stream.getvalue()).decode()


async def journey():
    with tempfile.TemporaryDirectory(prefix='room-integration-',dir='/private/tmp/opencode') as tmp:
        root = Path(tmp); auth = root/'auth'; auth.mkdir(mode=0o700)
        reference = auth/'token'
        reference.write_text('isolated-room-'+os.urandom(24).hex()); reference.chmod(0o600)
        app = Application(root/'state',broker=lambda _: (_ for _ in ()).throw(RuntimeError('no hardware')))
        server = Server(('127.0.0.1',0),app,reference.read_text(),root)
        origin = f'http://127.0.0.1:{server.server_address[1]}'
        thread = threading.Thread(target=server.serve_forever,daemon=True); thread.start()
        opener = build_opener(ProxyHandler({}),HTTPCookieProcessor(http.cookiejar.CookieJar()))
        try:
            operation = {'operation':'rooms.list','args':{}}
            assert post(opener,origin,'/api/operations',operation)[0] == 401
            status,login,headers = post(opener,origin,'/api/login',{'token':reference.read_text()})
            assert status == 200 and login['result']['role'] == 'automation'
            assert 'HttpOnly' in headers['Set-Cookie'] and 'SameSite=Strict' in headers['Set-Cookie']
            directives = dict(part.strip().split(' ',1) for part in headers['Content-Security-Policy'].split(';'))
            assert directives['img-src'] == "'self' data: blob:"
            assert directives['script-src'] == "'self'" and directives['connect-src'] == "'self'"
            assert all('blob:' not in value for name,value in directives.items() if name != 'img-src')
            csrf = login['result']['csrf']
            assert post(opener,origin,'/api/operations',operation)[0] == 403
            assert post(opener,origin,'/api/operations',operation,'wrong')[0] == 403
            assert post(opener,origin,'/api/operations',operation,csrf,{'Host':'evil.example'})[0] == 403
            assert post(opener,origin,'/api/operations',operation,csrf,{'Origin':'http://evil.example'})[0] == 403
            def http_call(op,args):
                status,result,headers = post(opener,origin,'/api/operations',{'operation':op,'args':args},csrf)
                assert status == 200 and headers['Cache-Control'] == 'no-store'
                return result
            def success(op,args):
                response = http_call(op,args); assert response['ok'],response
                return response['result']
            params = StdioServerParameters(command=sys.executable,args=['-m','garage_audio.mcp_server'],
                env={**os.environ,'GARAGE_APP_URL':origin,'GARAGE_APP_TOKEN_FILE':str(reference)})
            async with stdio_client(params) as (read,write):
                async with ClientSession(read,write) as client:
                    await client.initialize()
                    tools = {t.name:t for t in (await client.list_tools()).tools}
                    assert set(ROOM_TOOLS) <= tools.keys()
                    for name,op in ROOM_TOOLS.items():
                        assert tools[name].inputSchema == OPERATION_SCHEMAS[op]
                        assert tools[name].inputSchema['additionalProperties'] is False
                    resources = {str(r.uri) for r in (await client.list_resources()).resources}
                    assert {'garage://rooms','garage://listening-profiles','garage://measurement-jobs'} <= resources
                    async def mcp(name,args,ok=True):
                        result = json.loads((await client.call_tool(name,args)).content[0].text)
                        assert result['ok'] == ok,result
                        return result['result'] if ok else result['error']
                    error = await mcp('room_create',{'name':'wrong','consent':True},False)
                    assert error['code'] == 'invalid_arguments'
                    room = await mcp('room_create',{'name':'LOCAL synthetic integration room'})
                    rid = room['id']
                    agent_status = success('agent.status',{})
                    assert agent_status['state'] == 'inference_disabled'
                    assert not agent_status['provider_photo_upload'] and not agent_status['tracing_enabled']
                    session = await mcp('garage_operation',{'operation':'agent.session.create','args':{'room_id':rid}})
                    disabled = success('agent.run',{'id':session['id'],'expected_version':session['version'],'message':'Do not send private photos externally'})
                    assert disabled['state'] == 'inference_disabled' and not disabled['inference_enabled']
                    assert all(item['role'] == 'user' for item in disabled['items'])
                    assert disabled['last_result']['state'] == 'inference_disabled'
                    assert (await mcp('garage_operation',{'operation':'agent.session.get','args':{'id':session['id']}})) == disabled
                    assert http_call('agent.cancel',{'id':session['id'],'expected_version':session['version']})['error']['code'] == 'version_conflict'
                    assert success('rooms.get',{'id':rid}) == room
                    uploaded = success('rooms.photo_upload',{'id':rid,'expected_version':room['version'],'data_base64':photo_fixture()})
                    # Descriptor spelling is part of the domain implementation; obtain
                    # its generated asset ID without assuming an unauthenticated URL.
                    descriptor = uploaded.get('photo') or uploaded.get('photos',[])[-1]
                    pid = descriptor.get('id') or descriptor['photo_id']
                    photo = await mcp('room_photo_read',{'id':rid,'photo_id':pid})
                    payload = base64.b64decode(photo['data_base64'],validate=True)
                    assert len(payload) <= 525000 and b'PRIVATE SYNTHETIC EXIF CANARY' not in payload
                    with Image.open(io.BytesIO(payload)) as decoded:
                        assert not decoded.getexif() and decoded.size == (48,32)
                    assert success('rooms.photo_get',{'id':rid,'photo_id':pid}) == photo
                    assert http_call('rooms.photo_get',{'id':rid,'photo_id':'../token'})['error']['code'] == 'invalid_arguments'
                    other = success('rooms.create',{'name':'Other owning room'})
                    assert not http_call('rooms.photo_get',{'id':other['id'],'photo_id':pid})['ok']
                    room = await mcp('room_annotate',{'id':rid,'expected_version':uploaded['version'],'annotations':[
                        {'kind':'speaker','label':'Left','x':.1,'y':.2},
                        {'kind':'fit','label':'Seat 1','x':.4,'y':.6,'mic_height_m':1.2,'orientation':'90'},
                        {'kind':'fit','label':'Area 2','x':.5,'y':.5,'mic_height_m':1.2,'orientation':'90'},
                        {'kind':'verify','label':'Held-out 2','x':.6,'y':.6,'mic_height_m':1.2,'orientation':'90'}]})
                    assert not room['annotations_confirmed']
                    room = success('rooms.confirm',{'id':rid,'expected_version':room['version']})
                    fit = next(a['id'] for a in room['annotations'] if a['kind'] == 'fit')
                    fit2 = [a['id'] for a in room['annotations'] if a['kind'] == 'fit'][1]
                    verify = next(a['id'] for a in room['annotations'] if a['kind'] == 'verify')
                    profiles = []
                    for kind in ('whole-room','seat','area','custom'):
                        profile = await mcp('listening_profile_create',{'room_id':rid,'name':kind,'kind':kind,
                            'fit_positions':[{'position_id':position,'weight':1} for position in ([fit,fit2] if kind == 'whole-room' else [fit])], 'verify_positions':[verify]})
                        assert profile['sound_disabled'] and not profile['applied'] and not profile['verified_acoustically']
                        assert success('listening_profiles.get',{'id':profile['id']}) == profile
                        profiles.append(profile)
                    p = profiles[1]
                    guide = await mcp('listening_profile_guide',{'id':p['id']})
                    assert fit in json.dumps(guide)
                    assert not p['analysis_available'] and not p['proposal_available']
                    changed = success('listening_profiles.update',{'id':p['id'],'expected_version':p['version'],'notes':'manual review'})
                    error = await mcp('listening_profile_update',{'id':p['id'],'expected_version':p['version'],'name':'stale'},False)
                    assert error['code'] == 'version_conflict'
                    error = await mcp('room_update',{'id':rid,'expected_version':str(room['version']),'name':'coerced'},False)
                    assert error['code'] == 'invalid_arguments'
                    assert http_call('rooms.update',{'id':rid,'expected_version':True,'name':'boolean'})['error']['code'] == 'invalid_arguments'
                    assert http_call('rooms.annotate',{'id':rid,'expected_version':room['version'],'annotations':[
                        {'kind':'speaker','label':'bad','x':.1,'y':.2,'shell':'whoami'}]})['error']['code'] == 'invalid_arguments'
                    clone = await mcp('listening_profile_clone',{'id':changed['id'],'expected_version':changed['version'],'name':'Workbench copy'})
                    assert clone['id'] != changed['id']
                    # Real REW parser/import, truthfully synthetic provenance. Binding
                    # must reject it rather than relabel it as measured garage data.
                    plan = await mcp('calibration_create_plan',{'name':'synthetic binding negative','positions':[fit]})
                    imported = await mcp('calibration_import_response',{'session_id':plan['id'],'filename':'synthetic.txt',
                        'format':'rew-response','text':'20 0\n40 -1\n80 -2\n160 -3\n200 -4\n',
                        'metadata':{'source':'synthetic','position':fit,'channel':'left','sample_rate':48000,
                            'orientation':'90','calibration_sha256':'a'*64,'calibration_applied':True}})
                    measurement_id = imported['id']
                    error = await mcp('listening_profile_bind',{'id':changed['id'],'expected_version':changed['version'],
                        'bindings':[{'position_id':fit,'channel':'left','session_id':plan['id'],'measurement_id':measurement_id}]},False)
                    assert error['code'] in ('source_invalid','invalid_arguments')
                    assert rid in (await client.read_resource('garage://rooms')).contents[0].text
                    assert changed['id'] in (await client.read_resource('garage://listening-profiles')).contents[0].text
                    # A second real Application instance uses the same persistent
                    # domain and existing process lock, not MCP-side state.
                    reopened = Application(root/'state',broker=app.broker)
                    assert reopened.execute('rooms.get',{'id':rid})['result'] == room
                    assert reopened.execute('listening_profiles.get',{'id':changed['id']})['result'] == changed
                    archived = await mcp('room_archive',{'id':other['id'],'expected_version':other['version']})
                    assert archived['archived']
                    error = await mcp('room_update',{'id':other['id'],'expected_version':archived['version'],'name':'mutate archived'},False)
                    assert error['code'] == 'archived'
                    assert http_call('dsp.unmute',{})['error']['code'] == 'attended_authority_required'
                    print('PASS real LOCAL HTTP/official MCP shared room/photo/profile persistence; strict schemas, EXIF privacy, owner isolation, auth/CSRF/Host/Origin, version conflicts, synthetic binding rejection; no model/provider/hardware/sound')
        finally:
            server.shutdown(); server.server_close(); thread.join()


class Integration(unittest.TestCase):
    def test_real_local_http_mcp(self):
        asyncio.run(journey())


if __name__ == '__main__': unittest.main()
