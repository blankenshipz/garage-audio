"""Real official MCP client → stdio SDK server → authenticated shared HTTP."""
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
from garage_audio.profile import DEFAULT

async def journey():
    with tempfile.TemporaryDirectory() as tmp:
        token=Path(tmp)/'token';token.write_text('test-only-credential-'+'x'*32);token.chmod(0o600)
        server=Server(('127.0.0.1',0),Application(Path(tmp)/'state'),token.read_text(),tmp)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        env={**os.environ,'GARAGE_APP_URL':f'http://127.0.0.1:{server.server_address[1]}','GARAGE_APP_TOKEN_FILE':str(token)}
        params=StdioServerParameters(command=sys.executable,args=['-m','garage_audio.mcp_server'],env=env)
        try:
            async with stdio_client(params) as (read,write):
                async with ClientSession(read,write) as client:
                    await client.initialize()
                    tools=await client.list_tools()
                    expected_tools = {'garage_operation', 'garage_capabilities', 'garage_status',
                                      'playback_play', 'playback_play_url', 'profile_edit',
                                      'calibration_create_plan', 'calibration_import_response',
                                      'calibration_readback', 'calibration_analyze',
                                      'calibration_propose', 'calibration_cancel',
                                      'measurement_request','measurement_read','measurement_execute',
                                      'measurement_cancel','measurement_history',
                                      'room_list','room_create','room_read','room_update','room_archive',
                                      'room_annotate','room_confirm','room_photo_upload','room_photo_read',
                                      'listening_profile_list','listening_profile_create','listening_profile_read',
                                      'listening_profile_update','listening_profile_clone','listening_profile_archive',
                                      'listening_profile_bind','listening_profile_guide'}
                    assert {t.name for t in tools.tools} == expected_tools
                    assert len(tools.tools) == len(expected_tools)  # also reject duplicate names
                    by_name={t.name:t for t in tools.tools}
                    assert by_name['playback_play_url'].inputSchema['properties']['url']['type']=='string'
                    assert 'channel' in by_name['profile_edit'].inputSchema['properties']
                    resources=await client.list_resources();assert {r.uri.__str__() for r in resources.resources} == {'garage://status','garage://capabilities','garage://measurement-jobs','garage://rooms','garage://listening-profiles'}
                    prompts=await client.list_prompts();assert {p.name for p in prompts.prompts} == {'measurement_next_action'}
                    status=await client.call_tool('garage_status',{})
                    data=json.loads(status.content[0].text);assert data['ok'] and not data['result']['dsp']['available']
                    await client.read_resource('garage://capabilities')
                    created=await client.call_tool('garage_operation',{'operation':'sessions.create','args':{'name':'SDK synthetic local fixture'}})
                    record=json.loads(created.content[0].text)['result']
                    readback=await client.call_tool('garage_operation',{'operation':'sessions.get','args':{'id':record['id']}})
                    assert json.loads(readback.content[0].text)['result']==record
                    bom='\ufeff20 70\n100 75'
                    imported=await client.call_tool('garage_operation',{'operation':'measurements.import','args':{'session_id':record['id'],'filename':'bom.txt','format':'rew-response','text':bom}})
                    measured=json.loads(imported.content[0].text)['result']
                    import hashlib
                    assert measured['sha256']==hashlib.sha256(bom.encode('utf-8')).hexdigest()
                    assert measured['original_text']==bom and len(measured['points'])==2
                    denied=await client.call_tool('garage_operation',{'operation':'dsp.unmute','args':{}})
                    assert not json.loads(denied.content[0].text)['ok']
                    denied=await client.call_tool('playback_play_url',{'player_id':'garage','url':'https://example.invalid/audio.flac'})
                    assert json.loads(denied.content[0].text)['error']['code']=='attended_authority_required'
                    caps=await client.call_tool('garage_capabilities',{})
                    assert json.loads(caps.content[0].text)['result']['operations']['profiles.edit']['input_schema']['additionalProperties'] is False
                    saved=await client.call_tool('garage_operation',{'operation':'profiles.save','args':{'profile':DEFAULT}})
                    profile=json.loads(saved.content[0].text)['result']
                    edited=await client.call_tool('profile_edit',{'id':profile['id'],'channel':'sub','delay_ms':2.0,'invert':True})
                    result=json.loads(edited.content[0].text)['result']
                    assert result['profile']['channels']['sub']['delay_ms']==2.0 and result['delay_inferred'] is False
                    for tool,args in (
                        ('playback_play_url',{'player_id':'garage','url':'https://example.invalid/a','attended':'PRESENT-GAINS-LOW'}),
                        ('playback_play',{'player_id':'garage','presence':True}),
                        ('profile_edit',{'id':profile['id'],'channel':'left','delay_ms':'10'}),
                        ('profile_edit',{'id':profile['id'],'channel':'left','delay_ms':True}),
                        ('profile_edit',{'id':profile['id'],'channel':'left','gain':False}),
                        ('profile_edit',{'id':profile['id'],'channel':'left','peq':[{'freq':100,'q':1,'gain':-1,'raw':'command'}]}),
                    ):
                        rejected=await client.call_tool(tool,args)
                        assert json.loads(rejected.content[0].text)['error']['code']=='invalid_arguments',(tool,args)
                    print('PASS real LOCAL MCP initialize, exact 34 legacy/calibration/measurement/room/profile tools, 5 resources and retained prompt, schema discovery, missing-DSP status, authenticated create/read session, offline profile edit, strict raw argument validation, denied unmute/play_url; synthetic fixtures only, no hardware sound')
        finally:server.shutdown();server.server_close();thread.join()

if __name__=='__main__':asyncio.run(journey())
