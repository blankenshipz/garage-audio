"""Isolated actual MA server/API + synthetic WAV. No Spotify/provider/hardware.

One-time fixture-only native user creation; all useful actions then use MA public WS.
No custom control provider is loaded. Never run with a production /test-state mount.
"""
import asyncio
import array
import json
import logging
import math
from pathlib import Path
import secrets
import socket
import os
import wave
import aiohttp
from aiohttp import web
from music_assistant.mass import MusicAssistant
from music_assistant_models.auth import UserRole

PLAYER = '02:00:00:00:21:01'


async def main():
    logging.basicConfig(level=logging.ERROR)
    logging.disable(logging.INFO)  # prevent upstream DEBUG dumps during private fixture login
    root = Path('/test-state')
    root.mkdir(exist_ok=True)
    if (root/'settings.json').exists():
        raise RuntimeError('fresh isolated state required')
    address = os.environ.get('GARAGE_FIXTURE_IP') or socket.gethostbyname(socket.gethostname())
    (root/'settings.json').write_text(json.dumps({'core': {
        'discovery': {'domain': 'discovery', 'values': {'zeroconf_interfaces': 'all', 'upnp_network_scan': False}},
        'streams': {'domain': 'streams', 'values': {'bind_ip': '0.0.0.0', 'publish_ip': address}},
        'webserver': {'domain': 'webserver', 'values': {'bind_ip': '0.0.0.0', 'publish_ip': address}}}}))
    audio = root/'synthetic.wav'
    with wave.open(str(audio), 'wb') as out:
        out.setnchannels(2)
        out.setsampwidth(2)
        out.setframerate(48000)
        block = array.array('h')
        for i in range(48000):
            block.extend((int(10000*math.sin(2*math.pi*997*i/48000)),
                          int(5000*math.sin(2*math.pi*701*i/48000))))
        for _ in range(90):
            out.writeframesraw(block.tobytes())
    mass = MusicAssistant(str(root), str(root/'cache'), safe_mode=True)
    app = web.Application()
    app.router.add_get('/synthetic.wav', lambda _: web.FileResponse(audio))
    runner = web.AppRunner(app)
    await mass.start()
    password = secrets.token_urlsafe(32)
    await mass.webserver.auth.login_providers['builtin'].create_user_with_password(
        'isolated_file_listener', password, role=UserRole.ADMIN)
    client = aiohttp.ClientSession()
    ws = await client.ws_connect(f'http://127.0.0.1:{mass.webserver.publish_port}/ws')
    sequence = 0
    lock = asyncio.Lock()
    async def rpc(command, args):
        nonlocal sequence
        sequence += 1
        key = str(sequence)
        await ws.send_json({'message_id': key, 'command': command, 'args': args})
        while True:
            packet = await ws.receive(timeout=30)
            if packet.type != aiohttp.WSMsgType.TEXT:
                raise RuntimeError('actual MA websocket closed')
            reply = json.loads(packet.data)
            if reply.get('message_id') == key:
                if reply.get('error_code') is not None:
                    detail = ''
                    if command == 'player_queues/play_media':  # only this synthetic URI, never auth errors
                        detail = ' '+str(reply.get('details', reply.get('error_message', '')))[:1000]
                    raise RuntimeError('MA API denied ' + command + ' code=' + str(reply['error_code'])+detail)
                return reply.get('result')
    token = (await rpc('auth/login', {'username': 'isolated_file_listener',
        'password': password, 'provider_id': 'builtin'}))['access_token']
    await rpc('auth', {'token': token})
    await rpc('config/providers/setup', {'provider_domain': 'squeezelite'})
    async def state(_):
        async with lock:
            player = await rpc('players/get', {'player_id': PLAYER})
            if player is None:
                return web.json_response({'available': False, 'state': 'unregistered'})
            return web.json_response({'available': player['available'],
                'state': player['playback_state'], 'volume': player['volume_level'],
                'elapsed': player['elapsed_time'], 'source': player['active_source'],
                'synthetic_source': True, 'ma_version': '2.10.5'})
    async def policy(_):
        async with lock:
            entries = await rpc('config/providers/get_entries', {'instance_id': 'spotify_connect_unenrolled'})
            from music_assistant.providers.spotify_connect.provider import VOLUME_MODE_OPTIONS
            from music_assistant.providers.spotify_connect.soloist.backend import SoloistBackend
            return web.json_response({'options': [x.value for x in VOLUME_MODE_OPTIONS],
                'module_loaded': SoloistBackend.__name__, 'public_config_keys': [x['key'] for x in entries],
                'public_volume_enum_verified': False, 'provider_created': False, 'consent_supplied': False})
    async def command(request):
        value = await request.json()
        op = value['op']
        async with lock:
            if op == 'play':
                await rpc('player_queues/play_media', {'queue_id': PLAYER,
                    'media': f'http://{address}:18088/synthetic.wav', 'option': 'replace'})
            elif op == 'volume' and value['value'] in (25, 50, 100):
                await rpc('players/cmd/volume_set', {'player_id': PLAYER, 'volume_level': value['value']})
            elif op in ('pause', 'play_resume', 'stop'):
                await rpc('players/cmd/' + ('play' if op == 'play_resume' else op), {'player_id': PLAYER})
            else:
                raise web.HTTPBadRequest()
        return web.json_response({'ok': True, 'synthetic_fixture': True})
    app.router.add_get('/fixture/state', state)
    app.router.add_get('/fixture/policy', policy)
    app.router.add_post('/fixture/command', command)
    await runner.setup()
    await web.TCPSite(runner, '0.0.0.0', 18088).start()
    print('MA_ACTUAL_READY_SYNTHETIC_SOURCE_NO_SPOTIFY', flush=True)
    try:
        await asyncio.Event().wait()
    finally:
        await ws.close()
        await client.close()
        await mass.stop()
        await runner.cleanup()


if __name__ == '__main__':
    asyncio.run(main())
