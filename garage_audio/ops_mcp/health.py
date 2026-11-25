"""Read-only health: owner recovery state, unit state, heartbeat, containers, MA reachability, logs, diagnose."""
import re

from .. import audio_recovery
from .errors import OpsError

SINCE_PATTERN = re.compile(r'^(?:-?[0-9]{1,6}\s?(?:s|sec|min|m|h|hour|hours|d|days)(?:\s?ago)?|today|yesterday|'
                           r'[0-9]{4}-[0-9]{2}-[0-9]{2}(?:[ T][0-9]{2}:[0-9]{2}(?::[0-9]{2})?)?)$')


def _safe(fn):
    """Run one probe; return (value, None) or (None, error payload). Never raises OpsError."""
    try:
        return fn(), None
    except OpsError as error:
        return None, error.payload()


def recovery_status(ctx):
    """Owner recovery record via audio_recovery.collect_status (read-only, no locks)."""
    root = ctx.config.status_root
    try:
        if not root.is_dir():
            return {'state': 'unavailable', 'explanation': f'status root {root} not found on this host',
                    'restart_guidance': 'Check supervisor.status_root in the ops MCP configuration.'}
        report = audio_recovery.collect_status(root)
    except PermissionError:
        return {'state': 'unavailable', 'explanation': 'cannot read the status root (permission denied)',
                'restart_guidance': 'Run the ops server as an account with read access to the status root.'}
    except OSError:
        return {'state': 'unavailable', 'explanation': 'status root could not be read',
                'restart_guidance': 'Check the status root path and permissions.'}
    # Our configured unit name replaces the module default in guidance text.
    for key in ('explanation', 'restart_guidance'):
        if isinstance(report.get(key), str):
            report[key] = report[key].replace(audio_recovery.SERVICE, ctx.config.unit)
    report['notes'] = [n.replace(audio_recovery.SERVICE, ctx.config.unit) for n in report.get('notes', [])]
    return report


def unit_state(ctx):
    value, error = _safe(lambda: ctx.clients.system.unit_state())
    return value if value is not None else {'unit': ctx.config.unit, 'error': error}


def containers(ctx):
    value, error = _safe(lambda: ctx.clients.system.containers())
    return {'containers': value} if value is not None else {'containers': None, 'error': error}


def ma_reachability(ctx):
    value, error = _safe(lambda: ctx.clients.ma.info())
    return {'url': ctx.config.ma_url, 'reachable': value is not None, 'info': value, 'error': error}


def camilla_reachability(ctx):
    state, error = _safe(lambda: ctx.clients.camilla.get_state())
    return {'address': f'{ctx.config.camilla_host}:{ctx.config.camilla_port}', 'reachable': state is not None,
            'processing_state': state, 'error': error}


def overall_status(ctx):
    return {'recovery': recovery_status(ctx), 'owner_unit': unit_state(ctx), 'containers': containers(ctx),
            'music_assistant': ma_reachability(ctx), 'camilladsp': camilla_reachability(ctx),
            'write_enabled': ctx.config.allow_write, 'playback_enabled': ctx.config.allow_write and ctx.config.allow_playback}


def heartbeat(ctx):
    report = recovery_status(ctx)
    return {k: report.get(k) for k in ('state', 'generation', 'receipt', 'heartbeat_age_seconds', 'state_age_seconds',
                                        'tolerance', 'guard_failure', 'notes')}


def recent_logs(ctx, args):
    source = args.get('source', 'owner')
    lines = min(args.get('lines', 50), ctx.config.max_log_lines)
    since = args.get('since')
    if since is not None and not SINCE_PATTERN.fullmatch(since):
        raise OpsError('invalid_argument', 'since must look like "10 min ago", "2h", "today" or "YYYY-MM-DD HH:MM"')
    errors_only = args.get('errors_only', False)
    if source == 'owner':
        target = ctx.config.unit
        lines_out = ctx.clients.system.unit_logs(target, lines if not errors_only else ctx.config.max_log_lines, since)
    elif source in ctx.config.log_units:
        target = source
        lines_out = ctx.clients.system.unit_logs(target, lines if not errors_only else ctx.config.max_log_lines, since)
    elif source in ctx.config.log_containers:
        target = source
        lines_out = ctx.clients.system.container_logs(target, lines if not errors_only else ctx.config.max_log_lines, since)
    else:
        raise OpsError('invalid_argument', 'unknown log source; use "owner" or a configured logs.units/logs.containers name',
                       allowed=['owner'] + ctx.config.log_units + ctx.config.log_containers)
    if errors_only:
        pattern = re.compile(r'(?i)\b(error|fail|failed|failure|fatal|critical|exception|traceback|denied|refused|'
                             r'timeout|timed out|revoked|terminal|exhausted|stall|oom|killed|xrun|underrun|clipp)')
        lines_out = [line for line in lines_out if pattern.search(line)][-lines:]
    return {'source': target, 'lines': lines_out, 'count': len(lines_out), 'redacted': True,
            'max_lines': ctx.config.max_log_lines}


def diagnose(ctx):
    """Plain-words explanation and suggested next steps built from the read-only probes."""
    status = overall_status(ctx)
    findings, steps = [], []
    rec = status['recovery']
    state = rec.get('state')
    findings.append(f"Owner recovery state is '{state}': {rec.get('explanation', '')}")
    if rec.get('restart_guidance'):
        steps.append(rec['restart_guidance'])
    unit = status['owner_unit']
    if 'error' in unit:
        findings.append(f"The service manager state for {ctx.config.unit} could not be read ({unit['error']['message']}).")
    else:
        active = unit.get('ActiveState')
        findings.append(f"Service manager reports {ctx.config.unit} as {active}/{unit.get('SubState')}"
                        f" (restarts: {unit.get('NRestarts', '?')}).")
        if active == 'active' and state in ('terminal', 'exhausted', 'stopped'):
            findings.append('The unit is active but the recovery record says the generation closed; the record may be '
                            'from an earlier invocation. Re-check in a few seconds.')
        if active in ('inactive', 'failed') and state == 'backoff':
            findings.append('The recovery record says backoff but the unit is not running; the owner exited during '
                            'a retry wait. Treat as stopped and use the sanctioned start once the cause is understood.')
    age = rec.get('heartbeat_age_seconds')
    tolerance = (rec.get('tolerance') or {}).get('effective_seconds')
    if state in ('starting', 'ready') and isinstance(age, (int, float)):
        if isinstance(tolerance, (int, float)) and age > tolerance:
            findings.append(f'Guard heartbeat is {age:.1f} s old, beyond the {tolerance:g} s tolerance: the guard may be '
                            'stalled (host memory/CPU/IO pressure).')
            steps.append('Check host pressure (memory, CPU, IO) and the owner log; the owner handles stalls itself.')
        else:
            findings.append(f'Guard heartbeat is fresh ({age:.1f} s old).')
    if rec.get('guard_failure'):
        failure = rec['guard_failure']
        findings.append(f"Last guard failure: {failure.get('kind')}.")
    cont = status['containers']
    if cont.get('containers') is None:
        findings.append(f"Container states could not be read ({cont['error']['message']}).")
    else:
        down = [c['name'] for c in cont['containers'] if not c.get('running')]
        unhealthy = [c['name'] for c in cont['containers'] if c.get('health') == 'unhealthy']
        oom = [c['name'] for c in cont['containers'] if c.get('oom_killed')]
        if not cont['containers']:
            findings.append('No stack containers were found (none labelled or configured).')
        elif down:
            findings.append('Containers not running: ' + ', '.join(down) + '.')
        else:
            findings.append(f"All {len(cont['containers'])} stack containers are running.")
        if unhealthy:
            findings.append('Unhealthy containers: ' + ', '.join(unhealthy) + '.')
        if oom:
            findings.append('Containers killed for memory: ' + ', '.join(oom) + '.')
            steps.append('Investigate host memory pressure before restarting anything.')
        if down and state in ('ready', 'starting'):
            steps.append('Containers are owned by the supervisor: do not start/stop them directly; '
                         'let the owner recover or use the sanctioned restart once.')
    ma = status['music_assistant']
    findings.append('Music Assistant is reachable.' if ma['reachable'] else
                    f"Music Assistant is not reachable ({ma['error']['message']}).")
    dsp = status['camilladsp']
    if dsp['reachable']:
        findings.append(f"CamillaDSP is reachable; processing state {dsp['processing_state']}.")
        if dsp['processing_state'] not in ('Running', 'Starting'):
            steps.append('CamillaDSP is not processing; check the capture/playback devices and the owner log.')
    else:
        findings.append(f"CamillaDSP websocket is not reachable ({dsp['error']['message']}). In isolated deployments "
                        'the raw websocket is private by design; that alone is not a fault.')
    healthy = (state == 'ready' and ma['reachable'] and dsp['reachable'] and
               (cont.get('containers') is not None and all(c.get('running') for c in cont['containers'])))
    if healthy:
        steps.append('No fault detected. If there is still no sound, check the player volume, mute and the source.')
    return {'healthy': healthy, 'summary': ' '.join(findings), 'findings': findings, 'next_steps': steps,
            'evidence': status}
