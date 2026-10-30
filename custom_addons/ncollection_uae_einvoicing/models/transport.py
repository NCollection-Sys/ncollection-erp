# -*- coding: utf-8 -*-
"""The provider-independent transport engine: submission, retries, polling, callbacks, confirmations.

The ASP is reached ONLY through an adapter (lib/adapter_contract.py); nothing here knows a provider's URL, format or
credentials. What this file owns:

* **One submission per document revision.** ``ncollection.einvoice.submission`` carries the contract's idempotency key
  under a database UNIQUE constraint, so two workers, a double click or a retried job can never create a second legal
  submission of the same revision. The provider receives the same key on every attempt and must answer with the same
  document id (the contract's own rule), so a timeout AFTER a successful send is resolved by sending again.
* **Mutual exclusion.** A job locks its submission row (``FOR UPDATE NOWAIT``) before calling the adapter; a second
  worker that finds the row locked does nothing.
* **Retries.** Only a TRANSIENT failure is retried, with the backoff of lib/backoff.py (exponential, capped,
  Retry-After honoured). After ``MAX_ATTEMPTS`` the submission is a dead letter: final, visible, never retried by
  itself. Credentials or configuration refused: nothing retries until an administrator acts.
* **Events.** Every provider event (callback or poll) is stored once, keyed on (provider, message id), whatever
  arrives twice. An event that cannot apply YET (it arrived before the event it follows) is kept PENDING and applied
  as soon as the document reaches the state it needs; a stale or conflicting one is kept for audit and ignored.
* **Confirmations are the ASP's.** The Tax Data Document and the FTA report are the ASP's acts (Guidelines V2.0 §5.1);
  the engine stores the ASP's confirmation (``fta`` events) and the message level status (``mls`` events) on their own
  state dimensions, never on the business state of the invoice.

Work that waits on a provider runs in queue_job on the ``einvoice`` channel, never in a cron thread
(DESIGN_CRON_AND_QUEUE_TOPOLOGY.md §2): the crons here only ENQUEUE.
"""

import json
import logging
from datetime import timedelta

import psycopg2

from odoo import api, fields, models
from odoo.exceptions import UserError

from ..lib import adapter_contract as contract
from ..lib import backoff, issues

_logger = logging.getLogger(__name__)

CHANNEL = 'root.einvoice'
SERVICE_USER_XMLID = 'ncollection_uae_einvoicing.user_einvoice_service'
POLL_INTERVAL = timedelta(minutes=15)
LEASE = timedelta(minutes=10)

SUBMISSION_FLOW = [
    ('queued', 'Queued'),
    ('in_flight', 'Sending'),
    ('retry_wait', 'Waiting to retry'),
    ('attention', 'Needs an administrator'),
    ('done', 'Accepted by the ASP'),
    ('rejected', 'Rejected by the ASP'),
    ('dead', 'Failed (final)'),
]
OPEN_FLOW = ('queued', 'in_flight', 'retry_wait', 'attention')

# event (kind, status) -> (einvoice dimension, target state). Anything else is stored and ignored.
EVENT_MAP = {
    ('submission', 'accepted'): ('submission_state', 'asp_accepted'),
    ('submission', 'rejected'): ('submission_state', 'asp_rejected'),
    ('exchange', 'delivered'): ('exchange_state', 'delivered'),
    ('exchange', 'rejected'): ('exchange_state', 'rejected_by_buyer_asp'),
    ('exchange', 'report_only'): ('exchange_state', 'report_only'),
    ('fta', 'reported'): ('fta_state', 'reported'),
    ('fta', 'report_failed'): ('fta_state', 'report_failed'),
    ('fta', 'not_applicable'): ('fta_state', 'not_applicable'),
    ('mls', 'AP'): ('mls_state', 'accepted'),
    ('mls', 'AB'): ('mls_state', 'acknowledged'),
    ('mls', 'RE'): ('mls_state', 'rejected'),
    ('buyer_response', 'AB'): ('business_state', 'acknowledged'),
    ('buyer_response', 'IP'): ('business_state', 'in_process'),
    ('buyer_response', 'UQ'): ('business_state', 'under_query'),
    ('buyer_response', 'CA'): ('business_state', 'conditionally_accepted'),
    ('buyer_response', 'AP'): ('business_state', 'accepted'),
    ('buyer_response', 'RE'): ('business_state', 'rejected'),
    ('buyer_response', 'PD'): ('business_state', 'paid'),
}
# a confirmation about the document's journey needs the ASP to have the document first
NEEDS_ACCEPTED = {'exchange_state', 'fta_state', 'mls_state', 'business_state'}
# metadata keys an event may carry onto the e-invoice (reference fields only; never free text into a state)
REFERENCE_KEYS = {'exchange_reference': 'exchange_reference', 'fta_reference': 'fta_reference',
                  'tdd_reference': 'fta_reference'}


def _safe_text(value, limit=500):
    return (value or '')[:limit]


class EInvoiceSubmission(models.Model):
    _name = 'ncollection.einvoice.submission'
    _description = 'UAE e-invoice ASP submission'
    _order = 'id desc'
    _rec_name = 'idempotency_key'

    einvoice_id = fields.Many2one(
        'ncollection.einvoice', required=True, ondelete='restrict', index=True, readonly=True)
    company_id = fields.Many2one(related='einvoice_id.company_id', store=True, index=True)
    provider_id = fields.Many2one('ncollection.einvoice.provider', required=True, ondelete='restrict', readonly=True)
    revision = fields.Integer(required=True, readonly=True)
    xml_sha256 = fields.Char(required=True, readonly=True)
    idempotency_key = fields.Char(required=True, readonly=True, index=True)
    correlation_id = fields.Char(required=True, readonly=True, index=True)
    state = fields.Selection(SUBMISSION_FLOW, default='queued', required=True, readonly=True, index=True)
    attempt_count = fields.Integer(readonly=True)
    next_attempt_at = fields.Datetime(readonly=True, index=True)
    last_attempt_at = fields.Datetime(readonly=True)
    lease_until = fields.Datetime(readonly=True)
    last_polled_at = fields.Datetime(readonly=True)
    asp_document_id = fields.Char(readonly=True, index=True)
    received_at = fields.Char(string='Received by the ASP at', readonly=True)
    duplicate_seen = fields.Boolean(readonly=True, help="The ASP recognised a repeated send of the same key.")
    error_category = fields.Char(readonly=True)
    error_code = fields.Char(readonly=True)
    error_message = fields.Char(readonly=True)
    http_status = fields.Integer(readonly=True)
    attempt_ids = fields.One2many('ncollection.einvoice.submission.attempt', 'submission_id', readonly=True)

    _key_unique = models.Constraint('unique(idempotency_key)', 'A document revision is submitted once.')
    _correlation_unique = models.Constraint('unique(correlation_id)', 'A correlation id identifies one submission.')

    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.su:
            raise UserError(self.env._("Submissions are created by the e-invoicing engine only."))
        return super().create(vals_list)

    def write(self, vals):
        if not self.env.su:
            raise UserError(self.env._("Submissions are written by the e-invoicing engine only."))
        return super().write(vals)

    @api.ondelete(at_uninstall=False)
    def _nc_never_delete(self):
        raise UserError(self.env._("ASP submissions are part of the regulated archive and are never deleted."))


class EInvoiceSubmissionAttempt(models.Model):
    _name = 'ncollection.einvoice.submission.attempt'
    _description = 'UAE e-invoice ASP submission attempt (append-only)'
    _order = 'id'

    submission_id = fields.Many2one('ncollection.einvoice.submission', required=True, ondelete='restrict', index=True)
    company_id = fields.Many2one(related='submission_id.company_id', store=True, index=True)
    at = fields.Datetime(default=fields.Datetime.now, readonly=True)
    number = fields.Integer(readonly=True)
    outcome = fields.Char(readonly=True, required=True)
    error_category = fields.Char(readonly=True)
    error_code = fields.Char(readonly=True)
    http_status = fields.Integer(readonly=True)
    response_sha256 = fields.Char(readonly=True)
    retry_at = fields.Datetime(readonly=True)

    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.su:
            raise UserError(self.env._("Submission attempts are written by the e-invoicing engine only."))
        return super().create(vals_list)

    def write(self, vals):
        if vals:
            raise UserError(self.env._("Submission attempts are append-only."))
        return super().write(vals)

    @api.ondelete(at_uninstall=False)
    def _nc_append_only(self):
        raise UserError(self.env._("Submission attempts are append-only."))


class EInvoiceEvent(models.Model):
    """One provider event, stored exactly once. The payload itself is never stored: its sha256 and the normalised
    fields are, so a forged or replayed event can be proven against the provider's own record."""

    _name = 'ncollection.einvoice.event'
    _description = 'UAE e-invoice provider event'
    _order = 'id'

    provider_id = fields.Many2one('ncollection.einvoice.provider', required=True, ondelete='restrict', index=True)
    company_id = fields.Many2one(related='provider_id.company_id', store=True, index=True)
    source = fields.Selection([('callback', 'Callback'), ('poll', 'Status poll'), ('submit', 'Submission answer')],
                              required=True, readonly=True)
    message_id = fields.Char(required=True, readonly=True)
    kind = fields.Char(required=True, readonly=True)
    status = fields.Char(required=True, readonly=True)
    correlation_id = fields.Char(readonly=True, index=True)
    asp_document_id = fields.Char(readonly=True, index=True)
    occurred_at = fields.Char(readonly=True)
    payload_sha256 = fields.Char(readonly=True)
    metadata_json = fields.Text(readonly=True)
    einvoice_id = fields.Many2one('ncollection.einvoice', readonly=True, ondelete='restrict', index=True)
    state = fields.Selection(
        [('pending', 'Waiting for an earlier event'), ('applied', 'Applied'), ('ignored', 'Kept, not applied'),
         ('orphan', 'No matching e-invoice')],
        default='pending', required=True, readonly=True, index=True)
    note = fields.Char(readonly=True)
    applied_at = fields.Datetime(readonly=True)

    _message_unique = models.Constraint('unique(provider_id, message_id)', 'A provider event is recorded once.')

    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.su:
            raise UserError(self.env._("Provider events are recorded by the e-invoicing engine only."))
        return super().create(vals_list)

    def write(self, vals):
        if not self.env.su:
            raise UserError(self.env._("Provider events are recorded by the e-invoicing engine only."))
        return super().write(vals)

    @api.ondelete(at_uninstall=False)
    def _nc_never_delete(self):
        raise UserError(self.env._("Provider events are part of the regulated archive and are never deleted."))


class EInvoiceTransport(models.Model):
    _inherit = 'ncollection.einvoice'

    submission_ids = fields.One2many('ncollection.einvoice.submission', 'einvoice_id', readonly=True)
    event_ids = fields.One2many('ncollection.einvoice.event', 'einvoice_id', readonly=True)
    current_submission_id = fields.Many2one('ncollection.einvoice.submission', compute='_compute_current_submission')
    transport_state = fields.Selection(SUBMISSION_FLOW, related='current_submission_id.state', string='Transport')
    transport_attempts = fields.Integer(related='current_submission_id.attempt_count')
    transport_next_attempt = fields.Datetime(related='current_submission_id.next_attempt_at')
    transport_error = fields.Char(related='current_submission_id.error_message')
    correlation_id = fields.Char(related='current_submission_id.correlation_id')

    @api.depends('submission_ids')
    def _compute_current_submission(self):
        for record in self:
            record.current_submission_id = record.submission_ids[:1]

    # ------------------------------------------------------------------ helpers
    def _nc_service_user(self, company):
        """The inactive service account the adapter runs as: it alone may decrypt a provider credential. Inactive, so
        it can never sign in and holds no plan seat; it is given the document's company so company rules apply."""
        user = self.env.ref(SERVICE_USER_XMLID).sudo()
        if company not in user.company_ids:
            user.write({'company_ids': [(4, company.id)]})
        return user

    def _nc_provider(self):
        self.ensure_one()
        provider = self.env['ncollection.einvoice.provider'].sudo().search(
            [('company_id', '=', self.company_id.id)], limit=1)
        if not provider or not provider.adapter:
            raise UserError(self.env._("No ASP is configured for %s.", self.company_id.name))
        return provider

    def _nc_adapter(self, provider):
        user = self._nc_service_user(provider.company_id)
        return self.env['ncollection.einvoice.adapter'].with_user(user).with_company(provider.company_id)\
            ._nc_adapter_for(provider)

    # ------------------------------------------------------------------ the user's action
    def action_submit(self):
        """Queue an officially validated e-invoice for its ASP. Idempotent: a revision is queued once."""
        self._nc_require_usable()
        for record in self:
            if record.local_state != 'validated':
                raise UserError(self.env._("Only an officially validated e-invoice can be submitted."))
            if record.submission_state != 'not_submitted':
                raise UserError(self.env._("This e-invoice was already submitted."))
            engine = record.sudo()
            provider = engine._nc_provider()
            request = self.env['ncollection.einvoice.adapter'].sudo()._nc_submission_request(engine)
            existing = self.env['ncollection.einvoice.submission'].sudo().search(
                [('idempotency_key', '=', request.idempotency_key)], limit=1)
            if existing:
                continue
            submission = self.env['ncollection.einvoice.submission'].sudo().create({
                'einvoice_id': record.id, 'provider_id': provider.id, 'revision': record.revision,
                'xml_sha256': request.xml_sha256, 'idempotency_key': request.idempotency_key,
                'correlation_id': request.correlation_id, 'next_attempt_at': fields.Datetime.now(),
            })
            engine._transition('submission_state', 'queued', 'submission_queued',
                               'correlation %s' % submission.correlation_id)
            submission._nc_enqueue()
        return True

    def action_retry_submission(self):
        """A manager's restart of a submission that needs an administrator (credentials, configuration)."""
        if not self.env.user.has_group('ncollection_uae_einvoicing.group_einvoice_manager'):
            raise UserError(self.env._("Only an e-invoicing manager can restart a submission."))
        for record in self:
            submission = record.sudo().current_submission_id
            if submission.state != 'attention':
                raise UserError(self.env._("Only a submission waiting for an administrator can be restarted."))
            submission.sudo().write({'state': 'retry_wait', 'next_attempt_at': fields.Datetime.now()})
            record.sudo()._log('submission_restarted', 'by an administrator')
            submission._nc_enqueue()
        return True

    def action_poll_status(self):
        for record in self:
            submission = record.sudo().current_submission_id
            if not submission.asp_document_id:
                raise UserError(self.env._("The ASP has not acknowledged this document yet."))
            submission._nc_enqueue_poll()
        return True


class EInvoiceSubmissionEngine(models.Model):
    _inherit = 'ncollection.einvoice.submission'

    # ------------------------------------------------------------------ queueing (no network)
    def _nc_enqueue(self):
        for submission in self:
            eta = submission.next_attempt_at
            submission.with_delay(
                channel=CHANNEL, eta=eta if eta and eta > fields.Datetime.now() else None,
                description='E-invoice submit %s' % submission.einvoice_id.name,
                identity_key='nc-einv-submit-%s-%d' % (submission.idempotency_key, submission.attempt_count),
            )._nc_job_submit()

    def _nc_enqueue_poll(self):
        for submission in self:
            submission.with_delay(
                channel=CHANNEL, description='E-invoice status %s' % submission.einvoice_id.name,
                identity_key='nc-einv-poll-%s' % submission.idempotency_key,
            )._nc_job_poll()

    @api.model
    def _nc_cron_enqueue_due(self):
        """Cron: enqueue due retries, expired leases (a worker died mid-send) and status polls. Enqueues only."""
        now = fields.Datetime.now()
        Sub = self.sudo()
        for stale in Sub.search([('state', '=', 'in_flight'), ('lease_until', '<', now)]):
            stale.write({'state': 'retry_wait', 'next_attempt_at': now, 'lease_until': False})
            stale.einvoice_id._log('lease_expired', 'a worker stopped mid-send; the same key is sent again')
        due = Sub.search([('state', 'in', ('queued', 'retry_wait')), ('next_attempt_at', '<=', now)])
        due._nc_enqueue()
        polls = Sub.search([
            ('state', '=', 'done'), ('asp_document_id', '!=', False),
            '|', ('last_polled_at', '=', False), ('last_polled_at', '<', now - POLL_INTERVAL),
            '|', '|', ('einvoice_id.exchange_state', '=', 'pending'), ('einvoice_id.fta_state', '=', 'pending'),
            ('einvoice_id.mls_state', '=', 'pending'),
        ], limit=200)
        polls._nc_enqueue_poll()
        return True

    # ------------------------------------------------------------------ the jobs (these call the provider)
    def _nc_lock(self):
        """Lock this row for the transaction, or return False when another worker holds it."""
        try:
            with self.env.cr.savepoint(flush=False):
                self.env.cr.execute(
                    'SELECT id FROM ncollection_einvoice_submission WHERE id = %s FOR UPDATE NOWAIT', (self.id,))
        except psycopg2.errors.LockNotAvailable:
            return False
        self.invalidate_recordset()
        return True

    def _nc_job_submit(self):
        """Send one submission. Safe to run any number of times, concurrently or after a crash."""
        self.ensure_one()
        sub = self.sudo()
        if not sub._nc_lock():
            return 'locked by another worker'
        now = fields.Datetime.now()
        if sub.state not in ('queued', 'retry_wait') or (sub.next_attempt_at and sub.next_attempt_at > now):
            return 'nothing to do (%s)' % sub.state
        einvoice = sub.einvoice_id
        if einvoice.submission_state == 'transient_failure':
            einvoice._transition('submission_state', 'queued', 'retry')
        number = sub.attempt_count + 1
        sub.write({'state': 'in_flight', 'attempt_count': number, 'last_attempt_at': now, 'lease_until': now + LEASE})
        einvoice._transition('submission_state', 'submitting', 'submitting', 'attempt %d' % number)
        try:
            adapter = einvoice._nc_adapter(sub.provider_id)
            request = self.env['ncollection.einvoice.adapter'].sudo()._nc_submission_request(einvoice)
            request = contract.SubmissionRequest(**{**request.__dict__, 'correlation_id': sub.correlation_id})
            if request.idempotency_key != sub.idempotency_key:
                raise contract.AdapterError(contract.ErrorCategory.PERMANENT, 'document-changed',
                                            'the archived XML is not the one that was queued')
            result = adapter.submit(request)
        except contract.AdapterError as exc:
            result = contract.SubmissionResult(status='failed', correlation_id=sub.correlation_id,
                                               idempotency_key=sub.idempotency_key, error=exc)
        except Exception as exc:  # noqa: BLE001 - an adapter bug is a transient failure, never an unhandled job crash
            _logger.exception('ASP adapter raised during submit of %s', einvoice.name)
            result = contract.SubmissionResult(
                status='failed', correlation_id=sub.correlation_id, idempotency_key=sub.idempotency_key,
                error=contract.AdapterError(contract.ErrorCategory.TRANSIENT, type(exc).__name__, 'adapter failure'))
        return sub._nc_apply_result(result, number)

    def _nc_apply_result(self, result, number):
        sub, einvoice = self, self.einvoice_id
        error = result.error
        attempt = {'submission_id': sub.id, 'number': number, 'response_sha256': result.response_sha256,
                   'error_category': error and error.category.value,
                   'error_code': error and _safe_text(error.code, 64),
                   'http_status': error and error.http_status}
        common = {'lease_until': False, 'error_category': attempt['error_category'],
                  'error_code': attempt['error_code'], 'error_message': error and _safe_text(error.message),
                  'http_status': attempt['http_status']}
        if result.correlation_id != sub.correlation_id or result.idempotency_key != sub.idempotency_key:
            error = contract.AdapterError(contract.ErrorCategory.PERMANENT, 'correlation-mismatch',
                                          'the provider answered for another submission')
            result = contract.SubmissionResult(status='failed', correlation_id=sub.correlation_id,
                                               idempotency_key=sub.idempotency_key, error=error)
            common.update(error_category='permanent', error_code='correlation-mismatch',
                          error_message=error.message)
            attempt.update(error_category='permanent', error_code='correlation-mismatch')
        if result.status == 'accepted' and result.asp_document_id:
            self.env['ncollection.einvoice.submission.attempt'].create({**attempt, 'outcome': 'accepted'})
            sub.write({**common, 'state': 'done', 'asp_document_id': result.asp_document_id,
                       'received_at': result.received_at, 'duplicate_seen': result.duplicate})
            einvoice.write({'asp_reference': result.asp_document_id})
            einvoice._transition('submission_state', 'submitted', 'asp_received',
                                 'document %s%s' % (result.asp_document_id, ' (duplicate send)' if result.duplicate
                                                    else ''), response_digest=result.response_sha256)
            einvoice._transition('submission_state', 'asp_accepted', 'asp_accepted', result.asp_document_id)
            self.env['ncollection.einvoice.event']._nc_reapply_pending(einvoice)
            return 'accepted'
        if result.status == 'rejected':
            self.env['ncollection.einvoice.submission.attempt'].create({**attempt, 'outcome': 'rejected'})
            sub.write({**common, 'state': 'rejected'})
            einvoice._nc_store_findings('asp', [self._nc_issue(error)])
            einvoice._transition('submission_state', 'asp_rejected', 'asp_rejected',
                                 error and str(error) or 'rejected', response_digest=result.response_sha256)
            return 'rejected'
        category = error.category if error else contract.ErrorCategory.PERMANENT
        if category == contract.ErrorCategory.TRANSIENT and not backoff.exhausted(number):
            retry_at = fields.Datetime.now() + timedelta(
                seconds=backoff.delay(number, sub.idempotency_key, error.retry_after))
            self.env['ncollection.einvoice.submission.attempt'].create(
                {**attempt, 'outcome': 'transient', 'retry_at': retry_at})
            sub.write({**common, 'state': 'retry_wait', 'next_attempt_at': retry_at})
            einvoice._transition('submission_state', 'transient_failure', 'transient_failure',
                                 '%s; retry at %s' % (error, retry_at))
            sub._nc_enqueue()
            return 'retry'
        if category in (contract.ErrorCategory.AUTHENTICATION, contract.ErrorCategory.CONFIGURATION):
            self.env['ncollection.einvoice.submission.attempt'].create({**attempt, 'outcome': 'attention'})
            sub.write({**common, 'state': 'attention', 'next_attempt_at': False})
            einvoice._transition('submission_state', 'transient_failure', 'needs_administrator', str(error))
            return 'attention'
        self.env['ncollection.einvoice.submission.attempt'].create({**attempt, 'outcome': 'dead'})
        sub.write({**common, 'state': 'dead', 'next_attempt_at': False})
        if einvoice.submission_state == 'submitting':
            einvoice._transition('submission_state', 'permanent_failure', 'permanent_failure', str(error))
        else:
            einvoice._transition('submission_state', 'transient_failure', 'transient_failure', str(error))
            einvoice._transition('submission_state', 'permanent_failure', 'retries_exhausted', str(error))
        return 'dead'

    def _nc_issue(self, error):
        # the provider's own code is DATA in the finding, never a rule id: only OFFICIAL / NCOLLECTION rules exist
        return issues.make(
            'NCOLLECTION:asp_rejected', _safe_text(error.code if error else 'rejected', 64),
            "The ASP refused the document (%(code)s): %(message)s",
            {'code': _safe_text(error.code if error else 'rejected', 64),
             'message': _safe_text(error.message if error else '')})

    def _nc_job_poll(self):
        self.ensure_one()
        sub = self.sudo()
        if not sub.asp_document_id:
            return 'not acknowledged'
        sub.write({'last_polled_at': fields.Datetime.now()})
        try:
            status = sub.einvoice_id._nc_adapter(sub.provider_id).fetch_status(sub.asp_document_id,
                                                                               sub.correlation_id)
        except contract.AdapterError as exc:
            sub.einvoice_id._log('status_poll_failed', str(exc))
            return 'poll failed'
        self.env['ncollection.einvoice.event']._nc_ingest(sub.provider_id, list(status.events), 'poll')
        return 'polled'


class EInvoiceEventEngine(models.Model):
    _inherit = 'ncollection.einvoice.event'

    @api.model
    def _nc_ingest(self, provider, events, source):
        """Store each event once and apply what can be applied. Returns the stored records (duplicates excluded)."""
        stored = self.browse()
        for event in events:
            if not isinstance(event, contract.CallbackEvent) or not event.message_id:
                continue
            Event = self.sudo()
            if Event.search_count([('provider_id', '=', provider.id), ('message_id', '=', event.message_id)]):
                continue  # a duplicate delivery: already recorded, nothing changes
            if event.kind == 'inbound':
                stored |= self._nc_ingest_inbound(provider, event, source)
                continue
            einvoice = self._nc_match(provider, event)
            try:
                with self.env.cr.savepoint():
                    record = Event.create({
                        'provider_id': provider.id, 'source': source, 'message_id': _safe_text(event.message_id, 200),
                        'kind': _safe_text(event.kind, 32), 'status': _safe_text(event.status, 64),
                        'correlation_id': event.correlation_id, 'asp_document_id': event.asp_document_id,
                        'occurred_at': event.occurred_at, 'payload_sha256': event.payload_sha256,
                        'metadata_json': json.dumps(event.metadata or {}, sort_keys=True, default=str)[:20000],
                        'einvoice_id': einvoice.id or False, 'state': 'pending' if einvoice else 'orphan',
                        'note': False if einvoice else 'no e-invoice of this provider has this id',
                    })
            except psycopg2.errors.UniqueViolation:
                continue  # a concurrent delivery of the same event won the race
            stored |= record
            if einvoice:
                record._nc_apply()
                self._nc_reapply_pending(einvoice)
        return stored

    @api.model
    def _nc_ingest_inbound(self, provider, event, source):
        """An announcement that the ASP received a document for this Person: record it, enqueue the fetch. The
        document itself is fetched by the job (it waits on the provider), deduplicated there by its provider id."""
        if not event.asp_document_id:
            return self.browse()
        try:
            with self.env.cr.savepoint():
                record = self.sudo().create({
                    'provider_id': provider.id, 'source': source, 'message_id': _safe_text(event.message_id, 200),
                    'kind': 'inbound', 'status': _safe_text(event.status, 64),
                    'asp_document_id': event.asp_document_id,
                    'occurred_at': event.occurred_at, 'payload_sha256': event.payload_sha256, 'state': 'applied',
                    'applied_at': fields.Datetime.now(), 'note': 'inbound document announced; fetch queued'})
        except psycopg2.errors.UniqueViolation:
            return self.browse()
        self.env['ncollection.einvoice.inbound']._nc_enqueue_fetch(provider, event.asp_document_id)
        return record

    @api.model
    def _nc_match(self, provider, event):
        Sub = self.env['ncollection.einvoice.submission'].sudo()
        domain = [('provider_id', '=', provider.id)]
        sub = Sub.browse()
        if event.correlation_id:
            sub = Sub.search(domain + [('correlation_id', '=', event.correlation_id)], limit=1)
        if not sub and event.asp_document_id:
            sub = Sub.search(domain + [('asp_document_id', '=', event.asp_document_id)], limit=1)
        if sub and event.asp_document_id and sub.asp_document_id and sub.asp_document_id != event.asp_document_id:
            return self.env['ncollection.einvoice']  # ids disagree: never guess which document it is about
        return sub.einvoice_id

    def _nc_apply(self):
        """Apply ONE pending event. Returns True when it changed state or settled (applied/ignored)."""
        self.ensure_one()
        event = self.sudo()
        einvoice = event.einvoice_id
        target = EVENT_MAP.get((event.kind, event.status))
        if not target:
            event.write({'state': 'ignored', 'note': 'unknown event kind or status'})
            return True
        dimension, new_state = target
        if dimension in NEEDS_ACCEPTED and einvoice.submission_state != 'asp_accepted':
            if einvoice.submission_state in ('asp_rejected', 'permanent_failure'):
                event.write({'state': 'ignored', 'note': 'the ASP did not accept the document'})
                return True
            return False  # wait: the ASP's acceptance has not been recorded yet
        current = einvoice[dimension]
        if current == new_state:
            event.write({'state': 'applied', 'applied_at': fields.Datetime.now(), 'note': 'already in this state'})
            return True
        if dimension == 'submission_state' and current in ('not_submitted', 'queued', 'submitting'):
            return False  # the send is still in flight: decide after its answer
        from .einvoice import TRANSITIONS
        allowed = TRANSITIONS[dimension].get(current, set())
        if new_state not in allowed:
            if not allowed:
                event.write({'state': 'ignored', 'note': 'conflicts with the final state %s' % current})
                return True
            return False
        einvoice._transition(dimension, new_state, '%s_%s' % (event.kind, event.status),
                             'event %s' % event.message_id, response_digest=event.payload_sha256)
        meta = json.loads(event.metadata_json or '{}')
        refs = {field: _safe_text(str(meta[key]), 200) for key, field in REFERENCE_KEYS.items() if meta.get(key)}
        if refs:
            einvoice.write(refs)
        event.write({'state': 'applied', 'applied_at': fields.Datetime.now()})
        return True

    @api.model
    def _nc_reapply_pending(self, einvoice):
        """Apply waiting events until no more progress: events that arrived out of order settle in order."""
        for _round in range(10):
            pending = self.sudo().search([('einvoice_id', '=', einvoice.id), ('state', '=', 'pending')], order='id')
            progressed = False
            for event in pending:
                progressed |= event._nc_apply()
            if not progressed:
                break
