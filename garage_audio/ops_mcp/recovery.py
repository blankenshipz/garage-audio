"""Recovery through the supervisor's sanctioned path only: the service manager on the owner unit.

Never touches containers directly. Refuses while the owner is already retrying
(``backoff``), mid-transition, or has stopped on purpose (``terminal``/``exhausted``,
whose cause must be found first) unless ``force`` is set. The action rate limit
applies even with ``force``.
"""
from . import health
from .backups import atomic_write_json, read_json, _ensure_private_dir
from .errors import OpsError

ACTIONS_FILE = 'recovery-actions.json'
BUSY_UNIT_STATES = ('activating', 'deactivating', 'reloading')


def _last_action(ctx):
    try:
        value = read_json(ctx.config.state_dir / ACTIONS_FILE)
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def plan(ctx, action='auto', force=False):
    """Decide what a recovery request would do, without doing it (also used by the dry-run tool)."""
    rec = health.recovery_status(ctx)
    state = rec.get('state')
    unit = health.unit_state(ctx)
    active = unit.get('ActiveState')
    reasons, notes = [], []
    if action == 'auto':
        action = 'restart' if active in ('active', 'activating', 'reloading') else 'start'
    if state == 'backoff':
        msg = ('The owner is in backoff: it is already waiting to retry by itself '
               f"(delay {rec.get('backoff_delay_seconds', '?')} s, retries used {rec.get('retries')}). "
               'Restarting now would reset its bounded retry accounting.')
        (notes if force else reasons).append(msg)
    if state == 'starting':
        msg = 'A generation is being created or admitted right now; a restart would interrupt it.'
        (notes if force else reasons).append(msg)
    if active in BUSY_UNIT_STATES:
        msg = f'The service manager reports the unit as {active}; a job is already in progress.'
        (notes if force else reasons).append(msg)
    if state in ('terminal', 'exhausted'):
        msg = (f"State '{state}': the owner stopped retrying on purpose ({rec.get('explanation')}). "
               f"Guidance: {rec.get('restart_guidance')} Find and fix the cause first; starting it again "
               'unchanged will fail the same way. Pass force=true only once the cause is addressed.')
        (notes if force else reasons).append(msg)
    if state == 'ready' and action == 'restart':
        notes.append('The stack is ready; a restart interrupts any current playback and creates a new generation.')
    if state in ('unknown', 'unavailable'):
        notes.append('The recovery record is not available; the service manager state is the only evidence.')
    last = _last_action(ctx)
    wait = None
    if last and isinstance(last.get('time'), (int, float)):
        elapsed = ctx.clock() - last['time']
        if 0 <= elapsed < ctx.config.min_action_interval:
            wait = round(ctx.config.min_action_interval - elapsed, 1)
            reasons.append(f'A recovery action ran {elapsed:.0f} s ago; wait {wait} s before another '
                           '(min_action_interval_seconds). This is not bypassed by force.')
    return {'action': action, 'unit': ctx.config.unit, 'recovery_state': state, 'unit_active_state': active,
            'allowed': not reasons, 'refusal_reasons': reasons, 'notes': notes, 'force': force,
            'command': ctx.config.systemctl + [action, '--no-block', ctx.config.unit]}


def recover(ctx, args):
    action = args.get('action', 'auto')
    force = args.get('force', False)
    with ctx.exclusive():
        decision = plan(ctx, action, force)
        if not decision['allowed']:
            raise OpsError('recovery_refused', ' '.join(decision['refusal_reasons']), plan=decision)
        _ensure_private_dir(ctx.config.state_dir)
        atomic_write_json(ctx.config.state_dir / ACTIONS_FILE,
                          {'time': ctx.clock(), 'action': decision['action'], 'unit': ctx.config.unit, 'force': force,
                           'recovery_state': decision['recovery_state']})
        result = ctx.clients.system.unit_action(decision['action'])
    ok = result['exit_status'] == 0
    return {'ok': ok, 'plan': decision, 'result': result,
            'message': (f"Queued 'systemctl {decision['action']}' for {ctx.config.unit}. The owner now creates a new "
                        'generation; check status in ~10-30 s.') if ok else
                       'The service manager refused the request (see stderr; the ops server account may lack '
                       'permission to manage the unit, which is an operator setup step).',
            'unit_after': health.unit_state(ctx)}
