"""Internal Transfer between two bank / cash journals of one company.

Odoo 19 Community has no ``is_internal_transfer`` any more; its mechanism is the company's Internal Transfer account
(``transfer_account_id``, reconcilable). A transfer here is two ordinary payments, posted by Odoo:
  source:      Dr Internal Transfer account / Cr source outstanding (or liquidity) account
  destination: Dr destination outstanding (or liquidity) account / Cr Internal Transfer account
and the two Internal Transfer lines are reconciled together (Odoo books any exchange difference). Each side then
completes on its own bank exactly like any payment (statement matching or Mark as Paid), so the transfer is never
claimed to have arrived before the receiving journal has it. The pair is cross-referenced through Odoo's own
``paired_internal_transfer_payment_id``.
"""
from psycopg2 import errors

from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError


class InternalTransfer(models.Model):
    _name = 'ncollection.internal.transfer'
    _description = 'Internal Transfer'
    _inherit = ['mail.thread']
    _order = 'date desc, id desc'
    _check_company_auto = True

    name = fields.Char(string='Reference', readonly=True, copy=False, default=lambda self: self.env._('New'))
    state = fields.Selection([('draft', 'Draft'), ('posted', 'Posted')], default='draft', readonly=True,
                             tracking=True, copy=False)
    progress = fields.Selection(
        [('draft', 'Draft'), ('pending', 'Pending on both banks'), ('sent', 'Left the source, not yet received'),
         ('received', 'Received, source not yet confirmed'), ('done', 'Completed')],
        string='Status', compute='_compute_progress')
    company_id = fields.Many2one('res.company', related='source_journal_id.company_id', store=True, index=True)
    source_journal_id = fields.Many2one(
        'account.journal', string='From Journal', required=True, readonly=True, check_company=True,
        domain="[('type', 'in', ('bank', 'cash'))]", tracking=True)
    destination_journal_id = fields.Many2one(
        'account.journal', string='To Journal', required=True, check_company=True, tracking=True,
        domain="[('type', 'in', ('bank', 'cash')), ('id', '!=', source_journal_id), ('company_id', '=', company_id)]")
    source_currency_id = fields.Many2one('res.currency', compute='_compute_currencies', store=True)
    destination_currency_id = fields.Many2one('res.currency', compute='_compute_currencies', store=True)
    amount = fields.Monetary(currency_field='source_currency_id', required=True, tracking=True)
    destination_amount = fields.Monetary(
        string='Amount Received', currency_field='destination_currency_id', compute='_compute_destination_amount',
        store=True, readonly=False, tracking=True,
        help="Only differs from Amount when the two journals have different currencies (today's rate by default).")
    multi_currency = fields.Boolean(compute='_compute_currencies', store=True)
    date = fields.Date(required=True, default=fields.Date.context_today, tracking=True)
    memo = fields.Char(string='Description', tracking=True)
    source_payment_id = fields.Many2one('account.payment', readonly=True, copy=False)
    destination_payment_id = fields.Many2one('account.payment', readonly=True, copy=False)
    source_state = fields.Selection(related='source_payment_id.state', string='Sent')
    destination_state = fields.Selection(related='destination_payment_id.state', string='Received')

    _amount_positive = models.Constraint('CHECK(amount > 0)', 'The amount to transfer must be positive.')

    @api.depends('source_journal_id', 'destination_journal_id')
    def _compute_currencies(self):
        for rec in self:
            company_currency = rec.source_journal_id.company_id.currency_id
            rec.source_currency_id = rec.source_journal_id.currency_id or company_currency
            rec.destination_currency_id = rec.destination_journal_id.currency_id or company_currency
            rec.multi_currency = (bool(rec.destination_journal_id)
                                  and rec.source_currency_id != rec.destination_currency_id)

    @api.depends('amount', 'date', 'source_currency_id', 'destination_currency_id')
    def _compute_destination_amount(self):
        for rec in self:
            if rec.source_currency_id and rec.destination_currency_id and rec.company_id:
                rec.destination_amount = rec.source_currency_id._convert(
                    rec.amount, rec.destination_currency_id, rec.company_id, rec.date or fields.Date.today())
            else:
                rec.destination_amount = rec.amount

    @api.constrains('source_journal_id', 'destination_journal_id', 'destination_amount')
    def _check_journals(self):
        for rec in self:
            if rec.source_journal_id == rec.destination_journal_id:
                raise ValidationError(self.env._("From Journal and To Journal must be different."))
            for journal in rec.source_journal_id | rec.destination_journal_id:
                if journal.type not in ('bank', 'cash'):
                    raise ValidationError(self.env._("%(journal)s is not a bank or cash journal.",
                                                     journal=journal.display_name))
            if rec.destination_journal_id.company_id != rec.source_journal_id.company_id:
                raise ValidationError(self.env._("Both journals must belong to the same company."))
            if rec.destination_amount <= 0:
                raise ValidationError(self.env._("The amount received must be positive."))

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if (vals.get('amount') or 0.0) <= 0:
                raise ValidationError(self.env._("The amount to transfer must be positive."))
        return super().create(vals_list)

    def write(self, vals):
        if 'amount' in vals and (vals['amount'] or 0.0) <= 0:
            raise ValidationError(self.env._("The amount to transfer must be positive."))
        if 'source_journal_id' in vals and any(rec.source_journal_id.id != vals['source_journal_id'] for rec in self):
            raise ValidationError(self.env._("From Journal cannot be changed."))
        if set(vals) - {'message_main_attachment_id', 'state', 'name', 'source_payment_id',
                        'destination_payment_id'} and self.filtered(lambda rec: rec.state != 'draft'):
            raise UserError(self.env._("A posted transfer cannot be edited."))
        return super().write(vals)

    @staticmethod
    def _method_line(journal, direction):
        lines = journal.inbound_payment_method_line_ids if direction == 'inbound' \
            else journal.outbound_payment_method_line_ids
        manual = lines.filtered(lambda line: line.code == 'manual')
        return (manual or lines)[:1]

    def action_post(self):
        """Create and post both sides once. Locked: a second click or a second user is refused, never doubled."""
        self.ensure_one()
        self.env['account.payment'].check_access('create')
        try:
            self.env.cr.execute('SELECT id FROM ncollection_internal_transfer WHERE id = %s FOR UPDATE NOWAIT',
                                [self.id])
        except errors.LockNotAvailable:
            raise UserError(self.env._("This transfer is being posted by another user.")) from None
        self.invalidate_recordset()
        if self.state != 'draft' or self.source_payment_id:
            raise UserError(self.env._("%(transfer)s is already posted.", transfer=self.name))
        company = self.company_id
        if not company.transfer_account_id:
            raise UserError(self.env._("Set the Internal Transfer account in the accounting settings of %(company)s.",
                                       company=company.display_name))
        self._check_journals()
        source_method = self._method_line(self.source_journal_id, 'outbound')
        destination_method = self._method_line(self.destination_journal_id, 'inbound')
        if not source_method or not destination_method:
            raise UserError(self.env._(
                "Both journals need a payment method (Outgoing on %(sender)s, Incoming on %(receiver)s).",
                sender=self.source_journal_id.display_name, receiver=self.destination_journal_id.display_name))
        name = self.env['ir.sequence'].next_by_code('ncollection.internal.transfer') or '/'
        memo = self.memo or self.env._("Internal Transfer %(name)s", name=name)
        Payment = self.env['account.payment']
        common = {'nc_operation': 'transfer', 'nc_transfer_id': self.id, 'date': self.date, 'memo': memo,
                  'partner_id': False}
        source = Payment.create({**common, 'nc_source_journal_id': self.source_journal_id.id,
                                 'journal_id': self.source_journal_id.id, 'payment_type': 'outbound',
                                 'payment_method_line_id': source_method.id, 'amount': self.amount,
                                 'currency_id': self.source_currency_id.id})
        destination = Payment.create({**common, 'nc_source_journal_id': self.destination_journal_id.id,
                                      'journal_id': self.destination_journal_id.id, 'payment_type': 'inbound',
                                      'payment_method_line_id': destination_method.id,
                                      'amount': self.destination_amount,
                                      'currency_id': self.destination_currency_id.id})
        for pay in source | destination:
            pay.destination_account_id = company.transfer_account_id
        (source | destination).action_post()
        source.paired_internal_transfer_payment_id = destination
        destination.paired_internal_transfer_payment_id = source
        transfer_lines = (source.move_id | destination.move_id).line_ids.filtered(
            lambda line: line.account_id == company.transfer_account_id and not line.reconciled)
        if len(transfer_lines) != 2:
            raise UserError(self.env._(
                "The transfer could not be booked on the Internal Transfer account; nothing was saved."))
        transfer_lines.reconcile()
        self.write({'name': name, 'state': 'posted', 'source_payment_id': source.id,
                    'destination_payment_id': destination.id})
        self.message_post(body=self.env._(
            "Posted: %(sender)s from %(from)s, %(receiver)s into %(to)s.",
            sender=source.name, receiver=destination.name,
            **{'from': self.source_journal_id.display_name, 'to': self.destination_journal_id.display_name}))
        return {'type': 'ir.actions.client', 'tag': 'display_notification', 'params': {
            'type': 'success', 'title': self.env._('Internal Transfer posted'),
            'message': self.env._("%(name)s: each side completes when its bank confirms it (or with Mark as Paid).",
                                  name=name), 'next': {'type': 'ir.actions.act_window_close'}}}

    @api.depends('state', 'source_payment_id.state', 'destination_payment_id.state')
    def _compute_progress(self):
        for rec in self:
            if rec.state != 'posted':
                rec.progress = rec.state
            elif rec.source_payment_id.state == 'paid' and rec.destination_payment_id.state == 'paid':
                rec.progress = 'done'
            elif rec.source_payment_id.state == 'paid':
                rec.progress = 'sent'
            elif rec.destination_payment_id.state == 'paid':
                rec.progress = 'received'
            else:
                rec.progress = 'pending'

    def action_open_payments(self):
        self.ensure_one()
        return {'type': 'ir.actions.act_window', 'res_model': 'account.payment', 'name': self.name,
                'view_mode': 'list,form', 'domain': [('nc_transfer_id', '=', self.id)]}
