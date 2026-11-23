"""MCP resources and prompts (stdlib only; the transport adapts them to the SDK)."""
from . import dsp, health, measure_session, room, room_model
from .errors import OpsError


def _plan(ctx):
    view = measure_session.status(ctx, {'include_plan': True})
    if view.get('status') == 'active':
        return {'session_id': view['session_id'], 'plan': view['plan']}
    return {'session_id': None, 'plan': measure_session.plan_tool(ctx, {}),
            'note': 'preview from the room profile; no active measurement session'}


RESOURCES = {
    'garage-audio://status': ('System status', 'Overall stack status (same as get_status).',
                              lambda ctx: health.overall_status(ctx)),
    'garage-audio://dsp/config': ('Current DSP config', 'Active CamillaDSP configuration with limit violations.',
                                  lambda ctx: dsp.active_config(ctx)),
    'garage-audio://measurements': ('Measurement history', 'Imported and measured responses (newest first).',
                                    lambda ctx: room.list_measurements(ctx, {})),
    'garage-audio://room/profile': ('Room profile', 'Room dimensions, speaker/sub/listening positions and notes '
                                    '(metres), plus missing fields.', lambda ctx: room_model.get_profile(ctx, {})),
    'garage-audio://measure/plan': ('Measurement plan', 'Mic positions of the active session (or a preview from '
                                    'the room profile).', _plan),
    'garage-audio://measure/session': ('Measurement session progress', 'Phase, per-position progress and the next '
                                       'step of the active or latest session.',
                                       lambda ctx: measure_session.status(ctx, {})),
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
    'guided-room-measurement': {
        'title': 'Guided room measurement',
        'description': 'Measure the room with the built-in engine: room details, mic placement, noise check, '
                       'level check, sweeps per position, averaging, then the EQ tools. Never sweeps without '
                       'the user\'s confirmation.',
        'arguments': [{'name': 'channels', 'description': 'Channels to measure, e.g. "left,right,sub".',
                       'required': False},
                      {'name': 'units', 'description': 'Preferred units: metric or imperial.', 'required': False}],
        'text': (
            'Guide the user through a room measurement. Channels: {channels}. Preferred units: {units}.\n'
            'Rules: every audible step (measure_level_check, measure_sweep) needs the user\'s explicit go-ahead '
            'right before it; pass user_confirmed=true only then. Never assume the mic is placed: wait for the '
            'user to say so. If the user says stop, call measure_abort immediately. Speak plainly and give one '
            'instruction at a time.\n'
            '1. measure_status: if the measurement extra is missing, measure tools are disabled, or the player/'
            'recorder are not configured, explain what the operator must change and stop. measure_list_devices '
            'and measure_list_calibrations help them configure the mic.\n'
            '2. room_profile_get. Ask for the missing room details one or two at a time (length, width, ceiling '
            'height, where the speakers and sub are, where the main seat is, ear height, seating width, big '
            'obstacles) and store each answer with room_profile_update (units as the user gives them).\n'
            '3. room_modes: explain the expected problem frequencies and any placement advice in plain words; '
            'offer to move the seat or sub before measuring.\n'
            '4. If the user has a calibration file, measure_import_calibration (pick the 90-degree file for a mic '
            'pointing up).\n'
            '5. measure_session_start (with calibration_id). Read out the first position exactly as described '
            '(metric or imperial per the user), the mic orientation, and ask them to confirm when it is in place.\n'
            '6. For each position: after the user confirms, measure_session_confirm_placement; ask for quiet and '
            'measure_noise_check with session_id (if too noisy, explain and repeat); for each channel without a '
            'confirmed level, warn the user and after their go-ahead run measure_level_check; then for each '
            'channel warn the user a sweep will play and after their go-ahead run measure_sweep with session_id. '
            'Explain the quality result; redo or advance with measure_session_advance as measure_session_status '
            'says.\n'
            '7. measure_session_finish; then room_compare_to_target and room_propose_peq on the averaged '
            'measurements, room_propose_alignment with the main-position measurement_ids, room_preview_yaml, and '
            'only with the user\'s agreement room_apply_correction (backup first). Offer a verification sweep '
            'afterwards.\n'
            'See docs/ops-mcp-measurement.md.'),
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
