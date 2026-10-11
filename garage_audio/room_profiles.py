"""Private room/profile planning. Caller owns Application's thread/process locks.

No capture, policy mutation, inference, URL fetching, or correction generation.
"""
import base64
import binascii
import copy
import hashlib
import io
import json
import os
import re
import stat
import time
import uuid
import warnings
from contextlib import contextmanager
from pathlib import Path

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError


class DomainFailure(Exception):
    def __init__(self, code, message, requirements=None):
        super().__init__(message)
        self.code, self.message = code, message
        self.requirements = requirements or []


class DurabilityFailure(DomainFailure):
    """Replacement is visible; a later directory sync failed. Do not roll back."""
    def __init__(self):
        super().__init__('storage_durability_uncertain',
                         'Record replacement committed but durable sync could not be confirmed',
                         ['Reload current state before retrying; the mutation may already be visible'])


def obj(properties, required=None):
    return {'type': 'object', 'properties': properties,
            'required': list(properties) if required is None else required,
            'additionalProperties': False}


def number(lo, hi):
    return {'type': 'number', 'minimum': lo, 'maximum': hi}


ID = {'type': 'string', 'pattern': '^[a-f0-9]{32}$'}
NAME = {'type': 'string', 'minLength': 1, 'maxLength': 120}
ANNOTATION = obj({'id': ID, 'kind': {'enum': ['speaker', 'listening-area', 'fit', 'verify']},
                  'label': {'type': 'string', 'minLength': 1, 'maxLength': 80},
                  'x': number(0, 1), 'y': number(0, 1),
                  'mic_height_m': number(.1, 3), 'orientation': {'enum': ['0', '90']}},
                 ['kind', 'label', 'x', 'y'])
FIT = {'type': 'array', 'minItems': 1, 'maxItems': 8,
       'items': obj({'position_id': ID, 'weight': {'type': 'number', 'exclusiveMinimum': 0, 'maximum': 100}})}
VERIFY = {'type': 'array', 'minItems': 1, 'maxItems': 8, 'uniqueItems': True, 'items': ID}
TARGET = obj({'low_hz': number(20, 20000), 'high_hz': number(20, 20000), 'tilt_db': number(-12, 12)})
BINDING = obj({'position_id': ID, 'channel': {'enum': ['left', 'right', 'sub']},
               'session_id': ID, 'measurement_id': ID})
FIELDS = {'id': ID, 'room_id': ID, 'photo_id': ID, 'name': NAME,
          'notes': {'type': 'string', 'maxLength': 2000},
          'expected_version': {'type': 'integer', 'minimum': 1},
          'kind': {'enum': ['whole-room', 'seat', 'area', 'custom']},
          'annotations': {'type': 'array', 'maxItems': 32, 'items': ANNOTATION},
          'fit_positions': FIT, 'verify_positions': VERIFY, 'target': TARGET,
          'bindings': {'type': 'array', 'maxItems': 48, 'items': BINDING},
          'data_base64': {'type': 'string', 'minLength': 4, 'maxLength': 700000}}
SCHEMAS = {
    'rooms.list': ([], []), 'rooms.create': (['name'], ['notes']),
    'rooms.get': (['id'], []), 'rooms.update': (['id', 'expected_version'], ['name', 'notes']),
    'rooms.archive': (['id', 'expected_version'], []),
    'rooms.annotate': (['id', 'expected_version', 'annotations'], []),
    'rooms.confirm': (['id', 'expected_version'], []),
    'rooms.photo_upload': (['id', 'expected_version', 'data_base64'], []),
    'rooms.photo_get': (['id', 'photo_id'], []),
    'listening_profiles.list': ([], ['room_id']),
    'listening_profiles.create': (['room_id', 'name', 'kind', 'fit_positions', 'verify_positions'], ['target', 'notes']),
    'listening_profiles.get': (['id'], []),
    'listening_profiles.update': (['id', 'expected_version'], ['name', 'fit_positions', 'verify_positions', 'target', 'notes']),
    'listening_profiles.clone': (['id', 'expected_version', 'name'], []),
    'listening_profiles.archive': (['id', 'expected_version'], []),
    'listening_profiles.bind': (['id', 'expected_version', 'bindings'], []),
    'listening_profiles.guide': (['id'], []),
}
OPERATION_SCHEMAS = {op: obj({k: FIELDS[k] for k in required + optional}, required)
                     for op, (required, optional) in SCHEMAS.items()}
DEFAULT_TARGET = {'low_hz': 30, 'high_hz': 200, 'tilt_db': 0}
CHANNELS = ('left', 'right', 'sub')
MAX_RECORD = 1048576
MAX_BYTES = 64 * 1048576
LIMITS = {'rooms': 128, 'profiles': 128, 'photos': 512}


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def digest(value):
    return hashlib.sha256(encoded(value)).hexdigest()


def session_hash(record):
    # Analysis/proposal generation is not a measurement-input edit. Cancellation
    # is checked independently. Any new import, plan or metadata edit invalidates.
    return digest({k: v for k, v in record.items() if k not in
                   ('analysis', 'proposal', 'state', 'sound_disabled')})


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch('[a-f0-9]{32}', value):
        raise ValueError('invalid generated ID')
    return value


def fresh(name):
    now = time.time()
    return {'id': uuid.uuid4().hex, 'name': name, 'version': 1,
            'created_at': now, 'updated_at': now, 'archived': False}


def room_hash(room):
    return digest({k: room[k] for k in ('annotations', 'photo_id', 'archived')})


class Store:
    def __init__(self, root):
        self.root = Path(root).absolute()
        with self._root_fd() as fd:
            self._mkdir(fd, 'room-domain')
            with self._directory(fd, 'room-domain') as domain:
                for collection in LIMITS:
                    self._mkdir(domain, collection)

    @contextmanager
    def _root_fd(self):
        # Traverse every ancestor without following symlinks, not just the leaf.
        fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
        try:
            for part in self.root.parts[1:]:
                if part in ('.', '..'):
                    raise ValueError('unsafe root')
                child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                os.close(fd)
                fd = child
            yield fd
        finally:
            os.close(fd)

    @staticmethod
    def _mkdir(fd, name):
        try:
            os.mkdir(name, 0o700, dir_fd=fd)
        except FileExistsError:
            pass

    @contextmanager
    def _directory(self, parent, name):
        fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
        try:
            info = os.fstat(fd)
            if info.st_uid != os.getuid() or info.st_mode & 0o077:
                raise ValueError('domain directory must be private and owned')
            yield fd
        finally:
            os.close(fd)

    @contextmanager
    def _collection(self, collection):
        if collection not in LIMITS:
            raise ValueError('invalid collection')
        with self._root_fd() as root, self._directory(root, 'room-domain') as domain:
            with self._directory(domain, collection) as fd:
                yield fd

    @staticmethod
    def _read_fd(fd, name, bound=MAX_RECORD):
        try:
            file_fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
        except FileNotFoundError:
            raise DomainFailure('not_found', 'Record not found') from None
        with os.fdopen(file_fd, 'rb') as stream:
            info = os.fstat(stream.fileno())
            if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or
                    info.st_uid != os.getuid() or info.st_mode & 0o077):
                raise ValueError('unsafe record')
            if info.st_size > bound:
                raise DomainFailure('storage_limit', 'Record exceeds retrieval limit')
            data = stream.read(bound + 1)
            if len(data) > bound:
                raise DomainFailure('storage_limit', 'Record exceeds retrieval limit')
            return json.loads(data)

    def _read(self, collection, value):
        with self._collection(collection) as fd:
            return self._read_fd(fd, identifier(value) + '.json')

    def _inventory(self):
        total, counts = 0, {}
        for collection in LIMITS:
            with self._collection(collection) as fd:
                names = os.listdir(fd)
                counts[collection] = 0
                for name in names:
                    if re.fullmatch(r'\.write-[a-f0-9]{32}', name):
                        self._recover_temporary(fd, name)
                        continue
                    if not re.fullmatch('[a-f0-9]{32}\\.json', name):
                        raise ValueError('unexpected domain artifact')
                    info = os.stat(name, dir_fd=fd, follow_symlinks=False)
                    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                        raise ValueError('unsafe domain artifact')
                    total += info.st_size
                    counts[collection] += 1
        return total, counts

    @staticmethod
    def _recover_temporary(fd, name):
        # Only called during inventory under the caller's thread/process lock.
        # A crash can leave an empty/partial JSON write, so content is not parsed.
        # Never follow or remove symlinks, hardlinks, foreign/public/oversize files,
        # or artifacts outside our exact exclusive-writer temporary namespace.
        temporary_fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
        try:
            info = os.fstat(temporary_fd)
            if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or
                    info.st_uid != os.getuid() or info.st_mode & 0o077 or info.st_size > MAX_RECORD):
                raise ValueError('unsafe abandoned write artifact')
            current = os.stat(name, dir_fd=fd, follow_symlinks=False)
            if (current.st_dev, current.st_ino) != (info.st_dev, info.st_ino):
                raise ValueError('abandoned write artifact changed')
            os.unlink(name, dir_fd=fd)
            os.fsync(fd)
        finally:
            os.close(temporary_fd)

    def _save(self, collection, record):
        data = encoded(record)
        name = identifier(record['id']) + '.json'
        total, counts = self._inventory()
        with self._collection(collection) as fd:
            try:
                previous = os.stat(name, dir_fd=fd, follow_symlinks=False).st_size
                exists = True
            except FileNotFoundError:
                previous, exists = 0, False
            if (len(data) > MAX_RECORD or total - previous + len(data) > MAX_BYTES or
                    not exists and counts[collection] >= LIMITS[collection]):
                raise DomainFailure('storage_limit', 'Private room storage limit reached',
                                    ['Operator archive/export required; no automatic eviction'])
            temporary = '.write-' + uuid.uuid4().hex
            file_fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                              0o600, dir_fd=fd)
            try:
                with os.fdopen(file_fd, 'wb') as stream:
                    stream.write(data)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, name, src_dir_fd=fd, dst_dir_fd=fd)
                try:
                    os.fsync(fd)
                except OSError:
                    raise DurabilityFailure() from None
            finally:
                try:
                    os.unlink(temporary, dir_fd=fd)
                except FileNotFoundError:
                    pass
        return record

    def _list(self, collection):
        self._inventory()
        with self._collection(collection) as fd:
            return [self._read_fd(fd, name) for name in sorted(os.listdir(fd))]

    @staticmethod
    def _editable(record, version):
        if record['version'] != version:
            raise DomainFailure('version_conflict', 'Record changed; reload before editing')
        if record['archived']:
            raise DomainFailure('archived', 'Archived record is immutable')

    def _mutate(self, collection, record):
        record['version'] += 1
        record['updated_at'] = time.time()
        return self._save(collection, record)

    def execute(self, operation, args, application):
        if operation not in SCHEMAS or not isinstance(args, dict):
            raise ValueError('unknown operation')
        # A JSON round trip also rejects nonfinite numbers before schema comparison.
        encoded(args)
        try:
            Draft202012Validator(OPERATION_SCHEMAS[operation]).validate(args)
        except ValidationError:
            raise ValueError('argument schema violation') from None
        a = copy.deepcopy(args)
        if operation.startswith('rooms.'):
            return self._rooms(operation.split('.')[1], a)
        return self._profiles(operation.split('.')[1], a, application)

    def _rooms(self, op, a):
        if op == 'list':
            return self._list('rooms')
        if op == 'create':
            return self._save('rooms', fresh(a['name']) | {'notes': a.get('notes', ''),
                'annotations': [], 'annotations_confirmed': False, 'photo_id': None, 'photos': []})
        room = self._read('rooms', a['id'])
        if op == 'get':
            return room
        if op == 'photo_get':
            if a['photo_id'] not in {p['id'] for p in room['photos']}:
                raise DomainFailure('not_found', 'Photo not found in this room')
            return self._read('photos', a['photo_id'])
        self._editable(room, a['expected_version'])
        if op == 'update':
            if not set(a) & {'name', 'notes'}:
                raise ValueError('empty edit')
            for k in ('name', 'notes'):
                if k in a:
                    room[k] = a[k]
        elif op == 'archive':
            room['archived'] = True
        elif op == 'annotate':
            existing = {item['id']: item for item in room['annotations']}
            seen = set()
            for item in a['annotations']:
                if 'id' in item and item['id'] not in existing:
                    raise ValueError('annotation ID does not belong to room')
                item.setdefault('id', uuid.uuid4().hex)
                if item['id'] in seen:
                    raise ValueError('duplicate annotation ID')
                seen.add(item['id'])
                if item['kind'] in ('fit', 'verify') and not {'mic_height_m', 'orientation'} <= item.keys():
                    raise ValueError('microphone placement required')
            room['annotations'] = a['annotations']
            room['annotations_confirmed'] = False
        elif op == 'confirm':
            if not room['annotations']:
                raise ValueError('no annotations to confirm')
            room['annotations_confirmed'] = True
        elif op == 'photo_upload':
            photo = self._photo(a['data_base64'])
            self._save('photos', photo)
            room['photos'].append({k: v for k, v in photo.items() if k != 'data_base64'})
            room['photo_id'] = photo['id']
            room['annotations_confirmed'] = False
            try:
                return self._mutate('rooms', room)
            except DurabilityFailure:
                # Room replacement already references this asset. Keep it even
                # though the directory's crash durability could not be confirmed.
                raise
            except Exception:
                # Roll back this not-yet-referenced new asset, never evict history.
                with self._collection('photos') as fd:
                    os.unlink(photo['id'] + '.json', dir_fd=fd)
                    os.fsync(fd)
                raise
        return self._mutate('rooms', room)

    @staticmethod
    def _photo(text):
        from PIL import Image, ImageOps
        try:
            data = base64.b64decode(text, validate=True)
            if not 1 <= len(data) <= 525000:
                raise ValueError('image byte limit')
            with warnings.catch_warnings():
                warnings.simplefilter('error', Image.DecompressionBombWarning)
                with Image.open(io.BytesIO(data)) as image:
                    if (image.format not in ('JPEG', 'PNG') or getattr(image, 'n_frames', 1) != 1 or
                            max(image.size) > 4096 or image.width * image.height > 12000000):
                        raise ValueError('image format/dimension limit')
                    image.load()
                    oriented = ImageOps.exif_transpose(image)
                    oriented.thumbnail((1600, 1600))
                    # Fresh pixels do not carry EXIF/ICC/comments or source metadata.
                    rgb = oriented.convert('RGB')
                    clean = Image.new('RGB', rgb.size)
                    clean.paste(rgb)
                    output = io.BytesIO()
                    clean.save(output, format='JPEG', quality=82, optimize=True)
                    derivative = output.getvalue()
                    if len(derivative) > 525000:
                        raise ValueError('derivative byte limit')
                    return {'id': uuid.uuid4().hex, 'mime_type': 'image/jpeg',
                            'data_base64': base64.b64encode(derivative).decode('ascii'),
                            'width': clean.width, 'height': clean.height,
                            'sha256': hashlib.sha256(derivative).hexdigest()}
        except (ValueError, OSError, SyntaxError, binascii.Error,
                Image.DecompressionBombWarning, Image.DecompressionBombError):
            raise DomainFailure('photo_invalid', 'Use a bounded single-frame JPEG or PNG image') from None

    def _validate_plan(self, profile, room):
        if room['archived']:
            raise DomainFailure('archived', 'Room is archived')
        positions = {item['id']: item for item in room['annotations']}
        fit = [item['position_id'] for item in profile['fit_positions']]
        verify = profile['verify_positions']
        if (len(fit) != len(set(fit)) or set(fit) & set(verify) or
                any(positions.get(p, {}).get('kind') != 'fit' for p in fit) or
                any(positions.get(p, {}).get('kind') != 'verify' for p in verify)):
            raise ValueError('fit/held-out positions must belong to room and remain disjoint')
        if profile['kind'] == 'seat' and len(fit) != 1:
            raise ValueError('seat requires one fit position')
        if profile['kind'] == 'whole-room' and len(fit) < 2:
            raise ValueError('whole-room requires at least two fit positions')
        if profile['target']['low_hz'] >= profile['target']['high_hz']:
            raise ValueError('target frequency order')

    @staticmethod
    def _reset(profile, room, reason):
        profile['bindings'] = []
        profile['room_snapshot_hash'] = room_hash(room)
        profile['binding_room_hash'] = None
        profile['provenance'] = {'kind': 'planning-only', 'reason': reason}

    def _profiles(self, op, a, app):
        if op == 'list':
            return [self._view(p, app) for p in self._list('profiles')
                    if 'room_id' not in a or p['room_id'] == a['room_id']]
        if op == 'create':
            room = self._read('rooms', a['room_id'])
            p = fresh(a['name']) | {k: a[k] for k in ('room_id', 'kind', 'fit_positions', 'verify_positions')}
            p.update(target=a.get('target', copy.deepcopy(DEFAULT_TARGET)), notes=a.get('notes', ''))
            self._validate_plan(p, room)
            self._reset(p, room, 'new-profile')
            return self._view(self._save('profiles', p), app)
        p = self._read('profiles', a['id'])
        if op == 'get':
            return self._view(p, app)
        if op == 'guide':
            return self._guide(p, app)
        self._editable(p, a['expected_version'])
        room = self._read('rooms', p['room_id'])
        if op == 'archive':
            p['archived'] = True
        elif op == 'clone':
            self._validate_plan(p, room)
            p.update(fresh(a['name']))
            self._reset(p, room, 'clone-requires-independent-binding')
            return self._view(self._save('profiles', p), app)
        elif op == 'update':
            fields = set(a) - {'id', 'expected_version'}
            if not fields:
                raise ValueError('empty edit')
            p.update({k: a[k] for k in fields})
            self._validate_plan(p, room)
            if fields & {'fit_positions', 'verify_positions', 'target'}:
                self._reset(p, room, 'plan-edited')
        elif op == 'bind':
            self._validate_plan(p, room)
            if not room['annotations_confirmed']:
                raise DomainFailure('confirmation_required', 'Confirm the room map before binding measurements')
            p['bindings'] = self._bindings(p, room, a['bindings'], app)
            p['room_snapshot_hash'] = room_hash(room)
            p['binding_room_hash'] = room_hash(room)
            p['provenance'] = {'kind': 'user-imported-measured-source-claim',
                               'independently_verified': False, 'bound_at': time.time()}
        return self._view(self._mutate('profiles', p), app)

    def _source(self, app, value):
        # Harden the existing sessions directory too; application.read alone only
        # checks the leaf path. Same root, 4MiB existing application record cap.
        with self._root_fd() as root, self._directory(root, 'sessions') as fd:
            raw = self._read_fd(fd, identifier(value) + '.json', 4 * 1048576)
        return raw, app.session_view(raw)

    def _bindings(self, profile, room, inputs, app):
        positions = {item['id']: item for item in room['annotations']}
        allowed = {item['position_id'] for item in profile['fit_positions']} | set(profile['verify_positions'])
        seen, sources, result, cache = set(), set(), [], {}
        rates, calibration = set(), {channel: set() for channel in CHANNELS}
        for binding in inputs:
            key = (binding['position_id'], binding['channel'])
            source_key = (binding['session_id'], binding['measurement_id'])
            if key in seen or source_key in sources or key[0] not in allowed:
                raise ValueError('duplicate or foreign source binding')
            seen.add(key)
            sources.add(source_key)
            if binding['session_id'] not in cache:
                cache[binding['session_id']] = self._source(app, binding['session_id'])
            raw, view = cache[binding['session_id']]
            file = next((f for f in raw.get('files', []) if f['id'] == binding['measurement_id']), None)
            metadata = file.get('metadata', {}) if file else {}
            if (not file or file.get('format') != 'rew-response' or view.get('state') == 'cancelled' or
                    metadata.get('source') != 'measured' or metadata.get('channel') != key[1] or
                    metadata.get('position') != key[0] or
                    metadata.get('orientation') != positions[key[0]]['orientation'] or
                    metadata.get('sample_rate') not in (44100, 48000, 88200, 96000, 176400, 192000) or
                    metadata.get('calibration_applied') is not True or
                    not re.fullmatch('[a-f0-9]{64}', metadata.get('calibration_sha256', '')) or
                    not metadata.get('timing_reference', '').strip() or
                    metadata.get('timing_quality') not in ('unknown', 'unverified', 'verified')):
                raise DomainFailure('source_invalid', 'Measurement source does not match confirmed position/provenance',
                                    ['Import measured REW response with explicit matching metadata; synthetic is not measured'])
            result.append(binding | {'session_sha256': session_hash(raw), 'measurement_sha256': digest(file)})
            rates.add(metadata['sample_rate'])
            calibration[key[1]].add((metadata['calibration_sha256'], metadata.get('microphone_serial')))
        if len(rates) > 1 or any(len(values) > 1 for values in calibration.values()):
            raise DomainFailure('source_invalid', 'Sources use incompatible sample-rate or microphone provenance')
        return result

    def _view(self, profile, app):
        p = copy.deepcopy(profile)
        room = self._read('rooms', p['room_id'])
        p.update(applied=False, verified_acoustically=False, sound_disabled=True,
                 analysis_available=False, proposal_available=False, analysis_references=[], proposal_references=[],
                 binding_valid=False, invalidated_reason=None, fit_complete=False, held_out_complete=False,
                 fitting_supported=len({x['weight'] for x in p['fit_positions']}) == 1,
                 verification_status='held-out-imports-required')
        p['weighting_status'] = 'uniform-spatial-median' if p['fitting_supported'] else 'unsupported-nonuniform-weights'
        if p['archived']:
            p['status'] = 'archived'
            return p
        if room['archived']:
            p['invalidated_reason'] = 'room-archived'
        elif p['room_snapshot_hash'] != room_hash(room):
            p['invalidated_reason'] = 'room-map-changed'
        elif not room['annotations_confirmed']:
            if p['bindings']:
                p['invalidated_reason'] = 'room-confirmation-lost'
            else:
                p['status'] = 'confirmation-required'
                return p
        elif p['bindings']:
            inputs = [{k: b[k] for k in BINDING['properties']} for b in p['bindings']]
            try:
                current = self._bindings(p, room, inputs, app)
                if current != p['bindings'] or p['binding_room_hash'] != room_hash(room):
                    p['invalidated_reason'] = 'measurement-source-changed'
            except (DomainFailure, ValueError, OSError, KeyError, TypeError):
                p['invalidated_reason'] = 'measurement-source-unavailable-or-invalid'
        if p['invalidated_reason']:
            p['status'] = 'invalidated'
            return p
        p['binding_valid'] = bool(p['bindings'])
        fit_expected = {(x['position_id'], c) for x in p['fit_positions'] for c in CHANNELS}
        verify_expected = {(x, c) for x in p['verify_positions'] for c in CHANNELS}
        actual = {(b['position_id'], b['channel']) for b in p['bindings']}
        p['fit_complete'] = fit_expected <= actual
        p['held_out_complete'] = verify_expected <= actual
        p['verification_status'] = 'held-out-imports-ready-for-review' if p['held_out_complete'] else 'held-out-imports-required'
        p['status'] = 'fit-imports-complete' if p['fit_complete'] else 'collecting-fit' if actual else 'unmeasured'
        # References only: engine uses spatial median, no weights/custom target.
        fit = {x['position_id'] for x in p['fit_positions']}
        if p['binding_valid'] and len({x['weight'] for x in p['fit_positions']}) == 1 and p['target'] == DEFAULT_TARGET:
            for session_id in sorted({b['session_id'] for b in p['bindings'] if b['position_id'] in fit}):
                raw, view = self._source(app, session_id)
                plan = raw.get('plan', {})
                bound_ids = {b['measurement_id'] for b in p['bindings'] if b['session_id'] == session_id and b['position_id'] in fit}
                response_ids = {f['id'] for f in raw.get('files', []) if f.get('format') == 'rew-response'}
                if (set(plan.get('positions', [])) != fit or set(plan.get('channels', [])) != set(CHANNELS) or
                        bound_ids != response_ids or len(bound_ids) != len(fit) * 3):
                    continue
                if view.get('analysis_available') and view.get('analysis', {}).get('source') == 'measured':
                    p['analysis_references'].append({'session_id': session_id, 'session_sha256': session_hash(raw)})
                if view.get('proposal_available') and p['analysis_references']:
                    p['proposal_references'].append({'session_id': session_id, 'session_sha256': session_hash(raw)})
        p['analysis_available'] = bool(p['analysis_references'])
        p['proposal_available'] = bool(p['proposal_references'])
        if p['fit_complete']:
            if not p['fitting_supported']:
                p['status'] = 'unsupported-nonuniform-weights'
            elif p['proposal_available']:
                p['status'] = 'held-out-imports-complete' if p['held_out_complete'] else 'collecting-held-out'
            elif p['analysis_available']:
                p['status'] = 'fit-analysis-available'
        p['engine_limitations'] = 'Existing engine uses spatial median and its own magnitude target; profile target/weights are planning only. References are not applied correction or held-out acoustic verification.'
        return p

    def _guide(self, profile, app):
        view = self._view(profile, app)
        room = self._read('rooms', profile['room_id'])
        base = {'profile_id': profile['id'], 'status': view['status'], 'sound_disabled': True,
                'capture_available': False, 'metadata_source': 'measured',
                'warning': 'Measure externally only with separate attended authorization. Photo coordinates are not distances.'}
        if profile['archived'] or room['archived']:
            return base | {'action': 'create-active-profile'}
        if not room['annotations_confirmed'] or view['invalidated_reason'] == 'room-map-changed':
            return base | {'action': 'confirm-room-and-update-profile-plan'}
        if view['invalidated_reason']:
            return base | {'action': 'rebind-current-measurement-sources'}
        actual = {(b['position_id'], b['channel']) for b in profile['bindings']}
        positions = {item['id']: item for item in room['annotations']}
        ordered = [(x['position_id'], 'fit') for x in profile['fit_positions']]
        if view.get('fit_complete'):
            if not view['fitting_supported']:
                return base | {'action': 'choose-uniform-weights-or-external-qualified-fitting',
                               'weighting_status': view['weighting_status']}
            if not view['proposal_available']:
                fit = {x['position_id'] for x in profile['fit_positions']}
                for sid in sorted({b['session_id'] for b in profile['bindings'] if b['position_id'] in fit}):
                    raw, projected = self._source(app, sid)
                    plan = raw.get('plan', {})
                    if (set(plan.get('positions', [])) == fit and set(plan.get('channels', [])) == set(CHANNELS) and
                            {f['id'] for f in raw.get('files', []) if f.get('format') == 'rew-response'} ==
                            {b['measurement_id'] for b in profile['bindings'] if b['session_id'] == sid and b['position_id'] in fit} and
                            profile['target'] == DEFAULT_TARGET):
                        return base | {'action': 'review-and-propose-fit-only' if projected.get('analysis_available') else 'analyze-fit-only',
                                       'operation': 'calibration.propose' if projected.get('analysis_available') else 'calibration.analyze',
                                       'args': {'id': sid}, 'held_out_used_for_fit': False}
                return base | {'action': 'prepare-compatible-fit-only-calibration-plan',
                               'fit_position_ids': sorted(fit), 'held_out_used_for_fit': False,
                               'warning': 'Current profile target is planning only; existing engine cannot implement arbitrary targets. Fit session must contain only fit sources, never held-out imports.'}
            ordered += [(x, 'verify-held-out') for x in profile['verify_positions']]
        for position, role in ordered:
            for channel in CHANNELS:
                if (position, channel) not in actual:
                    return base | {'action': 'import-and-bind-rew-response', 'position_id': position,
                                   'position': positions[position], 'role': role, 'channel': channel,
                                   'required_metadata': ['source', 'channel', 'position', 'sample_rate', 'orientation',
                                                         'calibration_sha256', 'calibration_applied', 'timing_reference', 'timing_quality']}
        return base | {'action': 'review-fit-analysis-and-separate-held-out-validation',
                       'verified_acoustically': False}
