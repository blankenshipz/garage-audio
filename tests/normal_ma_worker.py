"""Isolated real MA/dummy interactive ACTION and real Squeezelite controls.
Never run against production data; container-only synthetic program source.
"""
import asyncio,json,logging,math,secrets,sys,tempfile,wave,array,socket,os
from datetime import datetime,timedelta,timezone
from pathlib import Path
import aiohttp
from aiohttp import web
from music_assistant.mass import MusicAssistant
from music_assistant.models.player import PlayerMedia
from music_assistant_models.auth import UserRole

PLAYER='02:00:00:00:21:01'
MODE=sys.argv[1] if len(sys.argv)>1 else 'journey'
async def main():
    logging.basicConfig(level=logging.ERROR)
    address=socket.gethostbyname(socket.gethostname())
    data=tempfile.mkdtemp(dir='/test-state');Path(data,'settings.json').write_text(json.dumps({'core':{'discovery':{'domain':'discovery','values':{'zeroconf_interfaces':'all','upnp_network_scan':False}},'streams':{'domain':'streams','values':{'bind_ip':'0.0.0.0','publish_ip':address}},'webserver':{'domain':'webserver','values':{'bind_ip':'0.0.0.0','publish_ip':address}}}}))
    audio=Path(data,'synthetic.wav')
    with wave.open(str(audio),'wb') as out:
        out.setnchannels(2);out.setsampwidth(2);out.setframerate(48000)
        block=array.array('h')
        for i in range(48000):block.extend((int(12000*math.sin(2*math.pi*997*i/48000)),int(6000*math.sin(2*math.pi*701*i/48000))))
        for _ in range(190):out.writeframesraw(block.tobytes())
    app=web.Application();app.router.add_get('/synthetic.wav',lambda request:web.FileResponse(audio))
    runner=web.AppRunner(app);await runner.setup();site=web.TCPSite(runner,'0.0.0.0',18088);await site.start()
    mass=MusicAssistant(data,data+'/cache',safe_mode=True)
    try:
        await mass.start();password=secrets.token_urlsafe(32)
        user=await mass.webserver.auth.login_providers['builtin'].create_user_with_password('isolated_listener',password,role=UserRole.ADMIN)
        async with aiohttp.ClientSession() as client:
            ws=await client.ws_connect(f'http://127.0.0.1:{mass.webserver.publish_port}/ws');sequence=0
            async def rpc(command,args):
                nonlocal sequence
                sequence+=1;message=str(sequence);await ws.send_json({'message_id':message,'command':command,'args':args})
                while True:
                    packet=await ws.receive(timeout=80)
                    if packet.type!=aiohttp.WSMsgType.TEXT:raise AssertionError('actual MA websocket closed')
                    reply=json.loads(packet.data)
                    if reply.get('message_id')==message:
                        if reply.get('error_code') is not None:raise AssertionError('MA command denied: '+command+' code '+str(reply['error_code'])+' '+str(reply.get('details',''))[:300])
                        return reply.get('result')
            token=(await rpc('auth/login',{'username':'isolated_listener','password':password,'provider_id':'builtin'}))['access_token']
            await rpc('auth',{'token':token})
            await rpc('config/providers/setup',{'provider_domain':'squeezelite'})
            if MODE!='browser_add':await rpc('config/providers/setup',{'provider_domain':'garage_speaker_control'})
            configs=await mass.config.get_provider_configs(provider_domain='garage_speaker_control');instance=configs[0].instance_id if configs else None
            if MODE in ('browser','browser_add'):
                reference=Path('/test-state/browser-login.json')
                fd=os.open(reference,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
                with os.fdopen(fd,'w') as f:json.dump({'username':'isolated_listener','password':password,'instance_id':instance},f)
            print('READY',flush=True);await asyncio.to_thread(sys.stdin.readline)
            if MODE in ('browser','browser_add'):await asyncio.sleep(180);return
            for _ in range(150):
                state=mass.players.get_player_state(PLAYER)
                if state and state.available:break
                await asyncio.sleep(.1)
            else:raise AssertionError('actual Squeezelite registration absent')
            async def action(name):return await rpc('config/providers/invoke_action',{'instance_id':instance,'action':name})
            if MODE=='integration':
                integration=await rpc('auth/token/create',{'name':'isolated automated token'})
                await rpc('auth',{'token':integration})
                try:await action('start_line_v1_2')
                except AssertionError:pass
                else:raise AssertionError('integration token accepted normal consent')
                print('DENIED_INTEGRATION',flush=True);await asyncio.to_thread(sys.stdin.readline);return
            # Old gain consent/actions must not become a new line-output grant.
            try:await action('start_listening_2_20')
            except AssertionError:pass
            else:raise AssertionError('old attenuation action accepted new line-output policy')
            entries=await asyncio.wait_for(action('start_line_v1_2'),25)
            assert any(e.get('key')=='listening_note' for e in entries)
            assert mass.players.get_player_state(PLAYER).volume_level==10
            print('STARTED_MUTED',flush=True);await asyncio.to_thread(sys.stdin.readline)
            await rpc('player_queues/play_media',{'queue_id':PLAYER,'media':f'http://{address}:18088/synthetic.wav','option':'replace'})
            async def wait_state(expected):
                for _ in range(200):
                    s=mass.players.get_player_state(PLAYER)
                    if s and s.playback_state.value==expected:return
                    await asyncio.sleep(.1)
                raise AssertionError('MA state '+expected+' absent')
            await wait_state('playing');print('PLAYING10',flush=True);await asyncio.to_thread(sys.stdin.readline)
            if MODE in ('logout','permission','expired'):
                auth=mass.webserver.auth
                if MODE=='logout':
                    try:await rpc('auth/logout',{})
                    except (AssertionError,ConnectionError,aiohttp.ClientError):pass
                elif MODE=='permission':await auth.database.update('users',{'user_id':user.user_id},{'role':'service'})
                else:
                    token_id=await auth.get_token_id_from_token(token)
                    await auth.database.update('auth_tokens',{'token_id':token_id},{'expires_at':(datetime.now(timezone.utc)-timedelta(seconds=1)).isoformat()})
                for _ in range(120):
                    if not mass.get_provider(instance)._windows:break
                    await asyncio.sleep(.1)
                else:raise AssertionError('normal MA guard did not revoke')
                print('REVOKED_REGISTERED',flush=True);await asyncio.to_thread(sys.stdin.readline);return
            await mass.players.cmd_volume_set(PLAYER,50)
            for _ in range(40):
                if mass.players.get_player_state(PLAYER).volume_level==50:break
                await asyncio.sleep(.1)
            else:raise AssertionError('MA volume50 readback absent')
            print('PLAYING50',flush=True);await asyncio.to_thread(sys.stdin.readline)
            if MODE=='controls100':
                await mass.players.cmd_volume_set(PLAYER,100)
                for _ in range(40):
                    if mass.players.get_player_state(PLAYER).volume_level==100:break
                    await asyncio.sleep(.1)
                else:raise AssertionError('MA volume100 readback absent')
                print('PLAYING100',flush=True);await asyncio.to_thread(sys.stdin.readline)
                await mass.players.cmd_volume_set(PLAYER,50)
                for _ in range(40):
                    if mass.players.get_player_state(PLAYER).volume_level==50:break
                    await asyncio.sleep(.1)
            await mass.players.cmd_pause(PLAYER);await wait_state('paused')
            print('PAUSED',flush=True);await asyncio.to_thread(sys.stdin.readline)
            await mass.players.cmd_play(PLAYER);await wait_state('playing')
            assert mass.players.get_player_state(PLAYER).volume_level==50
            print('RESUMED50',flush=True);await asyncio.to_thread(sys.stdin.readline)
            await mass.players.cmd_stop(PLAYER);await wait_state('idle')
            for _ in range(100):
                provider=mass.get_provider(instance)
                if not provider._windows:break
                await asyncio.sleep(.1)
            else:raise AssertionError('MA Stop did not end native approval')
            assert mass.players.get_player_state(PLAYER).available
            print('STOPPED_REGISTERED',flush=True);await asyncio.to_thread(sys.stdin.readline);await ws.close()
    except BaseException as error:
        print('FIXTURE_ERROR',type(error).__name__,str(error)[:400],file=sys.stderr,flush=True)
        raise
    finally:
        try:await asyncio.wait_for(mass.stop(),15)
        finally:await runner.cleanup()
if __name__=='__main__':asyncio.run(main())
