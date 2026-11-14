"""Synthetic rejection fixtures + actual Linux flock inheritance; no sound/device open."""
import copy
import json
import os
import runpy
from pathlib import Path
import stat
import subprocess
import tempfile
import types
import unittest
from unittest.mock import patch
from garage_audio.audio_device import (CAPABILITIES, IDENTITY, SUB_OUTPUTS, DeviceLease, device_environment,
                                       hardware_graph, load_policy, validate_inventory)
from garage_audio.audio_container import line_graph


def fixture():
    return {'identity': dict(IDENTITY), 'capabilities': dict(CAPABILITIES), 'card': 1,
        'connection': {'path': '/synthetic/usb', 'inode': 123, 'dev': 1, 'busnum': '1', 'devnum': '4'},
        'nodes': {name: {'dev': 1, 'inode': 10+i, 'rdev': os.makedev(116, minor),
                        'uid': 1002, 'gid': 1003, 'mode': 0o600}
                  for i, (name, minor) in enumerate([('pcmC1D0p', 12), ('controlC1', 15)])}}


def mapped(value):
    def result(path):
        v = value['nodes'][path.name]
        return types.SimpleNamespace(st_mode=stat.S_IFCHR | v['mode'], st_rdev=v['rdev'], st_uid=v['uid'], st_gid=v['gid'])
    return result


class IdentityTests(unittest.TestCase):
    def test_boot_mapping_follows_verified_card_index_and_rejects_legacy_owner(self):
        for card in (0, 1, 7):
            value = fixture()
            value['card'] = card
            value['nodes'] = {name.replace('C1', f'C{card}'): meta for name, meta in value['nodes'].items()}
            with patch('garage_audio.audio_device.inventory', return_value=value), patch.object(Path, 'lstat', mapped(value)):
                self.assertEqual(device_environment(), {
                    'GARAGE_AUDIO_PCM_NODE': f'/dev/snd/pcmC{card}D0p',
                    'GARAGE_AUDIO_CONTROL_NODE': f'/dev/snd/controlC{card}'})
                bad = copy.deepcopy(value)
                next(iter(bad['nodes'].values())).update(uid=999, gid=1002)
                with patch('garage_audio.audio_device.inventory', return_value=bad):
                    with self.assertRaisesRegex(PermissionError, 'exclusive numeric UID1002/GID1003'):
                        device_environment()
                value['identity']['serial'] = 'WRONG_DEVICE'
                with self.assertRaises(ValueError):
                    device_environment()

    def test_missing_or_wrong_mapped_device_is_actionable_and_fail_closed(self):
        value = fixture()
        with patch.object(Path, 'lstat', side_effect=FileNotFoundError):
            with self.assertRaisesRegex(PermissionError, 'recreate identity-bound mapping'):
                validate_inventory(value)
        for key, replacement in [('rdev', os.makedev(116, 6)), ('gid', 29)]:
            wrong = copy.deepcopy(value)
            wrong['nodes']['pcmC1D0p'][key] = replacement
            with patch.object(Path, 'lstat', mapped(wrong)):
                with self.assertRaisesRegex(PermissionError, 'exact mapped node'):
                    validate_inventory(value)

    def test_bootstrap_retires_only_known_legacy_rules_and_pins_exact_device(self):
        script = runpy.run_path(str(Path(__file__).resolve().parents[1]/'scripts/bootstrap-container-audio.py'))
        reconcile = script['reconciled_legacy_rules']
        legacy = '\n'.join('SUBSYSTEM=="sound", KERNEL=="'+kernel+'", ATTRS{idVendor}=="1397", ATTRS{idProduct}=="0514", ATTRS{serial}=="D72E24B7", OWNER="garage-dsp", GROUP="garage-dac", MODE="0600", TAG-="uaccess"' for kernel in ('pcmC*D*p', 'controlC*'))
        retired = reconcile(legacy)
        self.assertEqual(reconcile(retired), retired)
        with self.assertRaisesRegex(RuntimeError, 'unrecognized legacy'):
            reconcile(legacy+'\nSUBSYSTEM=="sound", MODE="0666"')
        rule = script['RULE']
        self.assertEqual(rule.count('OWNER:="1002", GROUP:="1003", MODE:="0600"'), 2)
        self.assertIn('KERNEL=="pcmC*D0p"', rule)
        self.assertNotIn('KERNEL=="pcmC*D*p"', rule)

    def test_synthetic_identity_format_channels_and_permissions_rejections(self):
        original = fixture()
        with patch.object(Path, 'lstat', mapped(original)):
            self.assertEqual(validate_inventory(original), original)
            for location, key, value in [('identity', 'serial', 'OTHER'), ('identity', 'product', '0000'),
                    ('capabilities', 'channels', 20), ('capabilities', 'format', 'S32_LE'),
                    ('capabilities', 'samplerate', 44100)]:
                bad = copy.deepcopy(original)
                bad[location][key] = value
                with self.assertRaises(ValueError):
                    validate_inventory(bad)
            for key, value in [('uid', 1000), ('gid', 46), ('mode', 0o660)]:
                bad = copy.deepcopy(original)
                bad['nodes']['pcmC1D0p'][key] = value
                with self.assertRaises(PermissionError):
                    validate_inventory(bad)

    def test_linux_exclusive_lock_survives_parent_close_while_child_owns_it(self):
        if os.getuid() != 1002:
            self.skipTest('real Linux container UID1002 required')
        with tempfile.TemporaryDirectory() as root:
            path = Path(root)/'owner.lock'
            path.touch(mode=0o600)
            with patch('garage_audio.audio_device.load_policy'), \
                 patch('garage_audio.audio_device.inventory', side_effect=lambda: fixture()), \
                 patch('garage_audio.audio_device.validate_inventory', side_effect=lambda v: v):
                first = DeviceLease(lock=str(path))
                child = subprocess.Popen(['sleep', '30'], pass_fds=(first.fd,))
                try:
                    first.close()
                    with self.assertRaises(BlockingIOError):
                        DeviceLease(lock=str(path))
                finally:
                    child.kill()
                    child.wait(timeout=2)
                second = DeviceLease(lock=str(path))
                second.close()

    def test_synthetic_reconnect_and_node_replacement_revoke_existing_binding(self):
        lease = object.__new__(DeviceLease)
        lease.policy = Path('/synthetic-policy')
        lease.binding = fixture()
        for key in ('devnum', 'inode'):
            changed = fixture()
            changed['connection'][key] = '5' if key == 'devnum' else 124
            with patch('garage_audio.audio_device.load_policy'), \
                 patch('garage_audio.audio_device.inventory', return_value=changed), \
                 patch('garage_audio.audio_device.validate_inventory', side_effect=lambda v: v):
                with self.assertRaises(ValueError):
                    lease.verify()
        changed = fixture()
        changed['nodes']['pcmC1D0p']['inode'] += 1
        with patch('garage_audio.audio_device.load_policy'), \
             patch('garage_audio.audio_device.inventory', return_value=changed), \
             patch('garage_audio.audio_device.validate_inventory', side_effect=lambda v: v):
            with self.assertRaises(ValueError):
                lease.verify()

    def test_hardware_graph_fixed_map_clock_and_no_default_fallback(self):
        graph = hardware_graph(line_graph('/not-opened.f32'), 'hw:CARD=UMC1820,DEV=0')
        self.assertEqual(graph['devices']['playback'], {'type': 'Alsa', 'channels': 12,
            'device': 'hw:CARD=UMC1820,DEV=0', 'format': 'S24_3_LE'})
        self.assertTrue(graph['devices']['enable_rate_adjust'])
        self.assertEqual(graph['filters']['headroom']['parameters']['gain'], -6)
        self.assertEqual([x['dest'] for x in graph['mixers']['route']['mapping']], [2, 3])
        with self.assertRaises(ValueError):
            hardware_graph(line_graph('/not-opened.f32'), 'default')

    def test_policy_admits_exact_stereo_or_out5_sub_layout_only(self):
        base = {'version': 1, 'identity': IDENTITY, 'capabilities': CAPABILITIES, 'device_uid': 1002, 'device_gid': 1003}
        root_owned = types.SimpleNamespace(st_mode=stat.S_IFREG | 0o444, st_uid=0, st_size=256)
        for outputs, admitted in (([3, 4], True), ([3, 4, 5], True), ([3, 5], False),
                                  ([4, 3, 5], False), ([3, 4, 6], False), ([3, 4, 5, 6], False)):
            with tempfile.NamedTemporaryFile('w', suffix='.json') as policy:
                json.dump({**base, 'outputs': outputs}, policy); policy.flush()
                with self.subTest(outputs=outputs), patch.object(Path, 'lstat', return_value=root_owned):
                    if admitted: self.assertEqual(load_policy(policy.name)['outputs'], outputs)
                    else: self.assertRaises(ValueError, load_policy, policy.name)

    def test_hardware_route_must_match_policy_outputs(self):
        from garage_audio.audio_config import DEFAULT, compile_graph
        sub = compile_graph(dict(DEFAULT, layout='powered-sub-2.1', sub=5), '/not-opened.f32')
        graph = hardware_graph(sub, 'hw:CARD=UMC1820,DEV=0', SUB_OUTPUTS)
        self.assertEqual([x['dest'] for x in graph['mixers']['route']['mapping']], [2, 3, 4])
        self.assertIn('OUT3/4/5', graph['title'])
        stereo = compile_graph(DEFAULT, '/not-opened.f32')
        for candidate, outputs in ((stereo, SUB_OUTPUTS), (sub, [3, 4]), (stereo, [3, 4, 6])):
            with self.subTest(outputs=outputs), self.assertRaises(PermissionError):
                hardware_graph(candidate, 'hw:CARD=UMC1820,DEV=0', outputs)


if __name__ == '__main__':
    unittest.main()
