"""Isolated actual MA process. Dummy login only; fixed native FILE test backend."""
import asyncio,json,logging,secrets,sys,tempfile
from datetime import datetime,timedelta,timezone
from pathlib import Path
import aiohttp
from music_assistant.mass import MusicAssistant
from music_assistant_models.auth import UserRole

async def main():
    logging.basicConfig(level=logging.ERROR)
    data=tempfile.mkdtemp(dir='/test-state')
    Path(data,'settings.json').write_text(json.dumps({'core':{'discovery':{'domain':'discovery','values':{'zeroconf_interfaces':'all','upnp_network_scan':False}}}}))
    mass=MusicAssistant(data,data+'/cache',safe_mode=True)
    try:
        await mass.start();auth=mass.webserver.auth;password=secrets.token_urlsafe(32)
        user=await auth.login_providers['builtin'].create_user_with_password('file_operator',password,role=UserRole.ADMIN)
        async with aiohttp.ClientSession() as client:
            ws=await client.ws_connect(f'http://127.0.0.1:{mass.webserver.publish_port}/ws');seq=0
            async def rpc(command,args):
                nonlocal seq
                seq+=1;i=str(seq);await ws.send_json({'message_id':i,'command':command,'args':args})
                while True:
                    m=await ws.receive(timeout=60)
                    if m.type!=aiohttp.WSMsgType.TEXT:return {'denied':True}
                    v=json.loads(m.data)
                    if v.get('message_id')==i:return v
            reply=await rpc('auth/login',{'username':'file_operator','password':password,'provider_id':'builtin'})
            token=reply['result']['access_token'];await rpc('auth',{'token':token})
            await rpc('config/providers/setup',{'provider_domain':'garage_speaker_control'})
            configs=await mass.config.get_provider_configs(provider_domain='garage_speaker_control');instance=configs[0].instance_id
            for _ in range(100):
                provider=mass.get_provider(instance)
                if provider:break
                await asyncio.sleep(.1)
            else:raise AssertionError('actual provider did not load')
            async def action(name,**extra):return await rpc('config/providers/invoke_action',{'instance_id':instance,'action':name,**extra})
            print('READY',flush=True)
            await asyncio.to_thread(sys.stdin.readline) # parent seals actual PID/namespace/provider hashes
            mode=sys.argv[1]
            if mode=='integration':
                reply=await rpc('auth/token/create',{'name':'FILE-test integration'})
                await rpc('auth',{'token':reply['result']})
                reply=await action('prepare_check',role='human',user='file_operator')
                if reply.get('error_code') is None:raise AssertionError('integration forged-role request accepted')
                print('DENIED',flush=True);return
            reply=await action('prepare_check')
            if reply.get('error_code') is not None:raise AssertionError('actual MA preparation action failed')
            if not any(v.get('action')=='confirm_check' for v in reply.get('result',[])):raise AssertionError('attended consent button absent')
            print('PREPARED',flush=True)
            if mode in ('stale','forged'):
                connection=next(iter(provider._windows.values()))
                try:
                    if mode=='stale':await connection.request('confirm',handle=connection.state['handle'],generation='STALE')
                    else:await connection.request('confirm',handle=connection.state['handle'],generation=connection.state['generation'],role='human')
                except Exception:pass
                else:raise AssertionError('stale/forged native command accepted')
                print('DENIED',flush=True);return
            reply=await action('confirm_check')
            if reply.get('error_code') is not None:raise AssertionError('actual MA attended action failed')
            if not any(v.get('key')=='listening_note' for v in reply.get('result',[])):raise AssertionError('native listening state absent')
            print('LISTENING',flush=True)
            await asyncio.to_thread(sys.stdin.readline) # parent verifies actual FILE mute/ceiling before revoke/Stop
            if mode=='death':await asyncio.sleep(300);return
            if mode in ('stop','stallstop'):
                reply=await action('stop_output')
                if reply.get('error_code') is not None:raise AssertionError('MA Stop action failed')
            elif mode=='logout':await rpc('auth/logout',{})
            elif mode=='expired':
                ident=await auth.get_token_id_from_token(token)
                await auth.database.update('auth_tokens',{'token_id':ident},{'expires_at':(datetime.now(timezone.utc)-timedelta(seconds=1)).isoformat()})
            elif mode=='permission':await auth.database.update('users',{'user_id':user.user_id},{'role':'service'})
            elif mode=='expiry':await asyncio.sleep(125)
            elif mode=='rootdeath':pass
            else:raise AssertionError('unknown scenario')
            for _ in range(60):
                if not provider._windows:break
                await asyncio.sleep(.1)
            else:raise AssertionError('MA session guardian did not close')
            print('CLOSED',flush=True);await ws.close()
    finally:await mass.stop()

if __name__=='__main__':asyncio.run(main())
