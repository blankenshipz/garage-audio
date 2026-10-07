"""Official Music Assistant client; token read from a private file, never printed."""
import argparse
import asyncio
import json
import os
from pathlib import Path
from music_assistant_client import MusicAssistantClient
from music_assistant_models.enums import QueueOption

async def run(args):
    token = None
    if args.token_file:
        path = Path(args.token_file)
        if path.stat().st_mode & 0o077: raise ValueError('token file must be mode 0600')
        token = path.read_text().strip()
    async with MusicAssistantClient(args.url, None, token=token) as ma:
        await ma.players.fetch_state()
        await ma.player_queues.fetch_state()
        if args.cmd == 'status':
            print(json.dumps([{'id': p.player_id, 'name': p.name, 'available': p.available}
                              for p in ma.players.players]))
        elif args.cmd == 'queue':
            items = await ma.player_queues.get_queue_items(args.player)
            print(json.dumps([{'id': i.queue_item_id, 'name': i.name} for i in items]))
        elif args.cmd == 'url':
            await ma.player_queues.play_media(args.player, args.media, option=QueueOption.REPLACE)
        else: await getattr(ma.player_queues, args.cmd)(args.player)

def main():
    p = argparse.ArgumentParser()
    p.add_argument('--url', default='http://127.0.0.1:8095')
    p.add_argument('--token-file', default=os.environ.get('MA_TOKEN_FILE'))
    sub = p.add_subparsers(dest='cmd', required=True)
    sub.add_parser('status')
    for cmd in ('queue','play','pause','stop','next','clear','url'):
        s = sub.add_parser(cmd); s.add_argument('player')
        if cmd == 'url': s.add_argument('media')
    args = p.parse_args()
    # Do not allow accidental real playback before graph commissioning.
    if args.cmd in ('url','play','next'):
        from .control import ROOT
        if not (ROOT / 'state/physical-map-verified').exists():
            raise SystemExit('Playback locked pending attended physical commissioning')
    asyncio.run(run(args))

if __name__ == '__main__': main()
