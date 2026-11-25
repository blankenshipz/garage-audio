"""CamillaDSP tools: read config/levels, bounded volume/mute and filter edits, backups and restore.

Every change goes through ``apply_config``: under the cross-process write lock it
reads the active config, builds the new one, rejects any *new* speaker-protection
violation, asks CamillaDSP to validate it, saves a backup of the config being
replaced (atomic, timestamped, bounded retention), applies it and reads it back.
"""
import copy
import re

from . import safety
from .errors import OpsError, SafetyError

NAME_PATTERN = re.compile(r'^[A-Za-z0-9_-]{1,64}$')
OPS_PREFIX = 'ops_'


def channel_index(ctx, channel):
    index = ctx.config.channels.get(channel)
    if index is None:
        raise OpsError('channel_not_mapped', f'camilladsp.channels.{channel} is not configured; map left/right/sub '
                       'to pipeline output channel indexes first')
    return index


def _step_channels(step):
    if isinstance(step.get('channels'), list):
        return step['channels']
    if isinstance(step.get('channel'), int):
        return [step['channel']]
    return None


def summarize(config):
    filters = config.get('filters') or {}
    pipeline = config.get('pipeline') or []
    devices = config.get('devices') or {}
    return {
        'title': config.get('title'), 'samplerate': devices.get('samplerate'),
        'capture_channels': (devices.get('capture') or {}).get('channels'),
        'playback_channels': (devices.get('playback') or {}).get('channels'),
        'filters': {name: {'type': f.get('type'), 'parameters': f.get('parameters')} for name, f in filters.items()
                    if isinstance(f, dict)},
        'mixers': config.get('mixers') or {},
        'pipeline': pipeline,
    }


def active_config(ctx, _args=None):
    config = ctx.clients.camilla.get_config()
    return {'config': config, 'limit_violations': safety.audit_config(ctx.config.policy, config),
            'channel_map': ctx.config.channels}


def filters_and_pipeline(ctx, _args=None):
    config = ctx.clients.camilla.get_config()
    value = summarize(config)
    value['channel_map'] = ctx.config.channels
    value['limit_violations'] = safety.audit_config(ctx.config.policy, config)
    return value


def _try(fn):
    try:
        return fn()
    except OpsError as error:
        return {'error': error.payload()}


def levels(ctx, _args=None):
    cam = ctx.clients.camilla
    return {'state': _try(cam.get_state), 'signal_levels': _try(cam.get_signal_levels),
            'peaks_since_start': _try(cam.get_peaks_since_start), 'clipped_samples': _try(cam.get_clipped_samples),
            'processing_load': _try(cam.get_processing_load), 'volume_db': _try(cam.get_volume),
            'mute': _try(cam.get_mute), 'volume_ceiling_db': ctx.config.policy.volume_ceiling_db,
            'note': 'Levels are dBFS. clipped_samples counts samples clipped since CamillaDSP started; a rising '
                    'count means reduce gain/boost or main volume.'}


def _snapshot_volume(ctx):
    cam = ctx.clients.camilla
    volume = _try(cam.get_volume)
    mute = _try(cam.get_mute)
    return (volume if isinstance(volume, (int, float)) and not isinstance(volume, bool) else None,
            mute if isinstance(mute, bool) else None)


def _backup(ctx, config, tool, reason):
    volume, mute = _snapshot_volume(ctx)
    record = ctx.backups.save({'tool': tool, 'reason': reason, 'config': config, 'volume_db': volume, 'mute': mute})
    return record['id']


def apply_config(ctx, tool, reason, mutate, whole_config=False):
    """Backup-then-apply a config change built by ``mutate(config_copy)``.

    ``whole_config=True`` is for writes that replace the configuration with one not
    derived from the live config (restore): every filter, processor, pipeline step and
    section in it must be boundable and inside the hard caps, not only the filters that
    differ from live (see ``safety.audit_config``).
    """
    policy = ctx.config.policy
    cam = ctx.clients.camilla
    with ctx.exclusive():
        current = cam.get_config()
        candidate = copy.deepcopy(current)
        changed = mutate(candidate)
        if whole_config:
            hard = safety.audit_whole_config(candidate)
            if hard:
                raise SafetyError('configuration rejected: it contains filters, processors or pipeline steps the '
                                  'server cannot bound, or values '
                                  'outside the hard speaker-protection caps; fix it through the reviewed '
                                  'configuration process instead', violations=hard)
        before = set(safety.audit_config(policy, current))
        after = safety.audit_config(policy, candidate)
        # Reject anything new, and any violation on a filter this change touches: an edited
        # filter must end up fully inside the limits even if it was outside them before.
        touched = tuple(f'{name}:' for name in changed)
        new = [p for p in after if p not in before or p.startswith(touched)]
        if new:
            raise SafetyError('change rejected: it would violate speaker-protection limits', violations=new)
        validation = 'ok'
        try:
            cam.validate_config(candidate)
        except OpsError as error:
            if error.code != 'camilladsp_unsupported':
                raise OpsError('config_invalid_for_camilladsp', 'CamillaDSP rejected the candidate configuration',
                               reason=error.details.get('reason')) from None
            validation = 'unavailable (CamillaDSP will still validate on apply)'
        backup_id = _backup(ctx, current, tool, reason)
        try:
            cam.set_config(candidate)
        except OpsError as error:
            raise OpsError(error.code, error.message + f'; nothing applied, backup {backup_id} kept',
                           backup_id=backup_id, **error.details) from None
        readback = _try(cam.get_config)
        verified = isinstance(readback, dict) and all(
            (readback.get('filters') or {}).get(name) == (candidate.get('filters') or {}).get(name) for name in changed)
        return {'ok': True, 'backup_id': backup_id, 'changed_filters': changed, 'validation': validation,
                'readback_verified': verified, 'preexisting_limit_violations': sorted(before),
                'rollback': f'dsp_restore_backup with backup_id={backup_id}'}


def ensure_in_pipeline(config, name, index):
    """Make sure filter ``name`` runs on output channel ``index`` (adds an ops step when needed)."""
    pipeline = config.setdefault('pipeline', [])
    for step in pipeline:
        if step.get('type') == 'Filter' and name in (step.get('names') or []):
            return
    legacy = any('channel' in step and 'channels' not in step for step in pipeline if isinstance(step, dict))
    for step in pipeline:
        names = step.get('names') or []
        if (step.get('type') == 'Filter' and _step_channels(step) == [index] and names
                and all(n.startswith(OPS_PREFIX) for n in names)):
            names.append(name)
            return
    step = {'type': 'Filter', 'names': [name]}
    if legacy:
        step['channel'] = index
    else:
        step['channels'] = [index]
    pipeline.append(step)


def remove_filters(config, names):
    names = set(names)
    for name in names:
        (config.get('filters') or {}).pop(name, None)
    kept = []
    for step in config.get('pipeline') or []:
        if step.get('type') == 'Filter' and step.get('names'):
            step['names'] = [n for n in step['names'] if n not in names]
            if not step['names']:
                continue
        kept.append(step)
    config['pipeline'] = kept


def update_peq(ctx, args):
    name = args['filter']
    if not NAME_PATTERN.fullmatch(name):
        raise OpsError('invalid_argument', 'filter name must be 1-64 letters, digits, _ or -')
    policy = ctx.config.policy

    def mutate(config):
        filters = config.setdefault('filters', {})
        existing = filters.get(name)
        if existing is None:
            if 'channel' not in args:
                raise OpsError('not_found', f'filter {name} does not exist; pass channel to create it')
            if not all(k in args for k in ('freq', 'q', 'gain')):
                raise OpsError('invalid_argument', 'creating a PEQ filter needs freq, q and gain')
            params = {}
        else:
            params = existing.get('parameters') or {}
            if existing.get('type') != 'Biquad' or params.get('type') != 'Peaking':
                raise OpsError('wrong_filter_type', f'filter {name} is not a Biquad Peaking filter')
        merged = {'freq': args.get('freq', params.get('freq')), 'q': args.get('q', params.get('q')),
                  'gain': args.get('gain', params.get('gain'))}
        checked = safety.check_peq(policy, merged)
        filters[name] = {'type': 'Biquad', 'parameters': checked}
        if existing is None:
            ensure_in_pipeline(config, name, channel_index(ctx, args['channel']))
        return [name]

    return apply_config(ctx, 'dsp_update_peq', args.get('reason') or f'PEQ {name}', mutate)


def set_delay(ctx, args):
    channel = args['channel']
    value = safety.check_delay_ms(ctx.config.policy, args['delay_ms'])
    name = f'{OPS_PREFIX}{channel}_delay'

    def mutate(config):
        index = channel_index(ctx, channel)
        config.setdefault('filters', {})[name] = {'type': 'Delay', 'parameters': {'delay': value, 'unit': 'ms',
                                                                                    'subsample': False}}
        ensure_in_pipeline(config, name, index)
        return [name]

    return apply_config(ctx, 'dsp_set_channel_delay', args.get('reason') or f'{channel} delay {value} ms', mutate)


def set_channel_gain(ctx, args):
    channel = args['channel']
    value = safety.check_channel_gain(ctx.config.policy, args['gain_db'])
    name = f'{OPS_PREFIX}{channel}_gain'

    def mutate(config):
        index = channel_index(ctx, channel)
        config.setdefault('filters', {})[name] = {'type': 'Gain', 'parameters': {'gain': value, 'inverted': False,
                                                                                   'scale': 'dB'}}
        ensure_in_pipeline(config, name, index)
        return [name]

    return apply_config(ctx, 'dsp_set_channel_gain', args.get('reason') or f'{channel} gain {value} dB', mutate)


def set_main_volume(ctx, args):
    value = safety.check_volume(ctx.config.policy, args['volume_db'])
    with ctx.exclusive():
        current = ctx.clients.camilla.get_config()
        backup_id = _backup(ctx, current, 'dsp_set_main_volume', args.get('reason') or f'main volume {value} dB')
        ctx.clients.camilla.set_volume(value)
        readback = _try(ctx.clients.camilla.get_volume)
    return {'ok': True, 'volume_db': value, 'readback_db': readback, 'ceiling_db': ctx.config.policy.volume_ceiling_db,
            'backup_id': backup_id}


def playback_allowed(ctx):
    return bool(ctx.config.allow_write and ctx.config.allow_playback)


def set_main_mute(ctx, args):
    muted = args['muted']
    if not muted and not playback_allowed(ctx):
        # Muting reduces output and only needs allow_write; unmuting can make sound, which is
        # the same authority as starting playback.
        raise OpsError('playback_disabled', 'unmuting is a sound-producing action: it needs allow_write=true and '
                                            'allow_playback=true in the ops server configuration',
                       tool='dsp_set_mute')
    with ctx.exclusive():
        current = ctx.clients.camilla.get_config()
        backup_id = _backup(ctx, current, 'dsp_set_mute', args.get('reason') or ('mute' if muted else 'unmute'))
        ctx.clients.camilla.set_mute(muted)
        readback = _try(ctx.clients.camilla.get_mute)
    return {'ok': True, 'muted': muted, 'readback': readback, 'backup_id': backup_id}


def list_backups(ctx, args):
    entries = []
    for ident in ctx.backups.list(args.get('limit', 20)):
        try:
            record = ctx.backups.get(ident)
        except OpsError as error:
            entries.append({'id': ident, 'error': error.payload()})
            continue
        entries.append({'id': ident, 'tool': record.get('tool'), 'reason': record.get('reason'),
                        'saved_at': record.get('saved_at'), 'volume_db': record.get('volume_db'),
                        'mute': record.get('mute'), 'title': (record.get('config') or {}).get('title'),
                        'filters': len((record.get('config') or {}).get('filters') or {})})
    return {'backups': entries, 'directory': str(ctx.backups.directory), 'retention': ctx.config.backup_retention}


def restore_backup(ctx, args):
    record = ctx.backups.get(args['backup_id'])
    config = record.get('config')
    if not isinstance(config, dict):
        raise OpsError('unreadable', 'backup has no configuration')

    def mutate(candidate):
        # Filters whose definition the restore changes must be inside the current limits.
        # Unchanged ones are already live; whole_config=True still requires every filter in
        # the restored config to be boundable and inside the hard caps.
        live = candidate.get('filters') or {}
        target = config.get('filters') or {}
        changed = sorted(name for name in target if target[name] != live.get(name))
        candidate.clear()
        candidate.update(copy.deepcopy(config))
        return changed

    volume = None
    if args.get('restore_volume') and record.get('volume_db') is not None:
        # Checked before anything is applied so a refused volume never leaves a half restore.
        volume = safety.check_volume(ctx.config.policy, record['volume_db'], 'backup volume_db')
    result = apply_config(ctx, 'dsp_restore_backup', f"restore {args['backup_id']}", mutate, whole_config=True)
    result['restored_from'] = args['backup_id']
    if volume is not None:
        # The pre-restore volume/mute were captured in the backup apply_config just wrote.
        with ctx.exclusive():
            ctx.clients.camilla.set_volume(volume)
            result['volume_db'] = volume
            if record.get('mute') is True or (record.get('mute') is False and playback_allowed(ctx)):
                ctx.clients.camilla.set_mute(record['mute'])
                result['mute'] = record['mute']
            elif record.get('mute') is False:
                result['mute_not_restored'] = ('the backup was unmuted; unmuting needs allow_playback=true, so the '
                                               'current mute state was left unchanged')
    return result
