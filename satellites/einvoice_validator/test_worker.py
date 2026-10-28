#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Unit tests of the validator worker's logic. Pure stdlib: no SaxonC, no Odoo, no network beyond localhost.

    python satellites/einvoice_validator/test_worker.py

The real run against the official rules is e2e_check.py (needs SaxonC in the worker environment)."""

import base64
import hashlib
import hmac
import http.client
import json
import os
import secrets
import shutil
import sys
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import worker  # noqa: E402

TOKEN = secrets.token_hex(24)  # random per run: no secret literal in the source
INVOICE = b'<Invoice xmlns="urn:oasis:names:specification:ubl:schema:xsd:Invoice-2"><x/></Invoice>'
CREDIT = b'<CreditNote xmlns="urn:oasis:names:specification:ubl:schema:xsd:CreditNote-2"/>'

SVRL = (
    '<svrl:schematron-output xmlns:svrl="http://purl.oclc.org/dsdl/svrl">'
    '<svrl:fired-rule context="/"/>'
    '<svrl:failed-assert id="ibr-190-ae" flag="fatal" location="/Invoice[1]">'
    '<svrl:text>  rate   must be 5 </svrl:text>'
    '</svrl:failed-assert><svrl:failed-assert flag="warning" location="/x"><svrl:text>w</svrl:text>'
    '</svrl:failed-assert><svrl:successful-report id="r"/></svrl:schematron-output>'
)
CLEAN_SVRL = (
    '<svrl:schematron-output xmlns:svrl="http://purl.oclc.org/dsdl/svrl"><svrl:fired-rule context="/"/>'
    '</svrl:schematron-output>'
)


class FakeTransformer:
    engine = 'Fake 1.0'

    def __init__(self, svrl=''):
        self.svrl, self.compiled, self.ran = svrl, [], 0

    def compile(self, path):
        self.compiled.append(os.path.basename(path))
        return path

    def run(self, sheet, xml):
        self.ran += 1
        return self.svrl


def make_rules(tmp):
    rules = os.path.join(tmp, 'rules')
    os.makedirs(os.path.join(rules, 'schematron'))
    entries = []
    for name in ('PINT-UBL-validation-preprocessed.xslt', 'PINT-jurisdiction-aligned-rules.xslt'):
        path = os.path.join(rules, 'schematron', name)
        with open(path, 'wb') as handle:
            handle.write(b'<x/>')
        entries.append('%s  schematron/%s' % (hashlib.sha256(b'<x/>').hexdigest(), name))
    with open(os.path.join(rules, 'MANIFEST.txt'), 'w', encoding='utf-8') as handle:
        handle.write('# test manifest\n' + '\n'.join(entries) + '\n')
    return rules


NONCE = 'ab' * 16


def request_body(xml=INVOICE, kind='invoice', version='1.0.4', nonce=NONCE):
    body = {'kind': kind, 'spec_version': version, 'xml_b64': base64.b64encode(xml).decode()}
    if nonce is not None:
        body['nonce'] = nonce
    return json.dumps(body).encode()


class TestParsing(unittest.TestCase):
    def test_failed_assertions_are_extracted_with_flags_and_normalised_text(self):
        failures = worker.parse_svrl(SVRL)
        self.assertEqual([f['id'] for f in failures], ['ibr-190-ae', ''])
        self.assertEqual(failures[0]['flag'], 'fatal')
        self.assertEqual(failures[0]['text'], 'rate must be 5')
        self.assertEqual(failures[1]['flag'], 'warning')

    def test_a_complete_report_with_no_failed_assertion_is_a_pass(self):
        self.assertEqual(worker.parse_svrl(CLEAN_SVRL), [])

    def test_an_empty_or_unusable_report_is_an_error_never_a_pass(self):
        for text in (
            '',
            None,
            '   ',
            '<foo/>',
            '<svrl:schematron-output xmlns:svrl="http://purl.oclc.org/dsdl/svrl"/>',
        ):
            with self.assertRaises(RuntimeError, msg=text):
                worker.parse_svrl(text)


class TestRequestDecoding(unittest.TestCase):
    def test_a_good_request(self):
        self.assertEqual(worker.decode_request(request_body()), ('invoice', '1.0.4', INVOICE, NONCE))
        self.assertEqual(worker.decode_request(request_body(CREDIT, 'credit_note'))[0], 'credit_note')

    def test_malformed_requests_are_refused(self):
        for body in (
            b'not json',
            b'{}',
            b'[]',
            json.dumps({'kind': 'invoice'}).encode(),
            json.dumps({'kind': 'invoice', 'spec_version': '1', 'xml_b64': '!!!'}).encode(),
        ):
            with self.assertRaises(worker.BadRequest, msg=body):
                worker.decode_request(body)

    def test_unknown_kind_is_refused(self):
        with self.assertRaises(worker.BadRequest):
            worker.decode_request(request_body(kind='receipt'))

    def test_oversize_is_refused(self):
        with self.assertRaises(worker.BadRequest):
            worker.decode_request(request_body(xml=b'x' * (worker.MAX_BYTES + 1)))

    def test_doctype_and_entity_declarations_are_refused_in_every_spelling(self):
        for xml in (
            b'<!DOCTYPE x>',
            b'<!doctype x>',
            b'<! DOCTYPE x>',
            b'<!ENTITY a "b">',
            b'<?xml version="1.0"?>\n<!DOCTYPE' b' x [<!ENTITY e SYSTEM "file:///etc/passwd">]><a/>',
        ):
            with self.assertRaises(worker.BadRequest, msg=xml):
                worker.decode_request(request_body(xml=xml))

    def test_non_utf8_is_refused(self):
        with self.assertRaises(worker.BadRequest):
            worker.decode_request(request_body(xml=b'\xff\xfe<a/>'))

    def test_utf16_and_other_encodings_cannot_smuggle_a_doctype_past_the_byte_scan(self):
        utf16 = '<?xml version="1.0" encoding="UTF-16"?><!DOCTYPE x [<!ENTITY e "x">]><Invoice/>'.encode('utf-16-le')
        for xml in (
            utf16,  # NUL bytes
            b'<?xml version="1.0" encoding="UTF-16"?><Invoice/>',
            b'<?xml version="1.0" encoding="ISO-8859-1"?><Invoice/>',
            b"<?xml version='1.0' encoding='windows-1252'?><Invoice/>",
            b'<Invoice>\x00</Invoice>',
        ):
            with self.assertRaises(worker.BadRequest, msg=xml[:40]):
                worker.decode_request(request_body(xml=xml))
        declared = (
            b'<?xml version="1.0" encoding="UTF-8"?>'
            b'<Invoice xmlns="urn:oasis:names:specification:ubl:schema:xsd:Invoice-2"/>'
        )
        self.assertEqual(worker.decode_request(request_body(xml=declared))[2], declared)

    def test_a_request_needs_a_well_formed_nonce(self):
        for nonce in (None, '', 'short', 'g' * 32, 'AB' * 16, 'ab' * 40, 12345, ['ab' * 16]):
            with self.assertRaises(worker.BadRequest, msg=repr(nonce)):
                worker.decode_request(request_body(nonce=nonce))

    def test_a_document_that_is_not_what_it_is_declared_to_be_is_refused(self):
        for xml, kind in (
            (b'<foo/>', 'invoice'),  # not UBL at all
            (b'<Invoice/>', 'invoice'),  # right name, no namespace
            (INVOICE, 'credit_note'),  # an invoice sent as a credit note
            (CREDIT, 'invoice'),
            (b'<Invoice xmlns="urn:oasis:names:specification:ubl:schema:xsd:Invoice-2"', 'invoice'),  # not well formed
        ):
            with self.assertRaises(worker.BadRequest, msg=(xml, kind)):
                worker.decode_request(request_body(xml=xml, kind=kind))


class TestRulesIntegrity(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.rules = make_rules(self.tmp)

    def test_an_intact_rule_set_reports_its_manifest_fingerprint(self):
        with open(os.path.join(self.rules, 'MANIFEST.txt'), 'rb') as handle:
            expected = hashlib.sha256(handle.read()).hexdigest()
        self.assertEqual(worker.verify_rules(self.rules), expected)

    def test_a_single_changed_byte_stops_the_worker(self):
        with open(os.path.join(self.rules, 'schematron', 'PINT-UBL-validation-preprocessed.xslt'), 'ab') as handle:
            handle.write(b' ')
        with self.assertRaises(RuntimeError):
            worker.verify_rules(self.rules)

    def test_both_official_stylesheets_are_compiled_once_at_start(self):
        transformer = FakeTransformer()
        worker.Validator(self.rules, '1.0.4', transformer)
        self.assertEqual(
            sorted(transformer.compiled),
            ['PINT-UBL-validation-preprocessed.xslt', 'PINT-jurisdiction-aligned-rules.xslt'],
        )


class TestToken(unittest.TestCase):
    def test_the_worker_refuses_to_start_without_a_usable_token(self):
        for token in (None, '', 'short', 'x' * (worker.TOKEN_MIN_LENGTH - 1)):
            with self.assertRaises(ValueError, msg=token):
                worker.require_token(token)
        worker.require_token('x' * worker.TOKEN_MIN_LENGTH)

    def test_the_signature_covers_every_part_of_the_answer(self):
        base = ('d' * 64, '1.0.4', 'm' * 64, 'Engine', [{'id': 'a', 'flag': 'fatal', 'location': '', 'text': ''}])
        base = base + (NONCE,)
        reference = worker.sign_answer(TOKEN, *base)
        message = json.dumps(
            dict(zip(('xml_sha256', 'spec_version', 'manifest_sha256', 'engine', 'failures', 'nonce'), base)),
            sort_keys=True,
            separators=(',', ':'),
        )
        signing_key = hmac.new(TOKEN.encode(), b'nc-einvoice/sign', hashlib.sha256).digest()
        self.assertEqual(reference, hmac.new(signing_key, message.encode(), hashlib.sha256).hexdigest())
        variants = [
            (TOKEN + 'x',) + base,
            (TOKEN, 'e' * 64) + base[1:],
            (TOKEN, base[0], '1.0.5') + base[2:],
            (TOKEN, base[0], base[1], 'n' * 64) + base[3:],
            (TOKEN, base[0], base[1], base[2], 'Other') + base[4:],
            (TOKEN,) + base[:4] + ([],) + base[5:],
            (TOKEN,) + base[:5] + ('cd' * 16,),
        ]
        for variant in variants:
            self.assertNotEqual(worker.sign_answer(*variant), reference)

    def test_the_bearer_is_a_one_way_derivative_that_is_not_the_signing_key(self):
        bearer = worker.auth_secret(TOKEN)
        self.assertNotEqual(bearer, TOKEN)
        self.assertNotIn(TOKEN, bearer)
        signing_key = hmac.new(TOKEN.encode(), b'nc-einvoice/sign', hashlib.sha256).digest()
        self.assertNotEqual(bearer, signing_key.hex())
        # holding the bearer (as a sniffer would) lets nobody sign an answer
        forged = hmac.new(bearer.encode(), b'{}', hashlib.sha256).hexdigest()
        self.assertNotEqual(forged, worker.sign_answer(TOKEN, 'd' * 64, '1.0.4', 'm' * 64, 'E', [], NONCE))


class TestValidatorAndHttp(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        self.transformer = FakeTransformer(SVRL)
        self.validator = worker.Validator(make_rules(tmp), '1.0.4', self.transformer)
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), worker.make_handler(self.validator, TOKEN))
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.port = self.server.server_address[1]

    def _call(self, method, path, body=None, headers=None):
        connection = http.client.HTTPConnection('127.0.0.1', self.port, timeout=10)
        connection.request(method, path, body=body, headers=headers if headers is not None else {})
        response = connection.getresponse()
        data = response.read()
        connection.close()
        return response.status, (json.loads(data) if data else {})

    def _post(self, body, path='/validate', bearer=None):
        # a caller that knows the shared token sends the derived bearer; bearer='' sends none
        headers = {'content-type': 'application/json'}
        bearer = worker.auth_secret(TOKEN) if bearer is None else bearer
        if bearer:
            headers['authorization'] = 'Bearer ' + bearer
        return self._call('POST', path, body, headers)

    def test_it_runs_both_stylesheets_and_reports_what_it_ran(self):
        status, data = self._post(request_body())
        self.assertEqual(status, 200)
        self.assertEqual(self.transformer.ran, 2)
        self.assertEqual((data['engine'], data['spec_version']), ('Fake 1.0', '1.0.4'))
        self.assertEqual(data['manifest_sha256'], self.validator.manifest_sha256)
        self.assertEqual(len(data['failures']), 4, "two stylesheets, two failed assertions each")

    def test_the_answer_names_the_document_and_is_signed(self):
        status, data = self._post(request_body())
        self.assertEqual(status, 200)
        self.assertEqual(data['xml_sha256'], hashlib.sha256(INVOICE).hexdigest())
        self.assertEqual(
            data['signature'],
            worker.sign_answer(
                TOKEN,
                data['xml_sha256'],
                data['spec_version'],
                data['manifest_sha256'],
                data['engine'],
                data['failures'],
                NONCE,
            ),
        )
        self.assertEqual(data['nonce'], NONCE)

    def test_a_caller_without_the_token_gets_nothing_and_nothing_runs(self):
        wrong = (
            '',
            worker.auth_secret('y' * 40),
            worker.auth_secret(TOKEN)[:-1],
            worker.auth_secret(TOKEN) + 'x',
            TOKEN,  # the shared token itself is NOT the bearer
        )
        for bearer in wrong:
            status, data = self._post(request_body(), bearer=bearer)
            self.assertEqual(status, 401, bearer)
            self.assertNotIn('failures', data)
        status, _data = self._call(
            'POST', '/validate', request_body(), {'authorization': 'Basic ' + worker.auth_secret(TOKEN)}
        )
        self.assertEqual(status, 401)
        self.assertEqual(self.transformer.ran, 0)

    def test_a_request_for_another_release_is_refused(self):
        status, _data = self._post(request_body(version='9.9.9'))
        self.assertEqual(status, 400)
        self.assertEqual(self.transformer.ran, 0)

    def test_bad_input_never_reaches_the_transformer(self):
        for body in (b'x', request_body(xml=b'<!DOCTYPE a>'), request_body(xml=b'<foo/>')):
            status, _data = self._post(body)
            self.assertEqual(status, 400)
        self.assertEqual(self.transformer.ran, 0)

    def test_a_missing_negative_or_oversized_length_is_refused_without_reading(self):
        auth = {'authorization': 'Bearer ' + worker.auth_secret(TOKEN)}
        for headers, expected in (
            ({'content-length': '-1'}, 413),
            ({'content-length': str(worker.MAX_BYTES * 2 + 1)}, 413),
            ({'content-length': 'abc'}, 411),
        ):
            status, _data = self._call('POST', '/validate', b'', dict(auth, **headers))
            self.assertEqual(status, expected, headers)
        self.assertEqual(self.transformer.ran, 0)

    def test_an_internal_error_leaks_nothing(self):
        def boom(sheet, xml):
            raise RuntimeError('SECRET-DOCUMENT-CONTENT')

        self.transformer.run = boom
        status, data = self._post(request_body(xml=INVOICE.replace(b'<x/>', b'SECRET-DOCUMENT-CONTENT')))
        self.assertEqual(status, 500)
        self.assertNotIn('SECRET', json.dumps(data))

    def test_an_empty_or_unusable_report_is_a_500_never_a_pass(self):
        for svrl in ('', '<foo/>', None):
            self.transformer.svrl = svrl
            status, data = self._post(request_body())
            self.assertEqual(status, 500, svrl)
            self.assertNotIn('failures', data)

    def test_a_busy_validator_answers_503_instead_of_waiting_forever(self):
        with mock.patch.object(worker, 'LOCK_WAIT_SECONDS', 0.05):
            self.validator._lock.acquire()
            try:
                status, _data = self._post(request_body())
            finally:
                self.validator._lock.release()
        self.assertEqual(status, 503)

    def test_health_is_open_and_says_nothing_secret_and_unknown_paths_are_404(self):
        status, data = self._call('GET', '/health')
        self.assertEqual((status, data['spec_version']), (200, '1.0.4'))
        self.assertNotIn(TOKEN, json.dumps(data))
        self.assertEqual(self._post(b'{}', path='/nope')[0], 404)
        self.assertEqual(self._call('GET', '/validate')[0], 404)

    def test_the_server_does_not_announce_its_python_version(self):
        connection = http.client.HTTPConnection('127.0.0.1', self.port, timeout=10)
        connection.request('GET', '/health')
        response = connection.getresponse()
        response.read()
        banner = response.getheader('Server')
        connection.close()
        self.assertNotIn('Python', banner)


if __name__ == '__main__':
    unittest.main()
