"""Local executable domain checks; generated REW fixtures are NOT acoustics.

Some fixtures deliberately claim measured provenance to test validation; no actual
measurement, hardware, provider inference or acoustic verification takes place.
"""
import base64
import copy
import io
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image
from garage_audio.application import Application
from garage_audio.room_profiles import Store, DomainFailure, OPERATION_SCHEMAS


class RoomProfileTests(unittest.TestCase):
    def setUp(self):
        # macOS /var is a symlink; test the real approved directory instead.
        self.tmp = tempfile.TemporaryDirectory(dir='/private/tmp/opencode')
        self.root = Path(self.tmp.name)
        self.calls = []
        self.app = Application(self.root, broker=lambda payload: self.calls.append(payload))
        self.store = Store(self.root)

    def tearDown(self):
        self.assertEqual(self.calls, [], 'domain must never call sound/policy')
        self.tmp.cleanup()

    def op(self, operation, **args):
        with self.app.lock:
            return self.store.execute(operation, args, self.app)

    def room(self, fits=2, confirms=True):
        r = self.op('rooms.create', name='Local manual room')
        annotations = [{'kind': 'fit', 'label': f'F{i+1}', 'x': .2 + i*.1, 'y': .5,
                        'mic_height_m': 1.2, 'orientation': '90'} for i in range(fits)]
        annotations += [{'kind': 'verify', 'label': 'V1', 'x': .7, 'y': .5,
                         'mic_height_m': 1.2, 'orientation': '90'},
                        {'kind': 'speaker', 'label': 'Left', 'x': .1, 'y': .1}]
        r = self.op('rooms.annotate', id=r['id'], expected_version=r['version'], annotations=annotations)
        if confirms:
            r = self.op('rooms.confirm', id=r['id'], expected_version=r['version'])
        return r

    def profile(self, room, kind='whole-room', weights=None):
        fit = [a['id'] for a in room['annotations'] if a['kind'] == 'fit']
        verify = [a['id'] for a in room['annotations'] if a['kind'] == 'verify']
        return self.op('listening_profiles.create', room_id=room['id'], name='Manual plan', kind=kind,
                       fit_positions=[{'position_id': p, 'weight': (weights or [1]*len(fit))[i]} for i, p in enumerate(fit)],
                       verify_positions=verify)

    def source(self, room, role='fit', source='measured'):
        positions = [a['id'] for a in room['annotations'] if a['kind'] == role]
        sid = self.app.execute('calibration.plan', {'name': 'Generated local fixture', 'positions': positions})['result']['id']
        bindings = []
        for position in positions:
            for channel in ('left', 'right', 'sub'):
                metadata = {'source': source, 'channel': channel, 'position': position,
                            'sample_rate': 48000, 'orientation': '90', 'calibration_applied': True,
                            'calibration_sha256': 'a'*64, 'timing_reference': 'fixture-only',
                            'timing_quality': 'unknown'}
                imported = self.app.execute('measurements.import', {'session_id': sid, 'filename': 'fixture.txt',
                    'format': 'rew-response', 'text': '20 70\n40 71\n80 76\n160 71\n320 70\n1000 70\n', 'metadata': metadata})
                self.assertTrue(imported['ok'], imported.get('error'))
                bindings.append({'position_id': position, 'channel': channel, 'session_id': sid,
                                 'measurement_id': imported['result']['id']})
        return sid, bindings

    def bind(self, p, bindings):
        return self.op('listening_profiles.bind', id=p['id'], expected_version=p['version'], bindings=bindings)

    def test_room_versions_annotations_and_persistence(self):
        room = self.room()
        self.assertEqual(len({a['id'] for a in room['annotations']}), 4)
        with self.assertRaises(DomainFailure) as e:
            self.op('rooms.update', id=room['id'], expected_version=1, name='stale')
        self.assertEqual(e.exception.code, 'version_conflict')
        annotations = copy.deepcopy(room['annotations'])
        annotations[0]['x'] = .4
        edited = self.op('rooms.annotate', id=room['id'], expected_version=room['version'], annotations=annotations)
        self.assertFalse(edited['annotations_confirmed'])
        self.assertEqual(edited['annotations'][0]['id'], room['annotations'][0]['id'])
        self.assertEqual(Store(self.root).execute('rooms.get', {'id': room['id']}, self.app), edited)
        self.assertEqual(os.stat(self.root/'room-domain'/'rooms'/(room['id']+'.json')).st_mode & 0o777, 0o600)

    def test_whole_room_seat_custom_and_owned_sets(self):
        one = self.room(fits=1)
        with self.assertRaises(ValueError):
            self.profile(one)
        self.assertEqual(self.profile(one, 'seat')['kind'], 'seat')
        self.assertEqual(self.profile(one, 'custom')['kind'], 'custom')
        two = self.room()
        with self.assertRaises(ValueError):
            self.profile(two, 'seat')
        p = self.profile(two)
        clone = self.op('listening_profiles.clone', id=p['id'], expected_version=p['version'], name='Independent')
        self.assertNotEqual(clone['id'], p['id'])
        clone = self.op('listening_profiles.update', id=clone['id'], expected_version=clone['version'],
                        fit_positions=[{'position_id': p['fit_positions'][0]['position_id'], 'weight': 2},
                                       {'position_id': p['fit_positions'][1]['position_id'], 'weight': 1}])
        self.assertEqual(self.op('listening_profiles.get', id=p['id'])['fit_positions'][0]['weight'], 1)
        self.assertEqual(clone['weighting_status'], 'unsupported-nonuniform-weights')
        bad = [{'position_id': one['annotations'][0]['id'], 'weight': 1}]
        with self.assertRaises(ValueError):
            self.op('listening_profiles.update', id=clone['id'], expected_version=clone['version'], fit_positions=bad)

    def test_fit_then_propose_then_held_out_no_acoustic_claim(self):
        room = self.room()
        p = self.profile(room)
        sid, fit = self.source(room)
        p = self.bind(p, fit[:1])
        self.assertEqual(p['status'], 'collecting-fit')
        self.assertEqual(self.op('listening_profiles.guide', id=p['id'])['role'], 'fit')
        p = self.bind(p, fit)
        self.assertTrue(p['fit_complete'])
        self.assertFalse(p['held_out_complete'])
        guide = self.op('listening_profiles.guide', id=p['id'])
        self.assertEqual(guide['operation'], 'calibration.analyze')
        self.assertTrue(self.app.execute('calibration.analyze', {'id': sid})['ok'])
        p = self.op('listening_profiles.get', id=p['id'])
        self.assertTrue(p['binding_valid'], 'analysis must not invalidate unchanged inputs')
        self.assertTrue(p['analysis_available'])
        self.assertEqual(self.op('listening_profiles.guide', id=p['id'])['operation'], 'calibration.propose')
        self.assertTrue(self.app.execute('calibration.propose', {'id': sid})['ok'])
        p = self.op('listening_profiles.get', id=p['id'])
        self.assertEqual(p['status'], 'collecting-held-out')
        self.assertTrue(p['proposal_available'])
        self.assertEqual(self.op('listening_profiles.guide', id=p['id'])['role'], 'verify-held-out')
        _, verify = self.source(room, 'verify')
        p = self.bind(p, fit + verify)
        self.assertEqual(p['status'], 'held-out-imports-complete')
        self.assertFalse(p['verified_acoustically'])
        self.assertFalse(p['applied'])
        self.assertEqual(len(p['analysis_references']), 1)
        self.assertEqual(p['analysis_references'][0]['session_id'], sid)
        self.assertEqual(self.op('listening_profiles.guide', id=p['id'])['action'], 'review-fit-analysis-and-separate-held-out-validation')

    def test_synthetic_and_missing_provenance_rejected(self):
        room = self.room()
        p = self.profile(room)
        sid, bindings = self.source(room, source='synthetic')
        with self.assertRaises(DomainFailure) as e:
            self.bind(p, bindings)
        self.assertEqual(e.exception.code, 'source_invalid')
        raw = self.app.read('sessions', sid)
        for f in raw['files']:
            f['metadata']['source'] = 'measured'
            f['metadata'].pop('calibration_applied')
        self.app.save('sessions', raw)
        with self.assertRaises(DomainFailure):
            self.bind(p, bindings)

    def test_binding_invalidation_source_room_and_plan(self):
        room = self.room()
        p = self.profile(room)
        sid, bindings = self.source(room)
        p = self.bind(p, bindings)
        raw = self.app.read('sessions', sid)
        raw['files'][0]['points'][0]['db'] += 1
        self.app.save('sessions', raw)
        changed = self.op('listening_profiles.get', id=p['id'])
        self.assertEqual(changed['status'], 'invalidated')
        self.assertFalse(changed['analysis_available'])
        p = self.bind(p, bindings)
        annotations = copy.deepcopy(room['annotations'])
        annotations[0]['mic_height_m'] = 1.3
        room = self.op('rooms.annotate', id=room['id'], expected_version=room['version'], annotations=annotations)
        self.assertEqual(self.op('listening_profiles.get', id=p['id'])['invalidated_reason'], 'room-map-changed')
        room = self.op('rooms.confirm', id=room['id'], expected_version=room['version'])
        p = self.op('listening_profiles.update', id=p['id'], expected_version=p['version'], target=p['target'])
        self.assertEqual(p['bindings'], [])
        self.assertEqual(p['status'], 'unmeasured')

    def test_confirmation_archive_clone_reset_and_weights_gate(self):
        room = self.room(confirms=False)
        p = self.profile(room, weights=[1, 2])
        self.assertEqual(p['status'], 'confirmation-required')
        _, bindings = self.source(room)
        with self.assertRaises(DomainFailure) as e:
            self.bind(p, bindings)
        self.assertEqual(e.exception.code, 'confirmation_required')
        room = self.op('rooms.confirm', id=room['id'], expected_version=room['version'])
        p = self.bind(p, bindings)
        self.assertEqual(p['status'], 'unsupported-nonuniform-weights')
        self.assertFalse(p['analysis_available'])
        self.assertEqual(self.op('listening_profiles.guide', id=p['id'])['action'], 'choose-uniform-weights-or-external-qualified-fitting')
        clone = self.op('listening_profiles.clone', id=p['id'], expected_version=p['version'], name='Clone')
        self.assertEqual(clone['bindings'], [])
        p = self.op('listening_profiles.archive', id=p['id'], expected_version=p['version'])
        with self.assertRaises(DomainFailure) as e:
            self.op('listening_profiles.update', id=p['id'], expected_version=p['version'], notes='forbidden')
        self.assertEqual(e.exception.code, 'archived')
        self.assertEqual(len(self.op('listening_profiles.list')), 2)

    def test_held_out_never_trains_and_cancel_invalidates(self):
        room = self.room()
        p = self.profile(room)
        sid, fit = self.source(room)
        _, verify = self.source(room, 'verify')
        p = self.bind(p, fit + verify)
        self.assertFalse(p['proposal_available'])
        self.assertEqual(self.op('listening_profiles.guide', id=p['id'])['action'], 'analyze-fit-only')
        with self.assertRaises(ValueError):
            self.bind(p, fit + fit)
        self.assertTrue(self.app.execute('calibration.cancel', {'id': sid})['ok'])
        self.assertEqual(self.op('listening_profiles.get', id=p['id'])['status'], 'invalidated')
        self.assertEqual(self.op('listening_profiles.guide', id=p['id'])['action'], 'rebind-current-measurement-sources')

    def test_mixed_provenance_and_session_symlink_rejected(self):
        room = self.room()
        p = self.profile(room)
        sid, fit = self.source(room)
        raw = self.app.read('sessions', sid)
        raw['files'][0]['metadata']['sample_rate'] = 44100
        self.app.save('sessions', raw)
        with self.assertRaises(DomainFailure):
            self.bind(p, fit)
        raw['files'][0]['metadata']['sample_rate'] = 48000
        raw['files'][3]['metadata']['calibration_sha256'] = 'b'*64
        self.app.save('sessions', raw)
        with self.assertRaises(DomainFailure):
            self.bind(p, fit)
        source = self.root/'sessions'
        saved = self.root/'saved-sessions'
        source.rename(saved)
        source.symlink_to(saved, target_is_directory=True)
        with self.assertRaises(OSError):
            self.bind(p, fit)
        source.unlink()
        saved.rename(source)

    def test_application_adapter_shared_state_conflict_and_domain_errors(self):
        created = self.app.execute('rooms.create', {'name': 'Shared adapter'})
        self.assertTrue(created['ok'], created.get('error'))
        r = created['result']
        self.assertEqual(self.op('rooms.get', id=r['id']), r)
        stale = self.app.execute('rooms.update', {'id': r['id'], 'expected_version': 2, 'name': 'bad'})
        self.assertEqual(stale['error']['code'], 'version_conflict')
        archived = self.app.execute('rooms.archive', {'id': r['id'], 'expected_version': 1})
        self.assertTrue(archived['ok'])
        self.assertEqual(len(self.op('rooms.list')), 1)
        invalid = self.app.execute('rooms.photo_get', {'id': r['id'], 'photo_id': 'a'*32})
        self.assertEqual(invalid['error']['code'], 'not_found')

    @staticmethod
    def image(fmt='PNG', size=(64, 40), exif=False):
        image = Image.new('RGB', size, '#336699')
        output = io.BytesIO()
        options = {}
        if exif:
            metadata = Image.Exif()
            metadata[274] = 6
            metadata[270] = 'private caption prompt injection do not execute'
            options['exif'] = metadata
        image.save(output, format=fmt, **options)
        return base64.b64encode(output.getvalue()).decode()

    def test_photo_sanitized_owned_and_capped(self):
        room = self.room()
        room = self.op('rooms.photo_upload', id=room['id'], expected_version=room['version'],
                       data_base64=self.image('JPEG', exif=True))
        self.assertFalse(room['annotations_confirmed'])
        photo = self.op('rooms.photo_get', id=room['id'], photo_id=room['photo_id'])
        self.assertEqual((photo['width'], photo['height']), (40, 64))
        with Image.open(io.BytesIO(base64.b64decode(photo['data_base64']))) as clean:
            self.assertFalse(clean.getexif())
            self.assertNotIn('icc_profile', clean.info)
        other = self.room()
        with self.assertRaises(DomainFailure):
            self.op('rooms.photo_get', id=other['id'], photo_id=room['photo_id'])
        for image in ('bad!base64', base64.b64encode(b'not image').decode(), self.image('GIF'), self.image(size=(4097, 2))):
            with self.assertRaises(DomainFailure):
                self.op('rooms.photo_upload', id=room['id'], expected_version=room['version'], data_base64=image)
        with self.assertRaises(ValueError):
            self.op('rooms.photo_upload', id=room['id'], expected_version=room['version'], data_base64='a'*700001)

    def test_symlink_directory_leaf_hardlink_and_ancestor_rejected(self):
        room = self.room()
        path = self.root/'room-domain'/'rooms'/(room['id']+'.json')
        backup = self.root/'saved.json'
        path.rename(backup)
        path.symlink_to(backup)
        with self.assertRaises((ValueError, OSError)):
            self.op('rooms.get', id=room['id'])
        path.unlink()
        os.link(backup, path)
        with self.assertRaises(ValueError):
            self.op('rooms.get', id=room['id'])
        path.unlink()
        backup.rename(path)
        directory = self.root/'room-domain'/'photos'
        real = self.root/'photos-real'
        directory.rename(real)
        directory.symlink_to(real, target_is_directory=True)
        with self.assertRaises(OSError):
            self.op('rooms.create', name='must reject directory symlink')
        directory.unlink()
        real.rename(directory)
        link = self.root/'root-link'
        link.symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(OSError):
            Store(link)

    def test_strict_schemas_capacity_no_eviction_atomic_rollback(self):
        self.assertEqual(len(OPERATION_SCHEMAS), 17)
        for args in ({'id': '../secret'}, {'id': 'a'*32, 'extra': True}):
            with self.assertRaises(ValueError):
                self.store.execute('rooms.get', args, self.app)
        with self.assertRaises(ValueError):
            self.op('rooms.create', name='bad', notes=float('nan'))
        room = self.room()
        with patch('garage_audio.room_profiles.LIMITS', {'rooms': 1, 'profiles': 128, 'photos': 512}):
            with self.assertRaises(DomainFailure) as e:
                self.op('rooms.create', name='no eviction')
            self.assertEqual(e.exception.code, 'storage_limit')
        original_save = self.store._save
        def fail_room(collection, record):
            if collection == 'rooms':
                raise DomainFailure('storage_limit', 'fixture write failure')
            return original_save(collection, record)
        with patch.object(self.store, '_save', side_effect=fail_room):
            with self.assertRaises(DomainFailure):
                self.op('rooms.photo_upload', id=room['id'], expected_version=room['version'], data_base64=self.image())
        self.assertEqual(list((self.root/'room-domain'/'photos').iterdir()), [])
        self.assertEqual(self.op('rooms.get', id=room['id']), room)
        with patch('garage_audio.room_profiles.MAX_BYTES', 1):
            with self.assertRaises(DomainFailure):
                self.op('rooms.update', id=room['id'], expected_version=room['version'], notes='too much')
        self.assertEqual(self.op('rooms.get', id=room['id']), room)

    def test_real_crash_before_replace_recovers_on_restart(self):
        room = self.room()
        child = '''
import os, sys
from unittest.mock import patch
from garage_audio.application import Application
app = Application(sys.argv[1])
with patch('garage_audio.room_profiles.os.replace', side_effect=lambda *a, **k: os._exit(71)):
    app.execute('rooms.update', {'id': sys.argv[2], 'expected_version': int(sys.argv[3]), 'notes': 'interrupted'})
'''
        crashed = subprocess.run([sys.executable, '-c', child, str(self.root), room['id'], str(room['version'])],
                                 capture_output=True, timeout=20)
        self.assertEqual(crashed.returncode, 71, crashed.stderr.decode())
        room_dir = self.root/'room-domain'/'rooms'
        abandoned = list(room_dir.glob('.write-*'))
        self.assertEqual(len(abandoned), 1)
        self.assertEqual(abandoned[0].stat().st_mode & 0o777, 0o600)
        restarted = Application(self.root)
        listed = restarted.execute('rooms.list', {})
        self.assertTrue(listed['ok'], listed.get('error'))
        self.assertEqual(listed['result'], [room], 'old committed room survives pre-replace crash')
        self.assertEqual(list(room_dir.glob('.write-*')), [])
        edited = restarted.execute('rooms.update', {'id': room['id'], 'expected_version': room['version'], 'notes': 'recovered'})
        self.assertTrue(edited['ok'], edited.get('error'))
        self.assertEqual(edited['result']['notes'], 'recovered')

    def test_temp_recovery_preserves_unsafe_and_unrelated_artifacts(self):
        room = self.room()
        room_dir = self.root/'room-domain'/'rooms'
        candidate = room_dir/('.write-' + 'a'*32)
        saved = self.root/'private-target'
        saved.write_bytes(b'untouched')
        saved.chmod(0o600)
        for kind in ('symlink', 'hardlink', 'public', 'oversized', 'unrelated'):
            with self.subTest(kind=kind):
                path = candidate if kind != 'unrelated' else room_dir/'.write-not-generated'
                if kind == 'symlink':
                    path.symlink_to(saved)
                elif kind == 'hardlink':
                    os.link(saved, path)
                else:
                    path.write_bytes(b'partial')
                    path.chmod(0o644 if kind == 'public' else 0o600)
                    if kind == 'oversized':
                        with path.open('r+b') as stream:
                            stream.truncate(1048577)
                with self.assertRaises((ValueError, OSError)):
                    self.op('rooms.list')
                self.assertTrue(path.exists(), 'unsafe artifact must never be deleted by recovery')
                self.assertEqual(saved.read_bytes(), b'untouched')
                path.unlink()
        # A real interrupted write may be empty or contain invalid partial JSON.
        for contents in (b'', b'{"id":'):
            candidate.write_bytes(contents)
            candidate.chmod(0o600)
            self.assertEqual(self.op('rooms.list'), [room])
            self.assertFalse(candidate.exists())

    def test_photo_retained_on_post_replace_directory_sync_failure(self):
        room = self.room()
        room_info = (self.root/'room-domain'/'rooms').stat()
        real_sync = os.fsync
        def fail_room_directory(fd):
            info = os.fstat(fd)
            if stat.S_ISDIR(info.st_mode) and (info.st_dev, info.st_ino) == (room_info.st_dev, room_info.st_ino):
                raise OSError('injected post-commit directory sync failure')
            return real_sync(fd)
        with patch('garage_audio.room_profiles.os.fsync', side_effect=fail_room_directory):
            response = self.app.execute('rooms.photo_upload', {'id': room['id'],
                'expected_version': room['version'], 'data_base64': self.image()})
        self.assertFalse(response['ok'])
        self.assertEqual(response['error']['code'], 'storage_durability_uncertain')
        committed = self.op('rooms.get', id=room['id'])
        self.assertEqual(committed['version'], room['version'] + 1)
        self.assertIsNotNone(committed['photo_id'])
        photo = self.op('rooms.photo_get', id=room['id'], photo_id=committed['photo_id'])
        self.assertEqual(photo['id'], committed['photo_id'])
        restarted = Application(self.root)
        self.assertTrue(restarted.execute('rooms.photo_get', {'id': room['id'], 'photo_id': photo['id']})['ok'])
        stale = restarted.execute('rooms.update', {'id': room['id'], 'expected_version': room['version'], 'notes': 'retry'})
        self.assertEqual(stale['error']['code'], 'version_conflict')

    def test_photo_rolled_back_on_pre_replace_failure(self):
        room = self.room()
        room_info = (self.root/'room-domain'/'rooms').stat()
        real_replace = os.replace
        def fail_room_replace(source, target, **kwargs):
            info = os.fstat(kwargs['dst_dir_fd'])
            if (info.st_dev, info.st_ino) == (room_info.st_dev, room_info.st_ino):
                raise OSError('injected pre-commit replacement failure')
            return real_replace(source, target, **kwargs)
        with patch('garage_audio.room_profiles.os.replace', side_effect=fail_room_replace):
            with self.assertRaises(OSError):
                self.op('rooms.photo_upload', id=room['id'], expected_version=room['version'], data_base64=self.image())
        self.assertEqual(self.op('rooms.get', id=room['id']), room)
        self.assertEqual(list((self.root/'room-domain'/'photos').iterdir()), [])
        self.assertEqual(list((self.root/'room-domain'/'rooms').glob('.write-*')), [])


if __name__ == '__main__':
    unittest.main()
