"""MCP resources and prompts (stdlib only; the transport adapts them to the SDK)."""
from . import dsp, health, room
from .errors import OpsError

RESOURCES = {
    'garage-audio://status': ('System status', 'Overall stack status (same as get_status).',
                              lambda ctx: health.overall_status(ctx)),
    'garage-audio://dsp/config': ('Current DSP config', 'Active CamillaDSP configuration with limit violations.',
                                  lambda ctx: dsp.active_config(ctx)),
    'garage-audio://measurements': ('Measurement history', 'Imported REW measurements (newest first).',
                                    lambda ctx: room.list_measurements(ctx, {})),
}


def list_resources():
    return [{'uri': uri, 'name': name, 'description': text, 'mimeType': 'application/json'}
            for uri, (name, text, _fn) in RESOURCES.items()]


def read_resource(ctx, uri):
    entry = RESOURCES.get(str(uri))
    if entry is None:
        raise OpsError('unknown_resource', f'unknown resource {uri}')
    try:
        return entry[2](ctx)
    except OpsError as error:
        return {'error': error.payload()}


PROMPTS = {
    'diagnose-garage-audio': {
        'title': 'Diagnose garage audio',
        'description': 'Step-by-step diagnosis of "no sound" or misbehaviour, read-only first.',
        'arguments': [{'name': 'symptom', 'description': 'What the listener noticed (optional).', 'required': False}],
        'text': (
            'Diagnose the garage audio stack. Symptom: {symptom}.\n'
            '1. Call diagnose and read its findings and next_steps.\n'
            '2. If the owner state is backoff, wait: the supervisor retries by itself. If terminal or exhausted, read '
            'get_recent_logs (errors_only=true) for the owner and explain the cause before any restart (a restart '
            'there needs force=true and should follow a fix).\n'
            '3. Check dsp_get_levels for signal and clipping, and ma_list_players for the player state and volume.\n'
            '4. Only if a restart is justified and writes are enabled, call plan_recovery, explain it, then '
            'recover_audio_stack once. Never loop restarts and never manage containers directly.\n'
            '5. Summarise in plain words: what is wrong, what you did, what the operator should do next.'),
    },
    'room-correction-walkthrough': {
        'title': 'Guided room correction walkthrough',
        'description': 'Measure, compare, propose, preview, apply and verify room correction safely.',
        'arguments': [{'name': 'channels', 'description': 'Channels to correct, e.g. "left,right,sub".',
                       'required': False}],
        'text': (
            'Guide the operator through room correction for channels: {channels}.\n'
            '1. Ask the operator to measure each channel in REW at the listening position (calibrated mic, '
            'loopback timing reference, same volume), then File > Export > Export measurement as text.\n'
            '2. room_import_measurement for each export with the right channel (and distance_m if known).\n'
            '3. room_compare_to_target; explain peaks (cuttable) versus dips (usually room nulls, leave them).\n'
            '4. room_propose_peq per channel (cut-only unless the operator explicitly accepts a small boost), and '
            'room_propose_alignment from impulse peaks (preferred) or distances, plus levels.\n'
            '5. room_preview_yaml and show the operator the filters. Check dsp_get_filters channel_map is right.\n'
            '6. Lower dsp main volume first, then room_apply_correction (needs allow_write). Note the backup_id.\n'
            '7. Re-measure. If worse, dsp_restore_backup with the backup_id. Keep notes in the measurement labels.\n'
            'See docs/ops-mcp-room-correction.md.'),
    },
    'listening-session': {
        'title': 'Set up a listening session',
        'description': 'Check health, pick music and set a safe volume.',
        'arguments': [{'name': 'request', 'description': 'What to play (optional).', 'required': False},
                      {'name': 'player', 'description': 'Player name or id (optional).', 'required': False}],
        'text': (
            'Set up a listening session. Request: {request}. Player: {player}.\n'
            '1. Call get_status; if not ready, run the diagnose-garage-audio steps instead.\n'
            '2. ma_list_players and pick the player (ask if ambiguous).\n'
            '3. ma_search for the request and confirm the choice with the operator.\n'
            '4. Playback needs allow_write and allow_playback. Set a modest volume first (ma_set_volume well below '
            'the cap), then ma_play_media. Check dsp_get_levels for clipping after a few seconds.\n'
            '5. If playback tools are disabled, tell the operator what to play and where, and stop there.'),
    },
}


def list_prompts():
    return [{'name': name, 'title': p['title'], 'description': p['description'], 'arguments': p['arguments']}
            for name, p in PROMPTS.items()]


def get_prompt(name, arguments=None):
    prompt = PROMPTS.get(name)
    if prompt is None:
        raise OpsError('unknown_prompt', f'unknown prompt {name}')
    arguments = arguments or {}
    allowed = {a['name'] for a in prompt['arguments']}
    if set(arguments) - allowed:
        raise OpsError('invalid_argument', f'unknown prompt argument(s) {sorted(set(arguments) - allowed)}')
    values = {key: str(arguments.get(key) or 'not specified')[:300] for key in allowed}
    return {'description': prompt['description'], 'text': prompt['text'].format(**values)}
