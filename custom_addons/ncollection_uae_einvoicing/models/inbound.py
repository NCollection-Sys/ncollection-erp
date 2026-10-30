# -*- coding: utf-8 -*-
"""Inbound e-invoices: what the ASP RECEIVED for this Person, turned at most into a DRAFT vendor bill - or, for a
SELF-BILLED document (389 / 261) its customer issued for it under a self-billing agreement, a DRAFT customer invoice.

Decided 2026-10-28: an inbound document only ever becomes a DRAFT document. Nothing here posts, validates or pays
anything, and nothing is guessed: a supplier is matched only on an authoritative identifier, a tax only through the
company's own tax map, a currency only when it exists and is active. Anything else stops in an explicit state.

States (``state``; separate from every outbound dimension, MLS, FTA and the business response)::

    received ──► validating ──► matching ──► ready ──► bill_created
                    │              │           │
                    ├► validation_failed       └► review   (tax / currency / allowances not mappable, no agreement)
                    ├► rejected   (not for this Person, not a purchase document)
                    ├► duplicate  (the same document already arrived: domain identity)
                    └► (validator unavailable: stays validating, retried)
                                   └► unmatched  (no supplier, or identifiers point to different suppliers)

Duplicate protection, three layers, each enforced by the DATABASE:

1. ``UNIQUE(provider, asp document id)`` and ``UNIQUE(provider, message id)``: a repeated callback, a poll that sees
   the same document, a retried fetch job, or a callback racing a poll all resolve to ONE intake record.
2. a partial unique index on the DOMAIN identity (company, supplier identifier, document kind, document number) over
   every record that is not itself a duplicate: the same invoice delivered again under a new provider id or message id
   is stored (it is evidence) in state ``duplicate`` and points at the original.
3. ``account.move.nc_inbound_id`` is unique: whatever happens, one intake record has at most one vendor bill.

The XML is the legal artefact: it is archived as a protected attachment with its sha256 (unlike transport events, of
which only a digest is kept).
"""

import hashlib
import logging
import os
from datetime import timedelta
from decimal import Decimal

import psycopg2

from odoo import api, fields, models
from odoo.exceptions import UserError

from ..lib import adapter_contract as contract
from ..lib import backoff, inbound_parse, spec, validator

_logger = logging.getLogger(__name__)

CHANNEL = 'root.einvoice'
VALIDATOR_URL_ENV = 'NC_EINVOICE_VALIDATOR_URL'
VALIDATOR_TOKEN_ENV = 'NC_EINVOICE_VALIDATOR_TOKEN'
INBOUND_POLL_INTERVAL = timedelta(minutes=15)

STATES = [
    ('received', 'Received'),
    ('validating', 'Validating'),
    ('validation_failed', 'Validation failed'),
    ('matching', 'Matching the supplier'),
    ('unmatched', 'Supplier not matched'),
    ('ready', 'Ready for a vendor bill'),
    ('review', 'Needs review'),
    ('bill_created', 'Draft vendor bill created'),
    ('duplicate', 'Duplicate'),
    ('rejected', 'Rejected'),
]
TRANSITIONS = {
    'received': {'validating', 'rejected'},
    'validating': {'validating', 'validation_failed', 'rejected', 'duplicate', 'matching', 'review'},
    'matching': {'unmatched', 'ready', 'review'},
    'unmatched': {'matching'},
    'review': {'validating'},
    'ready': {'bill_created', 'review'},
    'validation_failed': set(),
    'rejected': set(),
    'duplicate': set(),
    'bill_created': set(),
}
REPROCESSABLE = ('unmatched', 'review', 'validating')


def _dec(value):
    return float(value) if value is not None else 0.0


class EInvoiceInbound(models.Model):
    _name = 'ncollection.einvoice.inbound'
    _description = 'UAE inbound e-invoice'
    _order = 'id desc'
    _rec_name = 'invoice_number'

    provider_id = fields.Many2one('ncollection.einvoice.provider', required=True, ondelete='restrict', readonly=True,
                                  index=True)
    company_id = fields.Many2one('res.company', required=True, readonly=True, index=True)
    message_id = fields.Char(required=True, readonly=True)
    asp_document_id = fields.Char(required=True, readonly=True, index=True)
    correlation_id = fields.Char(readonly=True)
    sender_endpoint = fields.Char(readonly=True)
    receiver_endpoint = fields.Char(readonly=True)
    received_at = fields.Char(string='Received by the ASP at', readonly=True)
    xml_attachment_id = fields.Many2one('ir.attachment', readonly=True)
    xml_sha256 = fields.Char(readonly=True)

    document_kind = fields.Selection([('invoice', 'Invoice'), ('credit_note', 'Credit note')], readonly=True)
    type_code = fields.Char(readonly=True)
    invoice_number = fields.Char(readonly=True, index=True)
    document_uuid = fields.Char(string='UUID', readonly=True)
    issue_date = fields.Date(readonly=True)
    due_date = fields.Date(readonly=True)
    currency_code = fields.Char(readonly=True)
    billing_reference = fields.Char(readonly=True)
    line_total = fields.Float(digits=(16, 4), readonly=True)
    tax_exclusive = fields.Float(digits=(16, 4), readonly=True)
    tax_total = fields.Float(digits=(16, 4), readonly=True)
    tax_inclusive = fields.Float(digits=(16, 4), readonly=True)
    payable = fields.Float(digits=(16, 4), readonly=True)
    seller_name = fields.Char(readonly=True)
    seller_trn = fields.Char(readonly=True)
    seller_endpoint = fields.Char(readonly=True)
    seller_legal_id = fields.Char(readonly=True)
    buyer_name = fields.Char(readonly=True)
    buyer_trn = fields.Char(readonly=True)
    buyer_endpoint = fields.Char(readonly=True)
    buyer_legal_id = fields.Char(readonly=True)
    # a SELF-BILLED document (389 / 261): the customer issued it for this company, its supplier; it becomes a DRAFT
    # customer invoice / credit note, and the party matched is the customer
    self_billed = fields.Boolean(readonly=True)

    state = fields.Selection(STATES, default='received', required=True, readonly=True, index=True)
    reason_code = fields.Char(readonly=True)
    reason = fields.Text(readonly=True)
    attempt_count = fields.Integer(readonly=True)
    next_attempt_at = fields.Datetime(readonly=True)
    domain_key = fields.Char(readonly=True, index=True)
    duplicate_of_id = fields.Many2one('ncollection.einvoice.inbound', readonly=True, ondelete='restrict')
    partner_id = fields.Many2one('res.partner', string='Counterparty', readonly=True,
                                 help="The supplier (a received invoice) or the customer (a self-billed document).")
    match_basis = fields.Char(readonly=True)
    bill_id = fields.Many2one('account.move', string='Draft document', readonly=True, ondelete='restrict',
                              help="The draft vendor bill, or for a self-billed document the draft customer invoice.")
    history_ids = fields.One2many('ncollection.einvoice.inbound.history', 'inbound_id', readonly=True)

    _asp_document_unique = models.Constraint('unique(provider_id, asp_document_id)',
                                             'A received document is recorded once.')
    _message_unique = models.Constraint('unique(provider_id, message_id)', 'A received message is recorded once.')
    _domain_unique = models.UniqueIndex('(domain_key) WHERE domain_key IS NOT NULL AND state <> \'duplicate\'',
                                        'This document was already received.')

    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.su:
            raise UserError(self.env._("Inbound documents are recorded by the e-invoicing engine only."))
        return super().create(vals_list)

    def write(self, vals):
        if not self.env.su:
            raise UserError(self.env._("Inbound documents are written by the e-invoicing engine only."))
        return super().write(vals)

    @api.ondelete(at_uninstall=False)
    def _nc_never_delete(self):
        raise UserError(self.env._("Received e-invoices are part of the regulated archive and are never deleted."))

    # ------------------------------------------------------------------ state machine
    def _nc_move(self, new_state, event, code=None, detail=''):
        for record in self:
            current = record.state
            if new_state != current and new_state not in TRANSITIONS.get(current, set()):
                raise UserError(self.env._("Illegal inbound transition: %(old)s cannot go to %(new)s.",
                                           old=current, new=new_state))
            record.write({'state': new_state, 'reason_code': code or False, 'reason': detail or False})
            self.env['ncollection.einvoice.inbound.history'].sudo().create({
                'inbound_id': record.id, 'from_state': current, 'to_state': new_state, 'event': event,
                'detail': ('%s %s' % (code or '', detail or '')).strip()[:2000]})
        return self

    # ------------------------------------------------------------------ intake (no business decision yet)
    @api.model
    def _nc_receive(self, provider, asp_document_id, adapter=None):
        """Fetch ``asp_document_id`` from ``provider`` and record it ONCE. Returns the intake record."""
        Inbound = self.sudo()
        existing = Inbound.search([('provider_id', '=', provider.id), ('asp_document_id', '=', asp_document_id)],
                                  limit=1)
        if existing:
            return existing
        adapter = adapter or self.env['ncollection.einvoice']._nc_adapter(provider)
        document = adapter.fetch_inbound(asp_document_id)
        if not isinstance(document, contract.InboundDocument) or document.asp_document_id != asp_document_id:
            raise contract.AdapterError(contract.ErrorCategory.PERMANENT, 'inbound-mismatch',
                                        'the provider returned another document')
        digest = hashlib.sha256(document.xml or b'').hexdigest()
        if document.xml_sha256 and document.xml_sha256 != digest:
            raise contract.AdapterError(contract.ErrorCategory.PERMANENT, 'inbound-digest',
                                        'the document does not match the provider digest')
        try:
            with self.env.cr.savepoint():
                record = Inbound.create({
                    'provider_id': provider.id, 'company_id': provider.company_id.id,
                    'message_id': (document.message_id or asp_document_id)[:200],
                    'asp_document_id': asp_document_id[:200], 'correlation_id': document.correlation_id,
                    'sender_endpoint': document.sender_endpoint, 'receiver_endpoint': document.receiver_endpoint,
                    'received_at': document.received_at, 'xml_sha256': digest,
                })
        except psycopg2.errors.UniqueViolation:
            # a concurrent delivery (callback racing a poll) recorded it first
            return Inbound.search([('provider_id', '=', provider.id), '|', ('asp_document_id', '=', asp_document_id),
                                   ('message_id', '=', document.message_id)], limit=1)
        attachment = self.env['ir.attachment'].sudo().create({
            'name': 'inbound-%s.xml' % record.id, 'raw': document.xml, 'mimetype': 'application/xml',
            'res_model': record._name, 'res_id': record.id, 'company_id': record.company_id.id,
        })
        record.write({'xml_attachment_id': attachment.id})
        self.env['ncollection.einvoice.inbound.history'].sudo().create({
            'inbound_id': record.id, 'to_state': 'received', 'event': 'received', 'detail': 'sha256 %s' % digest})
        return record

    # ------------------------------------------------------------------ processing
    def _nc_lock(self):
        try:
            with self.env.cr.savepoint(flush=False):
                self.env.cr.execute('SELECT id FROM ncollection_einvoice_inbound WHERE id = %s FOR UPDATE NOWAIT',
                                    (self.id,))
        except psycopg2.errors.LockNotAvailable:
            return False
        self.invalidate_recordset()
        return True

    def _nc_process(self):
        """Advance ONE intake record as far as it can go. Safe to run any number of times."""
        self.ensure_one()
        record = self.sudo()
        if not record._nc_lock():
            return 'locked by another worker'
        if record.state == 'received':
            record._nc_move('validating', 'validation_started')
        if record.state == 'validating':
            outcome = record._nc_validate()
            if outcome != 'valid':
                return outcome
        if record.state == 'matching':
            record._nc_match_supplier()
        if record.state == 'ready':
            record._nc_create_bill()
        return record.state

    def _nc_validate(self):
        xml = self.xml_attachment_id.raw or b''
        if hashlib.sha256(xml).hexdigest() != self.xml_sha256:
            self._nc_move('validation_failed', 'integrity', 'NCOLLECTION:inbound_integrity',
                          'the archived document does not match its digest')
            return 'validation_failed'
        try:
            facts = inbound_parse.parse(xml)
        except inbound_parse.InboundError as exc:
            self._nc_move('validation_failed', 'unreadable', 'NCOLLECTION:inbound_%s' % exc.code, str(exc))
            return 'validation_failed'
        self._nc_store_facts(facts)
        self_billed = facts['type_code'] in inbound_parse.SELF_BILLED_TYPES
        allowed = (inbound_parse.SELF_BILLED_BY_KIND if self_billed else inbound_parse.PURCHASE_TYPES)[facts['kind']]
        if facts['type_code'] not in allowed:
            self._nc_move('rejected', 'not_a_purchase_document', 'NCOLLECTION:inbound_type',
                          'document type %s is not a tax invoice, credit note or commercial invoice'
                          % facts['type_code'])
            return 'rejected'
        # standard billing: this company is the BUYER; self-billing: the customer issued it, this company is the SELLER
        if not self._nc_addressed_to_us(facts, 'seller' if self_billed else 'buyer'):
            self._nc_move('rejected', 'wrong_recipient', 'NCOLLECTION:inbound_wrong_recipient',
                          'the %s of this document is not this company' % ('seller' if self_billed else 'buyer'))
            return 'rejected'
        problems = inbound_parse.check(facts)
        if problems:
            self._nc_move('validation_failed', 'checks_failed', 'NCOLLECTION:inbound_%s' % problems[0][0],
                          '; '.join(message for _code, message in problems))
            return 'validation_failed'
        official = self._nc_official_check(
            xml, facts['kind'], spec.get_profile(spec.SELF_BILLING_PROFILE_KEY if self_billed else None))
        if official is None:
            number = self.attempt_count + 1
            retry_at = fields.Datetime.now() + timedelta(seconds=backoff.delay(number, self.xml_sha256))
            self.write({'attempt_count': number, 'next_attempt_at': retry_at})
            self._nc_move('validating', 'validator_unavailable', detail='retry at %s' % retry_at)
            return 'validator unavailable'
        if official:
            self._nc_move('validation_failed', 'official_validation_failed', 'OFFICIAL:%s' % official[0],
                          'failed official rules: %s' % ', '.join(official))
            return 'validation_failed'
        key = self._nc_domain_key(facts)
        original = self.search([('domain_key', '=', key), ('state', '!=', 'duplicate'), ('id', '!=', self.id)],
                               limit=1)
        if original:
            self.write({'domain_key': key, 'duplicate_of_id': original.id})
            self._nc_move('duplicate', 'duplicate_document', 'NCOLLECTION:inbound_duplicate',
                          'the same document was received as %s' % original.asp_document_id)
            return 'duplicate'
        try:
            with self.env.cr.savepoint():
                self.write({'domain_key': key})
        except psycopg2.errors.UniqueViolation:
            # another worker claimed the identity at the same moment: this one is the duplicate
            self.invalidate_recordset()
            original = self.search([('domain_key', '=', key), ('state', '!=', 'duplicate')], limit=1)
            self.write({'domain_key': key, 'duplicate_of_id': original.id})
            self._nc_move('duplicate', 'duplicate_document', 'NCOLLECTION:inbound_duplicate', 'concurrent delivery')
            return 'duplicate'
        self._nc_move('matching', 'validated')
        return 'valid'

    def _nc_official_check(self, xml, kind, profile=None):
        """Failed OFFICIAL rule ids ([] = valid), or None when the validator cannot be reached (retried later). Each
        profile is validated by the worker holding ITS rule set (Billing or Self-Billing)."""
        profile = profile or spec.get_profile()
        try:
            client = validator.HttpValidatorClient(os.environ.get(profile.validator_url_env),
                                                   token=os.environ.get(VALIDATOR_TOKEN_ENV))
            outcome = client.validate(xml, kind, profile)
        except validator.ValidatorUnavailable:
            return None
        return sorted({f.rule for f in outcome.failures if f.flag != 'warning'})

    def _nc_store_facts(self, facts):
        seller, buyer = facts['seller'], facts['buyer']
        self.write({
            'document_kind': facts['kind'], 'type_code': facts['type_code'], 'invoice_number': facts['number'],
            'document_uuid': facts['uuid'], 'issue_date': facts['issue_date'], 'due_date': facts['due_date'],
            'currency_code': facts['currency'], 'billing_reference': facts['billing_reference'],
            'line_total': _dec(facts['line_total']), 'tax_exclusive': _dec(facts['tax_exclusive']),
            'tax_total': _dec(facts['tax_total']), 'tax_inclusive': _dec(facts['tax_inclusive']),
            'payable': _dec(facts['payable']), 'seller_name': seller.get('name'), 'seller_trn': seller.get('trn'),
            'seller_endpoint': seller.get('endpoint_id'), 'seller_legal_id': seller.get('legal_id'),
            'buyer_trn': buyer.get('trn'), 'buyer_endpoint': buyer.get('endpoint_id'),
            'buyer_name': buyer.get('name'), 'buyer_legal_id': buyer.get('legal_id'),
            'self_billed': facts['type_code'] in inbound_parse.SELF_BILLED_TYPES,
        })

    def _nc_addressed_to_us(self, facts, role='buyer'):
        """The party in ``role`` must be THIS company, by TRN or by its participant id (TIN). Never by name."""
        party = facts[role]
        partner = self.company_id.partner_id
        ours = {value for value in (partner.nc_trn, partner.nc_tin) if value}
        return bool(ours) and bool({party.get('trn'), party.get('endpoint_id')} & ours)

    def _nc_domain_key(self, facts):
        # the COUNTERPARTY's identity: the supplier, or the customer that self-billed this company
        seller = facts['buyer' if facts['type_code'] in inbound_parse.SELF_BILLED_TYPES else 'seller']
        identity = seller.get('trn') or seller.get('endpoint_id') or seller.get('legal_id')
        raw = '%s|%s|%s|%s' % (self.company_id.id, identity, facts['kind'], facts['number'])
        return hashlib.sha256(raw.encode('utf-8')).hexdigest()

    # ------------------------------------------------------------------ supplier matching
    def _nc_match_supplier(self):
        """Authoritative identifiers only, in order TRN, participant id (TIN), legal registration. Every identifier
        the document carries must point to the SAME commercial partner; a name never decides."""
        Partner = self.env['res.partner'].sudo().with_context(active_test=False)
        scope = ['|', ('company_id', '=', False), ('company_id', '=', self.company_id.id)]
        found = {}
        if self.self_billed:
            identifiers = (self.buyer_trn, self.buyer_endpoint, self.buyer_legal_id)
        else:
            identifiers = (self.seller_trn, self.seller_endpoint, self.seller_legal_id)
        for basis, field, value in zip(('trn', 'tin', 'legal'), ('nc_trn', 'nc_tin', 'company_registry'), identifiers):
            if not value:
                continue
            partners = Partner.search(scope + [(field, '=', value), ('active', '=', True)]).commercial_partner_id
            if partners:
                found[basis] = partners
        candidates = self.env['res.partner'].union(*found.values()) if found else self.env['res.partner']
        if not candidates:
            self._nc_move('unmatched', 'no_supplier', 'NCOLLECTION:inbound_no_supplier',
                          'no supplier of this company carries the identifiers of this document')
        elif len(candidates) > 1:
            self._nc_move('unmatched', 'ambiguous_supplier', 'NCOLLECTION:inbound_ambiguous_supplier',
                          'the identifiers of this document point to %d different suppliers' % len(candidates))
        else:
            self.write({'partner_id': candidates.id, 'match_basis': ','.join(sorted(found))})
            if self.self_billed and self.env['ncollection.einvoice.self.billing.agreement'].sudo()._nc_for(
                    self.company_id, candidates, self.issue_date, direction='receive')['state'] != 'valid':
                # a customer may issue this company's documents only under an agreement (Guidelines V2.0 §10.3)
                self._nc_move('review', 'no_self_billing_agreement', 'NCOLLECTION:inbound_self_billing_agreement',
                              'no active self-billing agreement lets %s issue documents for this company on %s'
                              % (candidates.display_name, self.issue_date))
                return
            self._nc_move('ready', 'supplier_matched', detail='matched on %s' % ','.join(sorted(found)))

    # ------------------------------------------------------------------ the draft vendor bill
    def _nc_create_bill(self):
        facts = inbound_parse.parse(self.xml_attachment_id.raw)
        company = self.company_id
        currency = self.env['res.currency'].sudo().search(
            [('name', '=', facts['currency']), ('active', '=', True)], limit=1)
        if not currency:
            return self._nc_move('review', 'currency_unavailable', 'NCOLLECTION:inbound_currency',
                                 'currency %s is not active in this database' % facts['currency'])
        if facts['allowance_total'] or facts['charge_total'] or facts['prepaid']:
            return self._nc_move('review', 'document_level_amounts', 'NCOLLECTION:inbound_document_amounts',
                                 'document-level allowances, charges or prepaid amounts need a person')
        lines = []
        for line in facts['lines']:
            use = 'sale' if self.self_billed else 'purchase'
            tax = self._nc_purchase_tax(line['tax_category'], line['tax_percent'], use)
            if tax is None:
                return self._nc_move('review', 'tax_unmapped', 'NCOLLECTION:inbound_tax_unmapped',
                                     'no single %s tax is mapped to category %s at %s%%'
                                     % (use, line['tax_category'], line['tax_percent']))
            quantity = line['quantity']
            # the line amount is the truth (it carries any line discount); the unit price follows from it exactly
            price = (line['net_amount'] / quantity) if quantity else line['net_amount']
            lines.append((0, 0, {'name': line['name'] or line['id'] or '/', 'quantity': float(quantity or 1),
                                 'price_unit': float(price), 'tax_ids': [(6, 0, tax.ids)]}))
        if self.self_billed:
            # the customer self-billed this company: a DRAFT customer invoice / credit note, never posted here
            move_type = 'out_invoice' if facts['kind'] == 'invoice' else 'out_refund'
        else:
            move_type = 'in_invoice' if facts['kind'] == 'invoice' else 'in_refund'
        try:
            with self.env.cr.savepoint():
                bill = self.env['account.move'].sudo().with_company(company).create({
                    'move_type': move_type, 'company_id': company.id, 'partner_id': self.partner_id.id,
                    'ref': facts['number'], 'invoice_date': facts['issue_date'],
                    'invoice_date_due': facts['due_date'] or facts['issue_date'], 'currency_id': currency.id,
                    'invoice_line_ids': lines, 'nc_inbound_id': self.id,
                    'narration': 'E-invoice %s (UUID %s) received through %s, provider id %s' % (
                        facts['number'], facts['uuid'] or '-', self.provider_id.name, self.asp_document_id),
                })
                if (abs(bill.amount_untaxed - float(facts['tax_exclusive'])) > float(inbound_parse.TOLERANCE)
                        or abs(bill.amount_tax - float(facts['tax_total'])) > float(inbound_parse.TOLERANCE)):
                    raise _TotalsMismatch('%s / %s' % (bill.amount_untaxed, bill.amount_tax))
        except _TotalsMismatch as exc:
            return self._nc_move('review', 'bill_totals_mismatch', 'NCOLLECTION:inbound_totals',
                                 'the mapped taxes give %s, the document says %s / %s'
                                 % (exc, facts['tax_exclusive'], facts['tax_total']))
        except psycopg2.errors.UniqueViolation:
            self.invalidate_recordset()
            return self  # another worker created the bill for this record
        self.write({'bill_id': bill.id})
        self._nc_move('bill_created', 'draft_bill_created', detail=bill.display_name)
        return self

    def _nc_purchase_tax(self, category, percent, use='purchase'):
        maps = self.env['ncollection.einvoice.tax.map'].sudo().search([
            ('company_id', '=', self.company_id.id), ('pint_category', '=', category),
            ('tax_id.type_tax_use', '=', use), ('tax_id.active', '=', True)])
        rate = Decimal(str(percent or 0))
        taxes = maps.filtered(lambda m: Decimal(str(m.pint_rate if m.pint_category == 'AE' else m.tax_id.amount))
                              .quantize(Decimal('0.01')) == rate.quantize(Decimal('0.01'))).tax_id
        return taxes if len(taxes) == 1 else None

    # ------------------------------------------------------------------ user actions
    def action_reprocess(self):
        """Try again after the master data was corrected (a supplier's TRN, the tax map, a currency)."""
        if not self.env.user.has_group('ncollection_uae_einvoicing.group_einvoice_user'):
            raise UserError(self.env._("Only an e-invoicing user can reprocess a received document."))
        for record in self:
            record.check_access('read')
            if record.state not in REPROCESSABLE:
                raise UserError(self.env._("Only an unmatched, waiting or reviewed document can be reprocessed."))
            engine = record.sudo()
            if engine.state == 'unmatched':
                engine._nc_move('matching', 'reprocess')
            elif engine.state == 'review':
                engine._nc_move('validating', 'reprocess')
            engine.write({'next_attempt_at': fields.Datetime.now()})
            engine._nc_enqueue_process()
        return True

    def action_open_bill(self):
        self.ensure_one()
        return {'type': 'ir.actions.act_window', 'res_model': 'account.move', 'res_id': self.bill_id.id,
                'view_mode': 'form'}

    # ------------------------------------------------------------------ queueing and jobs
    def _nc_enqueue_process(self):
        for record in self:
            eta = record.next_attempt_at
            record.with_delay(channel=CHANNEL, eta=eta if eta and eta > fields.Datetime.now() else None,
                              description='Inbound e-invoice %s' % record.id,
                              identity_key='nc-einv-inbound-%s-%d' % (record.id, record.attempt_count),
                              )._nc_job_process()

    def _nc_job_process(self):
        self.ensure_one()
        record = self.sudo()
        if record.next_attempt_at and record.next_attempt_at > fields.Datetime.now():
            return 'not yet'
        return record._nc_process()

    @api.model
    def _nc_enqueue_fetch(self, provider, asp_document_id):
        self.with_delay(channel=CHANNEL, description='Fetch inbound e-invoice',
                        identity_key='nc-einv-fetch-%s-%s' % (provider.id, asp_document_id)
                        )._nc_job_fetch(provider.id, asp_document_id)

    @api.model
    def _nc_job_fetch(self, provider_id, asp_document_id):
        provider = self.env['ncollection.einvoice.provider'].sudo().browse(provider_id).exists()
        if not provider:
            return 'no provider'
        record = self._nc_receive(provider, asp_document_id)
        return record._nc_process()

    @api.model
    def _nc_job_poll(self, provider_id):
        provider = self.env['ncollection.einvoice.provider'].sudo().browse(provider_id).exists()
        if not provider or not provider.adapter:
            return 'no provider'
        adapter = self.env['ncollection.einvoice']._nc_adapter(provider)
        ids, cursor = adapter.list_inbound(provider.nc_inbound_cursor or None)
        for asp_document_id in ids:
            self._nc_enqueue_fetch(provider, asp_document_id)
        provider.write({'nc_inbound_cursor': cursor, 'nc_inbound_polled_at': fields.Datetime.now()})
        return '%d listed' % len(ids)

    @api.model
    def _nc_cron_enqueue(self):
        """Cron: enqueue inbound polls and due re-validations. Enqueues only; the provider is called by the jobs."""
        now = fields.Datetime.now()
        for provider in self.env['ncollection.einvoice.provider'].sudo().search([('adapter', '!=', False)]):
            if not provider.nc_inbound_polled_at or provider.nc_inbound_polled_at < now - INBOUND_POLL_INTERVAL:
                self.with_delay(channel=CHANNEL, description='Poll inbound e-invoices',
                                identity_key='nc-einv-inbound-poll-%s' % provider.id)._nc_job_poll(provider.id)
        self.sudo().search([('state', '=', 'validating'), ('next_attempt_at', '<=', now)])._nc_enqueue_process()
        return True


class _TotalsMismatch(Exception):
    pass


class EInvoiceInboundHistory(models.Model):
    _name = 'ncollection.einvoice.inbound.history'
    _description = 'UAE inbound e-invoice history (append-only)'
    _order = 'id'

    inbound_id = fields.Many2one('ncollection.einvoice.inbound', required=True, ondelete='restrict', index=True)
    company_id = fields.Many2one(related='inbound_id.company_id', store=True, index=True)
    at = fields.Datetime(default=fields.Datetime.now, readonly=True)
    actor_id = fields.Many2one('res.users', default=lambda self: self.env.user, readonly=True)
    from_state = fields.Char(readonly=True)
    to_state = fields.Char(readonly=True)
    event = fields.Char(readonly=True, required=True)
    detail = fields.Text(readonly=True)

    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.su:
            raise UserError(self.env._("The inbound history is written by the e-invoicing engine only."))
        return super().create(vals_list)

    def write(self, vals):
        if vals:
            raise UserError(self.env._("The inbound history is append-only."))
        return super().write(vals)

    @api.ondelete(at_uninstall=False)
    def _nc_append_only(self):
        raise UserError(self.env._("The inbound history is append-only."))


class EInvoiceProviderInbound(models.Model):
    _inherit = 'ncollection.einvoice.provider'

    nc_inbound_cursor = fields.Char(readonly=True, copy=False, help="The provider's position in its inbound list.")
    nc_inbound_polled_at = fields.Datetime(readonly=True, copy=False)


class AccountMoveInbound(models.Model):
    _inherit = 'account.move'

    nc_inbound_id = fields.Many2one('ncollection.einvoice.inbound', string='Received e-invoice', readonly=True,
                                    copy=False, index=True, ondelete='restrict')

    _nc_inbound_unique = models.UniqueIndex('(nc_inbound_id) WHERE nc_inbound_id IS NOT NULL',
                                            'A received e-invoice has one vendor bill.')
