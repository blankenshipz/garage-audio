"""Fake calibration download sites, calibration files and /proc + /sys trees for the calibration tests.

Nothing here touches the network: ``FakeSite.transport`` and ``FakeSite.resolver`` replace the real
HTTPS transport and DNS resolver. Serial numbers are placeholders only (000-0000 style).
"""
import io
import math
import os
import zipfile
from pathlib import Path
from urllib.parse import parse_qs

PUBLIC_ADDRESS = '9.9.9.9'  # any globally routable address; never contacted (the transport is fake)
UMIK1_PAGE = '/products/acoustic-measurement/umik-1'
UMIK2_PAGE = '/products/acoustic-measurement/umik-2'
HOST = 'www.minidsp.com'
TOKEN_NAME = '0123456789abcdef0123456789abcdef'
NOT_FOUND_HTML = ('<html><body><div id="umikcalfile"><p style="color:red;">Calibration data for UMIK 000-0000 not '
                  'found. Please check your serial number and try again.</p></div></body></html>')


def cal_text(serno='0000000', count=40, sens='-1.25', offset=0.0, header=True, extra=''):
    """A UMIK-style calibration file: quoted header line plus ``count`` log-spaced freq/dB rows (10 Hz-20 kHz)."""
    lines = [f'"Sens Factor ={sens}dB, AGain =18dB, SERNO: {serno}"'] if header else []
    if extra:
        lines.append(extra)
    for i in range(count):
        f = 10.0 * (2000.0 ** (i / (count - 1)))
        lines.append(f'{f:.3f}\t{offset + 1.5 * math.sin(i / 5.0):.4f}')
    return '\n'.join(lines) + '\n'


def product_page(anchor='umikcalfile', honeypot='your_name_here', token_value='1795000000', form=True):
    cal_form = (f'<form method="POST" action="#{anchor}" id="{anchor}">'
                '<input type="text" name="serial1" maxlength="3" value=""> - '
                '<input type="text" name="serial2" maxlength="4" value="">'
                f'<input type="text" name="{honeypot}" value="" style="display:none" tabindex="-1">'
                f'<input type="hidden" name="{TOKEN_NAME}" value="{token_value}">'
                '<input type="submit" name="submit" value="Download">'
                '</form>') if form else ''
    return ('<!DOCTYPE html><html><head><title>UMIK</title></head><body>'
            '<form action="/search" method="get"><input type="text" name="q"></form>'
            '<a href="/files/software/umik-setup.zip">software</a>'
            f'{cal_form}</body></html>').encode()


def zip_bytes(members, compression=zipfile.ZIP_DEFLATED):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', compression) as archive:
        for name, data in members:
            archive.writestr(name, data)
    return buffer.getvalue()


def html(body, status=200):
    return status, [('Content-Type', 'text/html; charset=utf-8')], body.encode() if isinstance(body, str) else body


def text_file(data, filename=None, ctype='text/plain'):
    headers = [('Content-Type', ctype)]
    if filename:
        headers.append(('Content-Disposition', f'attachment; filename="{filename}"'))
    return 200, headers, data.encode() if isinstance(data, str) else data


class FakeSite:
    """Routes keyed by (method, host, path-without-query). Records every request it receives."""

    def __init__(self, addresses=(PUBLIC_ADDRESS,)):
        self.routes = {}
        self.calls = []
        self.resolved = []
        self.addresses = list(addresses)

    def route(self, method, host, path, reply):
        self.routes[(method, host, path)] = reply
        return self

    def minidsp(self, post_reply, page=None, model_path=UMIK1_PAGE):
        self.route('GET', HOST, model_path, html(page if page is not None else product_page()))
        self.route('POST', HOST, model_path, post_reply)
        return self

    def resolver(self, host):
        self.resolved.append(host)
        return list(self.addresses)

    def transport(self, method, address, host, path, headers, body, timeout, limit):
        self.calls.append({'method': method, 'address': address, 'host': host, 'path': path,
                           'headers': dict(headers), 'body': body, 'timeout': timeout, 'limit': limit})
        reply = self.routes.get((method, host, path.split('?', 1)[0]))
        if reply is None:
            return 404, [('Content-Type', 'text/html')], b'<html><body>missing</body></html>'
        if callable(reply):
            return reply(headers, body)
        return reply

    def posted(self):
        """The decoded fields of the last POST."""
        post = [c for c in self.calls if c['method'] == 'POST'][-1]
        return post, parse_qs(post['body'].decode('ascii'), keep_blank_values=True)


def wire(ctx, site):
    ctx.calibration_transport = site.transport
    ctx.calibration_resolver = site.resolver
    return site


# ---- fake /proc + /sys trees -------------------------------------------------------------
def _write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def usb_device(root, name, vendor, product_id, product=None, manufacturer=None, serial=None):
    base = Path(root) / 'sys/bus/usb/devices' / name
    _write(base / 'idVendor', vendor + '\n')
    _write(base / 'idProduct', product_id + '\n')
    for key, value in (('product', product), ('manufacturer', manufacturer), ('serial', serial)):
        if value is not None:
            _write(base / key, value + '\n')
    # an interface entry (no idVendor) is ignored
    (Path(root) / 'sys/bus/usb/devices' / f'{name}:1.0').mkdir(parents=True, exist_ok=True)


def alsa_card(root, number, card_id, desc, usbid=None, capture=True, playback=False):
    proc = Path(root) / 'proc/asound'
    cards = proc / 'cards'
    existing = cards.read_text() if cards.exists() else ''
    _write(cards, existing + f' {number} [{card_id:<15}]: USB-Audio - {desc}\n'
                             f'                      {desc} at usb-0000:00:14.0-1, full speed\n')
    if usbid:
        _write(proc / f'card{number}/usbid', usbid + '\n')
    stream = f'{desc} at usb-0000:00:14.0-1, full speed : USB Audio\n\n'
    if playback:
        stream += 'Playback:\n  Status: Stop\n'
    if capture:
        stream += 'Capture:\n  Status: Stop\n  Interface 1\n'
    _write(proc / f'card{number}/stream0', stream)


def umik1_tree(root, serial='000-0000'):
    usb_device(root, '1-2', '2752', '0007', product='Umik-1  Gain: 18dB', manufacturer='miniDSP', serial=serial)
    alsa_card(root, 1, 'U18dB', 'miniDSP Umik-1  Gain: 18dB', usbid='2752:0007')


def umik2_tree(root):
    usb_device(root, '1-3', '2752', '0016', product='UMIK-2', manufacturer='miniDSP', serial='ABCDEF')
    alsa_card(root, 2, 'UMIK2', 'miniDSP UMIK-2', usbid='2752:0016')


def unknown_capture_tree(root):
    usb_device(root, '1-4', '0d8c', '0014', product='USB Audio Device', manufacturer='Generic')
    alsa_card(root, 3, 'Device', 'Generic USB Audio Device', usbid='0d8c:0014', capture=True, playback=False)


def dac_only_tree(root):
    usb_device(root, '1-5', '0d8c', '0102', product='USB DAC', manufacturer='Generic')
    alsa_card(root, 4, 'DAC', 'Generic USB DAC', usbid='0d8c:0102', capture=False, playback=True)


def empty_tree(root):
    os.makedirs(Path(root) / 'sys/bus/usb/devices', exist_ok=True)
    os.makedirs(Path(root) / 'proc/asound', exist_ok=True)
