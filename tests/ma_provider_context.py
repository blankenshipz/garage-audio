"""Real isolated MA loader/WS/auth/action; no native bridge/hardware success claim."""
import asyncio,json,logging,secrets,tempfile,sys
from contextlib import nullcontext
from pathlib import Path
from datetime import datetime,timedelta,timezone
import aiohttp
from music_assistant.mass import MusicAssistant
from music_assistant_models.auth import UserRole

async def main():
    logging.basicConfig(level=logging.ERROR)
    # Retain isolated state/logs across failures; never production credentials.
    with nullcontext(tempfile.mkdtemp(dir='/test-state')) as data:
        # Isolated internal network has no default route. Use supported explicit
        # interface-list discovery mode, not a patched/mock Zeroconf controller.
        Path(data,'settings.json').write_text(json.dumps({'core':{'discovery':{'domain':'discovery','values':{'zeroconf_interfaces':'all','upnp_network_scan':False}}}}))
        mass=MusicAssistant(data,data+'/cache',safe_mode=True)
        try:
            await mass.start()
            auth=mass.webserver.auth
            password=secrets.token_urlsafe(32)  # ephemeral dummy only, never logged
            await auth.login_providers['builtin'].create_user_with_password('isolated_human',password,role=UserRole.ADMIN)
            async with aiohttp.ClientSession() as client:
                url=f'http://127.0.0.1:{mass.webserver.publish_port}/ws'
                seq=0
                async def rpc(ws,command,args):
                    nonlocal seq
                    seq+=1;i=str(seq)
                    await ws.send_json({'message_id':i,'command':command,'args':args})
                    while True:
                        message=await ws.receive(timeout=15)
                        if message.type!=aiohttp.WSMsgType.TEXT:return {'denied':True}
                        value=json.loads(message.data)
                        if value.get('message_id')==i:return value
                async def login(username,pw):
                    ws=await client.ws_connect(url)
                    reply=await rpc(ws,'auth/login',{'username':username,'password':pw,'provider_id':'builtin'})
                    result=reply.get('result',{})
                    if result.get('success') is not True:raise AssertionError('isolated real login failed')
                    token=result['access_token']
                    reply=await rpc(ws,'auth',{'token':token})
                    if reply.get('error_code') is not None:raise AssertionError('isolated WS auth failed')
                    return ws,token
                ws,token=await login('isolated_human',password)
                reply=await rpc(ws,'config/providers/setup',{'provider_domain':'garage_speaker_control'})
                if reply.get('error_code') is not None:raise AssertionError('normal provider setup failed')
                configs=await mass.config.get_provider_configs(provider_domain='garage_speaker_control')
                if len(configs)!=1:raise AssertionError('normal provider instance not loaded')
                instance=configs[0].instance_id
                # Provider setup starts loading in a background task; real available instance.
                for _ in range(100):
                    provider=mass.get_provider(instance)
                    if provider:break
                    await asyncio.sleep(.1)
                else:raise AssertionError('provider normal loader unavailable')
                if '--logout-only' in sys.argv:
                    await rpc(ws,'auth/logout',{})
                    try:reply=await rpc(ws,'config/providers/invoke_action',{'instance_id':instance,'action':'verify_session'})
                    except aiohttp.ClientConnectionError:reply={'denied':True}
                    if reply.get('error_code') is None and not reply.get('denied'):
                        raise AssertionError('logout left a usable action transport')
                    await ws.close();print('PASS actual logout/revocation closes action transport')
                    return
                reply=await rpc(ws,'config/providers/get_entries',{'instance_id':instance})
                if not any(e.get('action')=='verify_session' for e in reply.get('result',[])):
                    raise AssertionError('real ACTION entries unavailable')
                args={'instance_id':instance,'action':'verify_session'}
                reply=await rpc(ws,'config/providers/invoke_action',args)
                if 'No sound authorized' not in (reply.get('result',{}).get('message') or ''):
                    raise AssertionError('real authenticated context did not reach provider')
                print('PASS real normal folder loader/setup/ACTION/WS-login context; NO sound grant')
                async def deny(ws,args=args):
                    try:reply=await rpc(ws,'config/providers/invoke_action',args)
                    except aiohttp.ClientConnectionError:return  # revocation closes actual WS
                    if reply.get('error_code') is None and not reply.get('denied'):
                        raise AssertionError('untrusted context reached successful provider action')
                # Long-lived integration token belongs to SAME admin account; role alone insufficient.
                reply=await rpc(ws,'auth/token/create',{'name':'isolated-integration'})
                integration=reply['result'];other=await client.ws_connect(url)
                await rpc(other,'auth',{'token':integration});await deny(other)
                await deny(other,{**args,'user':'isolated_human'});await other.close()
                print('PASS same-admin integration token denied by actual provider guard')
                for role in (UserRole.GUEST,UserRole.SERVICE):
                    username='isolated_'+role.value
                    await auth.login_providers['builtin'].create_user_with_password(username,password,role=role)
                    other,_=await login(username,password);await deny(other);await other.close()
                    print('PASS real',role.value,'short-lived login denied')
                # The shared config action command ignores unknown arguments;
                # it does NOT activate impersonation. Guard uses dispatch state,
                # never the caller's user selector. Integration still denied above.
                reply=await rpc(ws,'config/providers/invoke_action',{**args,'user':'isolated_service'})
                if 'No sound authorized' not in (reply.get('result',{}).get('message') or ''):
                    raise AssertionError('caller selector unexpectedly changed authenticated context')
                print('PASS caller user selector cannot replace actual context; integration selector denied')
                # Invoke outside dispatch context: must not inherit a cached authenticated user.
                try:await provider.handle_config_action('verify_session')
                except Exception:pass
                else:raise AssertionError('null dispatch context accepted')
                print('PASS internal/null context denied')
                token_id=await auth.get_token_id_from_token(token)
                await auth.database.update('auth_tokens',{'token_id':token_id},{'expires_at':(datetime.now(timezone.utc)-timedelta(seconds=1)).isoformat()})
                await deny(ws);await ws.close()
                print('PASS actual token DB expiry denied despite previously-authenticated WS')
                ws,_=await login('isolated_human',password)
                await rpc(ws,'auth/logout',{});await deny(ws);await ws.close()
                print('PASS actual logout/revocation denies action')
        finally:await mass.stop()

if __name__=='__main__':asyncio.run(main())
