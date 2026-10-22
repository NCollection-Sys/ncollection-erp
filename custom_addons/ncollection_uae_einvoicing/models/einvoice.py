# -*- coding: utf-8 -*-
"""The prepared UAE e-invoice: one record per issued document revision, with five ORTHOGONAL states, an append-only
history
and an immutable archive.

IDENTITY IS NEVER SHARED. Nine different identifiers live in nine different fields and no field stands in for another:
Odoo's invoice number (``move_id.name``), this record's own id (``name``), the PINT invoice number snapshot
(``invoice_number``), the UUID, the seller TIN, the endpoint, the participant id registered by the ASP, the ASP /
exchange references and the FTA reference. The XML builder reads only snapshot fields; network identifiers are written
only by an adapter's result, never by this engine.

STATES ARE NOT "SENT". ``local_state`` (our own checks and the official validation), ``submission_state`` (what the ASP
has done), ``exchange_state`` (the buyer's ASP), ``fta_state`` (tax-authority reporting) and ``business_state`` are
separate and move independently; a document that validated locally says nothing about delivery.

IMMUTABILITY. Once a document leaves ``not_submitted`` it is ``locked``: its snapshot, its identity and its XML can no
longer change; corrections are a credit note or a new invoice (MD 243 Art 6(2)), never an edit. The history is
append-only, the XML attachments cannot be modified or deleted, and the record itself is never deleted.
"""

import json
import uuid as uuid_lib

from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

from ..lib import codelists
from ..lib.identity import FLAG_ORDER, transaction_type_code
from .guards import without_unsafe_defaults

LOCAL_STATES = [
    ('draft', 'Draft'),
    ('ready', 'Ready'),
    ('locally_checked', 'Locally checked'),
    ('validated', 'Validated'),
    ('validation_failed', 'Validation failed'),
]
SUBMISSION_STATES = [
    ('not_submitted', 'Not submitted'),
    ('queued', 'Queued'),
    ('submitting', 'Submitting'),
    ('submitted', 'Submitted'),
    ('asp_accepted', 'ASP accepted'),
    ('asp_rejected', 'ASP rejected'),
    ('transient_failure', 'Failed (will retry)'),
    ('permanent_failure', 'Failed (permanent)'),
]
EXCHANGE_STATES = [
    ('pending', 'Pending'),
    ('delivered', 'Delivered'),
    ('rejected_by_buyer_asp', 'Rejected by buyer ASP'),
    ('report_only', 'Reported to the tax authority only'),
]
FTA_STATES = [
    ('pending', 'Pending'),
    ('reported', 'Reported'),
    ('report_failed', 'Report failed'),
    ('not_applicable', 'Not applicable'),
]
BUSINESS_STATES = [('unknown', 'Unknown')]

# the legal moves of each dimension. Anything else is refused: no state can be skipped or revived by accident.
TRANSITIONS = {
    'local_state': {
        'draft': {'ready'},
        'ready': {'draft', 'locally_checked'},
        'locally_checked': {'draft', 'ready', 'validated', 'validation_failed'},
        'validation_failed': {'draft', 'ready', 'locally_checked'},
        'validated': {'draft'},
    },
    'submission_state': {
        'not_submitted': {'queued'},
        'queued': {'submitting', 'permanent_failure'},
        'submitting': {'submitted', 'transient_failure', 'permanent_failure', 'asp_rejected'},
        'submitted': {'asp_accepted', 'asp_rejected', 'transient_failure'},
        'transient_failure': {'queued', 'permanent_failure'},
        'asp_accepted': set(),
        'asp_rejected': set(),
        'permanent_failure': set(),
    },
    'exchange_state': {
        'pending': {'delivered', 'rejected_by_buyer_asp', 'report_only'},
        'delivered': set(),
        'rejected_by_buyer_asp': set(),
        'report_only': set(),
    },
    'fta_state': {
        'pending': {'reported', 'report_failed', 'not_applicable'},
        'report_failed': {'reported'},
        'reported': set(),
        'not_applicable': set(),
    },
}

# what an e-invoicing USER may type. Every other field is written only by the engine, which runs as ``sudo()`` (the
# engine channel). The channel is ``env.su`` and nothing the client can send: an earlier draft keyed it on a context
# flag, which any RPC caller could set (security review, 2026-10-22).
USER_FIELDS = {'flag_' + name for name in FLAG_ORDER} | {
    'buyer_not_onboarded',
    'billing_frequency',
    'period_start',
    'period_end',
    'beneficiary_id',
    'principal_id',
    'credit_reason',
    'payment_means_override',
}
# a manager's: they move the retention date of a record, so they are restricted and always logged
RETENTION_FIELDS = {'retention_class', 'retention_anchor_date'}
MANAGER_GROUP = 'ncollection_uae_einvoicing.group_einvoice_manager'
LICENCE_MODULE = 'ncollection_uae_einvoicing'

# fields that describe the document and its identity: frozen once the document is locked
LOCKED_FIELDS = (
    USER_FIELDS
    | {
        'move_id',
        'revision',
        'document_kind',
        'type_code',
        'spec_version',
        'customization_id',
        'profile_id',
        'uuid',
        'invoice_number',
        'payload_json',
        'xml_attachment_id',
        'xml_sha256',
        'seller_tin',
        'seller_trn',
        'seller_endpoint_scheme',
        'seller_endpoint_id',
        'buyer_tin',
        'buyer_trn',
        'buyer_endpoint_scheme',
        'buyer_endpoint_id',
        'buyer_endpoint_source',
        'payment_means_code',
        'credit_reason',
        'vat_point_date',
        'reconciliation_state',
        'reconciliation_json',
        'company_id',
    }
    | {'flag_' + name for name in FLAG_ORDER}
)
# fields only an adapter result / the state machine may write
STATE_FIELDS = {'local_state', 'submission_state', 'exchange_state', 'fta_state', 'business_state', 'locked'}
NETWORK_FIELDS = {'asp_participant_id', 'asp_reference', 'exchange_reference', 'fta_reference'}


class EInvoice(models.Model):
    _name = 'ncollection.einvoice'
    _description = 'UAE e-invoice'
    _order = 'id desc'

    name = fields.Char(string='E-invoice record', readonly=True, copy=False, default='/', index=True)
    company_id = fields.Many2one('res.company', required=True, index=True, default=lambda self: self.env.company)
    move_id = fields.Many2one(
        'account.move',
        required=True,
        ondelete='restrict',
        index=True,
        domain="[('move_type', 'in', ('out_invoice', 'out_refund')), ('state', '=', 'posted')]",
    )
    revision = fields.Integer(default=1, readonly=True)
    document_kind = fields.Selection([('invoice', 'Invoice'), ('credit_note', 'Credit note')], required=True)
    direction = fields.Selection([('outbound', 'Outbound')], default='outbound', required=True)  # 'inbound' reserved
    type_code = fields.Char(readonly=True)
    spec_version = fields.Char(string='PINT-AE release', readonly=True)
    customization_id = fields.Char(readonly=True)
    profile_id = fields.Char(readonly=True)

    # --- identity (nine separate fields: see the module docstring)
    uuid = fields.Char(string='UUID', readonly=True, copy=False, index=True)
    invoice_number = fields.Char(string='Invoice number (PINT BT-001 snapshot)', readonly=True)
    seller_tin = fields.Char(readonly=True)
    seller_trn = fields.Char(readonly=True)
    seller_endpoint_scheme = fields.Char(readonly=True)
    seller_endpoint_id = fields.Char(readonly=True)
    buyer_tin = fields.Char(readonly=True)
    buyer_trn = fields.Char(readonly=True)
    buyer_endpoint_scheme = fields.Char(readonly=True)
    buyer_endpoint_id = fields.Char(readonly=True)
    buyer_endpoint_source = fields.Char(readonly=True)
    asp_participant_id = fields.Char(string='Participant ID (ASP)', readonly=True, copy=False)
    asp_reference = fields.Char(readonly=True, copy=False)
    exchange_reference = fields.Char(readonly=True, copy=False)
    fta_reference = fields.Char(readonly=True, copy=False)

    # --- per-document PINT data (editable until the document is locked)
    flag_free_zone = fields.Boolean()
    flag_deemed_supply = fields.Boolean()
    flag_margin_scheme = fields.Boolean()
    flag_summary = fields.Boolean()
    flag_continuous_supply = fields.Boolean()
    flag_agent_billing = fields.Boolean()
    flag_ecommerce = fields.Boolean()
    flag_export = fields.Boolean()
    transaction_type_code = fields.Char(compute='_compute_transaction_type_code', store=True)
    buyer_not_onboarded = fields.Boolean(
        help="The buyer is known NOT to be on UAE e-invoicing. Only then is the predefined 'not onboarded' endpoint "
        "used; "
        "a missing TIN is never taken to mean this."
    )
    billing_frequency = fields.Selection(
        lambda self: [(c, r.get('name', c)) for c, r in sorted(codelists.load('billing_frequency').rows.items())]
    )
    period_start = fields.Date()
    period_end = fields.Date()
    beneficiary_id = fields.Char()
    principal_id = fields.Char()
    credit_reason = fields.Selection(
        lambda self: [(c, r.get('name', c)) for c, r in sorted(codelists.load('credit_reason').rows.items())]
    )
    payment_means_override = fields.Char(help="Overrides the payment-method map for this document (an official code).")
    payment_means_code = fields.Char(readonly=True)
    vat_point_date = fields.Date(readonly=True)

    # --- artefacts
    payload_json = fields.Text(readonly=True)
    xml_attachment_id = fields.Many2one('ir.attachment', readonly=True, ondelete='restrict')
    xml_sha256 = fields.Char(readonly=True)
    reconciliation_state = fields.Selection(
        [('exact', 'Exact'), ('within_tolerance', 'Within official tolerance'), ('blocked', 'Blocked')], readonly=True
    )
    reconciliation_json = fields.Text(readonly=True)

    # --- states
    local_state = fields.Selection(LOCAL_STATES, default='draft', required=True, readonly=True, index=True)
    submission_state = fields.Selection(SUBMISSION_STATES, default='not_submitted', required=True, readonly=True)
    exchange_state = fields.Selection(EXCHANGE_STATES, default='pending', required=True, readonly=True)
    fta_state = fields.Selection(FTA_STATES, default='pending', required=True, readonly=True)
    business_state = fields.Selection(BUSINESS_STATES, default='unknown', required=True, readonly=True)
    locked = fields.Boolean(readonly=True, copy=False)
    headline = fields.Char(compute='_compute_headline')

    line_ids = fields.One2many('ncollection.einvoice.line', 'einvoice_id')
    history_ids = fields.One2many('ncollection.einvoice.history', 'einvoice_id', readonly=True)
    validation_ids = fields.One2many('ncollection.einvoice.validation', 'einvoice_id', readonly=True)

    # --- retention (fail-closed: see retention.py)
    retention_class = fields.Selection(
        [
            ('unclassified', 'Not classified'),
            ('taxable_person', 'Taxable Person'),
            ('other_person', 'Other Person'),
            ('real_estate', 'Real estate record'),
        ],
        default='unclassified',
        required=True,
    )
    retention_anchor_date = fields.Date(
        help="The date the retention period runs from (e.g. the end of the Tax Period). "
        "Set by the accountant; never inferred."
    )
    retain_until = fields.Date(
        compute='_compute_retain_until',
        help="Empty means: NOT purgeable. Computed only from a " "confirmed retention rule and the legal holds.",
    )

    _move_revision_unique = models.Constraint(
        'unique(move_id, revision)', 'This document revision already has an e-invoice.'
    )
    _name_unique = models.Constraint('unique(name)', 'The e-invoice record number must be unique.')
    _uuid_unique = models.Constraint('unique(uuid)', 'The e-invoice UUID must be unique.')

    # ------------------------------------------------------------------ computed
    @api.depends(*['flag_' + name for name in FLAG_ORDER])
    def _compute_transaction_type_code(self):
        for record in self:
            record.transaction_type_code = transaction_type_code({name: record['flag_' + name] for name in FLAG_ORDER})

    @api.depends('local_state', 'submission_state', 'exchange_state', 'fta_state')
    def _compute_headline(self):
        labels = {
            'draft': 'Draft',
            'ready': 'Ready',
            'locally_checked': 'Checked',
            'validated': 'Validated',
            'validation_failed': 'Validation failed',
        }
        for record in self:
            if record.submission_state == 'not_submitted':
                record.headline = labels[record.local_state]
            else:
                record.headline = dict(SUBMISSION_STATES)[record.submission_state]

    @api.depends('retention_class', 'retention_anchor_date', 'company_id')
    def _compute_retain_until(self):
        for record in self:
            record.retain_until = self.env['ncollection.einvoice.retention.rule']._retain_until_for(record)

    # ------------------------------------------------------------------ lifecycle guards
    def _nc_licensed(self):
        """True only when this tenant's licence NAMES the module. Fail-CLOSED, unlike the product's generic licence
        enforcement (which is fail-open for an empty list): a regulated capability that nobody licensed does not run.
        ``ncollection_core``'s Ring 2 never gates ``ncollection.*`` models by name, so this mirrors the licence at the
        ORM here (Standing Rule 4)."""
        config = self.env['ncollection.workspace.config'].sudo().get_config()
        return bool(config) and LICENCE_MODULE in config.get_allowed_module_list()

    def _nc_require_licence(self):
        if not self._nc_licensed():
            raise UserError(self.env._("UAE e-invoicing is not licensed for this workspace."))

    @api.model_create_multi
    def create(self, vals_list):
        self._nc_require_licence()
        creator = self
        if not self.env.su:
            # a client-supplied ``default_<field>`` would fill state, identity or the archive link past the whitelist
            creator = without_unsafe_defaults(self, USER_FIELDS | RETENTION_FIELDS | {'move_id', 'company_id'})
        for vals in vals_list:
            if not self.env.su:
                # the engine, not the caller, supplies state, identity, snapshots, references and archive links
                engine = set(vals) - USER_FIELDS - RETENTION_FIELDS - {'move_id', 'company_id'}
                if engine:
                    raise UserError(
                        self.env._(
                            "These values are set by the e-invoicing engine and cannot be supplied: %s.",
                            ', '.join(sorted(engine)),
                        )
                    )
                if RETENTION_FIELDS & set(vals) and not self.env.user.has_group(MANAGER_GROUP):
                    raise AccessError(self.env._("Only an e-invoicing manager can set the retention class or anchor."))
            move = self.env['account.move'].browse(vals.get('move_id'))
            if not move:
                raise UserError(self.env._("An e-invoice belongs to an accounting document."))
            if move.move_type not in ('out_invoice', 'out_refund'):
                raise UserError(
                    self.env._(
                        "Only customer invoices and customer credit notes can be e-invoiced. A vendor bill is a "
                        "received document, not an outbound e-invoice."
                    )
                )
            if move.state != 'posted':
                raise UserError(self.env._("Only a posted document can be prepared as an e-invoice."))
            if vals.get('company_id') and vals['company_id'] != move.company_id.id:
                raise UserError(self.env._("The e-invoice company must be the company of the accounting document."))
            vals['company_id'] = move.company_id.id  # never the session's active company
            if vals.get('name', '/') == '/':
                vals['name'] = self.env['ir.sequence'].next_by_code('ncollection.einvoice') or '/'
            vals['document_kind'] = 'credit_note' if move.move_type == 'out_refund' else 'invoice'
        records = super(EInvoice, creator).create(vals_list)
        records._log('created')
        return records

    def write(self, vals):
        keys = set(vals)
        self._nc_require_licence()
        if not self.env.su:
            allowed = USER_FIELDS | RETENTION_FIELDS | {'line_ids'}
            if self.env.user.has_group('ncollection_uae_einvoicing.group_einvoice_service'):
                allowed |= NETWORK_FIELDS
            engine = keys - allowed
            if engine & STATE_FIELDS:
                raise UserError(self.env._("States are changed only by the e-invoicing engine, not directly."))
            if engine & NETWORK_FIELDS:
                raise UserError(self.env._("Network references are written only by the provider adapter."))
            if engine:
                raise UserError(
                    self.env._(
                        "These fields are written only by the e-invoicing engine: %s.", ', '.join(sorted(engine))
                    )
                )
            if RETENTION_FIELDS & keys and not self.env.user.has_group(MANAGER_GROUP):
                raise AccessError(self.env._("Only an e-invoicing manager can set the retention class or anchor."))
            if USER_FIELDS & keys and any(record.local_state not in ('draft', 'ready') for record in self):
                raise UserError(
                    self.env._("This e-invoice has already been checked. Prepare it again before changing its data.")
                )
        before = (
            {record.id: (record.retention_class, record.retention_anchor_date) for record in self}
            if RETENTION_FIELDS & keys
            else {}
        )
        for record in self:
            if record.locked and LOCKED_FIELDS & keys:
                raise UserError(
                    self.env._(
                        "This e-invoice has been submitted and is locked: its content cannot change. Issue a credit "
                        "note or a new invoice instead."
                    )
                )
        result = super().write(vals)
        for record in self if RETENTION_FIELDS & keys else self.browse():
            old = before[record.id]
            record._log(
                'retention_changed',
                '%s / %s -> %s / %s' % (old + (record.retention_class, record.retention_anchor_date)),
            )
        return result

    @api.constrains('company_id', 'move_id')
    def _check_company_is_the_documents(self):
        for record in self:
            if record.move_id.company_id != record.company_id:
                raise ValidationError(
                    self.env._("The e-invoice company must be the company of the accounting document.")
                )

    @api.ondelete(at_uninstall=False)
    def _nc_never_delete(self):
        """An e-invoice record is NEVER deleted. From its first row of history it is part of the regulated archive; a
        mistaken draft is simply not prepared further. (The append-only history also references it with a restrict
        key.) Uninstalling the module is the only path that removes the tables, and that is an infrastructure act."""
        raise UserError(self.env._("E-invoice records are part of the regulated archive and are never deleted."))

    # ------------------------------------------------------------------ state machine
    def _transition(self, dimension, new_state, event, detail='', response_digest=None, attachment=None):
        """Move one state dimension along a legal edge and append the history row. Returns self."""
        for record in self:
            current = record[dimension]
            if new_state == current:
                continue
            if new_state not in TRANSITIONS[dimension].get(current, set()):
                raise UserError(
                    self.env._(
                        "Illegal e-invoice transition: %(dimension)s cannot go from %(old)s to %(new)s.",
                        dimension=dimension,
                        old=current,
                        new=new_state,
                    )
                )
            vals = {dimension: new_state}
            if dimension == 'submission_state' and current == 'not_submitted':
                if record.local_state != 'validated':
                    raise UserError(
                        self.env._("Only an officially validated e-invoice can leave the 'not submitted' state.")
                    )
                vals['locked'] = True
            record.sudo().write(vals)
            self.env['ncollection.einvoice.history'].sudo().create(
                {
                    'einvoice_id': record.id,
                    'dimension': dimension,
                    'from_state': current,
                    'to_state': new_state,
                    'event': event,
                    'detail': (detail or '')[:2000],
                    'response_digest': response_digest,
                    'attachment_id': attachment.id if attachment else False,
                }
            )
        return self

    def _log(self, event, detail='', attachment=None):
        for record in self:
            self.env['ncollection.einvoice.history'].sudo().create(
                {
                    'einvoice_id': record.id,
                    'dimension': 'event',
                    'event': event,
                    'detail': (detail or '')[:2000],
                    'attachment_id': attachment.id if attachment else False,
                }
            )

    def _new_uuid(self):
        return str(uuid_lib.uuid4())

    def payload(self):
        self.ensure_one()
        return json.loads(self.payload_json or '{}')


class EInvoiceLine(models.Model):
    """The per-DOCUMENT-LINE toggle (D-13): whether a line of THIS document is an item, a document allowance or a
    charge.
    Never stored on the product (master data) and never on the accounting line (which the engine does not touch)."""

    _name = 'ncollection.einvoice.line'
    _description = 'UAE e-invoice line (per-document toggle)'
    _order = 'einvoice_id, sequence, id'

    einvoice_id = fields.Many2one('ncollection.einvoice', required=True, ondelete='cascade', index=True)
    move_line_id = fields.Many2one('account.move.line', required=True, ondelete='restrict', index=True)
    sequence = fields.Integer()
    name = fields.Char(related='move_line_id.name')
    adjustment = fields.Selection(
        [('allowance', 'Document allowance'), ('charge', 'Document charge')],
        help="Empty = an ordinary item line. A global discount or surcharge is marked here, on this "
        "document, never on the product.",
    )
    reason_code = fields.Char(help="Optional official allowance (UNCL5189) / charge (UNCL7161) reason code.")
    reason = fields.Char()

    _line_unique = models.Constraint('unique(einvoice_id, move_line_id)', 'A line appears once per e-invoice.')

    def _nc_assert_open(self, einvoice):
        if einvoice.locked or einvoice.local_state in ('locally_checked', 'validated'):
            raise UserError(
                self.env._(
                    "The lines of a checked, validated or submitted e-invoice cannot change; prepare it again first."
                )
            )

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            self._nc_assert_open(self.env['ncollection.einvoice'].browse(vals.get('einvoice_id')))
        return super().create(vals_list)

    def write(self, vals):
        if not self.env.su and set(vals) - {'adjustment', 'reason_code', 'reason', 'sequence'}:
            raise UserError(self.env._("Only the document toggle (allowance or charge) of a line can be changed."))
        for line in self:
            self._nc_assert_open(line.einvoice_id)
        return super().write(vals)

    @api.constrains('einvoice_id', 'move_line_id')
    def _check_line_belongs_to_the_document(self):
        for line in self:
            if line.move_line_id.move_id != line.einvoice_id.move_id:
                raise ValidationError(self.env._("The line belongs to another accounting document."))


class EInvoiceHistory(models.Model):
    """Append-only status history: who, when, which state, which event. No secret and no document content ever goes
    in."""

    _name = 'ncollection.einvoice.history'
    _description = 'UAE e-invoice status history (append-only)'
    _order = 'id'

    einvoice_id = fields.Many2one('ncollection.einvoice', required=True, ondelete='restrict', index=True)
    at = fields.Datetime(default=fields.Datetime.now, readonly=True)
    actor_id = fields.Many2one('res.users', default=lambda self: self.env.user, readonly=True)
    dimension = fields.Char(readonly=True)
    from_state = fields.Char(readonly=True)
    to_state = fields.Char(readonly=True)
    event = fields.Char(readonly=True, required=True)
    detail = fields.Text(readonly=True)
    response_digest = fields.Char(readonly=True, help="sha256 of a provider response, never the response itself.")
    attachment_id = fields.Many2one('ir.attachment', readonly=True)

    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.su:  # the engine writes history (as sudo); a user can read it and nothing else
            raise AccessError(self.env._("The e-invoice history is written by the e-invoicing engine only."))
        for vals in vals_list:
            vals.setdefault('actor_id', self.env.user.id)
        return super().create(vals_list)

    def write(self, vals):
        if vals:  # an empty write is a no-op; any real change is refused
            raise UserError(self.env._("The e-invoice history is append-only."))
        return super().write(vals)

    @api.ondelete(at_uninstall=False)
    def _nc_history_is_append_only(self):
        raise UserError(self.env._("The e-invoice history is append-only."))


class EInvoiceValidation(models.Model):
    _name = 'ncollection.einvoice.validation'
    _description = 'UAE e-invoice finding'
    _order = 'einvoice_id, layer, id'

    einvoice_id = fields.Many2one('ncollection.einvoice', required=True, ondelete='cascade', index=True)
    layer = fields.Selection(
        [
            ('readiness', 'Readiness'),
            ('reconciliation', 'Reconciliation'),
            ('official', 'Official validation'),
            ('asp', 'ASP'),
        ],
        required=True,
    )
    severity = fields.Selection([('error', 'Error'), ('warning', 'Warning')], required=True)
    rule = fields.Char(
        required=True, help="OFFICIAL:<id> is a rule of the pinned PINT-AE Schematron; NCOLLECTION:<name> is ours."
    )
    authority = fields.Char(compute='_compute_authority', store=True)
    subject = fields.Char()
    message = fields.Text()
    spec_version = fields.Char()

    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.su:
            raise AccessError(self.env._("Findings are written by the e-invoicing engine only."))
        return super().create(vals_list)

    @api.ondelete(at_uninstall=False)
    def _nc_findings_are_engine_owned(self):
        if not self.env.su:
            raise AccessError(self.env._("Findings are replaced by the e-invoicing engine only."))

    @api.depends('rule')
    def _compute_authority(self):
        for line in self:
            line.authority = (line.rule or '').split(':', 1)[0]
