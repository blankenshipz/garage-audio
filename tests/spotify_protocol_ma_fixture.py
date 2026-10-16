"""Actual isolated MA2.10.5 with test-owned private references, no Spotify auth/audio.

Never mount production state/auth. Native user creation is one-time fixture setup,
matching the existing FILE fixture; adapter operations use supported public APIs.
"""
import asyncio
import json
import logging
import os
from pathlib import Path
import secrets
import signal
import socket
import stat
import sys
import aiohttp
from music_assistant.mass import MusicAssistant
from music_assistant_models.auth import UserRole


def write_reference(root,name,value):
    fd=os.open(root,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:
        handle=os.open(name,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=fd)
        with os.fdopen(handle,'w') as stream:
            stream.write(value+'\n');stream.flush();os.fsync(stream.fileno())
        os.fsync(fd)
    finally: os.close(fd)


async def main():
    if os.environ.get('GARAGE_SETUP_PROTOCOL_FIXTURE')!='terms-abort-only':
        raise RuntimeError('Explicit isolated fixture required')
    logging.disable(logging.CRITICAL)  # No wire/auth values in retained runtime logs.
    os.umask(0o077)
    root=Path('/test-state');auth=Path('/test-auth')
    if not (root/'fixture-only').is_file() or not (auth/'fixture-only').is_file():
        raise RuntimeError('Test-owned volume markers required')
    fresh=not (root/'settings.json').exists()
    if fresh:
        address=socket.gethostbyname(socket.gethostname())
        (root/'settings.json').write_text(json.dumps({'core':{
            'discovery':{'domain':'discovery','values':{'zeroconf_interfaces':'all','upnp_network_scan':False}},
            'streams':{'domain':'streams','values':{'bind_ip':'0.0.0.0','publish_ip':address}},
            'webserver':{'domain':'webserver','values':{'bind_ip':'0.0.0.0','publish_ip':address}}}}))
    mass=MusicAssistant(str(root),str(root/'cache'),safe_mode=True)
    await mass.start()
    try:
        if fresh:
            password=secrets.token_urlsafe(32)
            await mass.webserver.auth.login_providers['builtin'].create_user_with_password(
                'isolated_setup_protocol',password,role=UserRole.ADMIN)
            async with aiohttp.ClientSession() as session:
                async with session.ws_connect(f'http://127.0.0.1:{mass.webserver.publish_port}/ws') as ws:
                    await ws.send_json({'message_id':'fixture-login','command':'auth/login','args':{
                        'username':'isolated_setup_protocol','password':password,'provider_id':'builtin'}})
                    while True:
                        packet=await ws.receive(timeout=15)
                        if packet.type!=aiohttp.WSMsgType.TEXT: raise RuntimeError('Fixture login failed')
                        reply=json.loads(packet.data)
                        if reply.get('message_id')!='fixture-login': continue
                        if reply.get('error_code') is not None: raise RuntimeError('Fixture login denied')
                        token=reply['result']['access_token'];break
            write_reference(auth,'ma-token',token)
            write_reference(auth,'app-token',secrets.token_urlsafe(48))
        for name in ('ma-token','app-token'):
            info=(auth/name).lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_uid!=1000 or info.st_mode&0o077:
                raise RuntimeError('Private fixture reference invalid')
        (auth/'ready').write_text('actual MA ready; private refs only\n')
        print('ACTUAL_ISOLATED_MA_READY_NO_SPOTIFY_AUTH_OR_SOUND',flush=True)
        stopped=asyncio.Event()
        loop=asyncio.get_running_loop()
        for signum in (signal.SIGTERM,signal.SIGINT):
            loop.add_signal_handler(signum,stopped.set)
        try: await stopped.wait()
        finally:
            for signum in (signal.SIGTERM,signal.SIGINT): loop.remove_signal_handler(signum)
    finally: await mass.stop()


if __name__=='__main__':
    try: asyncio.run(main())
    except Exception:
        print('Isolated MA fixture failed; credential/protocol details withheld',file=sys.stderr)
        raise SystemExit(1) from None
