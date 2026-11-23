"""Tool registry and the server-side permission gate.

Gate model (documented in docs/ops-mcp.md): every tool is always *listed* so the
agent can see what exists, but disabled tools are marked in their description and
annotations and are refused by ``call`` before any handler or client runs.

Access levels:
- ``read``     observes only; always allowed.
- ``local``    writes only the server's private state directory (measurement history,
               proposals); never touches the audio system; always allowed.
- ``write``    changes the running system (recovery, DSP, pause/stop); needs allow_write.
- ``playback`` starts/changes audible playback or volume in Music Assistant; needs
               allow_write AND allow_playback.
"""
import logging
from dataclasses import dataclass
from typing import Callable

from . import dsp, health, music, recovery, room
from .config import CHANNELS
from .errors import OpsError
from .schema import validate

LOG = logging.getLogger('garage_audio.ops_mcp')

READ, LOCAL, WRITE, PLAYBACK = 'read', 'local', 'write', 'playback'


@dataclass(frozen=True)
class Tool:
    name: str
    access: str
    description: str
    schema: dict
    handler: Callable


def obj(properties=None, required=(), **extra):
    return {'type': 'object', 'properties': properties or {}, 'required': list(required),
            'additionalProperties': False, **extra}


NUM = {'type': 'number'}
REASON = {'type': 'string', 'maxLength': 200, 'description': 'Short reason, stored with the automatic backup.'}
PLAYER = {'type': 'string', 'minLength': 1, 'maxLength': 256, 'description': 'Music Assistant player_id.'}
CHANNEL = {'type': 'string', 'enum': list(CHANNELS)}
PER_CHANNEL = lambda low, high: obj({c: {'type': 'number', 'minimum': low, 'maximum': high} for c in CHANNELS},  # noqa: E731
                                    minProperties=2)
ID = {'type': 'string', 'pattern': r'^[0-9]{8}T[0-9]{6}\.[0-9]{6}Z-[0-9a-f]{8}$'}
TARGET = obj({'tilt_db_per_octave': {'type': 'number', 'minimum': -3, 'maximum': 3},
              'bass_shelf_db': {'type': 'number', 'minimum': -12, 'maximum': 12},
              'bass_shelf_hz': {'type': 'number', 'minimum': 20, 'maximum': 500}})
CORRECTION = {'measurement_id': ID,
              'target': TARGET,
              'freq_min_hz': {'type': 'number', 'minimum': 20, 'maximum': 20000},
              'freq_max_hz': {'type': 'number', 'minimum': 20, 'maximum': 20000},
              'smoothing_octaves': {'type': 'number', 'minimum': 0, 'maximum': 1},
              'tolerance_db': {'type': 'number', 'minimum': 0.1, 'maximum': 10}}

TOOLS = [
    # 1 Health -----------------------------------------------------------------------
    Tool('get_status', READ, 'Overall stack status: owner recovery state (audio_recovery status), service-manager unit '
         'state, container states, Music Assistant and CamillaDSP reachability, and whether writes are enabled.',
         obj(), lambda ctx, a: health.overall_status(ctx)),
    Tool('get_supervisor_state', READ, 'Owner (supervisor) recovery record plus the service manager unit state.',
         obj(), lambda ctx, a: {'recovery': health.recovery_status(ctx), 'unit': health.unit_state(ctx)}),
    Tool('get_heartbeat', READ, 'Guard heartbeat age and tolerance for the newest generation (diagnostic only).',
         obj(), lambda ctx, a: health.heartbeat(ctx)),
    Tool('get_containers', READ, 'Read-only container states (docker ps/inspect) for the configured label/names.',
         obj(), lambda ctx, a: health.containers(ctx)),
    Tool('check_music_assistant', READ, 'Is the Music Assistant server reachable at the configured URL?',
         obj(), lambda ctx, a: health.ma_reachability(ctx)),
    Tool('get_recent_logs', READ, 'Recent log lines (bounded, credential-redacted) from the owner unit or a '
         'configured unit/container. errors_only keeps error-like lines.',
         obj({'source': {'type': 'string', 'maxLength': 140, 'description': '"owner" (default) or a configured '
                         'logs.units / logs.containers name.'},
              'lines': {'type': 'integer', 'minimum': 1, 'maximum': 2000},
              'since': {'type': 'string', 'maxLength': 40, 'description': 'e.g. "10 min ago", "today".'},
              'errors_only': {'type': 'boolean'}}),
         health.recent_logs),
    Tool('diagnose', READ, 'Plain-words diagnosis of the stack with suggested next steps, built from the read-only '
         'probes.', obj(), lambda ctx, a: health.diagnose(ctx)),
    Tool('get_ops_config', READ, 'Effective ops-server configuration and safety limits (no credential values).',
         obj(), lambda ctx, a: ctx.config.public()),
    # 2 Recovery ---------------------------------------------------------------------
    Tool('plan_recovery', READ, 'Dry run: what recover_audio_stack would do now and whether it would be refused.',
         obj({'action': {'type': 'string', 'enum': ['auto', 'start', 'restart']}, 'force': {'type': 'boolean'}}),
         lambda ctx, a: recovery.plan(ctx, a.get('action', 'auto'), a.get('force', False))),
    Tool('recover_audio_stack', WRITE, "Start or restart the owner unit through the service manager (the supervisor's "
         'sanctioned recovery path; never touches containers). Refused during backoff/starting or an in-progress '
         'job unless force=true; rate-limited. terminal/exhausted are explained: fix the cause first.',
         obj({'action': {'type': 'string', 'enum': ['auto', 'start', 'restart']}, 'force': {'type': 'boolean'}}),
         recovery.recover),
    # 3 Music Assistant --------------------------------------------------------------
    Tool('ma_list_players', READ, 'Music Assistant players with state, volume and current media.', obj(),
         music.players),
    Tool('ma_get_queue', READ, 'Queue of a player (current item and upcoming items).',
         obj({'player_id': PLAYER, 'limit': {'type': 'integer', 'minimum': 1, 'maximum': 100},
              'offset': {'type': 'integer', 'minimum': 0, 'maximum': 100000}}, ['player_id']), music.queue),
    Tool('ma_search', READ, 'Search the Music Assistant library and providers.',
         obj({'query': {'type': 'string', 'minLength': 1, 'maxLength': 200},
              'media_types': {'type': 'array', 'items': {'type': 'string', 'enum': list(music.MEDIA_TYPES)},
                              'maxItems': 5, 'uniqueItems': True},
              'limit': {'type': 'integer', 'minimum': 1, 'maximum': 50}, 'library_only': {'type': 'boolean'}},
             ['query']), music.search),
    Tool('ma_play_media', PLAYBACK, 'Play a media URI (from ma_search) on a player. Starts audible playback.',
         obj({'player_id': PLAYER, 'uri': {'type': 'string', 'minLength': 1, 'maxLength': 1024},
              'option': {'type': 'string', 'enum': ['play', 'replace', 'next', 'add', 'replace_next']},
              'radio_mode': {'type': 'boolean'}}, ['player_id', 'uri']), music.play_media),
    Tool('ma_player_command', PLAYBACK, 'Resume, skip to next or previous track on a player.',
         obj({'player_id': PLAYER, 'command': {'type': 'string', 'enum': ['play', 'next', 'previous']}},
             ['player_id', 'command']), music.transport),
    Tool('ma_stop_playback', WRITE, 'Pause or stop a player (reduces output; needs allow_write only).',
         obj({'player_id': PLAYER, 'command': {'type': 'string', 'enum': ['pause', 'stop']}},
             ['player_id', 'command']), music.transport),
    Tool('ma_set_volume', PLAYBACK, 'Set a player volume 0-100, capped at limits.ma_volume_max.',
         obj({'player_id': PLAYER, 'level': {'type': 'integer', 'minimum': 0, 'maximum': 100}},
             ['player_id', 'level']), music.set_volume),
    Tool('ma_set_mute', PLAYBACK, 'Mute or unmute a player.',
         obj({'player_id': PLAYER, 'muted': {'type': 'boolean'}}, ['player_id', 'muted']), music.set_mute),
    Tool('ma_group_players', PLAYBACK, 'Group (sync) member players to a target player.',
         obj({'target_player_id': PLAYER, 'member_player_ids': {'type': 'array', 'items': PLAYER, 'minItems': 1,
                                                                'maxItems': 16, 'uniqueItems': True}},
             ['target_player_id', 'member_player_ids']), music.group),
    Tool('ma_ungroup_player', PLAYBACK, 'Remove a player from its group.', obj({'player_id': PLAYER}, ['player_id']),
         music.ungroup),
    # 4 CamillaDSP -------------------------------------------------------------------
    Tool('dsp_get_config', READ, 'Active CamillaDSP configuration and any existing limit violations.', obj(),
         dsp.active_config),
    Tool('dsp_get_filters', READ, 'CamillaDSP filters, mixers and pipeline summary with the left/right/sub channel map.',
         obj(), dsp.filters_and_pipeline),
    Tool('dsp_get_levels', READ, 'Signal levels (dBFS), peaks, clipped-sample count, load, main volume and mute.',
         obj(), dsp.levels),
    Tool('dsp_set_main_volume', WRITE, 'Set CamillaDSP main volume in dB (never above limits.volume_ceiling_db). '
         'Backs up first.', obj({'volume_db': {'type': 'number', 'minimum': -100, 'maximum': 0}, 'reason': REASON},
                                ['volume_db']), dsp.set_main_volume),
    Tool('dsp_set_mute', WRITE, 'Mute or unmute CamillaDSP main output. Muting needs allow_write; unmuting '
         'also needs allow_playback (it can make sound). Backs up first.',
         obj({'muted': {'type': 'boolean'}, 'reason': REASON}, ['muted']), dsp.set_main_mute),
    Tool('dsp_update_peq', WRITE, 'Change freq/Q/gain of a Biquad Peaking filter, or create one on a mapped channel. '
         'Validated against hard and configured limits; backup first.',
         obj({'filter': {'type': 'string', 'pattern': r'^[A-Za-z0-9_-]{1,64}$'}, 'channel': CHANNEL,
              'freq': NUM, 'q': NUM, 'gain': NUM, 'reason': REASON}, ['filter']), dsp.update_peq),
    Tool('dsp_set_channel_delay', WRITE, 'Set delay (ms) on left/right/sub via an ops_<channel>_delay filter. Backup first.',
         obj({'channel': CHANNEL, 'delay_ms': NUM, 'reason': REASON}, ['channel', 'delay_ms']), dsp.set_delay),
    Tool('dsp_set_channel_gain', WRITE, 'Set level trim (dB) on left/right/sub via an ops_<channel>_gain filter. '
         'Backup first.', obj({'channel': CHANNEL, 'gain_db': NUM, 'reason': REASON}, ['channel', 'gain_db']),
         dsp.set_channel_gain),
    Tool('dsp_list_backups', READ, 'Automatic DSP backups (newest first).',
         obj({'limit': {'type': 'integer', 'minimum': 1, 'maximum': 200}}), dsp.list_backups),
    Tool('dsp_restore_backup', WRITE, 'Restore a DSP backup. The restore is validated against limits and itself backed '
         'up first; a backup with unbounded filter types or values outside the hard caps is refused. '
         'restore_volume also restores main volume/mute (ceiling enforced; an unmuted '
         'backup is only unmuted when allow_playback is on).',
         obj({'backup_id': ID, 'restore_volume': {'type': 'boolean'}}, ['backup_id']), dsp.restore_backup),
    # 5 Room correction --------------------------------------------------------------
    Tool('room_import_measurement', LOCAL, 'Import a REW frequency-response text export (File > Export > Measurement '
         'as text) for a channel into the measurement history.',
         obj({'text': {'type': 'string', 'minLength': 1, 'maxLength': 262144}, 'channel': CHANNEL,
              'label': {'type': 'string', 'maxLength': 100}, 'distance_m': {'type': 'number', 'minimum': 0.1,
                                                                            'maximum': 30},
              'notes': {'type': 'string', 'maxLength': 500}}, ['text', 'channel']), room.import_measurement),
    Tool('room_list_measurements', READ, 'Measurement history (newest first).',
         obj({'limit': {'type': 'integer', 'minimum': 1, 'maximum': 500}}), room.list_measurements),
    Tool('room_compare_to_target', READ, 'Compare a measurement to a target curve (flat by default; optional tilt and '
         'bass shelf). Returns the deviation curve and worst deviations.',
         obj(CORRECTION, ['measurement_id']), room.compare_to_target),
    Tool('room_propose_peq', LOCAL, 'Propose cut-biased PEQ filters for a measurement (bounded boost, filter count and '
         'range). Stored as a proposal; does not change DSP.',
         obj({**CORRECTION, 'channel': CHANNEL, 'max_filters': {'type': 'integer', 'minimum': 1, 'maximum': 10},
              'max_boost_db': {'type': 'number', 'minimum': 0, 'maximum': 6}}, ['measurement_id']), room.propose_peq),
    Tool('room_propose_alignment', LOCAL, 'Propose mains+sub delay and level alignment from distances, impulse peak '
         'times and/or measured levels. Stored as a proposal; does not change DSP.',
         obj({'distances_m': PER_CHANNEL(0.1, 30), 'impulse_peaks_ms': PER_CHANNEL(-1000, 1000),
              'levels_db': PER_CHANNEL(-200, 200)}), room.propose_alignment),
    Tool('room_preview_yaml', READ, 'CamillaDSP YAML fragment for a proposal (filters and pipeline steps).',
         obj({'proposal_id': ID}, ['proposal_id']), room.preview_yaml),
    Tool('room_apply_correction', WRITE, 'Apply a stored proposal to CamillaDSP (re-validated; backup first; replaces '
         'earlier ops_rc_* filters for that channel).', obj({'proposal_id': ID}, ['proposal_id']), room.apply),
]
BY_NAME = {tool.name: tool for tool in TOOLS}
assert len(BY_NAME) == len(TOOLS)


def enabled(config, tool):
    if tool.access in (READ, LOCAL):
        return True
    if tool.access == WRITE:
        return config.allow_write
    return config.allow_write and config.allow_playback


def gate_message(tool):
    if tool.access == PLAYBACK:
        return 'disabled: needs allow_write=true and allow_playback=true in the ops server configuration'
    return 'disabled: needs allow_write=true in the ops server configuration'


def describe(config):
    """Tool descriptors for list_tools. Disabled tools stay listed but say so."""
    out = []
    for tool in TOOLS:
        on = enabled(config, tool)
        prefix = {READ: '[read-only] ', LOCAL: '[local state only] ', WRITE: '[WRITE] ', PLAYBACK: '[PLAYBACK] '}
        description = prefix[tool.access] + tool.description
        if not on:
            description += f' ({gate_message(tool)}; calls are refused server-side)'
        out.append({'name': tool.name, 'description': description, 'inputSchema': tool.schema,
                    'access': tool.access, 'enabled': on,
                    'annotations': {'readOnlyHint': tool.access in (READ, LOCAL),
                                    'destructiveHint': tool.access in (WRITE, PLAYBACK),
                                    'idempotentHint': tool.access == READ, 'openWorldHint': False}})
    return out


def call(ctx, name, arguments):
    """Validate, gate and run one tool. Returns a JSON-serializable result or raises OpsError."""
    tool = BY_NAME.get(name)
    if tool is None:
        raise OpsError('unknown_tool', f'unknown tool {name!r}')
    args = {} if arguments is None else arguments
    if not enabled(ctx.config, tool):
        LOG.info('refused disabled tool %s', name)
        raise OpsError('write_disabled', f'{name} is {gate_message(tool)}', tool=name, access=tool.access)
    validate(tool.schema, args)
    try:
        result = tool.handler(ctx, args)
    except OpsError as error:
        if tool.access in (WRITE, PLAYBACK):
            LOG.info('write tool %s failed: %s', name, error.code)
        raise
    except Exception as error:  # never leak raw exception text to the agent
        LOG.exception('tool %s failed unexpectedly', name)
        raise OpsError('internal', f'{name} failed unexpectedly ({type(error).__name__}); see the server log') from None
    if tool.access in (WRITE, PLAYBACK):
        LOG.info('write tool %s ok', name)
    return result
