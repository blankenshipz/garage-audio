"""Ops MCP safety: read-only default, server-side write gate, hard limits, backup-before-write, rollback.

Synthetic fakes only; no service manager, Music Assistant or CamillaDSP is contacted.
"""
import importlib.util
import json
import os
import stat
import unittest
from pathlib import Path

from garage_audio.ops_mcp import safety, tools
from garage_audio.ops_mcp.errors import ConfigError, OpsError, SafetyError

_spec = importlib.util.spec_from_file_location('ops_mcp_fakes', Path(__file__).parent / 'fixtures/ops_mcp/fakes.py')
fakes = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fakes)

ID = '20261122T100000.000000Z-0123abcd'
# Arguments that would be valid if the tool were enabled (and some that are not: the gate runs first).
WRITE_CALLS = {
    'recover_audio_stack': {'action': 'restart'},
    'ma_play_media': {'player_id': 'player-a', 'uri': 'library://track/1'},
    'ma_player_command': {'player_id': 'player-a', 'command': 'next'},
    'ma_stop_playback': {'player_id': 'player-a', 'command': 'pause'},
    'ma_set_volume': {'player_id': 'player-a', 'level': 10},
    'ma_set_mute': {'player_id': 'player-a', 'muted': True},
    'ma_group_players': {'target_player_id': 'player-a', 'member_player_ids': ['player-b']},
    'ma_ungroup_player': {'player_id': 'player-b'},
    'dsp_set_main_volume': {'volume_db': -30},
    'dsp_set_mute': {'muted': True},
    'dsp_update_peq': {'filter': 'peq_left_1', 'gain': -4},
    'dsp_set_channel_delay': {'channel': 'sub', 'delay_ms': 2},
    'dsp_set_channel_gain': {'channel': 'sub', 'gain_db': -2},
    'dsp_restore_backup': {'backup_id': ID},
    'room_apply_correction': {'proposal_id': ID},
}


class WriteGate(unittest.TestCase):
    def assert_untouched(self, h):
        self.assertEqual(h.system.actions, [])
        self.assertEqual([c for c in h.ma.calls], [])
        self.assertNotIn('set_config', h.camilla.events)
        self.assertFalse(any(isinstance(e, tuple) for e in h.camilla.events))
        self.assertFalse((h.state_dir / 'dsp-backups').exists())

    def test_every_mutating_tool_is_covered(self):
        mutating = {t.name for t in tools.TOOLS if t.access in (tools.WRITE, tools.PLAYBACK)}
        self.assertEqual(mutating, set(WRITE_CALLS))

    def test_read_only_default_refuses_every_write_server_side(self):
        h = fakes.Harness(self)
        self.assertFalse(h.config.allow_write)
        for name, args in WRITE_CALLS.items():
            with self.subTest(tool=name), self.assertRaises(OpsError) as caught:
                tools.call(h.ctx, name, args)
            self.assertEqual(caught.exception.code, 'write_disabled')
        self.assert_untouched(h)

    def test_gate_runs_before_argument_validation(self):
        h = fakes.Harness(self)
        with self.assertRaises(OpsError) as caught:
            tools.call(h.ctx, 'dsp_update_peq', {'filter': 'x', 'gain': 99, 'bogus': 1})
        self.assertEqual(caught.exception.code, 'write_disabled')

    def test_playback_needs_both_flags(self):
        h = fakes.Harness(self, {'allow_write': True})
        for name in ('ma_play_media', 'ma_player_command', 'ma_set_volume', 'ma_set_mute', 'ma_group_players',
                     'ma_ungroup_player'):
            with self.subTest(tool=name), self.assertRaises(OpsError) as caught:
                tools.call(h.ctx, name, WRITE_CALLS[name])
            self.assertEqual(caught.exception.code, 'write_disabled')
        self.assertEqual(h.ma.calls, [])
        # Pause/stop only reduces output and needs allow_write alone.
        self.assertTrue(tools.call(h.ctx, 'ma_stop_playback', WRITE_CALLS['ma_stop_playback'])['ok'])
        h2 = fakes.Harness(self, {'allow_playback': True})  # playback without write stays off
        with self.assertRaises(OpsError):
            tools.call(h2.ctx, 'ma_play_media', WRITE_CALLS['ma_play_media'])

    def test_disabled_tools_are_listed_but_marked(self):
        described = {d['name']: d for d in tools.describe(fakes.Harness(self).config)}
        self.assertEqual(len(described), len(tools.TOOLS))
        for name in WRITE_CALLS:
            self.assertFalse(described[name]['enabled'])
            self.assertIn('refused server-side', described[name]['description'])
        self.assertTrue(described['get_status']['enabled'])
        self.assertTrue(described['room_import_measurement']['enabled'])
        enabled = {d['name'] for d in tools.describe(fakes.Harness(self, {'allow_write': True,
                                                                          'allow_playback': True}).config)
                   if d['enabled']}
        self.assertEqual(enabled, set(described))

    def test_read_tools_work_when_read_only(self):
        h = fakes.Harness(self)
        self.assertIn('config', tools.call(h.ctx, 'dsp_get_config', {}))
        self.assertIn('players', tools.call(h.ctx, 'ma_list_players', {}))


class HardLimits(unittest.TestCase):
    def test_config_can_only_tighten(self):
        for key, hard in safety.HARD.items():
            step = 1 if key in safety.INTEGER_KEYS else 0.5
            looser = hard + step if safety.DIRECTION[key] == 'max' else hard - step
            with self.subTest(key=key), self.assertRaises(ConfigError) as caught:
                safety.build_policy({key: looser})
            self.assertIn('loosen', caught.exception.message)
        policy = safety.build_policy({'max_boost_db': 1.0, 'volume_ceiling_db': -20.0, 'q_max': 5.0})
        self.assertEqual((policy.max_boost_db, policy.volume_ceiling_db, policy.q_max), (1.0, -20.0, 5.0))
        # Hard values themselves are allowed (operator opt-in up to the code cap).
        self.assertEqual(safety.build_policy({'max_boost_db': 6.0}).max_boost_db, 6.0)

    def test_hard_caps_are_sane(self):
        self.assertLessEqual(safety.HARD['volume_ceiling_db'], 0.0)
        self.assertLessEqual(safety.HARD['max_boost_db'], 6.0)
        self.assertEqual((safety.HARD['freq_min_hz'], safety.HARD['freq_max_hz']), (20.0, 20000.0))
        for key, value in safety.DEFAULTS.items():
            hard = safety.HARD[key]
            self.assertTrue(value <= hard if safety.DIRECTION[key] == 'max' else value >= hard, key)

    def test_invalid_limit_values(self):
        for bad in ({'max_boost_db': float('nan')}, {'max_boost_db': '3'}, {'max_filters_per_channel': 2.5},
                    {'q_min': 15.0, 'q_max': 10.0}, {'volume_ceiling_db': -200.0}, {'unknown': 1}):
            with self.subTest(bad=bad), self.assertRaises(ConfigError):
                safety.build_policy(bad)

    def test_value_checks(self):
        policy = safety.build_policy({})
        with self.assertRaises(SafetyError):
            safety.check_peq(policy, {'freq': 100, 'q': 1, 'gain': 3.5})  # default boost cap 3
        with self.assertRaises(SafetyError):
            safety.check_peq(policy, {'freq': 19, 'q': 1, 'gain': -1})
        with self.assertRaises(SafetyError):
            safety.check_peq(policy, {'freq': 100, 'q': 25, 'gain': -1})
        with self.assertRaises(SafetyError):
            safety.check_volume(policy, -9.0)  # default ceiling -10
        with self.assertRaises(SafetyError):
            safety.check_delay_ms(policy, 25)
        with self.assertRaises(SafetyError):
            safety.check_channel_gain(policy, 0.5)
        with self.assertRaises(SafetyError):
            safety.check_ma_volume(policy, 51)
        with self.assertRaises(SafetyError):
            safety.check_gain(policy, float('inf'))
        self.assertEqual(safety.check_peq(policy, {'freq': 100, 'q': 1, 'gain': 3})['gain'], 3.0)

    def test_audit_flags_existing_config(self):
        policy = safety.build_policy({})
        config = fakes.base_config()
        self.assertEqual(safety.audit_config(policy, config), [])
        config['filters']['loud'] = {'type': 'Biquad', 'parameters': {'type': 'Peaking', 'freq': 50, 'q': 1,
                                                                      'gain': 9}}
        config['filters']['far'] = {'type': 'Delay', 'parameters': {'delay': 3000, 'unit': 'samples'}}
        config['mixers']['to4']['mapping'][0]['sources'][0]['gain'] = 3
        problems = safety.audit_config(policy, config)
        self.assertTrue(any(p.startswith('loud:') for p in problems))
        self.assertTrue(any(p.startswith('far:') for p in problems))  # 3000 samples @48k = 62.5 ms
        self.assertTrue(any('mixer to4' in p for p in problems))


class BackupBeforeWrite(unittest.TestCase):
    def harness(self, **kw):
        return fakes.Harness(self, {'allow_write': True, **kw.pop('overrides', {})}, **kw)

    def test_backup_written_before_apply_with_private_atomic_files(self):
        h = self.harness()
        original = h.camilla.get_config()
        h.camilla.events.clear()
        result = tools.call(h.ctx, 'dsp_update_peq', {'filter': 'peq_left_1', 'gain': -6, 'reason': 'tame 120'})
        self.assertTrue(result['ok'] and result['readback_verified'])
        self.assertLess(h.camilla.events.index('validate'), h.camilla.events.index('set_config'))
        backup_dir = h.state_dir / 'dsp-backups'
        files = list(backup_dir.glob('*.json'))
        self.assertEqual(len(files), 1)
        record = json.loads(files[0].read_text())
        self.assertEqual(record['config'], original)  # the config that was replaced
        self.assertEqual((record['tool'], record['reason'], record['volume_db'], record['mute']),
                         ('dsp_update_peq', 'tame 120', -20.0, False))
        self.assertEqual(stat.S_IMODE(files[0].stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(backup_dir.stat().st_mode), 0o700)
        self.assertEqual([p for p in backup_dir.iterdir() if p.name.startswith('.')], [])
        self.assertEqual(result['rollback'], f"dsp_restore_backup with backup_id={result['backup_id']}")

    def test_backup_exists_when_apply_is_called(self):
        h = self.harness()
        seen = []
        real = h.camilla.set_config

        def spy(config):
            seen.append(len(list((h.state_dir / 'dsp-backups').glob('*.json'))))
            real(config)
        h.camilla.set_config = spy
        tools.call(h.ctx, 'dsp_set_channel_gain', {'channel': 'sub', 'gain_db': -3})
        tools.call(h.ctx, 'dsp_set_main_volume', {'volume_db': -25})
        self.assertEqual(seen, [1])
        self.assertEqual(len(list((h.state_dir / 'dsp-backups').glob('*.json'))), 2)

    def test_limit_violation_rejected_before_backup_or_apply(self):
        h = self.harness()
        for name, args in (('dsp_update_peq', {'filter': 'peq_left_1', 'gain': 4}),
                           ('dsp_update_peq', {'filter': 'peq_left_1', 'freq': 25000}),
                           ('dsp_set_channel_delay', {'channel': 'left', 'delay_ms': 21}),
                           ('dsp_set_channel_gain', {'channel': 'left', 'gain_db': 1}),
                           ('dsp_set_main_volume', {'volume_db': -5})):
            with self.subTest(tool=name, args=args), self.assertRaises(SafetyError):
                tools.call(h.ctx, name, args)
        self.assertNotIn('set_config', h.camilla.events)
        self.assertEqual(h.camilla.volume, -20.0)
        self.assertFalse(list((h.state_dir / 'dsp-backups').glob('*.json')) if (h.state_dir / 'dsp-backups').exists()
                         else [])

    def test_tightened_operator_limits_apply(self):
        h = self.harness(overrides={'limits': {'max_boost_db': 0.0, 'volume_ceiling_db': -30.0}})
        with self.assertRaises(SafetyError):
            tools.call(h.ctx, 'dsp_update_peq', {'filter': 'peq_left_1', 'gain': 0.5})
        with self.assertRaises(SafetyError):
            tools.call(h.ctx, 'dsp_set_main_volume', {'volume_db': -29})
        self.assertTrue(tools.call(h.ctx, 'dsp_set_main_volume', {'volume_db': -31})['ok'])

    def test_preexisting_violation_does_not_block_unrelated_safe_change(self):
        config = fakes.base_config()
        config['filters']['legacy_boost'] = {'type': 'Biquad', 'parameters': {'type': 'Peaking', 'freq': 40,
                                                                              'q': 1, 'gain': 5}}
        h = self.harness(camilla=fakes.FakeCamilla(config))
        result = tools.call(h.ctx, 'dsp_update_peq', {'filter': 'peq_left_1', 'gain': -4})
        self.assertTrue(result['ok'])
        self.assertTrue(any('legacy_boost' in p for p in result['preexisting_limit_violations']))
        # ...but editing the offending filter must bring it inside the limits.
        with self.assertRaises(SafetyError):
            tools.call(h.ctx, 'dsp_update_peq', {'filter': 'legacy_boost', 'freq': 45})
        self.assertTrue(tools.call(h.ctx, 'dsp_update_peq', {'filter': 'legacy_boost', 'gain': 2})['ok'])

    def test_camilladsp_rejection_means_no_backup_and_no_apply(self):
        h = self.harness(camilla=fakes.FakeCamilla(validate='reject'))
        with self.assertRaises(OpsError) as caught:
            tools.call(h.ctx, 'dsp_update_peq', {'filter': 'peq_left_1', 'gain': -4})
        self.assertEqual(caught.exception.code, 'config_invalid_for_camilladsp')
        self.assertNotIn('set_config', h.camilla.events)
        self.assertEqual(list(h.ctx.backups.list()), [])

    def test_validate_unsupported_still_applies_with_backup(self):
        h = self.harness(camilla=fakes.FakeCamilla(validate='unsupported'))
        result = tools.call(h.ctx, 'dsp_update_peq', {'filter': 'peq_left_1', 'gain': -4})
        self.assertTrue(result['validation'].startswith('unavailable'))
        self.assertEqual(len(h.ctx.backups.list()), 1)

    def test_failed_apply_keeps_backup_and_reports_it(self):
        h = self.harness()
        h.camilla.fail_set = True
        before = h.camilla.get_config()
        with self.assertRaises(OpsError) as caught:
            tools.call(h.ctx, 'dsp_update_peq', {'filter': 'peq_left_1', 'gain': -4})
        self.assertIn('backup_id', caught.exception.details)
        self.assertEqual(h.camilla.config, before)
        self.assertEqual(h.ctx.backups.list(), [caught.exception.details['backup_id']])

    def test_retention_is_bounded(self):
        h = self.harness(overrides={'camilladsp': {'channels': {'left': 0, 'right': 1, 'sub': 2},
                                                   'backup_retention': 5}})
        for _ in range(8):
            tools.call(h.ctx, 'dsp_set_mute', {'muted': True})
        self.assertEqual(len(h.ctx.backups.list(100)), 5)

    def test_unmute_needs_playback_permission(self):
        h = self.harness()   # allow_write only
        with self.assertRaises(OpsError) as caught:
            tools.call(h.ctx, 'dsp_set_mute', {'muted': False})
        self.assertEqual(caught.exception.code, 'playback_disabled')
        self.assertFalse([e for e in h.camilla.events if isinstance(e, tuple)])
        self.assertEqual(h.ctx.backups.list(), [])
        self.assertTrue(tools.call(h.ctx, 'dsp_set_mute', {'muted': True})['muted'])
        both = fakes.Harness(self, {'allow_write': True, 'allow_playback': True})
        self.assertFalse(tools.call(both.ctx, 'dsp_set_mute', {'muted': False})['muted'])
        self.assertIn(('set_mute', False), both.camilla.events)


class Rollback(unittest.TestCase):
    def test_restore_round_trip_is_itself_backed_up(self):
        h = fakes.Harness(self, {'allow_write': True})
        original = h.camilla.get_config()
        first = tools.call(h.ctx, 'dsp_update_peq', {'filter': 'peq_left_1', 'gain': -8})
        tools.call(h.ctx, 'dsp_set_channel_delay', {'channel': 'sub', 'delay_ms': 3.5})
        self.assertNotEqual(h.camilla.config, original)
        restored = tools.call(h.ctx, 'dsp_restore_backup', {'backup_id': first['backup_id']})
        self.assertEqual(h.camilla.config, original)
        self.assertEqual(restored['restored_from'], first['backup_id'])
        # The state before the restore is recoverable too.
        latest = h.ctx.backups.get(restored['backup_id'])
        self.assertEqual(latest['tool'], 'dsp_restore_backup')
        self.assertIn('ops_sub_delay', latest['config']['filters'])
        listed = tools.call(h.ctx, 'dsp_list_backups', {})['backups']
        self.assertEqual([b['id'] for b in listed][0], restored['backup_id'])
        self.assertEqual(len(listed), 3)

    def test_restore_is_validated_against_current_limits(self):
        h = fakes.Harness(self, {'allow_write': True})
        record = h.ctx.backups.save({'tool': 'synthetic', 'reason': 'x', 'volume_db': -20.0, 'mute': False,
                                     'config': dict(fakes.base_config(), filters={
                                         'boom': {'type': 'Biquad', 'parameters': {'type': 'Peaking', 'freq': 40,
                                                                                    'q': 1, 'gain': 6}}})})
        with self.assertRaises(SafetyError):
            tools.call(h.ctx, 'dsp_restore_backup', {'backup_id': record['id']})
        self.assertNotIn('set_config', h.camilla.events)

    def test_restore_volume_above_ceiling_refused_before_anything_applies(self):
        h = fakes.Harness(self, {'allow_write': True})
        record = h.ctx.backups.save({'tool': 'synthetic', 'reason': 'x', 'volume_db': -3.0, 'mute': False,
                                     'config': fakes.base_config()})
        with self.assertRaises(SafetyError):
            tools.call(h.ctx, 'dsp_restore_backup', {'backup_id': record['id'], 'restore_volume': True})
        self.assertNotIn('set_config', h.camilla.events)
        self.assertEqual(h.camilla.volume, -20.0)
        ok = h.ctx.backups.save({'tool': 'synthetic', 'reason': 'x', 'volume_db': -40.0, 'mute': True,
                                 'config': fakes.base_config()})
        result = tools.call(h.ctx, 'dsp_restore_backup', {'backup_id': ok['id'], 'restore_volume': True})
        self.assertEqual((result['volume_db'], result['mute']), (-40.0, True))
        self.assertEqual((h.camilla.volume, h.camilla.mute), (-40.0, True))

    def test_restore_does_not_unmute_without_playback(self):
        h = fakes.Harness(self, {'allow_write': True})
        h.camilla.mute = True
        record = h.ctx.backups.save({'tool': 'synthetic', 'reason': 'x', 'volume_db': -30.0, 'mute': False,
                                     'config': fakes.base_config()})
        result = tools.call(h.ctx, 'dsp_restore_backup', {'backup_id': record['id'], 'restore_volume': True})
        self.assertEqual(h.camilla.volume, -30.0)
        self.assertTrue(h.camilla.mute)
        self.assertNotIn(('set_mute', False), h.camilla.events)
        self.assertIn('mute_not_restored', result)
        both = fakes.Harness(self, {'allow_write': True, 'allow_playback': True})
        both.camilla.mute = True
        again = both.ctx.backups.save({'tool': 'synthetic', 'reason': 'x', 'volume_db': -30.0, 'mute': False,
                                       'config': fakes.base_config()})
        tools.call(both.ctx, 'dsp_restore_backup', {'backup_id': again['id'], 'restore_volume': True})
        self.assertFalse(both.camilla.mute)

    def test_bad_backup_ids(self):
        h = fakes.Harness(self, {'allow_write': True})
        for ident, code in (('../../etc/passwd', 'invalid_argument'), (ID, 'not_found')):
            with self.subTest(ident=ident), self.assertRaises(OpsError) as caught:
                tools.call(h.ctx, 'dsp_restore_backup', {'backup_id': ident})
            self.assertEqual(caught.exception.code, code)
        h.ctx.backups._dir()
        (h.ctx.backups.directory / f'{ID}.json').write_text('{"id": "other"}')
        with self.assertRaises(OpsError) as caught:
            tools.call(h.ctx, 'dsp_restore_backup', {'backup_id': ID})
        self.assertEqual(caught.exception.code, 'unreadable')

    def test_state_dir_must_be_private_and_owned(self):
        h = fakes.Harness(self, {'allow_write': True})
        h.state_dir.parent.mkdir(exist_ok=True)
        target = h.root / 'elsewhere'
        target.mkdir()
        os.symlink(target, h.state_dir)
        with self.assertRaises(OpsError) as caught:
            tools.call(h.ctx, 'dsp_set_mute', {'muted': True})
        self.assertEqual(caught.exception.code, 'state_dir_unsafe')
        self.assertNotIn(('set_mute', True), h.camilla.events)


if __name__ == '__main__':
    unittest.main()
