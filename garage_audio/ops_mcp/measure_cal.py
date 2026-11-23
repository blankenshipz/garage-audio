"""Measurement-microphone calibration: detect the mic, fetch its per-serial file, import, store, activate.

Stdlib only. Pieces:

- ``detect``: read-only look at ``/proc/asound`` and ``/sys/bus/usb/devices`` (the root is
  injectable for tests) plus the configured read-only ``list_devices_argv`` output. A USB
  serial string is only ever reported as a *candidate*: whether it equals the serial printed
  on the microphone is not verified, so the agent must confirm it with the user.
- Providers (``PROVIDERS``): ``minidsp`` (UMIK-1 / UMIK-2 through miniDSP's public serial
  form) and ``url`` (a direct download URL, disabled unless the operator enables it AND lists
  its hosts). Local files and pasted contents go through ``measure_import_calibration``.
  Other vendors (Dayton Audio, Cross-Spectrum, ...) have no verified endpoint and are not
  hard-coded; add a ``Provider`` subclass with its own host allowlist when one is verified.
- ``SafeFetcher``: HTTPS only, per-provider host allowlist, manual redirects inside the
  allowlist (at most ``MAX_REDIRECTS``), every resolved address must be globally routable
  (no private/loopback/link-local/multicast/reserved), the connection is pinned to the
  checked address (no DNS rebinding between check and connect), connect/read timeouts and a
  streamed response size cap. One request per step; no retry loops.
- Validation (strict for fetched and file imports), storage in the calibration journal with
  provenance metadata, bounded retention that never prunes an active calibration, and one
  active calibration per orientation (0 / 90 degrees) that the engine picks automatically.

miniDSP endpoint status: the form (fields, honeypot, token) and the "not found" reply were
observed on 2026-11-23; the SUCCESS reply was NOT observed (no real serial available). The
success parsing is therefore defensive and every miniDSP result says
``UNVERIFIED_NOTE``. miniDSP's robots.txt disallows ``/`` for all user agents: this is a single
user-initiated request for the user's own file (one GET of the product page, one POST of the
form, then at most ``MAX_DOWNLOADS`` file downloads), with an honest User-Agent, a minimum
interval per provider and an operator switch (``measurement.calibration.providers.minidsp.enabled``).
"""
import contextlib
import fcntl
import hashlib
import http.client
import io
import ipaddress
import os
import re
import socket
import ssl
import stat
import threading
import time
import zipfile
from html.parser import HTMLParser
from pathlib import Path, PurePosixPath
from urllib.parse import urlencode, urljoin, urlsplit

from . import measure
from .backups import Journal, _ensure_private_dir, atomic_write_json, read_json
from .errors import OpsError

VERSION = '0.1.0'  # pyproject.toml [project] version
USER_AGENT = f'garage-audio-ops-mcp/{VERSION} (user-initiated calibration download)'
UNVERIFIED_NOTE = ('miniDSP success response: unverified against a real serial (the form and the "not found" reply '
                   'were observed; the download step was not). Check the stored file summary.')
ORIENTATIONS = ('0', '90')
MAX_REDIRECTS = 3
MAX_DOWNLOADS = 4
ZIP_MAX_MEMBERS = 16
ZIP_MAX_TOTAL = 4 * 1024 * 1024
ZIP_MEMBER_EXT = ('.txt', '.cal', '.frd')
FILE_EXT = ZIP_MEMBER_EXT + ('.zip',)
MIN_POINTS = 10
FREQ_RANGE = (5.0, 48000.0)
MAX_ABS_DB = 30.0
ACTIVE_FILE = 'active.json'
FETCH_STATE_FILE = 'fetch-state.json'
LOCK_FILE = '.calibration.lock'
SERIAL = re.compile(r'^\s*([0-9]{3})-?([0-9]{4})\s*$')
SERIAL_PATTERN = r'^[0-9]{3}-?[0-9]{4}$'
SERNO = re.compile(r'(?i)SERNO\s*:?\s*([0-9]{3})-?([0-9]{4})(?![0-9])')
HTML = re.compile(r'(?is)^\s*(?:<!doctype|<html|<\?xml|<head|<body)|<(?:html|body|head|script|form|div|table|p)[\s>]')
NOT_FOUND = re.compile(r'(?is)Calibration data for UMIK[^<]{0,40}?not found\.[^<]{0,120}')
JS_LOCATION = re.compile(r'''(?i)(?:location(?:\.href)?\s*=|location\.(?:assign|replace)\(|window\.open\()\s*['"]([^'"]{1,2048})['"]''')
META_URL = re.compile(r'(?i)url\s*=\s*[\'"]?([^\'";]+)')
_THREAD_LOCK = threading.Lock()


def manual_steps(model=None):
    page = MINIDSP_PAGES.get(model, (None,))[0]
    first = (f'Open {page} in a browser, enter the serial number printed on the microphone (XXX-XXXX) in the '
             'calibration file form and submit.' if page else
             'Download the calibration file for this serial from the microphone vendor\'s web site in a browser.')
    return [first,
            'Save the downloaded file(s): usually a 0-degree file and a 90-degree file (use the 90-degree file with '
            'the mic pointing straight up).',
            'Then either paste the file contents into measure_import_calibration (text, name, orientation, serial), '
            'or ask the operator to copy the file into measurement.calibration.import_dir and call '
            'measure_import_calibration with path set to the file name.']


def normalize_serial(value, required=True):
    if value is None or value == '':
        if required:
            raise OpsError('needs_serial', 'the microphone serial number is required (7 digits printed on the mic or '
                           'its box, written XXX-XXXX); ask the user for it')
        return None
    match = SERIAL.match(value) if isinstance(value, str) else None
    if not match:
        raise OpsError('invalid_argument', 'serial must be 7 digits written XXX-XXXX (as printed on the microphone)')
    return f'{match.group(1)}-{match.group(2)}'


def _cfg(ctx):
    return ctx.config.measurement['calibration']


# ---- storage ---------------------------------------------------------------------------
class CalJournal(Journal):
    """The calibration journal: bounded, but an active (or just-written) calibration is never pruned."""

    def __init__(self, root, retention, clock, protected):
        super().__init__(root, measure.CAL_DIR, retention, clock)
        self.protected = protected
        self.pinned = set()

    def _prune(self, directory, keep):
        ids = self._ids(directory)
        keep_set = set(self.protected()) | self.pinned | {keep}
        excess = len(ids) - self.retention
        for ident in [i for i in ids if i not in keep_set][:max(0, excess)]:
            try:
                (directory / f'{ident}.json').unlink()
            except OSError:
                pass


def _dir(ctx):
    return _ensure_private_dir(_ensure_private_dir(Path(ctx.config.state_dir)) / measure.CAL_DIR)


def journal(ctx):
    return CalJournal(ctx.config.state_dir, _cfg(ctx)['retention'], ctx.clock,
                      lambda: [v['calibration_id'] for v in _read_active(Path(ctx.config.state_dir) / measure.CAL_DIR)
                               .values()])


@contextlib.contextmanager
def _locked(ctx):
    directory = _dir(ctx)
    with _THREAD_LOCK:
        fd = os.open(directory / LOCK_FILE, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield directory
        finally:
            os.close(fd)


def _read_state(path):
    try:
        value = read_json(path)
    except (OSError, ValueError, OpsError):
        return {}
    return value if isinstance(value, dict) else {}


def _read_active(directory):
    value = _read_state(Path(directory) / ACTIVE_FILE)
    return {k: v for k, v in value.items()
            if k in ORIENTATIONS and isinstance(v, dict) and isinstance(v.get('calibration_id'), str)}


def active_id(ctx, orientation):
    """The active calibration id for ``orientation`` ('0' or '90') if its record still exists, else None."""
    entry = _read_active(Path(ctx.config.state_dir) / measure.CAL_DIR).get(orientation)
    if not entry:
        return None
    try:
        measure.get_calibration(ctx, entry['calibration_id'])
    except OpsError:
        return None
    return entry['calibration_id']


def store(ctx, items, activate=True):
    """Save records and (optionally) activate them. ``items``: [(record, orientation_to_activate or None)]."""
    with _locked(ctx) as directory:
        book = journal(ctx)
        saved = []
        for record, target in items:
            stored = book.save(record)
            book.pinned.add(stored['id'])
            saved.append((stored, target))
        activated = {}
        if activate and any(target for _r, target in saved):
            active = _read_active(directory)
            for stored, target in saved:
                if target in ORIENTATIONS:
                    active[target] = {'calibration_id': stored['id'], 'activated_at': round(ctx.clock(), 3)}
                    activated[target] = stored['id']
            atomic_write_json(directory / ACTIVE_FILE, active)
    return saved, activated


def _rate_limit(ctx, provider):
    interval = _cfg(ctx)['min_fetch_interval_seconds']
    with _locked(ctx) as directory:
        state = _read_state(directory / FETCH_STATE_FILE)
        last = state.get(provider)
        now = ctx.clock()
        if isinstance(last, (int, float)) and abs(now - last) < interval:
            wait = round(interval - abs(now - last), 1)
            raise OpsError('rate_limited', f'a {provider} calibration fetch ran {round(abs(now - last), 1)} s ago; '
                           f'one request per {interval} s (no automatic retries)', retry_after_seconds=wait)
        state[provider] = round(now, 3)
        atomic_write_json(directory / FETCH_STATE_FILE, state)


# ---- validation ------------------------------------------------------------------------
def _decode(data):
    if isinstance(data, str):
        text = data
    else:
        if b'\x00' in data:
            raise OpsError('calibration_invalid', 'the file is binary, not a calibration text file')
        try:
            text = data.decode('utf-8-sig')
        except UnicodeDecodeError:
            raise OpsError('calibration_invalid', 'the file is not UTF-8/ASCII text') from None
    if re.search(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', text):
        raise OpsError('calibration_invalid', 'the file contains binary control characters')
    if HTML.search(text[:8192]):
        raise OpsError('calibration_invalid', 'this is an HTML page, not a calibration file')
    return text


def _prepare(text):
    """UMIK files may carry extra quoted header lines; mark them as comments for the shared parser."""
    out = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith('"') and not re.match(r'(?i)^"?Sens Factor\s*=', stripped):
            out.append('* ' + stripped)
        else:
            out.append(line)
    return '\n'.join(out) + '\n'


def validate(data, name, *, orientation=None, serial=None, strict=True):
    """Parse and sanity-check one calibration file. Returns (parsed record, summary)."""
    text = _decode(data)
    parsed = measure.parse_calibration(_prepare(text), name, orientation)
    points = parsed['points']
    if strict and len(points) < MIN_POINTS:
        raise OpsError('calibration_invalid', f'only {len(points)} data points; a calibration file has many more '
                       f'(at least {MIN_POINTS} required)')
    if points[0][0] < FREQ_RANGE[0] or points[-1][0] > FREQ_RANGE[1]:
        raise OpsError('calibration_invalid', f'frequencies must lie within {FREQ_RANGE[0]:g} Hz-{FREQ_RANGE[1]:g} Hz')
    if max(abs(p[1]) for p in points) > MAX_ABS_DB:
        raise OpsError('calibration_invalid', f'corrections above {MAX_ABS_DB:g} dB are not plausible for a '
                       'measurement microphone')
    header_serial = None
    for line in text.splitlines()[:40]:
        match = SERNO.search(line)
        if match:
            header_serial = f'{match.group(1)}-{match.group(2)}'
            break
    if serial and header_serial and header_serial != serial:
        raise OpsError('calibration_serial_mismatch', f'the file is for serial {header_serial}, not the requested '
                       f'{serial}', file_serial=header_serial)
    headers = [line.strip()[:200] for line in text.splitlines()[:40]
               if line.strip() and not re.match(r'^[-+0-9.]', line.strip())][:5]
    summary = {'points': len(points), 'freq_range_hz': [points[0][0], points[-1][0]],
               'correction_range_db': [round(min(p[1] for p in points), 2), round(max(p[1] for p in points), 2)],
               'sensitivity_factor_db': parsed['sensitivity_factor_db'], 'again_db': parsed['again_db'],
               'header_serial': header_serial, 'headers': headers}
    return parsed, summary


# ---- network -----------------------------------------------------------------------------
class Response:
    def __init__(self, status, headers, body, url):
        self.status = status
        self.headers = {k.lower(): v for k, v in headers}
        self.cookies = [v for k, v in headers if k.lower() == 'set-cookie']
        self.body = body
        self.url = url

    @property
    def content_type(self):
        return self.headers.get('content-type', '').split(';')[0].strip().lower()

    @property
    def filename(self):
        disposition = self.headers.get('content-disposition', '')
        match = re.search(r'''(?i)filename\*?\s*=\s*(?:UTF-8'')?["']?([^"';]+)''', disposition)
        return os.path.basename(match.group(1).strip()) if match else None

    @property
    def is_attachment(self):
        return self.headers.get('content-disposition', '').strip().lower().startswith('attachment')


def source_of(url):
    parts = urlsplit(url)
    return {'host': (parts.hostname or '').lower(), 'path': parts.path or '/'}


def check_url(url, hosts, allow_query=True):
    """Refuse anything but https://<allowlisted host>[:443]/... without credentials."""
    if not isinstance(url, str) or len(url) > 2048 or any(c in url for c in '\x00\r\n\t '):
        raise OpsError('url_refused', 'invalid URL')
    parts = urlsplit(url)
    if parts.scheme.lower() != 'https':
        raise OpsError('url_refused', 'only https:// URLs are allowed')
    if parts.username is not None or parts.password is not None or '@' in parts.netloc:
        raise OpsError('url_refused', 'URLs with credentials are not allowed')
    try:
        port = parts.port
    except ValueError:
        raise OpsError('url_refused', 'invalid port') from None
    if port not in (None, 443):
        raise OpsError('url_refused', 'only the standard HTTPS port is allowed')
    host = (parts.hostname or '').lower().rstrip('.')
    if host not in hosts:
        raise OpsError('url_refused', f'host {host or "(none)"} is not in this provider\'s allowlist',
                       allowed_hosts=sorted(hosts))
    if parts.query and not allow_query:
        raise OpsError('url_refused', 'URLs with a query string are not allowed here (no tokens or secrets in URLs)')
    return parts, host


def check_addresses(addresses, host):
    if not addresses:
        raise OpsError('calibration_fetch_failed', f'{host} did not resolve')
    for raw in addresses:
        try:
            ip = ipaddress.ip_address(str(raw).split('%')[0])
        except ValueError:
            raise OpsError('url_refused', f'{host} resolved to an invalid address') from None
        mapped = getattr(ip, 'ipv4_mapped', None)
        for candidate in (ip, mapped) if mapped else (ip,):
            if (not candidate.is_global or candidate.is_private or candidate.is_loopback or candidate.is_link_local
                    or candidate.is_multicast or candidate.is_reserved or candidate.is_unspecified):
                raise OpsError('url_refused', f'{host} resolves to a non-public address; refused')
    return str(addresses[0]).split('%')[0]


def default_resolver(host):
    infos = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    return [info[4][0] for info in infos]


def read_capped(stream, limit, deadline):
    chunks, total = [], 0
    while True:
        if time.monotonic() > deadline:
            raise TimeoutError('read deadline')
        chunk = stream.read(65536)
        if not chunk:
            return b''.join(chunks)
        total += len(chunk)
        if total > limit:
            raise OpsError('response_too_large', f'the response is larger than {limit} bytes; refused')
        chunks.append(chunk)


class _PinnedHTTPS(http.client.HTTPSConnection):
    """HTTPS to the already-checked address; certificate and SNI are verified for the host name."""

    def __init__(self, host, address, timeout):
        super().__init__(host, 443, timeout=timeout, context=ssl.create_default_context())
        self._address = address

    def connect(self):
        sock = socket.create_connection((self._address, 443), self.timeout)
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)


def default_transport(method, address, host, path, headers, body, timeout, limit):
    conn = _PinnedHTTPS(host, address, timeout)
    try:
        conn.request(method, path, body=body, headers=headers)
        resp = conn.getresponse()
        data = read_capped(resp, limit, time.monotonic() + 2 * timeout)
        return resp.status, resp.getheaders(), data
    finally:
        conn.close()


class SafeFetcher:
    def __init__(self, hosts, *, timeout, limit, transport=None, resolver=None):
        self.hosts = frozenset(h.lower() for h in hosts)
        self.timeout = timeout
        self.limit = limit
        self.transport = transport or default_transport
        self.resolver = resolver or default_resolver
        self.requests = []  # (method, host, path, status): provenance without query strings
        self._cookies = {}

    def request(self, method, url, *, form=None, referer=None, allow_query=True):
        body = urlencode(form).encode('ascii') if form is not None else None
        for _hop in range(MAX_REDIRECTS + 1):
            parts, host = check_url(url, self.hosts, allow_query)
            try:
                addresses = self.resolver(host)
            except OSError:
                raise OpsError('calibration_fetch_failed', f'{host} could not be resolved') from None
            address = check_addresses(list(addresses), host)
            path = (parts.path or '/') + (f'?{parts.query}' if parts.query else '')
            headers = {'User-Agent': USER_AGENT, 'Accept': '*/*', 'Accept-Encoding': 'identity',
                       'Connection': 'close'}
            if referer:
                headers['Referer'] = referer
            if self._cookies.get(host):
                headers['Cookie'] = '; '.join(f'{k}={v}' for k, v in self._cookies[host].items())
            if body is not None:
                headers['Content-Type'] = 'application/x-www-form-urlencoded'
            try:
                status, raw_headers, data = self.transport(method, address, host, path, headers, body,
                                                           self.timeout, self.limit)
            except OpsError:
                raise
            except (TimeoutError, socket.timeout):
                raise OpsError('calibration_fetch_failed', f'{host} timed out (no automatic retry)') from None
            except (OSError, http.client.HTTPException, ValueError):
                raise OpsError('calibration_fetch_failed', f'network error talking to {host} (no automatic retry)') \
                    from None
            if len(data) > self.limit:
                raise OpsError('response_too_large', f'the response is larger than {self.limit} bytes; refused')
            resp = Response(status, list(raw_headers), data, url)
            self.requests.append({'method': method, 'host': host, 'path': parts.path or '/', 'status': status})
            for cookie in resp.cookies:
                pair = cookie.split(';', 1)[0]
                if '=' in pair and len(pair) <= 4096:
                    name, value = pair.split('=', 1)
                    self._cookies.setdefault(host, {})[name.strip()] = value.strip()
            if status in (301, 302, 303, 307, 308):
                location = resp.headers.get('location')
                if not location:
                    raise OpsError('calibration_fetch_failed', 'redirect without a location')
                url = urljoin(url, location.strip())
                if status in (301, 302, 303) and method == 'POST':
                    method, body = 'GET', None
                continue
            if status != 200:
                raise OpsError('calibration_fetch_failed', f'{host} answered HTTP {status}', status=status)
            return resp
        raise OpsError('url_refused', f'more than {MAX_REDIRECTS} redirects; refused')


def extract_zip(data, limit):
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except (zipfile.BadZipFile, ValueError):
        raise OpsError('calibration_invalid', 'the zip archive is damaged') from None
    infos = archive.infolist()
    if len(infos) > ZIP_MAX_MEMBERS:
        raise OpsError('calibration_invalid', f'the zip has {len(infos)} entries (at most {ZIP_MAX_MEMBERS})')
    out, total, skipped = [], 0, []
    for info in infos:
        name = info.filename
        parts = PurePosixPath(name.replace('\\', '/')).parts
        if name.startswith(('/', '\\')) or '..' in parts or ':' in name or '\x00' in name:
            raise OpsError('calibration_invalid', 'the zip contains an unsafe path; refused')
        if info.is_dir():
            continue
        if info.flag_bits & 0x1:
            raise OpsError('calibration_invalid', 'the zip is encrypted')
        if not name.lower().endswith(ZIP_MEMBER_EXT):
            skipped.append(os.path.basename(name)[:100])
            continue
        if info.file_size > limit or total + info.file_size > ZIP_MAX_TOTAL:
            raise OpsError('calibration_invalid', 'the zip expands beyond the size limit; refused')
        with archive.open(info) as handle:
            content = handle.read(limit + 1)
        total += len(content)
        if len(content) > limit or total > ZIP_MAX_TOTAL:
            raise OpsError('calibration_invalid', 'the zip expands beyond the size limit; refused')
        out.append((os.path.basename(name), content))
    if not out:
        raise OpsError('calibration_invalid', 'the zip contains no .txt/.cal/.frd calibration file')
    return out, skipped


# ---- HTML parsing ----------------------------------------------------------------------
class _PageParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.forms = []
        self._form = None
        self.links = []    # (kind, url)

    def handle_starttag(self, tag, attrs):
        a = {k.lower(): (v if v is not None else '') for k, v in attrs}
        if tag == 'form':
            self._form = {'action': a.get('action', ''), 'method': a.get('method', 'get').lower(), 'inputs': []}
            self.forms.append(self._form)
        elif tag in ('input', 'button') and self._form is not None:
            self._form['inputs'].append({'tag': tag, 'name': a.get('name', ''),
                                         'type': a.get('type', 'text' if tag == 'input' else 'submit').lower(),
                                         'value': a.get('value', ''), 'checked': 'checked' in a})
        elif tag == 'a' and a.get('href'):
            self.links.append(('anchor', a['href']))
        elif tag == 'iframe' and a.get('src'):
            self.links.append(('iframe', a['src']))
        elif tag == 'meta' and a.get('http-equiv', '').lower() == 'refresh':
            match = META_URL.search(a.get('content', ''))
            if match:
                self.links.append(('meta-refresh', match.group(1).strip()))

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag):
        if tag == 'form':
            self._form = None


def parse_page(text):
    parser = _PageParser()
    try:
        parser.feed(text)
        parser.close()
    except Exception:  # noqa: BLE001 - html.parser is lenient; a crash just means "unparseable"
        raise OpsError('calibration_unparseable', 'the page could not be parsed') from None
    parser.links.extend(('script', m.group(1)) for m in JS_LOCATION.finditer(text))
    return parser


def build_form(form, serial):
    """The fields exactly as served: serial filled in, hidden token as served, every other text box EMPTY."""
    digits = serial.split('-')
    fields, info = [], {'honeypot_fields': 0, 'token_present': False}
    names = {i['name'] for i in form['inputs']}
    if not {'serial1', 'serial2'} <= names:
        raise OpsError('provider_changed', 'the miniDSP calibration form no longer has serial1/serial2 fields')
    submitted = False
    for item in form['inputs']:
        name, kind = item['name'], item['type']
        if not name:
            continue
        if name == 'serial1':
            fields.append((name, digits[0]))
        elif name == 'serial2':
            fields.append((name, digits[1]))
        elif kind == 'hidden':
            fields.append((name, item['value']))
            if re.fullmatch(r'[0-9a-f]{32}', name):
                info['token_present'] = True
        elif kind in ('submit', 'image'):
            if not submitted:
                fields.append((name, item['value']))
                submitted = True
        elif kind in ('checkbox', 'radio'):
            if item['checked']:
                fields.append((name, item['value'] or 'on'))
        elif kind in ('button', 'reset', 'file'):
            continue
        else:  # any other visible-or-hidden text box is a honeypot: it must stay empty
            fields.append((name, ''))
            info['honeypot_fields'] += 1
    return fields, info


# ---- providers ---------------------------------------------------------------------------
MINIDSP_PAGES = {
    'UMIK-1': ('https://www.minidsp.com/products/acoustic-measurement/umik-1', 'umikcalfile'),
    'UMIK-2': ('https://www.minidsp.com/products/acoustic-measurement/umik-2', 'umik2calfile'),
}


class Provider:
    """A calibration source. ``fetch`` returns [{'name', 'data', 'url', 'via'}] or raises OpsError."""
    name = ''
    models = ()
    verified = ''
    requires_serial_evidence = False

    def hosts(self, pcfg):
        raise NotImplementedError

    def fetch(self, fetcher, model, serial, args):
        raise NotImplementedError


def _files_from(resp, fetcher, serial, limit, allow_links):
    """Direct file / zip / link-bearing HTML -> list of files. Raises not_found / unparseable."""
    ctype = resp.content_type
    looks_zip = resp.body[:4] == b'PK\x03\x04'
    filename = resp.filename or os.path.basename(urlsplit(resp.url).path) or 'calibration.txt'
    if looks_zip or ctype in ('application/zip', 'application/x-zip-compressed'):
        members, skipped = extract_zip(resp.body, limit)
        return [{'name': n, 'data': d, 'url': resp.url, 'via': 'zip', 'skipped': skipped} for n, d in members]
    if resp.is_attachment or ctype in ('text/plain', 'application/octet-stream', 'text/csv', 'application/download',
                                       'application/force-download', 'application/x-download'):
        return [{'name': filename, 'data': resp.body, 'url': resp.url, 'via': 'direct'}]
    if ctype not in ('text/html', 'application/xhtml+xml', ''):
        raise OpsError('calibration_unparseable', f'unexpected content type {ctype!r}')
    text = resp.body.decode('utf-8', errors='replace')
    missing = NOT_FOUND.search(text)
    if missing:
        message = re.sub(r'\s+', ' ', missing.group(0)).strip()
        raise OpsError('not_found', message)
    if not allow_links:
        raise OpsError('calibration_unparseable', 'the reply is an HTML page, not a calibration file')
    page = parse_page(text)
    digits = serial.replace('-', '') if serial else None
    picked, offsite = [], 0
    for kind, link in page.links:
        target = urljoin(resp.url, link.strip())
        parts = urlsplit(target)
        if not parts.path.lower().endswith(FILE_EXT):
            continue
        if parts.scheme != 'https' or (parts.hostname or '').lower() not in fetcher.hosts:
            offsite += 1
            continue
        path_digits = re.sub(r'[^0-9]', '', parts.path)
        has_serial = bool(digits and digits in path_digits)
        # Anchors on a product page may point at unrelated downloads: only follow those naming the serial.
        # Redirect-style links (meta refresh, iframe, script location) are the "download starts" mechanism.
        if kind == 'anchor' and not has_serial:
            continue
        if target not in picked:
            picked.append(target)
    if not picked:
        raise OpsError('calibration_unparseable', 'the reply did not contain a recognisable calibration download '
                       '(no direct file, zip, or serial-named link on an allowed host)', ignored_offsite_links=offsite)
    files = []
    for target in picked[:MAX_DOWNLOADS]:
        sub = fetcher.request('GET', target, referer=resp.url)
        files.extend(_files_from(sub, fetcher, serial, limit, allow_links=False))
    return files


class MiniDSPProvider(Provider):
    name = 'minidsp'
    models = tuple(MINIDSP_PAGES)
    verified = ('form fields and the "not found" reply observed 2026-11-23; success reply unverified against a '
                'real serial')
    requires_serial_evidence = True

    def hosts(self, pcfg):
        return ('www.minidsp.com',)

    def fetch(self, fetcher, model, serial, args):
        if model not in MINIDSP_PAGES:
            raise OpsError('invalid_argument', f'the minidsp provider supports {list(MINIDSP_PAGES)}')
        page_url, anchor = MINIDSP_PAGES[model]
        page = fetcher.request('GET', page_url)
        if page.content_type not in ('text/html', 'application/xhtml+xml'):
            raise OpsError('provider_changed', 'the miniDSP product page is not HTML')
        parsed = parse_page(page.body.decode('utf-8', errors='replace'))
        forms = [f for f in parsed.forms if f['action'].endswith('#' + anchor)] or [
            f for f in parsed.forms if {'serial1', 'serial2'} <= {i['name'] for i in f['inputs']}]
        if len(forms) != 1:
            raise OpsError('provider_changed', 'the miniDSP calibration form was not found on the product page')
        form = forms[0]
        if form['method'] != 'post':
            raise OpsError('provider_changed', 'the miniDSP calibration form is no longer a POST form')
        fields, info = build_form(form, serial)
        action = form['action'].split('#', 1)[0]
        target = urljoin(page.url, action) if action else page.url.split('#', 1)[0]
        resp = fetcher.request('POST', target, form=fields, referer=page.url)
        files = _files_from(resp, fetcher, serial, fetcher.limit, allow_links=True)
        for item in files:
            item['form'] = info
        return files


class UrlProvider(Provider):
    name = 'url'
    models = ()
    verified = 'operator-enabled direct download; hosts are the operator\'s allowlist'

    def hosts(self, pcfg):
        return tuple(pcfg['allowed_hosts'])

    def fetch(self, fetcher, model, serial, args):
        url = args.get('url')
        if not url:
            raise OpsError('invalid_argument', 'the url provider needs url (an https download link on an allowed host)')
        resp = fetcher.request('GET', url, allow_query=False)
        return _files_from(resp, fetcher, serial, fetcher.limit, allow_links=False)


PROVIDERS = {p.name: p for p in (MiniDSPProvider(), UrlProvider())}


# ---- orientation / records ---------------------------------------------------------------
def _marker(name, headers, serial):
    text = (name + ' ' + ' '.join(headers)).lower()
    if serial:
        text = text.replace(serial, ' ').replace(serial.replace('-', ''), ' ')
    text = SERNO.sub(' ', text)
    if re.search(r'(?<![0-9.])90(?![0-9])', text):
        return '90'
    if re.search(r'(?<![0-9.])0(?:\s*deg|\s*°|_deg)|_0(?![0-9])|(?<![0-9.])0deg', text):
        return '0'
    return None


def _assign(entries, serial):
    """Pick 0 / 90 per file from name and header markers. Returns entries with 'orientation' and 'basis'."""
    for entry in entries:
        entry['orientation'] = _marker(entry['name'], entry['summary']['headers'], serial)
        entry['basis'] = 'file name / header marker' if entry['orientation'] else None
    if len(entries) == 2:
        marks = [e['orientation'] for e in entries]
        if sorted(marks, key=str) == sorted(['90', None], key=str):
            for entry in entries:
                if entry['orientation'] is None:
                    entry['orientation'], entry['basis'] = '0', 'the other file is marked 90-degree'
    for entry in entries:
        if entry['orientation'] is None:
            entry['orientation'], entry['basis'] = 'unknown', 'no 0/90 marker in the file name or header'
    return entries


def _record(entry, *, model, serial, provider, source, fetched_at, verification=None):
    parsed = dict(entry['parsed'], orientation=entry['orientation'])
    parsed.update({'name': entry['name'][:100], 'model': model, 'serial': serial, 'provider': provider,
                   'source': source, 'file_sha256': hashlib.sha256(entry['raw']).hexdigest(),
                   'fetched_at': fetched_at, 'parser': entry['summary'], 'orientation_basis': entry['basis']})
    if verification:
        parsed['verification'] = verification
    return parsed


def _plan_activation(entries, wanted):
    """Activate an orientation only when exactly one stored file has it."""
    targets, ambiguous = {}, []
    for orient in ORIENTATIONS:
        matches = [i for i, e in enumerate(entries) if e['orientation'] == orient]
        if len(matches) == 1 and orient in wanted:
            targets[matches[0]] = orient
        elif len(matches) > 1:
            ambiguous.append(orient)
    return targets, ambiguous


def _summary(stored, activated):
    return {'calibration_id': stored['id'], 'name': stored['name'], 'orientation': stored['orientation'],
            'orientation_basis': stored.get('orientation_basis'), 'model': stored.get('model'),
            'serial': stored.get('serial'), 'provider': stored.get('provider'), 'source': stored.get('source'),
            'file_sha256': stored.get('file_sha256'), 'active': stored['id'] in activated.values(),
            **{k: stored['parser'][k] for k in ('points', 'freq_range_hz', 'correction_range_db',
                                                'sensitivity_factor_db', 'header_serial')}}


def _ingest(ctx, files, *, model, serial, provider, wanted, activate, require_serial_evidence, source_kind,
            verification=None):
    entries, seen = [], set()
    for item in files:
        raw = item['data'] if isinstance(item['data'], bytes) else item['data'].encode('utf-8')
        digest = hashlib.sha256(raw).hexdigest()
        if digest in seen:
            continue
        seen.add(digest)
        parsed, summary = validate(raw, item['name'], orientation='unknown', serial=serial, strict=True)
        if require_serial_evidence:
            digits = serial.replace('-', '')
            in_name = digits in re.sub(r'[^0-9]', '', item['name'] + ' ' + urlsplit(item.get('url') or '').path)
            if summary['header_serial'] != serial and not in_name:
                raise OpsError('serial_unverified', 'a downloaded file could not be tied to the requested serial '
                               '(no matching SERNO header or serial in its name); nothing stored')
        entries.append({'name': item['name'], 'raw': raw, 'parsed': parsed, 'summary': summary,
                        'url': item.get('url'), 'via': item.get('via')})
    _assign(entries, serial)
    explicit = wanted if len(wanted) == 1 else None
    if explicit and len(entries) == 1 and entries[0]['orientation'] == 'unknown' and source_kind == 'import':
        entries[0]['orientation'], entries[0]['basis'] = explicit[0], 'given by the caller'
    keep = [e for e in entries if e['orientation'] in wanted or e['orientation'] == 'unknown']
    if not keep:
        raise OpsError('orientation_not_available', f'no {"/".join(wanted)}-degree file was returned',
                       returned=[e['orientation'] for e in entries])
    targets, ambiguous = _plan_activation(keep, wanted)
    now = round(ctx.clock(), 3)
    items = []
    for i, entry in enumerate(keep):
        source = (source_of(entry['url']) | {'via': entry['via']}) if entry.get('url') else {'kind': source_kind}
        items.append((_record(entry, model=model, serial=serial, provider=provider, source=source, fetched_at=now,
                              verification=verification), targets.get(i)))
    saved, activated = store(ctx, items, activate=activate)
    out = {'stored': [_summary(s, activated) for s, _t in saved], 'activated': activated}
    if ambiguous:
        out['ambiguous_orientations'] = ambiguous
    unknown = [s['id'] for s, _t in saved if s['orientation'] == 'unknown']
    if unknown:
        out['needs_orientation'] = ('Stored but NOT activated (orientation unknown): ask the user which file is the '
                                    '0-degree and which the 90-degree one, then call measure_import_calibration '
                                    'with calibration_id and orientation.')
    return out


# ---- tools -------------------------------------------------------------------------------
def fetch(ctx, args):
    cfg = _cfg(ctx)
    model = _model(args['model']) or args['model'].strip()
    name = args.get('provider') or ('minidsp' if model in MINIDSP_PAGES else 'url')
    provider = PROVIDERS.get(name)
    if provider is None:
        raise OpsError('invalid_argument', f'unknown provider {name}; known: {sorted(PROVIDERS)}')
    pcfg = cfg['providers'][name]
    manual = manual_steps(model)
    if not pcfg['enabled']:
        raise OpsError('provider_disabled', f'the {name} calibration provider is disabled by the operator '
                       f'(measurement.calibration.providers.{name}.enabled); use the manual steps',
                       manual_steps=manual)
    serial = normalize_serial(args.get('serial'), required=name == 'minidsp')
    orient = args.get('orientation', 'both')
    wanted = list(ORIENTATIONS) if orient == 'both' else [orient]
    _rate_limit(ctx, name)
    fetcher = SafeFetcher(provider.hosts(pcfg), timeout=cfg['timeout_seconds'], limit=cfg['max_download_bytes'],
                          transport=getattr(ctx, 'calibration_transport', None),
                          resolver=getattr(ctx, 'calibration_resolver', None))
    verification = UNVERIFIED_NOTE if name == 'minidsp' else None
    try:
        files = provider.fetch(fetcher, model, serial, args)
        result = _ingest(ctx, files, model=model, serial=serial, provider=name, wanted=wanted,
                         activate=args.get('activate', True), source_kind='download',
                         require_serial_evidence=provider.requires_serial_evidence, verification=verification)
    except OpsError as error:
        error.details.setdefault('manual_steps', manual)
        error.details.setdefault('requests', fetcher.requests)
        if verification:
            error.details.setdefault('verification', verification)
        raise
    result.update({'provider': name, 'model': model, 'serial': serial, 'requests': fetcher.requests,
                   'mic_orientation_advice': measure.orientation_advice('90' if '90' in result['activated'] else (
                       '0' if '0' in result['activated'] else 'unknown'))})
    if verification:
        result['verification'] = verification
    return result


def _open_in_import_dir(cfg, relpath):
    """Open ``relpath`` inside the operator's import directory: realpath containment, no symlink hops, size cap."""
    base = cfg['import_dir']
    if not base:
        raise OpsError('import_dir_not_configured', 'path imports need measurement.calibration.import_dir in the '
                       'ops server configuration; paste the file contents as text instead')
    base_real = os.path.realpath(base)
    candidate = relpath if os.path.isabs(relpath) else os.path.join(base_real, relpath)
    real = os.path.realpath(candidate)
    if real == base_real or os.path.commonpath([real, base_real]) != base_real:
        raise OpsError('path_refused', 'the path is outside the configured import directory')
    rel = os.path.relpath(real, base_real).split(os.sep)
    fd = os.open(base_real, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        for part in rel[:-1]:
            nxt = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=fd)
            os.close(fd)
            fd = nxt
        try:
            handle = os.open(rel[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=fd)
        except FileNotFoundError:
            raise OpsError('not_found', 'no such file in the import directory') from None
    except OSError as error:
        if isinstance(error, FileNotFoundError):
            raise OpsError('not_found', 'no such file in the import directory') from None
        raise OpsError('path_refused', 'the file could not be opened safely (symlink or not a regular file)') \
            from None
    finally:
        os.close(fd)
    try:
        info = os.fstat(handle)
        if not stat.S_ISREG(info.st_mode):
            raise OpsError('path_refused', 'not a regular file')
        if info.st_size > cfg['max_import_bytes']:
            raise OpsError('file_too_large', f'the file is larger than {cfg["max_import_bytes"]} bytes')
        with os.fdopen(handle, 'rb', closefd=False) as stream:
            data = stream.read(cfg['max_import_bytes'] + 1)
        if len(data) > cfg['max_import_bytes']:
            raise OpsError('file_too_large', f'the file is larger than {cfg["max_import_bytes"]} bytes')
        return os.path.basename(real), data
    finally:
        os.close(handle)


def import_calibration(ctx, args):
    """measure_import_calibration: contents (text), a file in the import directory (path), or re-activation."""
    sources = [k for k in ('text', 'path', 'calibration_id') if args.get(k)]
    if len(sources) != 1:
        raise OpsError('invalid_argument', 'give exactly one of text, path or calibration_id')
    serial = normalize_serial(args.get('serial'), required=False)
    model = (_model(args['model']) or args['model'].strip()) if args.get('model') else None
    activate = args.get('activate', True)
    if 'calibration_id' in sources:
        orient = args.get('orientation')
        if orient not in ORIENTATIONS:
            raise OpsError('invalid_argument', 'orientation (0 or 90) is required to activate a stored calibration')
        record = measure.get_calibration(ctx, args['calibration_id'])
        if record['orientation'] not in (orient, 'unknown'):
            raise OpsError('orientation_mismatch', f'that calibration is the {record["orientation"]}-degree file')
        if record['orientation'] == orient:
            with _locked(ctx) as directory:
                active = _read_active(directory)
                active[orient] = {'calibration_id': record['id'], 'activated_at': round(ctx.clock(), 3)}
                atomic_write_json(directory / ACTIVE_FILE, active)
            stored, activated = record, {orient: record['id']}
        else:
            copy = {k: v for k, v in record.items() if k not in ('id', 'saved_at')}
            copy.update({'orientation': orient, 'orientation_basis': 'confirmed by the user',
                         'derived_from': record['id']})
            [(stored, _t)], activated = store(ctx, [(copy, orient)], activate=True)
        return {'stored': [_summary_any(stored, activated)], 'activated': activated,
                'mic_orientation_advice': measure.orientation_advice(orient)}
    if 'text' in sources:
        name = args.get('name') or 'imported calibration'
        parsed, summary = validate(args['text'], name, orientation='unknown', serial=serial, strict=False)
        raw = args['text'].encode('utf-8')
        given = args.get('orientation')
        marked = _marker(name, summary['headers'], serial or summary['header_serial'])
        orientation = given or marked or 'unknown'
        entry = {'name': name, 'raw': raw, 'parsed': parsed, 'summary': summary, 'orientation': orientation,
                 'basis': ('given by the caller' if given else 'file name / header marker' if marked
                           else 'no 0/90 marker in the name or header')}
        target = entry['orientation'] if entry['orientation'] in ORIENTATIONS else None
        record = _record(entry, model=model, serial=serial or summary['header_serial'], provider='import',
                         source={'kind': 'contents'}, fetched_at=round(ctx.clock(), 3))
        [(stored, _t)], activated = store(ctx, [(record, target)], activate=activate)
        out = _legacy(stored, activated)
        if target is None:
            out['needs_orientation'] = ('Stored but NOT activated: orientation unknown. Call again with '
                                        'calibration_id and orientation (0 or 90) to activate it.')
        return out
    cfg = _cfg(ctx)
    filename, data = _open_in_import_dir(cfg, args['path'])
    if data[:4] == b'PK\x03\x04':
        members, _skipped = extract_zip(data, cfg['max_import_bytes'])
        files = [{'name': n, 'data': d} for n, d in members]
    else:
        files = [{'name': args.get('name') or filename, 'data': data}]
    wanted = [args['orientation']] if args.get('orientation') else list(ORIENTATIONS)
    result = _ingest(ctx, files, model=model, serial=serial, provider='import', wanted=wanted, activate=activate,
                     require_serial_evidence=False, source_kind='import')
    for item in result['stored']:
        item['source'] = {'kind': 'import_dir', 'file': filename}
    return result


def _summary_any(stored, activated):
    if isinstance(stored.get('parser'), dict):
        return _summary(stored, activated)
    pts = stored['points']
    return {'calibration_id': stored['id'], 'name': stored['name'], 'orientation': stored['orientation'],
            'active': stored['id'] in activated.values(), 'points': len(pts),
            'freq_range_hz': [pts[0][0], pts[-1][0]]}


def _legacy(stored, activated):
    """The original measure_import_calibration result shape, plus activation and provenance fields."""
    pts = stored['points']
    return {'calibration_id': stored['id'], 'name': stored['name'], 'orientation': stored['orientation'],
            'sensitivity_factor_db': stored['sensitivity_factor_db'], 'points': len(pts),
            'freq_range_hz': [pts[0][0], pts[-1][0]],
            'correction_range_db': [round(min(p[1] for p in pts), 2), round(max(p[1] for p in pts), 2)],
            'serial': stored.get('serial'), 'active': stored['id'] in activated.values(), 'activated': activated,
            'mic_orientation_advice': measure.orientation_advice(stored['orientation']),
            'note': 'An active calibration is applied automatically to measurements made with the mic in that '
                    'orientation (the session/plan orientation; 90 degrees by default). Sensitivity is recorded for '
                    'reference; responses stay relative (dB re digital transfer), which is what EQ needs.'}


def describe_record(record):
    return {'calibration_id': record['id'], 'name': record.get('name'), 'orientation': record.get('orientation'),
            'model': record.get('model'), 'serial': record.get('serial'), 'provider': record.get('provider'),
            'source': record.get('source'), 'fetched_at': record.get('fetched_at'),
            'file_sha256': record.get('file_sha256'), 'sha256': record.get('sha256'),
            'sensitivity_factor_db': record.get('sensitivity_factor_db'), 'points': len(record.get('points') or []),
            'parser': record.get('parser'), 'verification': record.get('verification')}


def status(ctx, _args):
    from . import measure_session  # late import: measure_session imports measure
    cfg = _cfg(ctx)
    active = {}
    for orient in ORIENTATIONS:
        ident = active_id(ctx, orient)
        active[orient] = describe_record(measure.get_calibration(ctx, ident)) if ident else None
    session = measure_session.active_calibration_view(ctx)
    if session is not None:
        orient = session['orientation']
        expected = active_id(ctx, orient)
        session['active_calibration_for_orientation'] = expected
        session['matching'] = bool(session['calibration_id']) and session['calibration_id'] == expected
        if not session['matching']:
            session['advice'] = (f'the session uses calibration {session["calibration_id"]}, not the active '
                                 f'{orient}-degree calibration {expected}; start a new session (replace=true) to use '
                                 'the active one' if session['calibration_id'] else
                                 f'the session has no calibration; with an active {orient}-degree calibration start a '
                                 'new session (replace=true) to apply it')
    default = cfg['default_orientation']
    ready = active[default] is not None
    providers = [{'name': p.name, 'enabled': cfg['providers'][p.name]['enabled'], 'models': list(p.models),
                  'hosts': list(p.hosts(cfg['providers'][p.name])), 'status': p.verified} for p in PROVIDERS.values()]
    providers.append({'name': 'import', 'enabled': True, 'models': ['any'],
                      'status': 'contents always; path only inside measurement.calibration.import_dir '
                                f'({"configured" if cfg["import_dir"] else "not configured"})'})
    out = {'active': active, 'default_orientation': default, 'ready_for_default_orientation': ready,
           'session': session, 'providers': providers, 'fetch_needs': 'allow_write (not allow_measure)',
           'allow_write': ctx.config.allow_write}
    if not ready:
        out['next_step'] = (f'No active {default}-degree calibration. Run measure_mic_detect; if it needs the serial, '
                            'ask the user for the number printed on the mic (XXX-XXXX); then measure_calibration_fetch '
                            '(needs allow_write). If fetching is disabled or fails, follow manual_steps.')
        out['manual_steps'] = manual_steps(None)
        out['vendor_pages'] = {model: page for model, (page, _anchor) in MINIDSP_PAGES.items()}
    return out


# ---- microphone detection ----------------------------------------------------------------
UMIK = re.compile(r'(?i)umik[-_\s]?([12])(?![0-9])')
_ASK = ('Ask the user for the serial number printed on the microphone body or its box (7 digits, written '
        'XXX-XXXX) and which model it is (e.g. UMIK-1 or UMIK-2).')


def _read_small(path, limit=256):
    try:
        with open(path, 'rb') as handle:
            return handle.read(limit).decode('utf-8', errors='replace').strip()
    except OSError:
        return None


def _model(text):
    match = UMIK.search(text or '')
    return f'UMIK-{match.group(1)}' if match else None


def detect(ctx, args, root=None):
    """Read-only: /sys USB attributes, /proc/asound cards and the configured listing commands."""
    root = Path(root or getattr(ctx, 'detect_root', None) or '/')
    mics = {}
    usb = root / 'sys/bus/usb/devices'
    try:
        devices = sorted(usb.iterdir())[:128]
    except OSError:
        devices = []
    for dev in devices:
        vendor, product_id = _read_small(dev / 'idVendor', 16), _read_small(dev / 'idProduct', 16)
        if not vendor or not product_id:
            continue
        product, maker = _read_small(dev / 'product'), _read_small(dev / 'manufacturer')
        usb_id = f'{vendor}:{product_id}'.lower()
        entry = {'usb_product': (product or '')[:100] or None, 'manufacturer': (maker or '')[:100] or None,
                 'usb_id': usb_id, 'model': _model(product)}
        by_maker = 'minidsp' in (maker or '').lower()
        if entry['model'] or by_maker:
            serial = _read_small(dev / 'serial', 64)
            mic = mics.setdefault(f'usb{len(mics)}', dict(
                entry, sources=['sysfs usb product string' if entry['model'] else 'sysfs usb manufacturer string'],
                confidence='high' if entry['model'] else 'low', alsa_card=None, serial=None))
            if serial:
                match = SERIAL.match(serial)
                mic['serial'] = ({'value': f'{match.group(1)}-{match.group(2)}', 'source': 'usb serial string',
                                  'status': 'candidate, confirm with user'} if match else None)
                mic['usb_serial_present_but_not_7_digits'] = not match
    cards_text = _read_small(root / 'proc/asound/cards', 16384) or ''
    for line in cards_text.splitlines():
        match = re.match(r'^\s*(\d+)\s+\[([^\]]*)\]\s*:\s*(.*)$', line)
        if not match:
            continue
        number, card_id, desc = match.group(1), match.group(2).strip(), match.group(3).strip()
        usbid = (_read_small(root / f'proc/asound/card{number}/usbid', 32) or '').lower()
        stream_text = _read_small(root / f'proc/asound/card{number}/stream0', 4096) or ''
        model = _model(desc) or _model(card_id) or _model(stream_text.splitlines()[0] if stream_text else '')
        known = [m for m in mics.values() if usbid and m['usb_id'] == usbid and not m.get('alsa_card')]
        if known:
            known[0]['alsa_card'] = {'number': int(number), 'id': card_id[:40]}
            known[0]['sources'].append('/proc/asound card')
            if model and not known[0]['model']:
                known[0]['model'] = model
            continue
        # A USB audio card that can capture but is not recognised is reported as an unknown microphone candidate.
        capture_only = 'Capture:' in stream_text and 'Playback:' not in stream_text
        if not model and not ('USB-Audio' in desc and capture_only):
            continue
        mics[f'card{number}'] = {'model': model, 'usb_product': desc[:100], 'manufacturer': None,
                                 'usb_id': usbid or None, 'alsa_card': {'number': int(number), 'id': card_id[:40]},
                                 'sources': ['/proc/asound card'], 'confidence': 'medium' if model else 'low',
                                 'serial': None}
    listing = []
    if not mics and args.get('include_device_listing', True):
        with contextlib.suppress(OpsError):
            for item in measure.list_devices(ctx, {}).get('devices', []):
                text = ' '.join(item.get('lines') or []) if isinstance(item, dict) else str(item)
                model = _model(text)
                if model:
                    listing.append(model)
        for model in sorted(set(listing)):
            mics[f'list-{model}'] = {'model': model, 'usb_product': None, 'manufacturer': None, 'usb_id': None,
                                     'alsa_card': None, 'sources': ['measure_list_devices output'],
                                     'confidence': 'low', 'serial': None}
    found = list(mics.values())
    for mic in found:
        mic.setdefault('serial', None)
        mic['model'] = mic['model'] or 'unknown'
    candidates = [m for m in found if m['serial']]
    out = {'microphones': found}
    if not found:
        out.update(status='no_microphone_found', ask_user=_ASK,
                   note='No UMIK-style USB microphone is visible to this server (is it plugged in?).')
    elif candidates:
        out.update(status='serial_candidate', ask_user=(
            f'The USB serial string looks like {candidates[0]["serial"]["value"]}. Ask the user to confirm it matches '
            'the number printed on the microphone body or box before fetching; the USB string is not verified to be '
            'the calibration serial.'))
    else:
        out.update(status='needs_serial', ask_user=_ASK)
    out['next_step'] = ('With the confirmed model and serial call measure_calibration_fetch (needs allow_write); '
                        'otherwise follow the manual steps and measure_import_calibration.')
    return out
