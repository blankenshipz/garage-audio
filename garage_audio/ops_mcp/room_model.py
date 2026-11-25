"""Room profile, axial room modes, placement advice and the microphone measurement plan (stdlib).

Coordinates (metres) are seen from the listening position facing the speakers:
``x`` from the LEFT side wall, ``y`` from the FRONT wall (the wall behind the speakers),
``z`` up from the floor. ``length`` runs front wall -> back wall, ``width`` left -> right.
The profile is a single private JSON file in the state directory, updated partially and
written atomically.
"""
import math
import threading
from pathlib import Path

from . import safety
from .backups import _ensure_private_dir, atomic_write_json, read_json
from .errors import OpsError

PROFILE_FILE = 'room-profile.json'
UNITS = {'m': 1.0, 'cm': 0.01, 'ft': 0.3048, 'in': 0.0254}
SPEAKERS = ('left', 'right', 'sub', 'sub2', 'center')
DIM_LIMITS = {'length': (1.5, 50.0), 'width': (1.5, 50.0), 'height': (1.8, 15.0)}
WALL_CLEARANCE_M = 0.2
_LOCK = threading.Lock()


# ---- formatting ------------------------------------------------------------------------
def imperial(metres):
    inches = round(metres / 0.0254)
    if inches < 12:
        return f'{inches} in'
    feet, rest = divmod(inches, 12)
    return f'{feet} ft' + (f' {rest} in' if rest else '')


def metric(metres):
    return f'{round(metres * 100)} cm' if metres < 1.0 else f'{metres:.2f} m'


def both(metres):
    return f'{metric(metres)} ({imperial(metres)})'


# ---- profile storage -------------------------------------------------------------------
def _path(ctx):
    return Path(ctx.config.state_dir) / PROFILE_FILE


def load(ctx):
    try:
        value = read_json(_path(ctx))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):
        raise OpsError('unreadable', 'room profile cannot be read') from None
    if not isinstance(value, dict):
        raise OpsError('unreadable', 'room profile is malformed')
    return value


def _convert(value, factor, key=None):
    """Scale every length field of a partial profile by ``factor`` (metres per unit)."""
    if isinstance(value, dict):
        return {k: (v if k in ('notes', 'label') else _convert(v, factor, k)) for k, v in value.items()}
    if isinstance(value, list):
        return value  # notes / obstacles are text
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return round(value * factor, 4)
    return value


def _merge(base, patch):
    out = dict(base)
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


def _check_point(name, point, dims, height_key='z'):
    for axis, dim in (('x', 'width'), ('y', 'length'), (height_key, 'height')):
        if axis not in point:
            continue
        value = point[axis]
        top = dims.get(dim)
        if value < 0 or (top is not None and value > top):
            raise OpsError('invalid_argument', f'{name}.{axis}={value:.2f} m is outside the room '
                           f'(0..{top if top is not None else "?"} m along {dim})')


def validate_profile(profile):
    dims = profile.get('dimensions_m') or {}
    for key, value in dims.items():
        low, high = DIM_LIMITS[key]
        if not low <= value <= high:
            raise OpsError('invalid_argument', f'room {key} {value:.2f} m is outside {low}-{high} m')
    for name, point in (profile.get('speakers') or {}).items():
        _check_point(f'speakers.{name}', point, dims)
    for name, point in (profile.get('listening_positions') or {}).items():
        _check_point(f'listening_positions.{name}', point, dims, height_key='ear_height')
        ear = point.get('ear_height')
        if ear is not None and not 0.3 <= ear <= 2.5:
            raise OpsError('invalid_argument', f'ear height {ear:.2f} m is outside 0.3-2.5 m')
    seating = profile.get('seating') or {}
    if 'width' in seating and not 0.3 <= seating['width'] <= 10:
        raise OpsError('invalid_argument', 'seating width must be 0.3-10 m')
    if len(profile.get('listening_positions') or {}) > 8:
        raise OpsError('invalid_argument', 'at most 8 listening positions')
    return profile


def get_profile(ctx, _args):
    profile = load(ctx)
    return {'profile': profile, 'missing': missing_fields(profile), 'coordinates': __doc__.split('\n\n')[1]}


def missing_fields(profile):
    out = []
    dims = profile.get('dimensions_m') or {}
    out += [f'dimensions_m.{k}' for k in ('length', 'width', 'height') if k not in dims]
    if 'main' not in (profile.get('listening_positions') or {}):
        out.append('listening_positions.main')
    speakers = profile.get('speakers') or {}
    out += [f'speakers.{k}' for k in ('left', 'right', 'sub') if k not in speakers]
    return out


def update_profile(ctx, args):
    patch = _convert(args.get('set') or {}, UNITS[args.get('units', 'm')])
    with _LOCK:
        profile = load(ctx)
        for path in args.get('remove') or []:
            head, _, tail = path.partition('.')
            if tail:
                (profile.get(head) or {}).pop(tail, None)
            else:
                profile.pop(head, None)
        profile = validate_profile(_merge(profile, patch))
        _ensure_private_dir(Path(ctx.config.state_dir))
        profile['updated_at'] = round(ctx.clock(), 3)
        atomic_write_json(_path(ctx), profile)
    return {'profile': profile, 'missing': missing_fields(profile),
            'note': 'Stored in metres. Next: room_modes for modes and placement advice; then measure_session_start.'}


# ---- modes and advice ------------------------------------------------------------------
def axial_modes(dims, max_hz=300.0):
    c = safety.SPEED_OF_SOUND_M_S
    out = []
    for axis in ('length', 'width', 'height'):
        size = dims.get(axis)
        if not size:
            continue
        n = 1
        while n * c / (2 * size) <= max_hz:
            out.append({'axis': axis, 'order': n, 'freq_hz': round(n * c / (2 * size), 1)})
            n += 1
    return sorted(out, key=lambda m: m['freq_hz'])


def _coupling(order, position, size):
    """Relative pressure of an axial mode at a coordinate: 1 = antinode (peak), 0 = node (null)."""
    return abs(math.cos(order * math.pi * position / size))


def analyse(profile, max_hz=300.0, listening='main'):
    dims = profile.get('dimensions_m') or {}
    if not all(k in dims for k in ('length', 'width', 'height')):
        raise OpsError('room_incomplete', 'room length, width and height are needed (room_profile_update)',
                       missing=missing_fields(profile))
    modes = axial_modes(dims, max_hz)
    volume = dims['length'] * dims['width'] * dims['height']
    problems, advice = [], []
    low = [m for m in modes if m['freq_hz'] <= 200]
    for a, b in zip(low, low[1:]):
        if a['axis'] != b['axis'] and b['freq_hz'] / a['freq_hz'] < 1.04:
            problems.append({'freq_hz': round((a['freq_hz'] + b['freq_hz']) / 2, 1), 'kind': 'stacked_modes',
                             'detail': f"{a['axis']} mode {a['order']} and {b['axis']} mode {b['order']} coincide: "
                                       'expect a strong peak or a deep null'})
    for a, b in zip(low, low[1:]):
        if b['freq_hz'] / a['freq_hz'] > 2 ** (1 / 3) and a['freq_hz'] >= 30:
            problems.append({'freq_hz': round(math.sqrt(a['freq_hz'] * b['freq_hz']), 1), 'kind': 'mode_gap',
                             'detail': f"no axial mode between {a['freq_hz']} and {b['freq_hz']} Hz: response may "
                                       'dip in between'})
    ratios = sorted(dims[k] for k in ('length', 'width', 'height'))
    for i, j in ((0, 1), (1, 2), (0, 2)):
        r = ratios[j] / ratios[i]
        if abs(r - round(r)) < 0.06:
            advice.append(f'Two room dimensions are (near) integer multiples ({ratios[j]:.2f} / {ratios[i]:.2f} m): '
                          'their modes stack, so expect fewer but stronger resonances.')
            break
    seat = (profile.get('listening_positions') or {}).get(listening)
    seat_modes = []
    if seat and 'x' in seat and 'y' in seat:
        rel_y, rel_x = seat['y'] / dims['length'], seat['x'] / dims['width']
        if abs(rel_y - 0.5) < 0.05:
            advice.append('The seat is near the middle of the room length: the first length mode '
                          f"({modes_of(modes, 'length', 1)} Hz) nulls there (bass sounds thin). Move the seat "
                          f"forward or back by about {both(0.1 * dims['length'])}.")
        for frac in (0.25, 0.75):
            if abs(rel_y - frac) < 0.04:
                advice.append(f'The seat is near {int(frac * 100)}% of the room length: the second length mode '
                              f"({modes_of(modes, 'length', 2)} Hz) nulls there.")
        if rel_y < 0.15 or rel_y > 0.85:
            advice.append('The seat is close to the front or back wall: strong bass build-up and reflections. '
                          f"A good starting point is about 38% of the length from a wall ({both(0.38 * dims['length'])}).")
        if abs(rel_x - 0.5) < 0.05:
            advice.append('The seat is centred between the side walls: the first width mode '
                          f"({modes_of(modes, 'width', 1)} Hz) nulls there. That is normal for stereo symmetry; "
                          'a sub off-centre or two subs can fill it.')
        ear = seat.get('ear_height')
        if ear is not None and abs(ear / dims['height'] - 0.5) < 0.06:
            advice.append(f"Ear height is near half the ceiling height: the first height mode "
                          f"({modes_of(modes, 'height', 1)} Hz) nulls there.")
        sub = (profile.get('speakers') or {}).get('sub')
        for mode in low:
            axis = {'length': ('y', 'length'), 'width': ('x', 'width'), 'height': ('ear_height', 'height')}[mode['axis']]
            pos = seat.get(axis[0])
            if pos is None:
                continue
            at_seat = _coupling(mode['order'], pos, dims[axis[1]])
            entry = {**mode, 'at_seat': round(at_seat, 2)}
            if sub and {'x', 'y'} <= set(sub):
                sub_pos = sub.get({'y': 'y', 'x': 'x', 'ear_height': 'z'}[axis[0]])
                if sub_pos is not None:
                    entry['sub_excites'] = round(_coupling(mode['order'], sub_pos, dims[axis[1]]), 2)
            if at_seat < 0.25:
                entry['effect'] = 'null at the seat (EQ cannot fill it; move the seat or the sub)'
            elif at_seat > 0.9 and entry.get('sub_excites', 1.0) > 0.5:
                entry['effect'] = 'peak at the seat (a good EQ cut candidate)'
            seat_modes.append(entry)
    sub = (profile.get('speakers') or {}).get('sub')
    if sub and {'x', 'y'} <= set(sub):
        near = [w for w, d in (('left', sub['x']), ('right', dims['width'] - sub['x']), ('front', sub['y']),
                               ('back', dims['length'] - sub['y'])) if d < 0.5]
        if len(near) >= 2:
            advice.append('The sub is in a corner: maximum output, but it excites every mode (most uneven bass). '
                          'Try the "sub crawl" below before settling.')
    advice.append('Sub crawl: temporarily put the sub at the main seat, then measure with the mic at a few '
                  'candidate sub locations on the floor (or listen); the smoothest spot is where the sub should go. '
                  'Positions around 1/4 or 1/2 of a wall length cancel some modes.')
    schroeder = 2000.0 * math.sqrt(0.4 / volume)
    return {'dimensions_m': dims, 'volume_m3': round(volume, 1), 'axial_modes': modes,
            'problem_frequencies': sorted(problems, key=lambda p: p['freq_hz']),
            'seat_coupling': seat_modes, 'advice': advice,
            'schroeder_hz_estimate': round(schroeder),
            'note': 'Axial modes only (the strongest); tangential/oblique modes add more. Below about the Schroeder '
                    'frequency the room behaves modally: EQ can cut peaks, but nulls need placement changes.'}


def modes_of(modes, axis, order):
    for m in modes:
        if m['axis'] == axis and m['order'] == order:
            return m['freq_hz']
    return '?'


def modes_tool(ctx, args):
    profile = load(ctx)
    if args.get('dimensions_m'):
        dims = _convert(args['dimensions_m'], UNITS[args.get('units', 'm')])
        profile = validate_profile(_merge(profile, {'dimensions_m': dims}))
    return analyse(profile, args.get('max_hz', 300.0), args.get('listening_position', 'main'))


# ---- measurement plan ------------------------------------------------------------------
# Angles (degrees; 0 = right, 90 = forward toward the speakers) in the order positions are added.
ANGLES = (180, 0, 90, 270, 135, 45, 225, 315)
_DIRECTIONS = {180: 'to the left of', 0: 'to the right of', 90: 'in front of (toward the speakers)',
               270: 'behind'}


def _describe(angle, radius, ear):
    height = f'same height as the ears ({metric(ear)} / {imperial(ear)} from the floor)'
    if angle in _DIRECTIONS:
        return (f'about {metric(radius)} {_DIRECTIONS[angle]} the centre of the seat, {height}',
                f'about {imperial(radius)} {_DIRECTIONS[angle]} the center of the seat, same height as the ears '
                f'({imperial(ear)} from the floor)')
    side = 'left' if math.cos(math.radians(angle)) < 0 else 'right'
    fwd = 'forward (toward the speakers)' if math.sin(math.radians(angle)) > 0 else 'back'
    leg = radius / math.sqrt(2)
    return (f'about {metric(leg)} to the {side} and {metric(leg)} {fwd} of the centre of the seat, {height}',
            f'about {imperial(leg)} to the {side} and {imperial(leg)} {fwd} of the center of the seat, same height '
            f'as the ears ({imperial(ear)} from the floor)')


def orientation_text(orientation):
    if orientation == '0':
        return 'mic pointing horizontally at the point between the two speakers (0-degree calibration)'
    return 'mic pointing straight up at the ceiling (90-degree calibration)'


def plan(profile, *, count, radius, listening='main', ear_height=None, orientation='90'):
    """Main seat + ``count`` nearby positions on a circle of ``radius`` around it, at ear height."""
    seat = (profile.get('listening_positions') or {}).get(listening) or {}
    seating = profile.get('seating') or {}
    if seating.get('width'):
        radius = min(radius, max(0.1, seating['width'] / 2))
    ear = ear_height or seat.get('ear_height') or 1.1
    dims = profile.get('dimensions_m') or {}
    positions = [{'id': 'P0', 'role': 'main', 'offset_m': {'right': 0.0, 'forward': 0.0},
                  'description': 'at the centre of the main seat where the listener\'s head is, same height as '
                                 f'the ears ({metric(ear)} / {imperial(ear)} from the floor)',
                  'description_imperial': f'at the center of the main seat where the listener\'s head is, same height '
                                          f'as the ears ({imperial(ear)} from the floor)'}]
    for i, angle in enumerate(ANGLES[:count], start=1):
        right = round(radius * math.cos(math.radians(angle)), 3) + 0.0
        forward = round(radius * math.sin(math.radians(angle)), 3) + 0.0
        text, text_imp = _describe(angle, radius, ear)
        positions.append({'id': f'P{i}', 'role': 'nearby', 'offset_m': {'right': right, 'forward': forward},
                          'description': text, 'description_imperial': text_imp})
    warnings = []
    for p in positions:
        p['height_m'] = round(ear, 3)
        if 'x' in seat and 'y' in seat:
            x, y = seat['x'] + p['offset_m']['right'], seat['y'] - p['offset_m']['forward']
            p['room_m'] = {'x': round(x, 3), 'y': round(y, 3), 'z': round(ear, 3)}
            if dims.get('width') and dims.get('length'):
                gap = min(x, dims['width'] - x, y, dims['length'] - y)
                if gap < WALL_CLEARANCE_M:
                    warnings.append(f"{p['id']} is within {metric(WALL_CLEARANCE_M)} of a wall; move it inward or "
                                    'skip it')
    return {'listening_position': listening, 'radius_m': radius, 'ear_height_m': ear,
            'mic_orientation': orientation_text(orientation), 'positions': positions, 'warnings': warnings,
            'why': 'Several positions around the seat average out position-specific dips, so the EQ fixes what '
                   'the whole listening area hears rather than one spot.'}
