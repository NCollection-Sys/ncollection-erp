# -*- coding: utf-8 -*-
"""The adapter contract (lib/adapter_contract.py) proven on the test-only fake adapter, plus the engine side: the
registry, the provider-neutral request built from a validated e-invoice, and the absence of any transport in core."""

import ast
import os
import secrets as pysecrets
import time

from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged

from ..lib import adapter_contract as contract
from . import fake_adapter
from .adapter_contract import AdapterContractMixin
from .common import EInvoiceCase

ADDON = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@tagged('post_install', '-at_install')
class TestFakeAdapterHonoursTheContract(AdapterContractMixin, TransactionCase):

    def make_adapter(self):
        self.callback_secret = pysecrets.token_hex(16)
        return fake_adapter.FakeAdapter(provider=None, secrets=lambda name: self.callback_secret)

    def make_request(self, key_suffix=''):
        return contract.SubmissionRequest(
            einvoice_uuid='f12f329f-6430-4399-b661-7c5cd9c3a9e6' + key_suffix,
            revision=1,
            document_kind='invoice',
            type_code='380',
            xml=b'<Invoice/>',
            xml_sha256='a' * 64,
            idempotency_key=contract.idempotency_key('f12f329f' + key_suffix, 1, 'a' * 64),
            correlation_id='corr-1',
            seller_endpoint='0235:1987654321',
            buyer_endpoint='0235:1345678901',
        )

    def signed_callback(self, body):
        timestamp, nonce = int(time.time()), pysecrets.token_hex(8)
        return {
            'X-Fake-Timestamp': str(timestamp),
            'X-Fake-Nonce': nonce,
            'X-Fake-Signature': fake_adapter.sign(self.callback_secret, timestamp, nonce, body),
        }

    def force_transient(self, adapter):
        adapter.fail_next = 'transient'

    def force_rejection(self, adapter):
        adapter.fail_next = 'rejected'

    def test_a_stale_callback_is_refused(self):
        body = b'{"events": []}'
        timestamp, nonce = int(time.time()) - 3600, 'n-stale'
        headers = {
            'X-Fake-Timestamp': str(timestamp),
            'X-Fake-Nonce': nonce,
            'X-Fake-Signature': fake_adapter.sign(self.callback_secret, timestamp, nonce, body),
        }
        self.assertFalse(self.adapter.verify_callback(headers, body))


@tagged('post_install', '-at_install')
class TestContractPrimitives(TransactionCase):

    def test_the_idempotency_key_is_deterministic_and_bound_to_the_xml(self):
        key = contract.idempotency_key('uuid-1', 1, 'a' * 64)
        self.assertEqual(key, contract.idempotency_key('uuid-1', 1, 'a' * 64))
        self.assertNotEqual(key, contract.idempotency_key('uuid-1', 2, 'a' * 64))
        self.assertNotEqual(key, contract.idempotency_key('uuid-1', 1, 'b' * 64))
        with self.assertRaises(ValueError):
            contract.idempotency_key('', 1, 'a' * 64)

    def test_the_registry_refuses_unknown_and_conflicting_adapters(self):
        with self.assertRaises(contract.AdapterError) as caught:
            contract.get('no-such-adapter')
        self.assertEqual(caught.exception.category, contract.ErrorCategory.CONFIGURATION)
        with self.assertRaises(ValueError):
            contract.register(object)

        class Other(contract.AdapterBase):
            code = fake_adapter.FakeAdapter.code

        contract.register(fake_adapter.FakeAdapter)
        try:
            with self.assertRaises(ValueError):
                contract.register(Other)
        finally:
            contract.unregister(fake_adapter.FakeAdapter.code)

    def test_the_core_ships_no_adapter_and_no_transport(self):
        self.assertEqual(self.env['ncollection.einvoice.provider']._fields['adapter'].selection, [])
        forbidden = {'requests', 'urllib', 'urllib3', 'http', 'httpx', 'aiohttp', 'socket'}
        for name in ('lib/adapter_contract.py', 'models/adapter.py'):
            with open(os.path.join(ADDON, name), encoding='utf-8') as handle:
                tree = ast.parse(handle.read())
            imported = {
                (node.module or '').split('.')[0] if isinstance(node, ast.ImportFrom) else alias.name.split('.')[0]
                for node in ast.walk(tree)
                if isinstance(node, (ast.Import, ast.ImportFrom))
                for alias in node.names
            }
            self.assertFalse(imported & forbidden, '%s imports %s' % (name, imported & forbidden))


@tagged('post_install', '-at_install')
class TestSubmissionRequestFromARealEInvoice(EInvoiceCase):

    def test_only_a_validated_einvoice_yields_a_request(self):
        record = self._einvoice()
        record.action_prepare()
        record.action_generate_xml()
        with self.assertRaisesRegex(UserError, 'officially validated'):
            self.env['ncollection.einvoice.adapter']._nc_submission_request(record)

    def test_the_request_carries_the_archived_xml_and_a_stable_key(self):
        record = self._einvoice()
        record.action_prepare()
        record.action_generate_xml()
        self._validated(record)
        Adapter = self.env['ncollection.einvoice.adapter']
        request = Adapter._nc_submission_request(record)
        self.assertEqual(request.xml, record.xml_attachment_id.raw)
        self.assertEqual(request.xml_sha256, record.xml_sha256)
        self.assertEqual(request.idempotency_key, Adapter._nc_submission_request(record).idempotency_key)
        self.assertNotEqual(request.correlation_id, Adapter._nc_submission_request(record).correlation_id)
        self.assertEqual(request.seller_endpoint, '0235:1987654321')
        self.assertEqual(record.submission_state, 'not_submitted', "building a request sends nothing")

    def test_a_provider_without_adapter_is_a_configuration_error(self):
        provider = self.env['ncollection.einvoice.provider'].create(
            {'name': 'Some ASP', 'company_id': self.company.id}
        )
        with self.assertRaises(contract.AdapterError) as caught:
            self.env['ncollection.einvoice.adapter']._nc_adapter_for(provider)
        self.assertEqual(caught.exception.category, contract.ErrorCategory.CONFIGURATION)
