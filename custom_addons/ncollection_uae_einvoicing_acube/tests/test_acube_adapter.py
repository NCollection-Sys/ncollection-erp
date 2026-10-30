# -*- coding: utf-8 -*-
"""The A-Cube adapter against a SCRIPTED transport shaped by A-Cube's published OpenAPI schema.

These are NOT recorded provider answers: no sandbox credential exists yet (docs/markdown/UAE_EINVOICING.md §9f). The
scripted server implements only what the published contract states (paths, status codes, field names, statuses); the
items the contract leaves open are marked SANDBOX-VERIFY in lib/acube.py and must be re-proven on the real sandbox.

The engine's adapter CONTRACT SUITE runs unchanged for submission, idempotency, status and error classification. Its
five callback tests assume a SIGNED callback whose body is the truth; A-Cube's webhooks are unsigned (a static token
only), so the adapter treats a callback as a trigger and reads the state from the API: those five tests are replaced
below by tests of that model, which give the same guarantee (a forged, tampered or replayed callback changes nothing).
"""

import base64
import json
import secrets as pysecrets
import time
import types
import uuid as uuidlib

from odoo.tests import BaseCase, tagged

from odoo.addons.ncollection_uae_einvoicing.lib import adapter_contract as contract
from odoo.addons.ncollection_uae_einvoicing.tests.adapter_contract import AdapterContractMixin

from ..lib import acube

TOKEN = pysecrets.token_hex(24)  # the webhook token: random per run
OWNER_TIN = '1987654321'
UBL_INV = 'urn:oasis:names:specification:ubl:schema:xsd:Invoice-2'
CBC = 'urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2'


def _jwt(exp):
    def part(data):
        return base64.urlsafe_b64encode(json.dumps(data).encode()).decode().rstrip('=')
    return '%s.%s.sig' % (part({'alg': 'none'}), part({'exp': exp}))


class ScriptedAcube:
    """A stand-in for the A-Cube API, implementing ONLY the published contract."""

    def __init__(self):
        self.documents = {}
        self.calls = []
        self.fail_next_post = None
        self.logins = 0
        self.expire_tokens = False
        self.recipient = '0235:1345678901'
        self.lose_next_answer = False
        self.list_status = 200

    def __call__(self, method, url, headers=None, body=None, timeout=None):
        self.calls.append((method, url))
        if url == acube.LOGIN_URL:
            creds = json.loads(body)
            if creds.get('password') != 'right-password':
                return acube.HttpResponse(401, b'{}')
            self.logins += 1
            return acube.HttpResponse(200, json.dumps({'token': _jwt(time.time() + 86400)}).encode())
        assert url.startswith(acube.HOSTS['sandbox']), url
        if not (headers or {}).get('Authorization', '').startswith('Bearer ') or self.expire_tokens:
            self.expire_tokens = False
            return acube.HttpResponse(401, b'{}')
        path, _sep, query = url[len(acube.HOSTS['sandbox']):].partition('?')
        parts = path.strip('/').split('/')
        if method == 'POST' and parts[1:] == ['outgoing']:
            if self.fail_next_post:
                status, self.fail_next_post = self.fail_next_post, None
                body_out = b'{"violations": [{"propertyPath": "x", "message": "m", "code": "ibr-001-ae"}]}' \
                    if status == 422 else b'{}'
                return acube.HttpResponse(status, body_out)
            from lxml import etree
            number = etree.fromstring(body).findtext('{%s}ID' % CBC)
            type_code = etree.fromstring(body).findtext('{%s}InvoiceTypeCode' % CBC) or '381'
            doc_id = str(uuidlib.uuid4())
            self.documents[doc_id] = {'invoiceId': doc_id, 'invoiceNumber': number, 'documentTypeCode': int(type_code),
                                      'direction': 'outgoing', 'status': 'sending_queued', 'collection': parts[0],
                                      'xml': body, 'tdd': None, 'recipientIdentifier': self.recipient}
            if self.lose_next_answer:
                self.lose_next_answer = False
                raise contract.AdapterError(contract.ErrorCategory.TRANSIENT, 'ReadTimeout', 'timeout')
            return acube.HttpResponse(201, json.dumps({'invoiceId': doc_id, 'status': 'sending_queued'}).encode())
        if method == 'GET' and len(parts) == 1 and self.list_status != 200:
            return acube.HttpResponse(self.list_status, b'{}')
        if method == 'GET' and len(parts) == 1:
            items = [{k: v for k, v in d.items() if k not in ('xml', 'tdd', 'collection')}
                     for d in self.documents.values() if d['collection'] == parts[0]
                     and ('direction=incoming' in query) == (d['direction'] == 'incoming')]
            return acube.HttpResponse(200, json.dumps(items).encode())
        doc = self.documents.get(parts[1]) if len(parts) > 1 else None
        if not doc or doc['collection'] != parts[0]:
            return acube.HttpResponse(404, b'{}')
        if len(parts) == 2:
            return acube.HttpResponse(200, json.dumps({k: v for k, v in doc.items()
                                                       if k not in ('xml', 'tdd', 'collection')}).encode())
        if parts[2] == 'tdd':
            return acube.HttpResponse(200, doc['tdd']) if doc['tdd'] else acube.HttpResponse(204, b'')
        if parts[2] == 'source':
            return acube.HttpResponse(200, doc['xml'])
        return acube.HttpResponse(404, b'{}')


def _xml(number, type_code='380', uuid='f12f329f-6430-4399-b661-7c5cd9c3a9e6'):
    return ('<Invoice xmlns="%s" xmlns:cbc="%s"><cbc:ID>%s</cbc:ID><cbc:UUID>%s</cbc:UUID>'
            '<cbc:InvoiceTypeCode>%s</cbc:InvoiceTypeCode></Invoice>'
            % (UBL_INV, CBC, number, uuid, type_code)).encode()


class AcubeCase(BaseCase):

    def make_adapter(self):
        acube._TOKENS.clear()
        self.server = ScriptedAcube()
        provider = types.SimpleNamespace(id=4242, nc_acube_environment='sandbox',
                                         database=getattr(self, 'database', 'tenanta'))
        values = {'email': 'ops@example.com', 'password': 'right-password', 'webhook_token': TOKEN}

        def read(name):
            if name not in values:
                raise contract.AdapterError(contract.ErrorCategory.CONFIGURATION, 'secret-not-set', name)
            return values[name]
        self.values = values
        return acube.AcubeAdapter(provider, read, transport=self.server)

    def make_request(self, key_suffix='', type_code='380', self_billed=False, uuid=None, number=None):
        number = number or 'INV/2026/0001' + key_suffix
        xml = _xml(number, type_code, uuid or str(uuidlib.uuid5(uuidlib.NAMESPACE_URL, 'doc' + key_suffix)))
        import hashlib
        digest = hashlib.sha256(xml).hexdigest()
        return contract.SubmissionRequest(
            einvoice_uuid='u-1' + key_suffix, revision=1, document_kind='invoice', type_code=type_code, xml=xml,
            xml_sha256=digest, idempotency_key=contract.idempotency_key('u-1' + key_suffix, 1, digest),
            correlation_id='corr-1', seller_endpoint='0235:' + OWNER_TIN, buyer_endpoint='0235:1345678901',
            customization_id='urn:peppol:pint:billing-1@ae-1', self_billed=self_billed)

    def force_transient(self, adapter):
        self.server.fail_next_post = 503

    def force_rejection(self, adapter):
        self.server.fail_next_post = 422


@tagged('post_install', '-at_install')
class TestAcubeContract(AdapterContractMixin, AcubeCase):
    """The engine's contract suite; its four signed-callback tests are re-stated for the trigger model."""

    # The suite's signed-callback tests, re-stated for A-Cube's unsigned trigger model (same guarantee, see module doc)
    def _delivered(self):
        result = self.adapter.submit(self.make_request())
        uuid = result.asp_document_id.split(':')[1]
        body = json.dumps({'eventId': 'e-1', 'eventType': 'document.delivered', 'invoiceUuid': uuid,
                           'documentType': 'invoice', 'status': 'delivered'}).encode()
        return uuid, body

    def test_contract_an_authentic_callback_verifies_and_parses(self):
        uuid, body = self._delivered()
        self.server.documents[uuid]['status'] = 'delivered'
        self.assertTrue(self.adapter.verify_callback({acube.CALLBACK_HEADER: TOKEN}, body))
        events = self.adapter.parse_callback({}, body)
        self.assertEqual([(e.kind, e.status) for e in events], [('exchange', 'delivered')])
        self.assertTrue(all(e.message_id for e in events))

    def test_contract_a_tampered_callback_is_refused(self):
        uuid, body = self._delivered()  # the body claims delivery; the API does not
        events = self.adapter.parse_callback({}, body)
        self.assertNotIn(('exchange', 'delivered'), [(e.kind, e.status) for e in events])

    def test_contract_a_replayed_callback_is_refused(self):
        uuid, body = self._delivered()
        self.server.documents[uuid]['status'] = 'delivered'
        first, again = self.adapter.parse_callback({}, body), self.adapter.parse_callback({}, body)
        self.assertEqual([e.message_id for e in first], [e.message_id for e in again])

    def test_contract_a_malformed_callback_is_a_classified_error_never_a_crash(self):
        for body in (b'not json', b'{}', b'\xff\xfe'):
            with self.assertRaises(contract.AdapterError, msg=body):
                self.adapter.parse_callback({}, body)


@tagged('post_install', '-at_install')
class TestAcubeAdapter(AcubeCase):

    def setUp(self):
        super().setUp()
        self.adapter = self.make_adapter()

    def test_the_document_goes_to_the_endpoint_of_its_kind(self):
        for code, collection in (('380', 'invoices'), ('480', 'invoices'), ('389', 'invoices'),
                                 ('381', 'credit-notes'), ('81', 'credit-notes'), ('261', 'credit-notes')):
            result = self.adapter.submit(self.make_request(key_suffix='-' + code, type_code=code))
            self.assertEqual(result.status, 'accepted', code)
            self.assertTrue(result.asp_document_id.startswith(collection + ':'), code)
            self.assertIn(('POST', acube.HOSTS['sandbox'] + '/%s/outgoing' % collection), self.server.calls)

    def test_a_self_billed_document_is_owned_by_the_buyer(self):
        request = self.make_request(type_code='389', self_billed=True)
        number, owner = self.adapter._number_and_owner(request)
        self.assertEqual((number, owner), ('INV/2026/0001', '1345678901'))

    def test_the_token_is_reused_and_renewed_once_on_401(self):
        self.adapter.submit(self.make_request(key_suffix='-a'))
        self.adapter.submit(self.make_request(key_suffix='-b'))
        self.assertEqual(self.server.logins, 1, 'one login for both')
        self.server.expire_tokens = True
        self.assertEqual(self.adapter.submit(self.make_request(key_suffix='-c')).status, 'accepted')
        self.assertEqual(self.server.logins, 2)

    def test_refused_credentials_are_an_authentication_failure(self):
        self.values['password'] = 'wrong'
        result = self.adapter.submit(self.make_request())
        self.assertEqual((result.status, result.error.category), ('failed', contract.ErrorCategory.AUTHENTICATION))

    def test_a_missing_legal_entity_is_configuration_not_rejection(self):
        self.server.fail_next_post = 404
        result = self.adapter.submit(self.make_request())
        self.assertEqual(result.error.category, contract.ErrorCategory.CONFIGURATION)

    def test_a_rejection_keeps_the_rule_code_not_the_text(self):
        self.server.fail_next_post = 422
        result = self.adapter.submit(self.make_request())
        self.assertEqual(result.error.code, 'ibr-001-ae')
        self.assertEqual(result.error.message, 'A-Cube refused the document at x', 'the field path, never the text')

    def test_a_violation_path_with_free_text_is_dropped(self):
        body = {'violations': [{'propertyPath': 'buyer name ACME LLC', 'message': 'm'}]}
        response = acube.HttpResponse(422, json.dumps(body).encode())
        self.assertEqual(acube.AcubeAdapter._violation_path(response), '')
        self.assertEqual(acube.AcubeAdapter._violation_path(acube.HttpResponse(422, b'not json')), '')

    def test_retry_after_is_honoured(self):
        server = self.server

        def throttled(method, url, headers=None, body=None, timeout=None):
            if method == 'POST' and url.endswith('/outgoing'):
                return acube.HttpResponse(429, b'{}', {'Retry-After': '120'})
            return server(method, url, headers, body, timeout)
        self.adapter.transport = throttled
        result = self.adapter.submit(self.make_request())
        self.assertEqual((result.error.category, result.error.retry_after), (contract.ErrorCategory.TRANSIENT, 120))

    def test_status_maps_only_what_the_contract_settles(self):
        result = self.adapter.submit(self.make_request())
        uuid = result.asp_document_id.split(':')[1]
        self.server.documents[uuid]['status'] = 'delivered'
        events = self.adapter.fetch_status(result.asp_document_id, 'corr-1').events
        self.assertEqual([(e.kind, e.status) for e in events], [('exchange', 'delivered')])
        self.server.documents[uuid]['status'] = 'accepted'  # no published meaning: never mapped onto a state
        events = self.adapter.fetch_status(result.asp_document_id).events
        self.assertEqual([(e.kind, e.status) for e in events], [('exchange', 'acube:accepted')])

    def test_the_tdd_is_a_stored_confirmation_never_generated(self):
        result = self.adapter.submit(self.make_request())
        uuid = result.asp_document_id.split(':')[1]
        self.server.documents[uuid]['tdd'] = b'<TaxDataDocument/>'
        fta = [e for e in self.adapter.fetch_status(result.asp_document_id).events if e.kind == 'fta']
        self.assertEqual(fta[0].status, 'reported')
        self.assertTrue(fta[0].metadata['tdd_reference'].startswith('acube-tdd-sha256:'))

    def test_the_host_is_never_taken_from_configuration(self):
        provider = types.SimpleNamespace(id=1, nc_acube_environment='http://evil.example')
        with self.assertRaises(contract.AdapterError):
            acube.AcubeAdapter(provider, lambda n: 'x', transport=self.server)

    def test_inbound_documents_are_listed_and_fetched_with_their_xml(self):
        doc_id = str(uuidlib.uuid4())
        self.server.documents[doc_id] = {'invoiceId': doc_id, 'invoiceNumber': 'S-1', 'documentTypeCode': 380,
                                         'direction': 'incoming', 'status': 'received', 'collection': 'invoices',
                                         'xml': _xml('S-1'), 'tdd': None, 'senderIdentifier': '0235:1456789012'}
        ids, cursor = self.adapter.list_inbound()
        self.assertEqual((ids, cursor), (['invoices:' + doc_id], '1,1'))
        document = self.adapter.fetch_inbound(ids[0])
        self.assertEqual(document.xml, _xml('S-1'))
        self.assertEqual(document.sender_endpoint, '0235:1456789012')

    def test_an_outgoing_document_is_never_fetched_as_inbound(self):
        result = self.adapter.submit(self.make_request())
        with self.assertRaises(contract.AdapterError):
            self.adapter.fetch_inbound(result.asp_document_id)

    def test_no_secret_appears_in_a_request_url(self):
        self.adapter.submit(self.make_request())
        for _method, url in self.server.calls:
            for secret in self.values.values():
                self.assertNotIn(secret, url)


@tagged('post_install', '-at_install')
class TestAcubeCallbacks(AcubeCase):

    def setUp(self):
        super().setUp()
        self.adapter = self.make_adapter()
        self.result = self.adapter.submit(self.make_request())
        self.uuid = self.result.asp_document_id.split(':')[1]

    def _body(self, event='document.delivered', **extra):
        return json.dumps(dict({'eventId': 'e-1', 'eventType': event, 'invoiceUuid': self.uuid,
                                'documentType': 'invoice'}, **extra)).encode()

    def test_only_the_configured_token_verifies(self):
        body = self._body()
        self.assertTrue(self.adapter.verify_callback({acube.CALLBACK_HEADER: TOKEN}, body))
        self.assertTrue(self.adapter.verify_callback({acube.CALLBACK_HEADER.lower(): TOKEN}, body))
        for headers in ({}, {acube.CALLBACK_HEADER: TOKEN[:-1]}, {acube.CALLBACK_HEADER: ''}, {'X-Other': TOKEN}):
            self.assertFalse(self.adapter.verify_callback(headers, body))

    def test_a_short_or_missing_token_never_verifies(self):
        self.values['webhook_token'] = 'short'
        self.assertFalse(self.adapter.verify_callback({acube.CALLBACK_HEADER: 'short'}, self._body()))
        del self.values['webhook_token']
        self.assertFalse(self.adapter.verify_callback({acube.CALLBACK_HEADER: TOKEN}, self._body()))

    def test_the_state_comes_from_the_api_not_from_the_body(self):
        # a forged / tampered body claiming delivery: the API still says sending_queued, so nothing is delivered
        events = self.adapter.parse_callback({}, self._body(status='delivered'))
        self.assertEqual([(e.kind, e.status) for e in events], [('exchange', 'acube:sending_queued')])
        self.server.documents[self.uuid]['status'] = 'delivered'
        events = self.adapter.parse_callback({}, self._body())
        self.assertEqual([(e.kind, e.status) for e in events], [('exchange', 'delivered')])

    def test_a_replayed_callback_yields_the_same_event_ids(self):
        self.server.documents[self.uuid]['status'] = 'delivered'
        first = self.adapter.parse_callback({}, self._body())
        again = self.adapter.parse_callback({}, self._body(eventId='e-2'))
        self.assertEqual([e.message_id for e in first], [e.message_id for e in again], 'the engine dedupes them')

    def test_a_received_document_is_announced_by_id_only(self):
        events = self.adapter.parse_callback({}, self._body(event='document.received'))
        self.assertEqual([(e.kind, e.asp_document_id) for e in events], [('inbound', 'invoices:' + self.uuid)])

    def test_malformed_callbacks_are_classified_never_a_crash(self):
        for body in (b'not json', b'{}', b'\xff\xfe', b'{"eventType": "document.delivered", "invoiceUuid": "../x"}'):
            with self.assertRaises(contract.AdapterError, msg=body):
                self.adapter.parse_callback({}, body)

    def test_an_unknown_document_in_a_callback_is_an_error_not_a_state(self):
        with self.assertRaises(contract.AdapterError):
            self.adapter.parse_callback({}, self._body(invoiceUuid=str(uuidlib.uuid4())))

    def test_an_unknown_event_type_changes_nothing(self):
        self.assertEqual(self.adapter.parse_callback({}, self._body(event='legal_entity.synchronized')), [])


@tagged('post_install', '-at_install')
class TestAcubeLimits(AcubeCase):

    def test_an_oversized_inbound_document_is_refused_before_archiving(self):
        adapter = self.make_adapter()
        doc_id = str(uuidlib.uuid4())
        self.server.documents[doc_id] = {'invoiceId': doc_id, 'invoiceNumber': 'BIG', 'documentTypeCode': 380,
                                         'direction': 'incoming', 'status': 'received', 'collection': 'invoices',
                                         'xml': b'<x>' + b'a' * (acube.MAX_INBOUND_BYTES + 1) + b'</x>', 'tdd': None}
        with self.assertRaises(contract.AdapterError) as caught:
            adapter.fetch_inbound('invoices:' + doc_id)
        self.assertEqual(caught.exception.code, 'inbound-too-large')


@tagged('post_install', '-at_install')
class TestAcubeIdempotency(AcubeCase):
    """A-Cube has no idempotency key: the adapter proves a duplicate by OUR cbc:UUID in the stored document, and it
    never POSTs when that proof cannot be made."""

    def setUp(self):
        super().setUp()
        self.adapter = self.make_adapter()

    def _posts(self):
        return [c for c in self.server.calls if c[0] == 'POST' and c[1].endswith('/outgoing')]

    def test_a_timeout_after_acceptance_then_a_retry_gives_one_document(self):
        self.server.lose_next_answer = True
        first = self.adapter.submit(self.make_request())
        self.assertEqual((first.status, first.retryable), ('failed', True))
        self.assertEqual(len(self.server.documents), 1, 'A-Cube DID store it')
        retry = self.adapter.submit(self.make_request())
        self.assertEqual((retry.status, retry.duplicate), ('accepted', True))
        self.assertEqual(len(self.server.documents), 1, 'no second document')
        self.assertEqual(len(self._posts()), 1)
        uuid = retry.asp_document_id.split(':')[1]
        self.assertIn(uuid, self.server.documents)

    def test_the_same_number_with_another_uuid_is_not_a_duplicate(self):
        self.adapter.submit(self.make_request(uuid=str(uuidlib.uuid4()), number='INV-7'))
        other = self.adapter.submit(self.make_request(key_suffix='-r2', uuid=str(uuidlib.uuid4()), number='INV-7'))
        self.assertFalse(other.duplicate, 'a number alone never suppresses a legitimate document')
        self.assertEqual(len(self.server.documents), 2)

    def test_the_same_number_for_another_recipient_is_not_a_duplicate(self):
        uuid = str(uuidlib.uuid4())
        self.adapter.submit(self.make_request(uuid=uuid, number='INV-8'))
        self.server.recipient = '0235:1999999999'
        for doc in self.server.documents.values():
            doc['recipientIdentifier'] = '0235:1999999999'
        again = self.adapter.submit(self.make_request(uuid=uuid, number='INV-8'))
        self.assertFalse(again.duplicate)

    def test_an_unreadable_list_never_sends_blind(self):
        self.server.list_status = 503
        result = self.adapter.submit(self.make_request())
        self.assertEqual((result.status, result.error.code), ('failed', 'duplicate-check-inconclusive'))
        self.assertTrue(result.retryable)
        self.assertEqual(self._posts(), [])

    def test_two_tenants_never_share_a_token(self):
        self.adapter.submit(self.make_request(key_suffix='-a'))
        # another tenant database whose provider record has the SAME id
        provider = types.SimpleNamespace(id=4242, nc_acube_environment='sandbox', database='tenantb')
        other = acube.AcubeAdapter(provider, lambda name: self.values[name], transport=self.server)
        other.submit(self.make_request(key_suffix='-b'))
        self.assertEqual(self.server.logins, 2, 'the second tenant logs in itself')
        self.assertEqual(len(acube._TOKENS), 2, 'one token per tenant database')

    def test_an_oversized_provider_answer_is_refused(self):
        import requests

        class Huge:
            status_code, headers = 200, {}

            def iter_content(self, size):
                for _ in range(acube.MAX_RESPONSE_BYTES // 65536 + 2):
                    yield b'a' * 65536

            def close(self):
                pass
        seen = {}

        def fake_request(*args, **kwargs):
            seen.update(kwargs)
            return Huge()
        original = requests.request
        requests.request = fake_request
        try:
            with self.assertRaises(contract.AdapterError) as caught:
                acube.requests_transport('GET', acube.HOSTS['sandbox'] + '/invoices')
        finally:
            requests.request = original
        self.assertEqual(caught.exception.code, 'response-too-large')
        self.assertEqual((seen['verify'], seen['allow_redirects']), (True, False))
