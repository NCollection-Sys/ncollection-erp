#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""UAE e-invoice validator worker: runs the OFFICIAL PINT-AE Schematron, unmodified, out of process.

WHAT THIS PROCESS IS, AND IS NOT -------------------------------- It executes the official, preprocessed Schematron
stylesheets (XSLT 2.0) of the pinned PINT-AE release with SaxonC-HE (MPL-2.0) over a document it is handed, and reports
the failed assertions. It is NOT part of Odoo, holds NO database credentials, stores NOTHING (no document, no result,
no log of content) and reaches NOTHING on the network. The Odoo image deliberately has no XSLT 2.0 processor; this
satellite is where one lives, so it can be replaced or upgraded on its own.

IT DOES NOT TRUST ITS INPUT --------------------------- A document is attacker-influenced text (names, notes). Before
Saxon sees it the worker rejects: anything over the size limit, anything that is not UTF-8 text, and ANY document type
declaration or entity declaration (a UBL invoice needs neither, and refusing them closes XXE and entity-expansion). The
stylesheets are trusted, checksum-verified data read from a read-only mount at start-up: a single changed byte in the
pinned rule set stops the worker.

IT PROVES WHICH RULES IT RAN, AND FOR WHOM ---------------------------------------- Every answer carries the release it
ran and ``manifest_sha256`` (the fingerprint of the pinned MANIFEST.txt), the sha256 of the document it was given, and
an HMAC-SHA256 signature over all of it keyed by the shared token. The engine accepts an answer only if the signature
verifies, the document is the one it sent and both fingerprints equal what it is pinned to.

IT ONLY SERVES A CALLER THAT HOLDS THE TOKEN ---------------------------------------------- ``POST /validate`` needs
``Authorization: Bearer <derived from NC_EINVOICE_VALIDATOR_TOKEN>`` (the bearer is a one-way derivative, so a captured
request does not reveal the signing key; compared in constant time); without it the answer is 401 and
nothing is read. The worker refuses to START without a token of at least 32 characters. The token is read from the
environment, never logged and never echoed. ``GET /health`` is unauthenticated and says only that the process is up,
which release it holds and the rule-set fingerprint (no document, no secret).

IT FAILS CLOSED ---------------- Anything that is not a complete Schematron report (an empty result, a result that is
not SVRL, a report in which no rule fired, a document whose root is not the Invoice or CreditNote it was declared to
be) is an ERROR, never "no failures".

RUN
    NC_EINVOICE_RULES_DIR=/rules NC_EINVOICE_SPEC_VERSION=<release>
    NC_EINVOICE_VALIDATOR_TOKEN=<32+ chars> python3 worker.py   (the three variables, one process)
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import os
import re
import sys
import threading
import xml.etree.ElementTree as ET
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MAX_BYTES = 2 * 1024 * 1024
TOKEN_MIN_LENGTH = 32
MAX_CONCURRENT = 8  # connections being served at once; more are answered 503 immediately
LOCK_WAIT_SECONDS = (
    30  # a request that cannot get its turn in this time is answered 503 (the engine then fails closed)
)
SOCKET_TIMEOUT = 15  # a client that stalls mid-request is dropped
_ROOTS = {
    'invoice': '{urn:oasis:names:specification:ubl:schema:xsd:Invoice-2}Invoice',
    'credit_note': '{urn:oasis:names:specification:ubl:schema:xsd:CreditNote-2}CreditNote',
}
STYLESHEETS = ('PINT-UBL-validation-preprocessed', 'PINT-jurisdiction-aligned-rules')
_SVRL = '{http://purl.oclc.org/dsdl/svrl}'
_NONCE = re.compile(r'[0-9a-f]{16,64}')
_NON_UTF8_DECLARATION = re.compile(rb'^\s*<\?xml[^>]*encoding\s*=\s*["\'](?!utf-8["\'])', re.IGNORECASE)
_FORBIDDEN = re.compile(rb'<!\s*(DOCTYPE|ENTITY)', re.IGNORECASE)


class BadRequest(Exception):
    pass


class Busy(Exception):
    pass


def verify_rules(rules_dir):
    """Check every file listed in MANIFEST.txt and return the manifest's sha256. Raises if anything differs."""
    manifest = os.path.join(rules_dir, 'MANIFEST.txt')
    with open(manifest, 'rb') as handle:
        manifest_bytes = handle.read()
    listed = {}
    for line in manifest_bytes.decode('utf-8').splitlines():
        if line and not line.startswith('#'):
            digest, _sep, relpath = line.partition('  ')
            listed[relpath] = digest
    for relpath, digest in listed.items():
        with open(os.path.join(rules_dir, *relpath.split('/')), 'rb') as handle:
            if hashlib.sha256(handle.read()).hexdigest() != digest:
                raise RuntimeError('rule file changed: %s' % relpath)
    return hashlib.sha256(manifest_bytes).hexdigest()


def auth_secret(token):
    """What the caller sends as its bearer: a one-way derivative of the shared token, so that seeing a request (a proxy
    log, a sniffer) does not reveal the key the answers are signed with."""
    return hmac.new(token.encode('utf-8'), b'nc-einvoice/auth', hashlib.sha256).hexdigest()


def sign_answer(token, xml_sha256, spec_version, manifest_sha256, engine, failures, nonce):
    """HMAC-SHA256 over the answer, keyed by a SECOND derivative of the token (never the bearer). Byte-for-byte the
    function the engine's client uses to verify it. The caller's nonce is covered, so a recorded answer cannot be
    replayed for a later request."""
    key = hmac.new(token.encode('utf-8'), b'nc-einvoice/sign', hashlib.sha256).digest()
    message = json.dumps(
        {
            'nonce': nonce,
            'xml_sha256': xml_sha256,
            'spec_version': spec_version,
            'manifest_sha256': manifest_sha256,
            'engine': engine,
            'failures': failures,
        },
        sort_keys=True,
        separators=(',', ':'),
        ensure_ascii=True,
    )
    return hmac.new(key, message.encode('ascii'), hashlib.sha256).hexdigest()


def parse_svrl(svrl_text):
    """Failed assertions from a Schematron Validation Report Language document. A report that is empty, is not SVRL or
    in which no rule fired proves nothing, so it is an error and never "no failures"."""
    if not svrl_text or not svrl_text.strip():
        raise RuntimeError('empty schematron report')
    failures = []
    root = ET.fromstring(svrl_text)  # produced by our own trusted stylesheets, never by the caller
    if root.tag != _SVRL + 'schematron-output':
        raise RuntimeError('not a schematron report')
    if next(root.iter(_SVRL + 'fired-rule'), None) is None:
        raise RuntimeError('no rule fired')
    for failed in root.iter(_SVRL + 'failed-assert'):
        text = failed.find(_SVRL + 'text')
        failures.append(
            {
                'id': failed.get('id') or '',
                'flag': failed.get('flag') or 'fatal',
                'location': failed.get('location') or '',
                'text': ' '.join((text.text if text is not None and text.text else '').split()),
            }
        )
    return failures


def decode_request(body):
    """``(kind, spec_version, xml_bytes, nonce)`` from a request body, or :class:`BadRequest`."""
    if len(body) > MAX_BYTES * 2:
        raise BadRequest('request too large')
    try:
        data = json.loads(body)
        kind, spec_version, xml_b64 = data['kind'], data['spec_version'], data['xml_b64']
        nonce = data['nonce']
        xml = base64.b64decode(xml_b64, validate=True)
    except (ValueError, KeyError, TypeError, binascii.Error):
        raise BadRequest('malformed request') from None
    if not isinstance(nonce, str) or not _NONCE.fullmatch(nonce):
        raise BadRequest('malformed request')
    if kind not in ('invoice', 'credit_note'):
        raise BadRequest('unknown kind')
    if len(xml) > MAX_BYTES:
        raise BadRequest('document too large')
    if b'\x00' in xml or _NON_UTF8_DECLARATION.search(xml):
        raise BadRequest('document is not UTF-8')  # a UTF-16 document would slip past the byte scan below
    if _FORBIDDEN.search(xml):
        raise BadRequest('document type / entity declarations are not accepted')
    try:
        xml.decode('utf-8')
    except UnicodeDecodeError:
        raise BadRequest('document is not UTF-8') from None
    try:
        root_tag = ET.fromstring(xml).tag
    except ET.ParseError:
        raise BadRequest('document is not well-formed XML') from None
    if root_tag != _ROOTS[kind]:
        raise BadRequest('the document is not the %s it was declared to be' % kind)
    return kind, str(spec_version), xml, nonce


class Validator:
    """Holds the compiled official stylesheets. ``transformer`` is injected so the logic is testable without SaxonC."""

    def __init__(self, rules_dir, spec_version, transformer):
        self.spec_version = spec_version
        self.manifest_sha256 = verify_rules(rules_dir)
        self.engine = transformer.engine
        self._transformer = transformer
        self._lock = threading.Lock()  # one Saxon transformation at a time: simple and safe
        self._sheets = [
            transformer.compile(os.path.join(rules_dir, 'schematron', name + '.xslt')) for name in STYLESHEETS
        ]

    def validate(self, kind, spec_version, xml):
        if spec_version != self.spec_version:
            raise BadRequest('this worker runs release %s' % self.spec_version)
        failures = []
        if not self._lock.acquire(timeout=LOCK_WAIT_SECONDS):
            raise Busy('the validator is busy')
        try:
            for sheet in self._sheets:
                failures.extend(parse_svrl(self._transformer.run(sheet, xml)))
        finally:
            self._lock.release()
        return {
            'engine': self.engine,
            'spec_version': self.spec_version,
            'manifest_sha256': self.manifest_sha256,
            'failures': failures,
        }


class SaxonTransformer:
    """SaxonC-HE binding. Imported lazily so the rest of this file (and its tests) need no native library."""

    def __init__(self):
        from saxonche import PySaxonProcessor

        self._processor = PySaxonProcessor(license=False)
        self._xslt = self._processor.new_xslt30_processor()
        self.engine = str(self._processor.version).strip()

    def compile(self, path):
        return self._xslt.compile_stylesheet(stylesheet_file=path)

    def run(self, sheet, xml):
        node = self._processor.parse_xml(xml_text=xml.decode('utf-8'))
        return sheet.transform_to_string(xdm_node=node)


def require_token(token):
    if not token or len(token) < TOKEN_MIN_LENGTH:
        raise ValueError('NC_EINVOICE_VALIDATOR_TOKEN must be at least %d characters' % TOKEN_MIN_LENGTH)


def make_handler(validator, token):
    require_token(token)
    slots = threading.BoundedSemaphore(MAX_CONCURRENT)
    expected_bearer = auth_secret(token).encode('utf-8')

    class Handler(BaseHTTPRequestHandler):
        server_version = 'nc-einvoice-validator'
        sys_version = ''  # do not announce the Python version
        timeout = SOCKET_TIMEOUT

        def log_message(self, fmt, *args):  # metadata only: never a body
            sys.stderr.write('%s %s\n' % (self.command, self.path))

        def _send(self, status, payload):
            data = json.dumps(payload).encode('utf-8')
            self.send_response(status)
            self.send_header('content-type', 'application/json')
            self.send_header('content-length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if self.path == '/health':
                self._send(
                    200,
                    {
                        'ok': True,
                        'spec_version': validator.spec_version,
                        'manifest_sha256': validator.manifest_sha256,
                        'engine': validator.engine,
                    },
                )
            else:
                self._send(404, {'error': 'not found'})

        def _authorised(self):
            given = (self.headers.get('authorization') or '').strip()
            return given.startswith('Bearer ') and hmac.compare_digest(given[7:].encode('utf-8'), expected_bearer)

        def do_POST(self):
            if self.path != '/validate':
                return self._send(404, {'error': 'not found'})
            if not self._authorised():
                return self._send(401, {'error': 'unauthorised'})
            if not slots.acquire(blocking=False):
                return self._send(503, {'error': 'busy'})
            try:
                try:
                    length = int(self.headers.get('content-length') or '')
                except ValueError:
                    return self._send(411, {'error': 'content-length is required'})
                if length < 0 or length > MAX_BYTES * 2:
                    return self._send(413, {'error': 'request too large'})
                kind, version, xml, nonce = decode_request(self.rfile.read(length))
                result = validator.validate(kind, version, xml)
                digest = hashlib.sha256(xml).hexdigest()
                result['xml_sha256'] = digest
                result['nonce'] = nonce
                result['signature'] = sign_answer(
                    token,
                    digest,
                    result['spec_version'],
                    result['manifest_sha256'],
                    result['engine'],
                    result['failures'],
                    nonce,
                )
                self._send(200, result)
            except BadRequest as exc:
                self._send(400, {'error': str(exc)})
            except Busy:
                self._send(503, {'error': 'busy'})
            except Exception:  # noqa: BLE001 - never leak a trace or the document
                self._send(500, {'error': 'validation failed'})
            finally:
                slots.release()

    return Handler


def main():
    rules_dir = os.environ.get('NC_EINVOICE_RULES_DIR', '/rules')
    spec_version = os.environ['NC_EINVOICE_SPEC_VERSION']
    token = os.environ.get('NC_EINVOICE_VALIDATOR_TOKEN', '')
    require_token(token)  # refuse to start without a usable token, before anything else is loaded
    validator = Validator(rules_dir, spec_version, SaxonTransformer())
    port = int(os.environ.get('NC_EINVOICE_VALIDATOR_PORT', '8090'))
    ThreadingHTTPServer(('0.0.0.0', port), make_handler(validator, token)).serve_forever()


if __name__ == '__main__':
    main()
