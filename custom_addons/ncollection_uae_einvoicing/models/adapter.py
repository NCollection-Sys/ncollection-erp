# -*- coding: utf-8 -*-
"""The engine side of the ASP adapter contract (lib/adapter_contract.py), as an Odoo AbstractModel.

This file holds NO transport: it resolves the adapter a provider record names, and builds the provider-neutral
:class:`~..lib.adapter_contract.SubmissionRequest` of a validated e-invoice. Sending, polling and callbacks are
implemented by a provider module and wired in a later phase; nothing here changes an e-invoice's state.
"""

import hashlib
import uuid as uuid_lib

from odoo import models
from odoo.exceptions import UserError

from ..lib import adapter_contract as contract


class EInvoiceAdapter(models.AbstractModel):
    _name = 'ncollection.einvoice.adapter'
    _description = 'UAE e-invoicing ASP adapter (contract)'

    def _nc_adapter_for(self, provider):
        """An instance of the adapter ``provider`` names, given a secret reader bound to that provider only."""
        provider.ensure_one()
        if not provider.adapter:
            raise contract.AdapterError(
                contract.ErrorCategory.CONFIGURATION, 'no-adapter', 'the provider names no adapter'
            )
        adapter_class = contract.get(provider.adapter)

        user = self.env.user

        def read_secret(name):
            # locate the row (sudo: the ciphertext column is restricted), then decrypt AS THE CALLER: _reveal refuses
            # anyone but the e-invoicing service account, and sudo must not lift that check
            row = (
                self.env['ncollection.einvoice.secret']
                .sudo()
                .search([('provider_id', '=', provider.id), ('secret_name', '=', name)], limit=1)
            )
            if not row:
                raise contract.AdapterError(
                    contract.ErrorCategory.CONFIGURATION, 'secret-not-set', 'the credential %r is not set' % name
                )
            return row.with_user(user)._reveal()

        return adapter_class(provider, read_secret)

    def _nc_submission_request(self, einvoice):
        """The provider-neutral request for ONE officially validated e-invoice. Builds; sends nothing."""
        einvoice.ensure_one()
        if einvoice.local_state != 'validated':
            raise UserError(self.env._("Only an officially validated e-invoice can be submitted to an ASP."))
        xml = einvoice.xml_attachment_id.raw
        digest = hashlib.sha256(xml).hexdigest()
        if digest != einvoice.xml_sha256:
            raise UserError(self.env._("The archived XML no longer matches its checksum."))
        return contract.SubmissionRequest(
            einvoice_uuid=einvoice.uuid,
            revision=einvoice.revision,
            document_kind=einvoice.document_kind,
            type_code=einvoice.type_code,
            xml=xml,
            xml_sha256=digest,
            idempotency_key=contract.idempotency_key(einvoice.uuid, einvoice.revision, digest),
            correlation_id=str(uuid_lib.uuid4()),
            seller_endpoint='%s:%s' % (einvoice.seller_endpoint_scheme, einvoice.seller_endpoint_id),
            buyer_endpoint='%s:%s' % (einvoice.buyer_endpoint_scheme, einvoice.buyer_endpoint_id),
        )
