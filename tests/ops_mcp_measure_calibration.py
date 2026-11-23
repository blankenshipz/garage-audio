"""Ops MCP microphone calibration: detection, the miniDSP / URL providers, network safety, validation,
import (contents / confined path / re-activation), storage and retention, automatic selection by orientation,
gating and the guided prompt. Fakes only: no socket is ever opened (enforced per test).
Serial numbers are placeholders (000-0000 style).
"""
import importlib.util
import io
import json
import os
import stat
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from garage_audio.ops_mcp import content, measure, measure_cal, measure_session, tools
from garage_audio.ops_mcp.config import DEFAULT, Config, _merge
from garage_audio.ops_mcp.errors import ConfigError, OpsError

FIXTURES = Path(__file__).parent / 'fixtures/ops_mcp'


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


fakes = _load('ops_mcp_fakes', FIXTURES / 'fakes.py')
site_mod = _load('ops_mcp_cal_site', FIXTURES / 'cal_site.py')
FakeSite, cal_text, html, text_file = site_mod.FakeSite, site_mod.cal_text, site_mod.html, site_mod.text_file
PAGE = 'https://www.minidsp.com' + site_mod.UMIK1_PAGE
PROFILE = {'dimensions_m': {'length': 5.0, 'width': 3.7, 'height': 2.6},
           'speakers': {'left': {'x': 0.9, 'y': 0.6, 'z': 1.0}, 'right': {'x': 2.8, 'y': 0.6, 'z': 1.0},
                        'sub': {'x': 0.4, 'y': 0.4, 'z': 0.2}},
           'listening_positions': {'main': {'x': 1.85, 'y': 3.1, 'ear_height': 1.1}},
           'seating': {'width': 1.8, 'depth': 0.9}}


def _no_network(*_a, **_k):
    raise AssertionError('a real network call was attempted in a calibration test')


class NoSockets(unittest.TestCase):
    """Every test here runs with real DNS and TCP connections replaced by a failing guard."""

    def setUp(self):
        self.guards = [mock.patch('socket.getaddrinfo', side_effect=_no_network),
                       mock.patch('socket.create_connection', side_effect=_no_network),
                       mock.patch('socket.socket.connect', side_effect=_no_network)]
        self.guard_mocks = [g.start() for g in self.guards]
        for guard in self.guards:
            self.addCleanup(guard.stop)

    def harness(self, overrides=None, site=None, allow_write=True):
        data = {'allow_write': allow_write}
        data.update(overrides or {})
        h = fakes.Harness(self, data)
        h.ctx.measure_runner = lambda argv, timeout: (0, '', '')  # never run real listing commands
        if site is not None:
            site_mod.wire(h.ctx, site)
        return h

    def fetch(self, h, **args):
        return tools.call(h.ctx, 'measure_calibration_fetch', {'model': 'UMIK-1', 'serial': '000-0000', **args})

    def refused(self, code, fn, *args, **kwargs):
        with self.assertRaises(OpsError) as caught:
            fn(*args, **kwargs)
        self.assertEqual(caught.exception.code, code, caught.exception.message)
        return caught.exception


def cal_config(**calibration):
    return {'measurement': {'calibration': calibration}}


# ---- miniDSP provider: success variants -------------------------------------------------------------
class MiniDSPSuccess(NoSockets):
    def test_form_is_submitted_as_served_with_honest_user_agent(self):
        site = FakeSite().minidsp(text_file(cal_text(), '0000000_90deg.txt'),
                                  page=site_mod.product_page(honeypot='your_address_here', token_value='1795000123'))
        h = self.harness(site=site)
        out = self.fetch(h, serial='0000000')
        self.assertEqual([(c['method'], c['host'], c['path']) for c in site.calls],
                         [('GET', 'www.minidsp.com', site_mod.UMIK1_PAGE), ('POST', 'www.minidsp.com',
                                                                            site_mod.UMIK1_PAGE)])
        post, fields = site.posted()
        self.assertEqual(fields['serial1'], ['000'])
        self.assertEqual(fields['serial2'], ['0000'])
        self.assertEqual(fields['your_address_here'], [''])          # the rotating honeypot stays empty
        self.assertEqual(fields[site_mod.TOKEN_NAME], ['1795000123'])  # token submitted exactly as served
        self.assertNotIn('q', fields)                                 # the unrelated search form is not mixed in
        self.assertEqual(post['headers']['Content-Type'], 'application/x-www-form-urlencoded')
        self.assertEqual(post['headers']['Referer'], PAGE)
        for call in site.calls:
            ua = call['headers']['User-Agent']
            self.assertTrue(ua.startswith('garage-audio-ops-mcp/'), ua)
            self.assertNotIn('Mozilla', ua)
            self.assertEqual(call['address'], site_mod.PUBLIC_ADDRESS)  # pinned to the checked address
        self.assertEqual(out['serial'], '000-0000')
        self.assertEqual(out['activated'], {'90': out['stored'][0]['calibration_id']})
        self.assertIn('unverified against a real serial', out['verification'])
        self.assertEqual(out['requests'][1]['path'], site_mod.UMIK1_PAGE)

    def test_umik2_uses_its_own_page_and_form(self):
        site = FakeSite().minidsp(text_file(cal_text(), '0000000_90deg.txt'),
                                  page=site_mod.product_page(anchor='umik2calfile'), model_path=site_mod.UMIK2_PAGE)
        out = self.fetch(self.harness(site=site), model='umik-2')
        self.assertEqual(out['model'], 'UMIK-2')
        self.assertEqual(site.calls[-1]['path'], site_mod.UMIK2_PAGE)

    def test_direct_unmarked_file_is_stored_not_activated_until_confirmed(self):
        site = FakeSite().minidsp(text_file(cal_text(), '0000000.txt'))
        h = self.harness(site=site)
        out = self.fetch(h)
        self.assertEqual(out['activated'], {})
        self.assertEqual(out['stored'][0]['orientation'], 'unknown')
        self.assertIn('needs_orientation', out)
        cid = out['stored'][0]['calibration_id']
        again = tools.call(h.ctx, 'measure_import_calibration', {'calibration_id': cid, 'orientation': '90'})
        derived = again['stored'][0]['calibration_id']
        self.assertNotEqual(derived, cid)
        self.assertEqual(again['activated'], {'90': derived})
        record = measure.get_calibration(h.ctx, derived)
        self.assertEqual(record['derived_from'], cid)
        self.assertEqual(record['orientation_basis'], 'confirmed by the user')
        self.assertEqual(measure_cal.active_id(h.ctx, '90'), derived)

    def test_zip_with_both_orientations(self):
        body = site_mod.zip_bytes([('cal/0000000.txt', cal_text()), ('cal/0000000_90deg.txt', cal_text(offset=1.0)),
                                   ('readme.pdf', b'%PDF-1.4')])
        site = FakeSite().minidsp((200, [('Content-Type', 'application/zip')], body))
        h = self.harness(site=site)
        out = self.fetch(h)
        by_orient = {s['orientation']: s for s in out['stored']}
        self.assertEqual(set(by_orient), {'0', '90'})
        self.assertEqual(by_orient['0']['orientation_basis'], 'the other file is marked 90-degree')
        self.assertEqual(out['activated'], {'0': by_orient['0']['calibration_id'],
                                            '90': by_orient['90']['calibration_id']})

    def test_orientation_subset(self):
        body = site_mod.zip_bytes([('0000000.txt', cal_text()), ('0000000_90deg.txt', cal_text(offset=1.0))])
        site = FakeSite().minidsp((200, [('Content-Type', 'application/zip')], body))
        out = self.fetch(self.harness(site=site), orientation='0')
        self.assertEqual([s['orientation'] for s in out['stored']], ['0'])
        self.assertEqual(list(out['activated']), ['0'])

    def test_link_page_follows_only_serial_named_links_on_allowed_hosts(self):
        page = ('<html><body><p>Your download will begin automatically.</p>'
                '<a href="/files/cal/0000000.txt">0 degree</a>'
                '<a href="/files/cal/0000000_90deg.txt">90 degree</a>'
                '<a href="/files/software/umik-setup.zip">software</a>'
                '<a href="https://elsewhere.example/0000000.txt">mirror</a></body></html>')
        site = FakeSite().minidsp(html(page))
        site.route('GET', site_mod.HOST, '/files/cal/0000000.txt', text_file(cal_text()))
        site.route('GET', site_mod.HOST, '/files/cal/0000000_90deg.txt', text_file(cal_text(offset=0.5)))
        out = self.fetch(self.harness(site=site))
        paths = [c['path'] for c in site.calls]
        self.assertIn('/files/cal/0000000_90deg.txt', paths)
        self.assertNotIn('/files/software/umik-setup.zip', paths)
        self.assertEqual({c['host'] for c in site.calls}, {site_mod.HOST})
        self.assertEqual(set(out['activated']), {'0', '90'})
        sources = {s['orientation']: s['source'] for s in out['stored']}
        self.assertEqual(sources['90'], {'host': site_mod.HOST, 'path': '/files/cal/0000000_90deg.txt', 'via': 'direct'})

    def test_meta_refresh_and_script_location(self):
        page = ('<html><head><meta http-equiv="refresh" content="0; url=/dl/umik90.txt?t=1"></head><body>'
                '<script>window.location.href = "/dl/umik0deg.txt";</script></body></html>')
        site = FakeSite().minidsp(html(page))
        site.route('GET', site_mod.HOST, '/dl/umik90.txt', text_file(cal_text()))
        site.route('GET', site_mod.HOST, '/dl/umik0deg.txt', text_file(cal_text(offset=0.3)))
        h = self.harness(site=site)
        out = self.fetch(h)
        self.assertEqual(set(out['activated']), {'0', '90'})
        for stored in out['stored']:
            self.assertNotIn('?', stored['source']['path'])  # provenance never keeps a query string
            record = measure.get_calibration(h.ctx, stored['calibration_id'])
            self.assertNotIn('t=1', json.dumps(record['source']))

    def test_two_files_both_marked_90_are_ambiguous(self):
        body = site_mod.zip_bytes([('0000000_90deg.txt', cal_text()), ('0000000_90deg_b.txt', cal_text(offset=1))])
        site = FakeSite().minidsp((200, [('Content-Type', 'application/zip')], body))
        out = self.fetch(self.harness(site=site))
        self.assertEqual(out['activated'], {})
        self.assertEqual(out['ambiguous_orientations'], ['90'])
        self.assertEqual(len(out['stored']), 2)

    def test_serial_digits_containing_90_are_not_an_orientation_marker(self):
        text = cal_text(serno='0000090')
        site = FakeSite().minidsp(text_file(text, '0000090.txt'))
        out = self.fetch(self.harness(site=site), serial='000-0090')
        self.assertEqual(out['stored'][0]['orientation'], 'unknown')


# ---- miniDSP provider: refusals and failures ------------------------------------------------------
class MiniDSPFailures(NoSockets):
    def test_not_found_message_and_manual_steps(self):
        site = FakeSite().minidsp(html(site_mod.NOT_FOUND_HTML))
        h = self.harness(site=site)
        error = self.refused('not_found', self.fetch, h)
        self.assertIn('Calibration data for UMIK 000-0000 not found.', error.message)
        self.assertIn('Please check your serial number', error.message)
        self.assertIn('umik-1', ' '.join(error.details['manual_steps']))
        self.assertEqual(len(site.calls), 2)  # one GET, one POST, no retries
        self.assertEqual(tools.call(h.ctx, 'measure_list_calibrations', {})['calibrations'], [])

    def test_unparseable_success_page_returns_manual_steps(self):
        site = FakeSite().minidsp(html('<html><body><p>Thank you!</p></body></html>'))
        error = self.refused('calibration_unparseable', self.fetch, self.harness(site=site))
        self.assertIn('measure_import_calibration', ' '.join(error.details['manual_steps']))
        self.assertIn('unverified', error.details['verification'])

    def test_form_changed_or_missing(self):
        site = FakeSite().minidsp(html(''), page=site_mod.product_page(form=False))
        self.refused('provider_changed', self.fetch, self.harness(site=site))

    def test_serial_mismatch_in_header_stores_nothing(self):
        site = FakeSite().minidsp(text_file(cal_text(serno='0000001'), '0000000_90deg.txt'))
        h = self.harness(site=site)
        error = self.refused('calibration_serial_mismatch', self.fetch, h)
        self.assertEqual(error.details['file_serial'], '000-0001')
        self.assertEqual(tools.call(h.ctx, 'measure_list_calibrations', {})['calibrations'], [])

    def test_file_not_tied_to_serial_is_refused(self):
        page = '<html><head><meta http-equiv="refresh" content="0; url=/dl/umik90.txt"></head></html>'
        site = FakeSite().minidsp(html(page))
        site.route('GET', site_mod.HOST, '/dl/umik90.txt', text_file(cal_text(header=False)))
        self.refused('serial_unverified', self.fetch, self.harness(site=site))

    def test_html_disguised_as_text_is_rejected(self):
        site = FakeSite().minidsp(text_file('<html><body>' + cal_text() + '</body></html>', '0000000_90deg.txt'))
        self.refused('calibration_invalid', self.fetch, self.harness(site=site))

    def test_redirects_outside_allowlist_plain_http_and_loops(self):
        for location in ('https://elsewhere.example/0000000.txt', 'http://www.minidsp.com/x/0000000.txt',
                         'https://user:pw@www.minidsp.com/x.txt', 'https://www.minidsp.com:8443/x.txt'):
            site = FakeSite().minidsp((302, [('Location', location)], b''))
            self.refused('url_refused', self.fetch, self.harness(site=site))
            self.assertEqual(len(site.calls), 2, location)  # the refused hop is never requested
        site = FakeSite().minidsp((302, [('Location', '/loop')], b''))
        site.route('GET', site_mod.HOST, '/loop', (302, [('Location', '/loop')], b''))
        self.refused('url_refused', self.fetch, self.harness(site=site))
        self.assertEqual(len(site.calls), 1 + 1 + measure_cal.MAX_REDIRECTS)

    def test_redirect_within_allowlist_is_followed_as_get(self):
        site = FakeSite().minidsp((303, [('Location', '/dl/0000000_90deg.txt')], b''))
        site.route('GET', site_mod.HOST, '/dl/0000000_90deg.txt', text_file(cal_text()))
        out = self.fetch(self.harness(site=site))
        self.assertEqual(site.calls[-1]['method'], 'GET')
        self.assertIsNone(site.calls[-1]['body'])
        self.assertEqual(list(out['activated']), ['90'])

    def test_private_and_special_addresses_are_refused_before_connecting(self):
        for address in ('10.0.0.5', '127.0.0.1', '169.254.1.1', '192.168.1.2', '100.64.0.1', '224.0.0.1',
                        '0.0.0.0', '192.0.2.1', '::1', 'fe80::1', '::ffff:127.0.0.1', 'fd00::1'):
            site = FakeSite(addresses=[site_mod.PUBLIC_ADDRESS, address])
            h = self.harness(site=site)
            self.refused('url_refused', self.fetch, h)
            self.assertEqual(site.calls, [], address)

    def test_response_size_cap(self):
        big = b'x' * (2 * 1024 * 1024)
        site = FakeSite().minidsp(text_file(big, '0000000.txt'))
        self.refused('response_too_large', self.fetch, self.harness(site=site))
        stream = io.BytesIO(b'y' * 5000)
        self.refused('response_too_large', measure_cal.read_capped, stream, 4096, time.monotonic() + 5)

    def test_timeout_is_not_retried(self):
        site = FakeSite()
        site.route('GET', site_mod.HOST, site_mod.UMIK1_PAGE, lambda _h, _b: (_ for _ in ()).throw(TimeoutError()))
        error = self.refused('calibration_fetch_failed', self.fetch, self.harness(site=site))
        self.assertIn('timed out', error.message)
        self.assertEqual(len(site.calls), 1)
        with self.assertRaises(TimeoutError):
            measure_cal.read_capped(io.BytesIO(b'z' * 10), 100, time.monotonic() - 1)

    def test_timeouts_and_limits_come_from_config(self):
        site = FakeSite().minidsp(text_file(cal_text(), '0000000_90deg.txt'))
        self.fetch(self.harness(cal_config(timeout_seconds=7, max_download_bytes=65536), site=site))
        self.assertEqual({(c['timeout'], c['limit']) for c in site.calls}, {(7, 65536)})

    def test_zip_bombs_traversal_and_member_limits(self):
        cases = [
            [('0000000.txt', b'0' * (2 * 1024 * 1024))],                      # expands past the size cap
            [(f'f{i}.txt', cal_text()) for i in range(measure_cal.ZIP_MAX_MEMBERS + 1)],
            [('../0000000.txt', cal_text())],
            [('/abs/0000000.txt', cal_text())],
            [('readme.pdf', b'%PDF')],                                          # no calibration member
            [('0000000.txt', b'\x00\x01\x02binary')],                           # binary member
        ]
        for members in cases:
            site = FakeSite().minidsp((200, [('Content-Type', 'application/zip')], site_mod.zip_bytes(members)))
            self.refused('calibration_invalid', self.fetch, self.harness(site=site))

    def test_unexpected_content_type(self):
        site = FakeSite().minidsp((200, [('Content-Type', 'image/png')], b'\x89PNG'))
        self.refused('calibration_unparseable', self.fetch, self.harness(site=site))

    def test_operator_can_disable_minidsp(self):
        site = FakeSite()
        h = self.harness(cal_config(providers={'minidsp': {'enabled': False}}), site=site)
        error = self.refused('provider_disabled', self.fetch, h)
        self.assertTrue(error.details['manual_steps'])
        self.assertEqual(site.calls, [])

    def test_rate_limit_per_provider(self):
        site = FakeSite().minidsp(html(site_mod.NOT_FOUND_HTML))
        h = self.harness(site=site)
        self.refused('not_found', self.fetch, h)
        error = self.refused('rate_limited', self.fetch, h)
        self.assertGreater(error.details['retry_after_seconds'], 0)
        self.assertEqual(len(site.calls), 2)
        h.clock.advance(31)
        self.refused('not_found', self.fetch, h)

    def test_serial_argument_is_required_and_checked(self):
        h = self.harness(site=FakeSite())
        self.refused('needs_serial', measure_cal.fetch, h.ctx, {'model': 'UMIK-1'})
        self.refused('invalid_argument', tools.call, h.ctx, 'measure_calibration_fetch',
                     {'model': 'UMIK-1', 'serial': '12-345'})

    def test_without_fakes_the_socket_guard_catches_a_real_lookup(self):
        h = self.harness()  # no fake transport/resolver: the default resolver would use DNS
        with self.assertRaises(AssertionError):
            measure_cal.fetch(h.ctx, {'model': 'UMIK-1', 'serial': '000-0000'})
        self.assertTrue(self.guard_mocks[0].called)


# ---- generic URL provider ------------------------------------------------------------------------
class UrlProviderTests(NoSockets):
    URL_ON = cal_config(providers={'url': {'enabled': True, 'allowed_hosts': ['cal.example.com']}})

    def test_disabled_by_default(self):
        site = FakeSite()
        h = self.harness(site=site)
        self.assertFalse(h.config.measurement['calibration']['providers']['url']['enabled'])
        self.refused('provider_disabled', self.fetch, h, model='ECM8000', provider='url',
                     url='https://cal.example.com/f.txt')
        self.assertEqual(site.calls, [])

    def test_enabled_host_download(self):
        site = FakeSite().route('GET', 'cal.example.com', '/files/mic_0000000_90deg.txt',
                                text_file(cal_text(header=False)))
        h = self.harness(self.URL_ON, site=site)
        out = self.fetch(h, model='ECM8000', url='https://cal.example.com/files/mic_0000000_90deg.txt')
        self.assertEqual(out['provider'], 'url')
        self.assertNotIn('verification', out)
        self.assertEqual(list(out['activated']), ['90'])
        record = measure.get_calibration(h.ctx, out['activated']['90'])
        self.assertEqual(record['source'], {'host': 'cal.example.com', 'path': '/files/mic_0000000_90deg.txt',
                                            'via': 'direct'})

    def test_refusals(self):
        site = FakeSite()
        h = self.harness(self.URL_ON, site=site)
        for url in ('https://cal.example.com/f.txt?token=abc', 'https://other.example.com/f.txt',
                    'https://www.minidsp.com/f.txt'):
            h.clock.advance(60)
            self.refused('url_refused', self.fetch, h, model='ECM8000', url=url)
        self.refused('invalid_argument', self.fetch, h, model='ECM8000', url='http://cal.example.com/f.txt')
        h.clock.advance(60)
        self.refused('invalid_argument', self.fetch, h, model='ECM8000')  # url is required
        self.assertEqual(site.calls, [])
        page = site_mod.html('<html><a href="/0000000.txt">x</a></html>')
        site.route('GET', 'cal.example.com', '/page', page)
        h.clock.advance(60)
        self.refused('calibration_unparseable', self.fetch, h, model='ECM8000', url='https://cal.example.com/page')

    def test_config_validation(self):
        bad = [{'providers': {'url': {'enabled': True}}},
               {'providers': {'url': {'enabled': True, 'allowed_hosts': ['192.0.2.1']}}},
               {'providers': {'url': {'enabled': True, 'allowed_hosts': ['*.example.com']}}},
               {'providers': {'url': {'enabled': True, 'allowed_hosts': ['cal.example.com:8443']}}},
               {'providers': {'url': {'enabled': 'yes', 'allowed_hosts': ['cal.example.com']}}},
               {'providers': {'dayton': {'enabled': True}}},
               {'import_dir': 'relative/dir'}, {'default_orientation': '45'}, {'retention': 1},
               {'max_download_bytes': 10 * 1024 * 1024}]
        for value in bad:
            with self.assertRaises(ConfigError, msg=value):
                Config(_merge(DEFAULT, {'state_dir': '/tmp/opencode/x', 'measurement': {'calibration': value}}))
        good = Config(_merge(DEFAULT, {'state_dir': '/tmp/opencode/x', 'measurement': {'calibration': {
            'providers': {'url': {'enabled': True, 'allowed_hosts': ['Cal.Example.COM.']}}}}}))
        self.assertEqual(good.measurement['calibration']['providers']['url']['allowed_hosts'], ['cal.example.com'])

    def test_check_url_rules(self):
        hosts = frozenset({'cal.example.com'})
        for url in ('http://cal.example.com/a.txt', 'ftp://cal.example.com/a.txt', 'https://cal.example.com:80/a',
                    'https://u@cal.example.com/a', 'https://cal.example.com/a b', 'https://evil.example/a',
                    'file:///etc/passwd', 'https://cal.example.com.evil.example/a'):
            self.refused('url_refused', measure_cal.check_url, url, hosts)
        self.refused('url_refused', measure_cal.check_url, 'https://cal.example.com/a?k=v', hosts, False)
        measure_cal.check_url('https://cal.example.com/a.txt', hosts)


# ---- validation and import -----------------------------------------------------------------------
class ImportAndValidation(NoSockets):
    def import_dir(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        base = Path(temp.name)
        (base / 'import').mkdir()
        (base / 'outside').mkdir()
        return base

    def test_text_import_activates_and_keeps_legacy_shape(self):
        h = self.harness(allow_write=False)
        out = tools.call(h.ctx, 'measure_import_calibration', {'text': cal_text(), 'name': 'umik 90deg'})
        for key in ('calibration_id', 'name', 'orientation', 'sensitivity_factor_db', 'points', 'freq_range_hz',
                    'correction_range_db', 'mic_orientation_advice', 'note'):
            self.assertIn(key, out)
        self.assertEqual(out['orientation'], '90')
        self.assertEqual(out['serial'], '000-0000')  # from the SERNO header
        self.assertEqual(out['activated'], {'90': out['calibration_id']})
        stored = tools.call(h.ctx, 'measure_import_calibration', {'text': cal_text(), 'name': 'spare 0deg',
                                                                  'activate': False})
        self.assertEqual(stored['activated'], {})
        self.assertIsNone(measure_cal.active_id(h.ctx, '0'))

    def test_sensitivity_value_is_not_an_orientation_marker(self):
        h = self.harness()
        out = tools.call(h.ctx, 'measure_import_calibration', {'text': cal_text(sens='-0.90'), 'name': 'mic'})
        self.assertEqual(out['orientation'], 'unknown')
        self.assertIn('needs_orientation', out)

    def test_garbage_is_rejected(self):
        h = self.harness()
        rows = lambda pts: '\n'.join(f'{f} {db}' for f, db in pts)  # noqa: E731
        cases = {
            'html': '<!DOCTYPE html><html><body>nope</body></html>',
            'non_monotonic': rows([(20, 0), (1000, 0), (500, 0), (10000, 0), (20000, 0)]),
            'too_low_freq': rows([(3, 0), (100, 0), (1000, 0), (10000, 0), (20000, 0)]),
            'too_high_freq': rows([(20, 0), (100, 0), (1000, 0), (10000, 0), (50000, 0)]),
            'too_much_db': rows([(20, 35), (100, 0), (1000, 0), (10000, 0), (20000, 0)]),
            'control_chars': rows([(20, 0), (100, 0), (1000, 0), (10000, 0), (20000, 0)]) + '\x07',
        }
        for label, text in cases.items():
            with self.assertRaises(OpsError, msg=label) as caught:
                tools.call(h.ctx, 'measure_import_calibration', {'text': text, 'name': 'mic 90deg'})
            self.assertEqual(caught.exception.code, 'calibration_invalid', label)
        self.refused('calibration_serial_mismatch', tools.call, h.ctx, 'measure_import_calibration',
                     {'text': cal_text(serno='0000001'), 'name': 'x', 'serial': '000-0000'})
        self.assertEqual(tools.call(h.ctx, 'measure_list_calibrations', {})['calibrations'], [])

    def test_exactly_one_source(self):
        h = self.harness()
        self.refused('invalid_argument', tools.call, h.ctx, 'measure_import_calibration', {'name': 'x'})
        self.refused('invalid_argument', tools.call, h.ctx, 'measure_import_calibration',
                     {'text': cal_text(), 'path': 'a.txt'})

    def test_path_import_is_confined_to_the_import_dir(self):
        base = self.import_dir()
        imp = base / 'import'
        (imp / '0000000_90deg.txt').write_text(cal_text())
        (imp / 'sub').mkdir()
        (imp / 'sub' / '0000000.txt').write_text(cal_text(offset=0.5))
        (base / 'outside' / 'secret.txt').write_text(cal_text())
        os.symlink(base / 'outside' / 'secret.txt', imp / 'link.txt')
        os.symlink(base / 'outside', imp / 'outdir')
        (imp / 'big.txt').write_text(cal_text(count=400))
        (imp / 'short.txt').write_text(cal_text(count=6))
        os.mkfifo(imp / 'fifo.txt')
        h = self.harness(cal_config(import_dir=str(imp), max_import_bytes=4096))
        out = tools.call(h.ctx, 'measure_import_calibration', {'path': '0000000_90deg.txt'})
        self.assertEqual(list(out['activated']), ['90'])
        self.assertEqual(out['stored'][0]['source'], {'kind': 'import_dir', 'file': '0000000_90deg.txt'})
        sub = tools.call(h.ctx, 'measure_import_calibration', {'path': 'sub/0000000.txt', 'orientation': '0'})
        self.assertEqual(list(sub['activated']), ['0'])
        for path, code in (('../outside/secret.txt', 'path_refused'), ('link.txt', 'path_refused'),
                           ('outdir/secret.txt', 'path_refused'), (str(base / 'outside' / 'secret.txt'),
                                                                   'path_refused'),
                           ('/etc/hostname', 'path_refused'), ('.', 'path_refused'),
                           ('missing.txt', 'not_found'), ('big.txt', 'file_too_large'),
                           ('fifo.txt', 'path_refused'), ('short.txt', 'calibration_invalid')):
            self.refused(code, tools.call, h.ctx, 'measure_import_calibration', {'path': path})

    def test_path_import_needs_configured_dir(self):
        self.refused('import_dir_not_configured', tools.call, self.harness().ctx, 'measure_import_calibration',
                     {'path': 'a.txt'})

    def test_path_import_zip(self):
        imp = self.import_dir() / 'import'
        (imp / 'cal.zip').write_bytes(site_mod.zip_bytes([('0000000.txt', cal_text()),
                                                          ('0000000_90deg.txt', cal_text(offset=1))]))
        h = self.harness(cal_config(import_dir=str(imp)))
        out = tools.call(h.ctx, 'measure_import_calibration', {'path': 'cal.zip', 'serial': '000-0000'})
        self.assertEqual(set(out['activated']), {'0', '90'})

    def test_reactivation_rules(self):
        h = self.harness()
        zero = tools.call(h.ctx, 'measure_import_calibration', {'text': cal_text(), 'name': 'mic 0deg'})
        self.refused('orientation_mismatch', tools.call, h.ctx, 'measure_import_calibration',
                     {'calibration_id': zero['calibration_id'], 'orientation': '90'})
        self.refused('invalid_argument', tools.call, h.ctx, 'measure_import_calibration',
                     {'calibration_id': zero['calibration_id']})


# ---- storage, retention, concurrency ---------------------------------------------------------------
class Storage(NoSockets):
    def test_metadata_permissions_and_no_raw_download(self):
        site = FakeSite().minidsp(text_file(cal_text(), '0000000_90deg.txt'))
        h = self.harness(site=site)
        out = self.fetch(h)
        ident = out['activated']['90']
        record = measure.get_calibration(h.ctx, ident)
        for key in ('model', 'serial', 'orientation', 'provider', 'source', 'file_sha256', 'fetched_at', 'parser',
                    'verification'):
            self.assertIn(key, record)
        self.assertEqual((record['model'], record['serial'], record['provider']), ('UMIK-1', '000-0000', 'minidsp'))
        self.assertEqual(record['parser']['points'], 40)
        self.assertEqual(record['parser']['header_serial'], '000-0000')
        directory = Path(h.config.state_dir) / measure.CAL_DIR
        self.assertEqual(stat.S_IMODE(directory.stat().st_mode), 0o700)
        for path in directory.iterdir():
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600, path.name)
            self.assertFalse(path.name.endswith(('.txt', '.zip')))
        status = tools.call(h.ctx, 'measure_calibration_status', {})
        self.assertEqual(status['active']['90']['calibration_id'], ident)
        self.assertTrue(status['ready_for_default_orientation'])

    def test_retention_never_prunes_the_active_calibration(self):
        h = self.harness(cal_config(retention=5))
        active = tools.call(h.ctx, 'measure_import_calibration', {'text': cal_text(), 'name': 'mic 90deg'})
        for i in range(8):
            tools.call(h.ctx, 'measure_import_calibration', {'text': cal_text(offset=i / 10), 'name': f'spare {i}'})
        listed = tools.call(h.ctx, 'measure_list_calibrations', {})
        ids = [c['calibration_id'] for c in listed['calibrations']]
        self.assertEqual(len(ids), 5)
        self.assertIn(active['calibration_id'], ids)
        self.assertEqual(listed['active']['90'], active['calibration_id'])
        self.assertEqual([c['active_for'] for c in listed['calibrations'] if c['calibration_id'] ==
                          active['calibration_id']], [['90']])

    def test_concurrent_imports_keep_state_consistent(self):
        h = self.harness()
        results, errors = [], []

        def worker(i):
            try:
                results.append(tools.call(h.ctx, 'measure_import_calibration',
                                          {'text': cal_text(offset=i / 10), 'name': f'mic {"90deg" if i % 2 else "0deg"}'}))
            except Exception as error:  # noqa: BLE001
                errors.append(error)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(errors, [])
        self.assertEqual(len({r['calibration_id'] for r in results}), 8)
        active = json.loads((Path(h.config.state_dir) / measure.CAL_DIR / measure_cal.ACTIVE_FILE).read_text())
        self.assertEqual(set(active), {'0', '90'})
        ids = {r['calibration_id'] for r in results}
        self.assertTrue({v['calibration_id'] for v in active.values()} <= ids)


# ---- automatic selection, sessions, status, prompt -------------------------------------------------
class AutoSelection(NoSockets):
    def setUp(self):
        super().setUp()
        self.h = self.harness()
        tools.call(self.h.ctx, 'room_profile_update', {'set': PROFILE})

    def install(self):
        zero = tools.call(self.h.ctx, 'measure_import_calibration', {'text': cal_text(), 'name': 'mic 0deg'})
        ninety = tools.call(self.h.ctx, 'measure_import_calibration', {'text': cal_text(offset=1), 'name': 'mic 90deg'})
        return zero['calibration_id'], ninety['calibration_id']

    def test_resolution_order(self):
        zero, ninety = self.install()
        ctx = self.h.ctx
        self.assertEqual(measure.resolve_calibration(ctx, {}, None), (ninety, 'active 90-degree calibration'))
        self.assertEqual(measure.resolve_calibration(ctx, {'calibration_id': zero}, None), (zero, 'explicit'))
        self.assertEqual(measure.resolve_calibration(ctx, {}, {'session_id': 'x', 'calibration_id': None}),
                         (None, 'session'))
        h0 = self.harness(cal_config(default_orientation='0'), allow_write=False)  # separate, empty state
        self.assertEqual(measure.resolve_calibration(h0.ctx, {}, None), (None, None))

    def test_session_start_warns_then_pins_the_matching_calibration(self):
        ctx = self.h.ctx
        bare = tools.call(ctx, 'measure_session_start', {'positions': 4, 'channels': ['left']})
        self.assertIn('no active 90-degree microphone calibration', bare['warning'])
        self.assertIn('measure_calibration_status', bare['warning'])
        status = tools.call(ctx, 'measure_calibration_status', {})
        self.assertFalse(status['session']['matching'])
        self.assertIn('next_step', status)
        zero, ninety = self.install()
        up = tools.call(ctx, 'measure_session_start', {'positions': 4, 'channels': ['left'], 'replace': True})
        self.assertEqual(up['calibration_id'], ninety)
        self.assertEqual(up['mic_orientation'], '90')
        self.assertNotIn('warning', up)
        self.assertIn('applied automatically', up['calibration_note'])
        self.assertTrue(tools.call(ctx, 'measure_calibration_status', {})['session']['matching'])
        flat = tools.call(ctx, 'measure_session_start', {'positions': 4, 'channels': ['left'], 'replace': True,
                                                         'mic_orientation': '0'})
        self.assertEqual(flat['calibration_id'], zero)
        self.assertIn('horizontally', flat['plan']['mic_orientation'])
        newer = tools.call(ctx, 'measure_import_calibration', {'text': cal_text(offset=2), 'name': 'new 0deg'})
        status = tools.call(ctx, 'measure_calibration_status', {})
        self.assertFalse(status['session']['matching'])
        self.assertEqual(status['session']['active_calibration_for_orientation'], newer['calibration_id'])
        self.assertIn('replace=true', status['session']['advice'])
        mismatch = tools.call(ctx, 'measure_session_start', {'positions': 4, 'channels': ['left'], 'replace': True,
                                                             'mic_orientation': '90', 'calibration_id': zero})
        self.assertIn('0-degree file', mismatch['warning'])

    def test_legacy_session_orientation_inference(self):
        self.assertEqual(measure_session.session_orientation({'plan': {'mic_orientation': 'pointing straight up'}}),
                         '90')
        self.assertEqual(measure_session.session_orientation({'plan': {'mic_orientation': 'held horizontally'}}),
                         '0')

    def test_status_lists_providers_and_gating(self):
        status = tools.call(self.harness(allow_write=False).ctx, 'measure_calibration_status', {})
        names = {p['name']: p for p in status['providers']}
        self.assertEqual(set(names), {'minidsp', 'url', 'import'})
        self.assertTrue(names['minidsp']['enabled'])
        self.assertFalse(names['url']['enabled'])
        self.assertEqual(names['minidsp']['hosts'], ['www.minidsp.com'])
        self.assertIn('unverified', names['minidsp']['status'])
        self.assertEqual(status['fetch_needs'], 'allow_write (not allow_measure)')
        self.assertTrue(status['manual_steps'])

    def test_prompt_checks_status_first_and_explains_fallback(self):
        text = content.get_prompt('guided-room-measurement')['text']
        for needle in ('measure_calibration_status', 'measure_mic_detect', 'needs_serial',
                       'measure_calibration_fetch', 'allow_write', 'manual_steps', 'measure_import_calibration',
                       'unverified'):
            self.assertIn(needle, text)
        self.assertLess(text.index('measure_calibration_status'), text.index('measure_session_start'))


# ---- gating and tool metadata --------------------------------------------------------------------
class Gating(NoSockets):
    def test_fetch_needs_allow_write_not_allow_measure(self):
        site = FakeSite().minidsp(text_file(cal_text(), '0000000_90deg.txt'))
        off = self.harness(site=site, allow_write=False)
        self.refused('write_disabled', self.fetch, off)
        self.assertEqual(site.calls, [])
        on = self.harness(site=site, allow_write=True)
        self.assertFalse(on.config.allow_measure)
        self.assertEqual(list(self.fetch(on)['activated']), ['90'])

    def test_tool_tiers_and_hints(self):
        h = self.harness(allow_write=False)
        described = {d['name']: d for d in tools.describe(fakes.Harness(self, {'allow_write': True}).config)}
        self.assertEqual(tools.BY_NAME['measure_calibration_fetch'].access, tools.WRITE)
        self.assertEqual(tools.BY_NAME['measure_mic_detect'].access, tools.READ)
        self.assertEqual(tools.BY_NAME['measure_calibration_status'].access, tools.READ)
        self.assertEqual(tools.BY_NAME['measure_import_calibration'].access, tools.LOCAL)
        fetch = described['measure_calibration_fetch']['annotations']
        self.assertTrue(fetch['openWorldHint'])
        self.assertFalse(fetch['readOnlyHint'])
        others = [d for n, d in described.items() if n != 'measure_calibration_fetch']
        self.assertFalse(any(d['annotations']['openWorldHint'] for d in others))
        self.assertIn('measure_mic_detect', {d['name'] for d in tools.describe(h.config)})


# ---- detection ---------------------------------------------------------------------------------------
class Detection(NoSockets):
    def root(self, *builders):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        site_mod.empty_tree(temp.name)
        for build in builders:
            build(temp.name)
        return temp.name

    def detect(self, root, runner=None, **args):
        h = self.harness(allow_write=False)
        h.ctx.detect_root = root
        if runner:
            h.ctx.measure_runner = runner
        return tools.call(h.ctx, 'measure_mic_detect', args)

    def test_umik1_with_candidate_serial(self):
        out = self.detect(self.root(site_mod.umik1_tree))
        self.assertEqual(out['status'], 'serial_candidate')
        [mic] = out['microphones']
        self.assertEqual(mic['model'], 'UMIK-1')
        self.assertEqual(mic['confidence'], 'high')
        self.assertEqual(mic['serial'], {'value': '000-0000', 'source': 'usb serial string',
                                         'status': 'candidate, confirm with user'})
        self.assertEqual(mic['alsa_card']['number'], 1)
        self.assertIn('/proc/asound card', mic['sources'])
        self.assertIn('confirm', out['ask_user'])

    def test_umik2_without_usable_serial_needs_serial(self):
        out = self.detect(self.root(site_mod.umik2_tree))
        self.assertEqual(out['status'], 'needs_serial')
        [mic] = out['microphones']
        self.assertEqual(mic['model'], 'UMIK-2')
        self.assertIsNone(mic['serial'])
        self.assertTrue(mic['usb_serial_present_but_not_7_digits'])
        self.assertIn('printed on the microphone', out['ask_user'])

    def test_unknown_capture_device(self):
        out = self.detect(self.root(site_mod.unknown_capture_tree, site_mod.dac_only_tree))
        self.assertEqual(out['status'], 'needs_serial')
        [mic] = out['microphones']
        self.assertEqual(mic['model'], 'unknown')
        self.assertEqual(mic['confidence'], 'low')

    def test_nothing_found_and_listing_fallback(self):
        calls = []

        def runner(argv, timeout):
            calls.append(argv)
            return 0, '', ''

        out = self.detect(self.root(site_mod.dac_only_tree), runner)
        self.assertEqual(out['status'], 'no_microphone_found')
        self.assertEqual(out['microphones'], [])
        self.assertTrue(calls)  # the configured read-only listing commands (faked here)
        listed = self.detect(self.root(), lambda argv, timeout: (0, 'card 1: U18dB [Umik-1  Gain: 18dB], device 0', ''))
        self.assertEqual([m['model'] for m in listed['microphones']], ['UMIK-1'])
        self.assertEqual(listed['microphones'][0]['sources'], ['measure_list_devices output'])
        calls.clear()
        self.detect(self.root(), runner, include_device_listing=False)
        self.assertEqual(calls, [])

    def test_umik_10_is_not_umik_1(self):
        self.assertIsNone(measure_cal._model('UMIK-10'))
        self.assertEqual(measure_cal._model('miniDSP umik_2'), 'UMIK-2')


if __name__ == '__main__':
    unittest.main()
