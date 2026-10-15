# -*- coding: utf-8 -*-
import hashlib

from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError

from . import renderer
from .field_registry import OUTPUT_LANGS

# a payment that can no longer be paid out must not get a cheque
_BLOCKED_PAYMENT_STATES = ('canceled', 'rejected')
DOWNLOAD_WINDOW_MINUTES = 15
_MIN_REASON = 5


class ChequePrintEvent(models.Model):
    """One line of the audit trail of a print job. Append-only: an event is never edited and never deleted."""
    _name = 'ncollection.cheque.print.event'
    _description = 'Cheque print event'
    _order = 'id desc'

    job_id = fields.Many2one('ncollection.cheque.print.job', required=True, ondelete='restrict', index=True,
                             readonly=True)
    kind = fields.Selection(
        [('created', 'Created'), ('print', 'Printed'), ('reprint', 'Reprinted'), ('void', 'Voided'),
         ('cancel', 'Cancelled')], required=True, readonly=True)
    reason = fields.Text(readonly=True)
    user_id = fields.Many2one('res.users', required=True, readonly=True,
                              default=lambda self: self.env.user)
    at = fields.Datetime(string='When', required=True, readonly=True, default=fields.Datetime.now)
    pdf_sha256 = fields.Char(string='PDF fingerprint', readonly=True,
                             help="A hash of the PDF that was produced; the PDF itself is not kept.")
    offset_x_mm = fields.Float(digits=(6, 2), readonly=True)
    offset_y_mm = fields.Float(digits=(6, 2), readonly=True)
    scale_pct = fields.Float(digits=(6, 2), readonly=True)
    warnings = fields.Text(readonly=True)

    # An audit line is append-only: refusing the write outright IS the behaviour, so there is no super() to call.
    def write(self, vals):  # pylint: disable=method-required-super
        raise UserError(self.env._("The print history is append-only and cannot be edited."))

    # refusing a delete IS the audit rule (only the superuser may purge)
    def unlink(self):
        if not self.env.su:
            raise UserError(self.env._("The print history cannot be deleted."))  # pylint: disable=no-raise-unlink
        return super().unlink()


class ChequePrintJob(models.Model):
    """One cheque to print for one payment: a SNAPSHOT of what was printed, and its history.

    The payment stays the source of truth and is never changed by a print job (no state change, no posting). The job
    copies the facts at creation so that what was printed on 14 October is still known after the payment, the partner
    or the template is edited. The account number is NOT copied (last four digits only); a reprint reads it again
    from the
    journal."""
    _name = 'ncollection.cheque.print.job'
    _description = 'Cheque print job'
    _inherit = ['mail.thread']
    _order = 'id desc'

    name = fields.Char(readonly=True, copy=False, default='New')
    payment_id = fields.Many2one('account.payment', required=True, readonly=True, ondelete='restrict', index=True)
    company_id = fields.Many2one('res.company', required=True, readonly=True, index=True,
                                 default=lambda self: self.env.company)
    template_id = fields.Many2one('ncollection.cheque.template', required=True, readonly=True, ondelete='restrict',
                                  tracking=True)
    profile_id = fields.Many2one('ncollection.cheque.printer.profile', readonly=True, ondelete='set null')
    state = fields.Selection(
        [('draft', 'Draft'), ('printed', 'Printed'), ('reprinted', 'Reprinted'), ('void', 'Void'),
         ('cancelled', 'Cancelled')], default='draft', required=True, readonly=True, tracking=True, index=True)
    # ---- the snapshot
    payee = fields.Char(readonly=True)
    currency_id = fields.Many2one('res.currency', readonly=True)
    amount = fields.Monetary(readonly=True, currency_field='currency_id')
    date = fields.Date(string='Payment date', readonly=True)
    output_lang = fields.Selection(
        OUTPUT_LANGS, string='Print language', default='en', required=True, readonly=True,
        help="The language this cheque is printed in: it decides the month name of the date and an automatic amount "
             "in words. It is what was chosen when printing, not the language of the screen.")
    amount_words_en = fields.Char(string='Amount in words (English)', readonly=True)
    amount_words_ar = fields.Char(string='Amount in words (Arabic)', readonly=True)
    reference = fields.Char(readonly=True)
    cheque_number = fields.Char(readonly=True)
    account_last4 = fields.Char(string='Account (last 4)', readonly=True)
    bank_name = fields.Char(readonly=True)
    profile_offset_x_mm = fields.Float(digits=(6, 2), readonly=True)
    profile_offset_y_mm = fields.Float(digits=(6, 2), readonly=True)
    profile_scale_pct = fields.Float(digits=(6, 2), readonly=True, default=100.0)
    template_confirmed = fields.Boolean(string='Template was confirmed', readonly=True)
    template_confirmed_now = fields.Boolean(
        related='template_id.dimensions_confirmed', string='Template confirmed now')
    # ---- the history
    user_id = fields.Many2one('res.users', string='Prepared by', readonly=True, default=lambda self: self.env.user)
    printed_at = fields.Datetime(string='First printed', readonly=True)
    last_printed_at = fields.Datetime(string='Last printed', readonly=True)
    print_count = fields.Integer(readonly=True)
    event_ids = fields.One2many('ncollection.cheque.print.event', 'job_id', string='History', readonly=True)

    _SNAPSHOT = frozenset({
        'payment_id', 'company_id', 'template_id', 'profile_id', 'payee', 'currency_id', 'amount', 'date',
        'output_lang', 'amount_words_en', 'amount_words_ar', 'reference', 'cheque_number', 'account_last4',
        'bank_name',
        'profile_offset_x_mm', 'profile_offset_y_mm', 'profile_scale_pct', 'template_confirmed', 'user_id',
        # the lifecycle moves only through the action_* methods, which write as sudo after their own checks
        'name', 'state', 'print_count', 'printed_at', 'last_printed_at'})

    # ------------------------------------------------------------------ guards
    @api.model
    def _check_can_print(self, payment, template):
        if payment.payment_type != 'outbound':
            raise UserError(self.env._("A cheque is printed for a payment you make (an outbound payment)."))
        if payment.state in _BLOCKED_PAYMENT_STATES:
            raise UserError(self.env._("A cancelled or rejected payment cannot be paid by cheque."))
        if not payment.partner_id:
            raise UserError(self.env._(
                "The payment has no partner, so there is no payee to print. Set the partner on the payment first."))
        if not payment.amount or payment.amount <= 0:
            raise UserError(self.env._("The payment has no amount to print."))
        if not template.active:
            raise UserError(self.env._("The template '%s' is archived.", template.name))
        if template.company_id and template.company_id != payment.company_id:
            raise UserError(self.env._(
                "The template '%(template)s' belongs to another company than the payment.", template=template.name))
        if template.currency_id and template.currency_id != payment.currency_id:
            raise UserError(self.env._(
                "The cheque paper of '%(template)s' is printed for %(paper)s, but the payment is in %(pay)s.",
                template=template.name, paper=template.currency_id.name, pay=payment.currency_id.name))

    @api.model
    def _open_jobs(self, payment):
        return self.search([('payment_id', '=', payment.id), ('state', 'in', ('draft', 'printed', 'reprinted'))])

    # ------------------------------------------------------------------ creation
    @api.model
    def create_for_payment(self, payment, template, profile=None, cheque_number='', lang=None):
        payment.ensure_one()
        template.ensure_one()
        payment.check_access('read')
        self._check_can_print(payment, template)
        open_jobs = self._open_jobs(payment)
        if open_jobs:
            raise UserError(self.env._(
                "A cheque is already prepared for this payment (%(job)s). Reprint it with a reason, or void it first; "
                "a second cheque is never created silently.", job=open_jobs[:1].name))
        raw = self.env['ncollection.cheque.values'].from_payment(payment, cheque_number, lang)
        profile = profile or template.printer_profile_id
        account = (raw['account_number'] or '').replace(' ', '')
        job = self.create({
            'payment_id': payment.id, 'company_id': payment.company_id.id, 'template_id': template.id,
            'profile_id': profile.id or False, 'output_lang': raw['lang'], 'payee': raw['payee'],
            'currency_id': payment.currency_id.id,
            'amount': payment.amount, 'date': payment.date, 'reference': raw['reference'],
            'amount_words_en': raw['amount_words'].get('en'), 'amount_words_ar': raw['amount_words'].get('ar'),
            'cheque_number': raw['cheque_number'], 'account_last4': account[-4:] if account else '',
            'bank_name': raw['bank_name'], 'profile_offset_x_mm': profile.offset_x_mm if profile else 0.0,
            'profile_offset_y_mm': profile.offset_y_mm if profile else 0.0,
            'profile_scale_pct': profile.scale_pct if profile else 100.0,
            'template_confirmed': template.dimensions_confirmed,
        })
        job._log('created')
        return job

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not vals.get('name') or vals['name'] == 'New':
                vals['name'] = self.env['ir.sequence'].next_by_code('ncollection.cheque.print.job') or 'New'
        return super().create(vals_list)

    def write(self, vals):
        if not self.env.su and self._SNAPSHOT & set(vals):
            raise AccessError(self.env._("What was printed is a snapshot and cannot be changed."))
        return super().write(vals)

    # refusing a delete IS the audit rule (only the superuser may purge)
    def unlink(self):
        if not self.env.su:
            raise UserError(self.env._(  # pylint: disable=no-raise-unlink
                "A print job is part of the audit trail and cannot be deleted. Void it instead."))
        return super().unlink()

    # ------------------------------------------------------------------ rendering
    def _raw(self):
        """The typed facts for the renderer, from the SNAPSHOT (plus the account number, read again from the
        journal)."""
        self.ensure_one()
        journal_account = self.payment_id.journal_id.bank_account_id.acc_number or ''
        return {
            'lang': self.output_lang or 'en', 'payee': self.payee or '', 'date': self.date,
            'amount': self.amount,
            'decimals': self.currency_id.decimal_places, 'currency': self.currency_id.name or '',
            'amount_words': {'en': self.amount_words_en or '', 'ar': self.amount_words_ar or ''},
            'reference': self.reference or '', 'cheque_number': self.cheque_number or '',
            'account_number': journal_account, 'bank_name': self.bank_name or '',
        }

    def _profile_dict(self):
        self.ensure_one()
        return {'offset_x_mm': self.profile_offset_x_mm, 'offset_y_mm': self.profile_offset_y_mm,
                'scale_pct': self.profile_scale_pct}

    def _render(self, mode='print', preview=False):
        self.ensure_one()
        template = self.template_id
        return renderer.render_pdf(template._spec(), self._raw(), mode=mode, profile=self._profile_dict(),
                                   background=template._background_bytes(), preview=preview)

    # ------------------------------------------------------------------ the history
    def _log(self, kind, reason=None, pdf=None, warnings=None):
        self.ensure_one()
        event = self.env['ncollection.cheque.print.event'].sudo().create({
            'job_id': self.id, 'kind': kind, 'reason': reason or False, 'user_id': self.env.user.id,
            'pdf_sha256': hashlib.sha256(pdf).hexdigest() if pdf else False,
            'offset_x_mm': self.profile_offset_x_mm, 'offset_y_mm': self.profile_offset_y_mm,
            'scale_pct': self.profile_scale_pct, 'warnings': '\n'.join(warnings) if warnings else False,
        })
        return event

    @staticmethod
    def _download(event):
        # an attachment (download=1) in the SAME tab: the page stays, the file is saved or caught by a download
        # manager, and no empty tab is left behind
        return {'type': 'ir.actions.act_url', 'url': '/ncollection/cheque/download/%d?download=1' % event.id,
                'target': 'self'}

    def _require_reason(self, reason):
        if len((reason or '').strip()) < _MIN_REASON:
            raise UserError(self.env._("Give a reason (at least %s characters).", _MIN_REASON))
        return reason.strip()

    def _can_act(self):
        self.ensure_one()
        self.check_access('read')
        if not self.env.user.has_group('account.group_account_user'):
            raise AccessError(self.env._("Only an accountant can print a cheque."))

    # ------------------------------------------------------------------ the transitions
    def action_print(self):
        """The FIRST print of a draft job. Anything after this is a reprint, and a reprint needs a reason."""
        self.ensure_one()
        self._can_act()
        if self.state != 'draft':
            raise UserError(self.env._("This cheque was already printed. Use Reprint and give a reason."))
        pdf, warnings = self._render('print')
        now = fields.Datetime.now()
        job = self.sudo()
        job.write({'state': 'printed', 'printed_at': now, 'last_printed_at': now, 'print_count': 1})
        event = self._log('print', pdf=pdf, warnings=warnings)
        job.message_post(body=self.env._("Cheque printed by %s.", self.env.user.name))
        return self._download(event)

    def action_reprint(self, reason):
        self.ensure_one()
        self._can_act()
        reason = self._require_reason(reason)
        if self.state not in ('printed', 'reprinted'):
            raise UserError(self.env._("Only a printed cheque can be reprinted."))
        pdf, warnings = self._render('print')
        job = self.sudo()
        job.write({'state': 'reprinted', 'last_printed_at': fields.Datetime.now(),
                   'print_count': self.print_count + 1})
        event = self._log('reprint', reason=reason, pdf=pdf, warnings=warnings)
        job.message_post(body=self.env._("Cheque reprinted by %(user)s. Reason: %(reason)s",
                                         user=self.env.user.name, reason=reason))
        return self._download(event)

    def action_void(self, reason):
        self.ensure_one()
        self._can_act()
        reason = self._require_reason(reason)
        if self.state not in ('printed', 'reprinted'):
            raise UserError(self.env._("Only a printed cheque can be voided; cancel a draft instead."))
        self.sudo().write({'state': 'void'})
        self._log('void', reason=reason)
        self.sudo().message_post(body=self.env._("Cheque voided by %(user)s. Reason: %(reason)s",
                                                 user=self.env.user.name, reason=reason))
        return True

    def action_cancel(self):
        self.ensure_one()
        self._can_act()
        if self.state != 'draft':
            raise UserError(self.env._("Only a draft can be cancelled; void a printed cheque instead."))
        self.sudo().write({'state': 'cancelled'})
        self._log('cancel')
        return True

    # ------------------------------------------------------------------ the buttons on the form
    def action_open_reprint(self):
        return self._reason_wizard('reprint')

    def action_open_void(self):
        return self._reason_wizard('void')

    def _reason_wizard(self, kind):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window', 'res_model': 'ncollection.cheque.reason.wizard', 'view_mode': 'form',
            'target': 'new', 'context': {'default_job_id': self.id, 'default_kind': kind},
            'name': self.env._("Reprint cheque") if kind == 'reprint' else self.env._("Void cheque"),
        }
