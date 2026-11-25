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
- ``measure``  plays measurement signals through the stack / records the measurement
               microphone; needs allow_write AND allow_measure.
- ``stop``     measure_abort: only stops what this server started; always allowed.
"""
import logging
from dataclasses import dataclass
from typing import Callable

from . import dsp, health, measure, measure_cal, measure_session, music, recovery, room, room_model
from .config import CHANNELS, MEASURE_CHANNELS, SMOOTHING
from .errors import OpsError
from .schema import validate

LOG = logging.getLogger('garage_audio.ops_mcp')

READ, LOCAL, WRITE, PLAYBACK, MEASURE, STOP = 'read', 'local', 'write', 'playback', 'measure', 'stop'


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
MCHANNEL = {'type': 'string', 'enum': list(MEASURE_CHANNELS)}
UNITS = {'type': 'string', 'enum': list(room_model.UNITS), 'description': 'Length unit of the values (default m).'}
LEN = {'type': 'number', 'minimum': 0, 'maximum': 5000}  # any unit; metric bounds are checked after conversion
POINT = obj({'x': LEN, 'y': LEN, 'z': LEN})
DIMS = obj({'length': LEN, 'width': LEN, 'height': LEN})
LISTEN_NAMES = ('main',) + tuple(f'seat{i}' for i in range(2, 9))
LISTEN = {'type': 'string', 'enum': list(LISTEN_NAMES)}
TEXT = {'type': 'string', 'maxLength': 500}
PROFILE = obj({
    'dimensions_m': DIMS,
    'shape': TEXT, 'notes': {'type': 'string', 'maxLength': 2000},
    'speakers': obj({name: POINT for name in room_model.SPEAKERS}),
    'listening_positions': obj({name: obj({'x': LEN, 'y': LEN, 'ear_height': LEN, 'label': TEXT})
                                for name in LISTEN_NAMES}),
    'seating': obj({'width': LEN, 'depth': LEN}),
    'obstacles': {'type': 'array', 'items': TEXT, 'maxItems': 20}})
AVERAGE = {'type': 'string', 'enum': ['power', 'vector']}
MIC_MODEL = {'type': 'string', 'pattern': r'^[A-Za-z0-9][A-Za-z0-9 ._-]{0,39}$',
             'description': 'Microphone model, e.g. UMIK-1 or UMIK-2 (other models: provider "url" or an import).'}
MIC_SERIAL = {'type': 'string', 'pattern': measure_cal.SERIAL_PATTERN,
              'description': 'Serial number printed on the microphone, XXX-XXXX (7 digits).'}
PLAN = {'positions': {'type': 'integer', 'minimum': 4, 'maximum': 8},
        'radius_m': {'type': 'number', 'minimum': 0.1, 'maximum': 1.5},
        'ear_height_m': {'type': 'number', 'minimum': 0.3, 'maximum': 2.5},
        'listening_position': LISTEN, 'mic_orientation': {'type': 'string', 'enum': ['90', '0']}}

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
         'sanctioned recovery path; never touches containers). Refused during backoff/starting, an in-progress '
         'job or terminal/exhausted (fix the cause first) unless force=true; rate-limited even with force.',
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
         'up first; a backup with unbounded filters, processors or pipeline steps, or values outside '
         'the hard caps, is refused. '
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
    Tool('room_propose_alignment', LOCAL, 'Propose mains+sub delay and level alignment from measure_sweep results '
         '(measurement_ids: arrival times, DSP delays active while measuring removed), distances, impulse peak '
         'times and/or measured levels. Stored as a proposal; does not change DSP.',
         obj({'measurement_ids': {'type': 'array', 'items': ID, 'minItems': 2, 'maxItems': 3, 'uniqueItems': True},
              'distances_m': PER_CHANNEL(0.1, 30), 'impulse_peaks_ms': PER_CHANNEL(-1000, 1000),
              'levels_db': PER_CHANNEL(-200, 200)}), room.propose_alignment),
    Tool('room_preview_yaml', READ, 'CamillaDSP YAML fragment for a proposal (filters and pipeline steps).',
         obj({'proposal_id': ID}, ['proposal_id']), room.preview_yaml),
    Tool('room_apply_correction', WRITE, 'Apply a stored proposal to CamillaDSP (re-validated; backup first; replaces '
         'earlier ops_rc_* filters for that channel).', obj({'proposal_id': ID}, ['proposal_id']), room.apply),
    # 6 Room model and measurement plan ---------------------------------------------
    Tool('room_profile_get', READ, 'The stored room profile (dimensions, speakers, listening positions, seating, '
         'obstacles, notes; metres) and which fields are still missing.', obj(), room_model.get_profile),
    Tool('room_profile_update', LOCAL, 'Partially update the room profile: objects in "set" are merged (send only '
         'what changed), lists/text replace; "remove" deletes fields (e.g. "speakers.sub2"). Lengths in "units". '
         'x from the left side wall, y from the front (speaker) wall, z / ear_height from the floor.',
         obj({'set': PROFILE, 'remove': {'type': 'array', 'maxItems': 20,
                                         'items': {'type': 'string', 'pattern': r'^[a-z_]{1,32}(\.[a-z0-9_]{1,32})?$'}},
              'units': UNITS}), room_model.update_profile),
    Tool('room_modes', READ, 'Axial room modes, expected problem frequencies (stacked modes, gaps), how strongly each '
         'mode couples at the seat and the sub, and simple placement advice. Uses the stored profile; '
         'dimensions_m overrides it for a what-if.',
         obj({'dimensions_m': DIMS, 'units': UNITS, 'max_hz': {'type': 'number', 'minimum': 50, 'maximum': 500},
              'listening_position': LISTEN}), room_model.modes_tool),
    Tool('measure_plan', READ, 'Preview the microphone plan: main seat plus 4-8 nearby positions around it at ear '
         'height, each described in plain words (metric and imperial), with the mic orientation.',
         obj(PLAN), measure_session.plan_tool),
    # 7 Measurement ------------------------------------------------------------------
    Tool('measure_status', READ, 'Measurement readiness: measurement extra installed, backend, player/recorder '
         'configured, channels, loopback/timing reference, level limits, confirmed levels, running measurement.',
         obj(), measure.status),
    Tool('measure_list_devices', READ, 'List capture devices via the configured read-only commands (e.g. arecord -l, '
         'pw-record --list-targets).', obj(), measure.list_devices),
    Tool('measure_import_calibration', LOCAL, 'Import a microphone calibration file (UMIK/miniDSP text with "Sens '
         'Factor" header, or generic "freq dB" lines; 0- or 90-degree variant). Give exactly one of: text (the file '
         'contents), path (a file name inside the operator-configured measurement.calibration.import_dir; nothing '
         'else on the server is readable), or calibration_id + orientation (activate a stored calibration). A '
         'calibration with a known orientation becomes the active one for it (activate=false to only store it) and '
         'is then applied automatically to measurements with the mic in that orientation.',
         obj({'text': {'type': 'string', 'minLength': 1, 'maxLength': 262144},
              'path': {'type': 'string', 'minLength': 1, 'maxLength': 255},
              'calibration_id': ID,
              'name': {'type': 'string', 'minLength': 1, 'maxLength': 100},
              'orientation': {'type': 'string', 'enum': ['0', '90']},
              'model': MIC_MODEL, 'serial': MIC_SERIAL, 'activate': {'type': 'boolean'}}),
         measure.import_calibration),
    Tool('measure_list_calibrations', READ, 'Stored microphone calibrations (newest first) with model, serial, '
         'provider and which orientation each is active for.', obj(), measure.list_calibrations),
    Tool('measure_calibration_status', READ, 'Which microphone calibration is active for 0 and 90 degrees (with its '
         'provenance), whether the active measurement session uses the calibration matching its mic orientation, '
         'which download providers are enabled, and the next step when none is installed.', obj(),
         measure_cal.status),
    Tool('measure_mic_detect', READ, 'Detect the measurement microphone (read-only: USB device strings in /sys, '
         'ALSA cards in /proc/asound, the configured device listing). Reports the model (UMIK-1 / UMIK-2 / unknown) '
         'and a USB serial only as a candidate to confirm with the user; otherwise asks for the serial printed on '
         'the mic.', obj({'include_device_listing': {'type': 'boolean'}}), measure_cal.detect),
    Tool('measure_calibration_fetch', WRITE, 'Download the per-serial calibration file for the user\'s microphone '
         'and install it (validated, stored with provenance, activated per orientation). provider "minidsp" '
         '(UMIK-1/UMIK-2; default for those models) submits miniDSP\'s public serial form once; its success reply is '
         'UNVERIFIED against a real serial. provider "url" (operator-enabled hosts only) downloads one https file. '
         'One request per call, no retries, rate-limited. Needs allow_write (not allow_measure). If it fails or is '
         'disabled, follow the returned manual_steps and use measure_import_calibration.',
         obj({'model': MIC_MODEL, 'serial': MIC_SERIAL,
              'orientation': {'type': 'string', 'enum': ['0', '90', 'both']},
              'provider': {'type': 'string', 'enum': sorted(measure_cal.PROVIDERS)},
              'url': {'type': 'string', 'minLength': 9, 'maxLength': 2048, 'pattern': r'^https://\S+$'},
              'activate': {'type': 'boolean'}}, ['model']), measure_cal.fetch),
    Tool('measure_get', READ, 'One stored measurement: settings, timing (source/confidence), quality, octave '
         'summary, exported files (.frd, REW .txt, impulse WAV); include_points for the full curve.',
         obj({'measurement_id': ID, 'include_points': {'type': 'boolean'}}, ['measurement_id']),
         measure.get_measurement),
    Tool('measure_noise_check', MEASURE, 'Record ~3 s of background noise from the measurement mic (plays nothing) '
         'and judge it: ok / too_noisy / suspiciously_silent. With session_id it advances the session; '
         'accept_noisy=true only if the user accepts a noisy room.',
         obj({'session_id': ID, 'seconds': {'type': 'number', 'minimum': 1, 'maximum': 10},
              'accept_noisy': {'type': 'boolean'}}), measure_session.noise_check_tool),
    Tool('measure_level_check', MEASURE, 'AUDIBLE: short test tones on one channel, starting gentle and rising in '
         'capped steps until the target SNR or the mic headroom is reached (never above the level cap). The '
         'confirmed level expires (limits.level_ttl_seconds) and is bound to the main volume, mute, routing, '
         'capture device and calibration_id (default: the session\'s). Tell the user first; user_confirmed=true '
         'only after they agreed.',
         obj({'channel': MCHANNEL, 'session_id': ID, 'user_confirmed': {'type': 'boolean'},
              'start_level_dbfs': {'type': 'number', 'minimum': -90, 'maximum': 0},
              'calibration_id': ID}, ['channel', 'user_confirmed']),
         measure_session.level_check_tool),
    Tool('measure_sweep', MEASURE, 'AUDIBLE: one log sweep on one channel through the stack (CamillaDSP limits '
         'apply), analysed (response, phase, group delay, arrival time, SNR, clipping), quality-checked and stored '
         'with REW-importable exports. Level is limited to the level-check result. Tell the user first; '
         'user_confirmed=true only after they agreed.',
         obj({'channel': MCHANNEL, 'session_id': ID, 'user_confirmed': {'type': 'boolean'},
              'level_dbfs': {'type': 'number', 'minimum': -90, 'maximum': 0},
              'freq_min_hz': {'type': 'number', 'minimum': 5, 'maximum': 24000},
              'freq_max_hz': {'type': 'number', 'minimum': 5, 'maximum': 24000},
              'sweep_seconds': {'type': 'number', 'minimum': 1, 'maximum': 60},
              'smoothing': {'type': 'string', 'enum': list(SMOOTHING)},
              'calibration_id': ID, 'label': {'type': 'string', 'maxLength': 100}},
             ['channel', 'user_confirmed']), measure_session.sweep_tool),
    Tool('measure_abort', STOP, 'Stop the running measurement now (only the player/recorder this server started). '
         'Always allowed.', obj(), measure.abort),
    Tool('measure_average', LOCAL, 'Average 2-16 stored measurements (power = level average, position independent; '
         'vector = complex average of peak-referenced phase; refused unless every input has high-confidence '
         'loopback timing, or allow_low_confidence=true, which flags the result) into a new history measurement.',
         obj({'measurement_ids': {'type': 'array', 'items': ID, 'minItems': 2, 'maxItems': 16, 'uniqueItems': True},
              'method': AVERAGE, 'channel': MCHANNEL, 'label': {'type': 'string', 'maxLength': 100},
              'allow_low_confidence': {'type': 'boolean'}},
             ['measurement_ids']), measure_session.average_tool),
    # 8 Guided measurement session ---------------------------------------------------
    Tool('measure_session_start', LOCAL, 'Start a guided multi-position measurement session from the room profile '
         '(resumable; persisted). Returns the plan and the first step. Plays nothing.',
         obj({**PLAN, 'channels': {'type': 'array', 'items': MCHANNEL, 'minItems': 1, 'maxItems': 4,
                                   'uniqueItems': True},
              'calibration_id': ID, 'average': AVERAGE, 'replace': {'type': 'boolean'}}),
         measure_session.start),
    Tool('measure_session_status', READ, 'Progress of a measurement session (default: the active or latest one) '
         'and the exact next step.', obj({'session_id': ID, 'include_plan': {'type': 'boolean'}}),
         measure_session.status),
    Tool('measure_session_confirm_placement', LOCAL, 'Record that the USER confirmed the mic is placed at the '
         'current position. Call only after the user said so.',
         obj({'session_id': ID, 'position_id': {'type': 'string', 'pattern': r'^P[0-9]$'}},
             ['session_id', 'position_id']), measure_session.confirm_placement),
    Tool('measure_session_advance', LOCAL, 'After the quality check: next position, redo one channel, redo the '
         'position, or skip a nearby position.',
         obj({'session_id': ID, 'action': {'type': 'string',
                                           'enum': ['next', 'redo_channel', 'redo_position', 'skip_position']},
              'channel': MCHANNEL, 'accept_quality': {'type': 'boolean'}}, ['session_id', 'action']),
         measure_session.advance),
    Tool('measure_session_finish', LOCAL, 'Average the completed positions per channel (power or vector), store '
         'the averages in the measurement history and hand off to room_compare_to_target / room_propose_peq.',
         obj({'session_id': ID, 'method': AVERAGE, 'allow_partial': {'type': 'boolean'},
              'allow_low_confidence': {'type': 'boolean'}}, ['session_id']),
         measure_session.finish),
    Tool('measure_session_cancel', LOCAL, 'Cancel a session (aborts its running measurement; keeps stored '
         'measurements).', obj({'session_id': ID}, ['session_id']), measure_session.cancel),
]
BY_NAME = {tool.name: tool for tool in TOOLS}
assert len(BY_NAME) == len(TOOLS)
OPEN_WORLD = frozenset({'measure_calibration_fetch'})  # the only tool that talks to the internet


def enabled(config, tool):
    if tool.access in (READ, LOCAL, STOP):
        return True
    if tool.access == WRITE:
        return config.allow_write
    if tool.access == MEASURE:
        return config.allow_write and config.allow_measure
    return config.allow_write and config.allow_playback


def gate_message(tool):
    if tool.access == PLAYBACK:
        return 'disabled: needs allow_write=true and allow_playback=true in the ops server configuration'
    if tool.access == MEASURE:
        return 'disabled: needs allow_write=true and allow_measure=true in the ops server configuration'
    return 'disabled: needs allow_write=true in the ops server configuration'


def describe(config):
    """Tool descriptors for list_tools. Disabled tools stay listed but say so."""
    out = []
    for tool in TOOLS:
        on = enabled(config, tool)
        prefix = {READ: '[read-only] ', LOCAL: '[local state only] ', WRITE: '[WRITE] ', PLAYBACK: '[PLAYBACK] ',
                  MEASURE: '[MEASURE] ', STOP: '[STOP] '}
        description = prefix[tool.access] + tool.description
        if not on:
            description += f' ({gate_message(tool)}; calls are refused server-side)'
        out.append({'name': tool.name, 'description': description, 'inputSchema': tool.schema,
                    'access': tool.access, 'enabled': on,
                    'annotations': {'readOnlyHint': tool.access in (READ, LOCAL),
                                    'destructiveHint': tool.access in (WRITE, PLAYBACK, MEASURE),
                                    'idempotentHint': tool.access == READ,
                                    'openWorldHint': tool.name in OPEN_WORLD}})
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
        if tool.access in (WRITE, PLAYBACK, MEASURE, STOP):
            LOG.info('write tool %s failed: %s', name, error.code)
        raise
    except Exception as error:  # never leak raw exception text to the agent
        LOG.exception('tool %s failed unexpectedly', name)
        raise OpsError('internal', f'{name} failed unexpectedly ({type(error).__name__}); see the server log') from None
    if tool.access in (WRITE, PLAYBACK, MEASURE, STOP):
        LOG.info('write tool %s ok', name)
    return result
