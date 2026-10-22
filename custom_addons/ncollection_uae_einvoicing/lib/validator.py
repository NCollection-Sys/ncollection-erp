# -*- coding: utf-8 -*-
"""Boundary to the official-rule validator, which runs OUT OF PROCESS.

The official PINT-AE Schematron is XSLT 2.0 (``queryBinding="xslt2"``); the Odoo image has no XSLT 2.0 processor and
none is added to it. The rules are executed, unmodified, by an isolated worker (``satellites/einvoice_validator``:
SaxonC-HE, MPL-2.0, its own container, no database, no Odoo import). This module is only the CLIENT and the translation
of the worker's answer; it can be replaced or upgraded independently of both the engine and the worker.

Contract (JSON over HTTP, ``POST /validate``):

    request : {"kind": "invoice"|"credit_note", "spec_version": "<release>", "nonce": "<16 random bytes, hex>",
               "xml_b64": "<base64 of the exact bytes>"}
    response: {"engine": "SaxonC-HE 13.1", "spec_version": "<release>", "manifest_sha256": "<of the rules it ran>",
               "failures": [{"id": "ibr-190-ae", "flag": "fatal", "location": "...", "text": "..."}],
               "xml_sha256": "<of the document it was given>", "nonce": "<echoed>", "signature": "<HMAC-SHA256>"}

AUTHENTICATED BOTH WAYS. The worker only answers a caller that presents the bearer derived from the shared token
(``Authorization: Bearer``), and it signs every answer: an HMAC-SHA256 over the sha256 of the document it was given,
the release, the rule-set fingerprint, the engine, the failures and the caller's one-time nonce. The signing key is a
SECOND derivative of the token, so a captured request (which carries only the bearer) does not reveal it. The client
recomputes the signature, so a forged "no failures" from anything that does not hold the token (a process on the
network, a proxy) is rejected, and an answer cannot be replayed for a different document or a later request. The token
comes from the container environment, never the database, and is never logged.

FAIL CLOSED. Anything other than a well-formed, correctly signed answer about THIS document from a worker that ran
EXACTLY the pinned rules (same version, same manifest fingerprint) is "unavailable", never "valid": an unreachable
worker, a timeout, a non-200, malformed JSON, a bad signature, a different document, a different release or a different
rule set all stop the document from becoming ``validated``. The client also refuses to send more than ``MAX_BYTES``
and never follows redirects.
"""

import base64
import hashlib
import hmac
import http.client
import json
import secrets
import urllib.error
import urllib.request
from collections import namedtuple

from . import issues as _issues
from .spec import get_profile, manifest_sha256

MAX_BYTES = 2 * 1024 * 1024
MAX_RESPONSE_BYTES = 1024 * 1024
DEFAULT_TIMEOUT = 20
TOKEN_MIN_LENGTH = 32

Failure = namedtuple('Failure', 'rule flag location text')
Outcome = namedtuple('Outcome', 'ok engine spec_version failures manifest_sha256', defaults=('',))


class ValidatorUnavailable(Exception):
    """The official validation could not be performed (or could not be trusted). The document is NOT validated."""


def auth_secret(token):
    """The bearer the client sends: a one-way derivative of the shared token, never the token itself."""
    return hmac.new(token.encode('utf-8'), b'nc-einvoice/auth', hashlib.sha256).hexdigest()


def sign_answer(token, xml_sha256, spec_version, manifest_sha256, engine, failures, nonce):
    """The HMAC-SHA256 hex digest a worker puts on its answer, keyed by a SECOND derivative of the token. The SAME
    function lives in the worker (a separate program that imports nothing from here);
    ``satellites/einvoice_validator/e2e_check.py`` proves they agree."""
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


class ValidatorClient:
    def validate(self, xml_bytes, kind, profile=None):
        raise NotImplementedError


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class HttpValidatorClient(ValidatorClient):
    def __init__(self, base_url, timeout=DEFAULT_TIMEOUT, token=None):
        if not base_url or not base_url.startswith(('http://', 'https://')):
            raise ValidatorUnavailable('the validator worker URL is not configured')
        if not token or len(token) < TOKEN_MIN_LENGTH:
            raise ValidatorUnavailable('the validator token is not configured')
        self.base_url = base_url.rstrip('/')
        self.timeout = timeout
        self._token = token

    def validate(self, xml_bytes, kind, profile=None):
        profile = profile or get_profile()
        if kind not in ('invoice', 'credit_note'):
            raise ValueError(kind)
        if len(xml_bytes) > MAX_BYTES:
            raise ValidatorUnavailable('the document is larger than the validator accepts')
        nonce = secrets.token_hex(16)
        body = json.dumps(
            {
                'kind': kind,
                'spec_version': profile.pint_version,
                'xml_b64': base64.b64encode(xml_bytes).decode('ascii'),
                'nonce': nonce,
            }
        ).encode('utf-8')
        request = urllib.request.Request(
            self.base_url + '/validate',
            data=body,
            method='POST',
            headers={'content-type': 'application/json', 'authorization': 'Bearer ' + auth_secret(self._token)},
        )
        opener = urllib.request.build_opener(_NoRedirect)
        try:
            with opener.open(request, timeout=self.timeout) as response:
                if response.status != 200:
                    raise ValidatorUnavailable('the validator answered HTTP %s' % response.status)
                raw = response.read(MAX_RESPONSE_BYTES + 1)
        except ValidatorUnavailable:
            raise
        except (urllib.error.URLError, OSError, ValueError, http.client.HTTPException) as exc:
            raise ValidatorUnavailable('the validator could not be reached (%s)' % type(exc).__name__) from None
        if len(raw) > MAX_RESPONSE_BYTES:
            raise ValidatorUnavailable('the validator answer is too large')
        return parse_response(raw, self._token, hashlib.sha256(xml_bytes).hexdigest(), nonce, profile)


def parse_response(raw, token, xml_sha256, nonce, profile=None):
    """The worker's answer as an :class:`Outcome`, or :class:`ValidatorUnavailable` if it cannot be trusted: it must be
    signed with ``token``, be about the document whose sha256 is ``xml_sha256`` and carry the caller's ``nonce``.
    ANY malformed answer (not only the expected shapes) is "unavailable": the caller records and returns it."""
    try:
        return _parse_response(raw, token, xml_sha256, nonce, profile or get_profile())
    except ValidatorUnavailable:
        raise
    except (
        Exception
    ):  # noqa: BLE001 - e.g. a non-ASCII signature or absurdly nested JSON must not escape as a traceback
        raise ValidatorUnavailable('the validator answer is malformed') from None


def _parse_response(raw, token, xml_sha256, nonce, profile):
    try:
        data = json.loads(raw)
        raw_failures = data['failures']
        failures = [
            Failure(str(f['id']), str(f.get('flag', 'fatal')), str(f.get('location', '')), str(f.get('text', '')))
            for f in raw_failures
        ]
        engine, version, manifest = str(data['engine']), str(data['spec_version']), str(data['manifest_sha256'])
        echoed, signature, answered_nonce = str(data['xml_sha256']), str(data['signature']), str(data['nonce'])
    except (ValueError, KeyError, TypeError):
        raise ValidatorUnavailable('the validator answer is malformed') from None
    expected = sign_answer(token, echoed, version, manifest, engine, raw_failures, answered_nonce)
    if not hmac.compare_digest(signature.encode('utf-8'), expected.encode('ascii')):
        raise ValidatorUnavailable('the validator answer is not signed with the shared token')
    if answered_nonce != nonce:
        raise ValidatorUnavailable('the validator answered a different request')
    if echoed != xml_sha256:
        raise ValidatorUnavailable('the validator answered about a different document')
    if version != profile.pint_version:
        raise ValidatorUnavailable('the validator ran release %s, not the pinned %s' % (version, profile.pint_version))
    if manifest != manifest_sha256(profile):
        raise ValidatorUnavailable('the validator did not run the pinned official rule set')
    return Outcome(not [f for f in failures if f.flag != 'warning'], engine, version, failures, manifest)


def to_issues(outcome):
    """The worker's failures as engine issues; every one is an OFFICIAL rule by construction."""
    found = []
    for failure in outcome.failures:
        severity = _issues.WARNING if failure.flag == 'warning' else _issues.ERROR
        found.append(
            _issues.make(
                'OFFICIAL:' + failure.rule,
                failure.location or 'document',
                '%(text)s',
                {'text': failure.text},
                severity=severity,
            )
        )
    return found
