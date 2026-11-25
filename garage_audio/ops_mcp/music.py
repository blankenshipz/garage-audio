"""Music Assistant tools over the MA server JSON RPC API (command names target the MA 2.x server API)."""
from .errors import OpsError
from .safety import check_ma_volume

MEDIA_TYPES = ('track', 'album', 'artist', 'playlist', 'radio')
PLAYER_KEYS = ('player_id', 'display_name', 'name', 'provider', 'type', 'available', 'powered', 'state',
               'volume_level', 'volume_muted', 'group_childs', 'synced_to', 'active_source', 'active_group')


def _media(value):
    if not isinstance(value, dict):
        return None
    out = {k: value.get(k) for k in ('uri', 'title', 'name', 'artist', 'album', 'duration', 'media_type') if k in value}
    artists = value.get('artists')
    if isinstance(artists, list):
        out['artists'] = [a.get('name') for a in artists if isinstance(a, dict)][:5]
    return out


def _player(value, allowed):
    out = {k: value.get(k) for k in PLAYER_KEYS if k in value}
    if 'current_media' in value:
        out['current_media'] = _media(value.get('current_media'))
    out['controllable'] = not allowed or value.get('player_id') in allowed
    return out


def _require_player(ctx, player_id, label='player_id'):
    allowed = ctx.config.ma_players
    if allowed and player_id not in allowed:
        raise OpsError('player_not_allowed', f'{label} is not in music_assistant.players', allowed=allowed)
    return player_id


def players(ctx, _args):
    value = ctx.clients.ma.call('players/all')
    if not isinstance(value, list):
        raise OpsError('ma_error', 'unexpected players response')
    return {'players': [_player(p, ctx.config.ma_players) for p in value if isinstance(p, dict)]}


def queue(ctx, args):
    player_id = args['player_id']
    queues = ctx.clients.ma.call('player_queues/all')
    match = None
    for item in queues if isinstance(queues, list) else []:
        if isinstance(item, dict) and player_id in (item.get('queue_id'), item.get('player_id')):
            match = item
            break
    queue_id = (match or {}).get('queue_id', player_id)
    items = ctx.clients.ma.call('player_queues/items', {'queue_id': queue_id, 'limit': args.get('limit', 20),
                                                        'offset': args.get('offset', 0)})
    projected = []
    for entry in items if isinstance(items, list) else []:
        if isinstance(entry, dict):
            projected.append({'queue_item_id': entry.get('queue_item_id'), 'name': entry.get('name'),
                              'duration': entry.get('duration'), 'media': _media(entry.get('media_item'))})
    head = {}
    if match:
        head = {k: match.get(k) for k in ('queue_id', 'display_name', 'state', 'shuffle_enabled', 'repeat_mode',
                                          'current_index', 'items') if k in match}
        head['current_item'] = _media((match.get('current_item') or {}).get('media_item')) if isinstance(
            match.get('current_item'), dict) else None
    return {'queue': head or {'queue_id': queue_id}, 'items': projected}


def search(ctx, args):
    types = args.get('media_types') or ['track', 'album', 'artist', 'playlist']
    value = ctx.clients.ma.call('music/search', {'search_query': args['query'], 'media_types': types,
                                                 'limit': args.get('limit', 10),
                                                 'library_only': args.get('library_only', False)})
    results = {}
    if isinstance(value, dict):
        for key, items in value.items():
            if isinstance(items, list):
                results[key] = [_media(i) | {'item_id': i.get('item_id'), 'provider': i.get('provider')}
                                for i in items[:args.get('limit', 10)] if isinstance(i, dict)]
    return {'query': args['query'], 'results': results}


def play_media(ctx, args):
    player_id = _require_player(ctx, args['player_id'])
    uri = args['uri']
    option = args.get('option', 'play')
    ctx.clients.ma.call('player_queues/play_media', {'queue_id': player_id, 'media': [uri], 'option': option,
                                                     'radio_mode': args.get('radio_mode', False)})
    return {'ok': True, 'player_id': player_id, 'uri': uri, 'option': option}


TRANSPORT = {'play': 'players/cmd/play', 'pause': 'players/cmd/pause', 'stop': 'players/cmd/stop',
             'next': 'players/cmd/next', 'previous': 'players/cmd/previous'}


def transport(ctx, args):
    player_id = _require_player(ctx, args['player_id'])
    command = args['command']
    ctx.clients.ma.call(TRANSPORT[command], {'player_id': player_id})
    return {'ok': True, 'player_id': player_id, 'command': command}


def set_volume(ctx, args):
    player_id = _require_player(ctx, args['player_id'])
    level = check_ma_volume(ctx.config.policy, args['level'])
    ctx.clients.ma.call('players/cmd/volume_set', {'player_id': player_id, 'volume_level': level})
    return {'ok': True, 'player_id': player_id, 'volume_level': level,
            'ceiling': ctx.config.policy.ma_volume_max}


def set_mute(ctx, args):
    player_id = _require_player(ctx, args['player_id'])
    ctx.clients.ma.call('players/cmd/volume_mute', {'player_id': player_id, 'muted': args['muted']})
    return {'ok': True, 'player_id': player_id, 'muted': args['muted']}


def group(ctx, args):
    target = _require_player(ctx, args['target_player_id'], 'target_player_id')
    members = [_require_player(ctx, p, 'member_player_ids') for p in args['member_player_ids']]
    if target in members:
        raise OpsError('invalid_argument', 'target_player_id must not be listed as a member')
    ctx.clients.ma.call('players/cmd/group_many', {'target_player': target, 'child_player_ids': members})
    return {'ok': True, 'target_player_id': target, 'member_player_ids': members}


def ungroup(ctx, args):
    player_id = _require_player(ctx, args['player_id'])
    ctx.clients.ma.call('players/cmd/ungroup', {'player_id': player_id})
    return {'ok': True, 'player_id': player_id}
