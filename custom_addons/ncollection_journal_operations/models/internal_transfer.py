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
from odoo.exceptions import AccessError, UserError, ValidationError


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
    payment_method_line_id = fields.Many2one(
        'account.payment.method.line', string='Payment Method', compute='_compute_payment_method_line_id',
        store=True, readonly=False, precompute=True, tracking=True,
        domain="[('journal_id', '=', source_journal_id), ('payment_type', '=', 'outbound')]",
        help="An outgoing method of the From Journal. A method with an outstanding account leaves the money there "
             "until the bank confirms it (In Progress); a method without one books it straight to the journal "
             "account.")
    destination_payment_method_line_id = fields.Many2one(
        'account.payment.method.line', string='Incoming Payment Method',
        compute='_compute_destination_payment_method_line_id', store=True, readonly=False, precompute=True,
        tracking=True, domain="[('journal_id', '=', destination_journal_id), ('payment_type', '=', 'inbound')]",
        help="An incoming method of the To Journal, used by the receiving payment. A method with an outstanding "
             "account keeps the money there until the receiving bank confirms it (In Progress).")
    nc_status = fields.Selection([('draft', 'Draft'), ('in_progress', 'In Progress'), ('paid', 'Paid')],
                                 string='Transfer Status', compute='_compute_progress')
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
    # ---- Requests 13 / 14: bank fee and exchange difference (docs/markdown/JOURNAL_OPERATIONS.md §6) ----
    fee_amount = fields.Monetary(
        string='Bank Fee', currency_field='source_currency_id', tracking=True,
        help="What the banks charged, in the currency sent. Booked to the company's Bank Fees account, apart from "
             "any exchange gain or loss. Amount Received is what actually arrived.")
    company_currency_id = fields.Many2one(related='company_id.currency_id', string='Company Currency')
    accounting_rate = fields.Float(
        digits=0, compute='_compute_exchange_preview',
        help="Odoo's rate on the transfer date: one unit sent = this many units received.")
    effective_rate = fields.Float(digits=0, compute='_compute_exchange_preview',
                                  help="Amount Received / (Amount sent - Bank Fee).")
    expected_amount = fields.Monetary(
        currency_field='destination_currency_id', compute='_compute_exchange_preview',
        store=True, help="(Amount - Bank Fee) at the accounting rate.")
    difference_amount = fields.Monetary(
        string='Conversion Difference', currency_field='destination_currency_id',
        compute='_compute_exchange_preview', help="Amount Received - Expected Amount.")
    fee_company_amount = fields.Monetary(string='Bank Fee (company currency)', currency_field='company_currency_id',
                                         compute='_compute_exchange_preview')
    exchange_estimate = fields.Monetary(
        string='Exchange Gain / Loss (estimate)', currency_field='company_currency_id',
        compute='_compute_exchange_preview',
        help="Company currency: value received - value sent net of the fee, at the accounting rates. Positive = gain.")
    show_exchange = fields.Boolean(compute='_compute_exchange_preview')
    rate_missing = fields.Boolean(compute='_compute_exchange_preview')
    fee_move_id = fields.Many2one('account.move', string='Bank Fee Entry', readonly=True, copy=False)
    exchange_move_id = fields.Many2one('account.move', string='Exchange Difference Entry', readonly=True, copy=False)
    exchange_amount = fields.Monetary(
        string='Exchange Gain / Loss', currency_field='company_currency_id', readonly=True, copy=False,
        help="Booked by Odoo when the transfer lines were reconciled (company currency). Positive = gain.")
    source_payment_id = fields.Many2one('account.payment', readonly=True, copy=False)
    destination_payment_id = fields.Many2one('account.payment', readonly=True, copy=False)
    source_state = fields.Selection(related='source_payment_id.state', string='Sent')
    destination_state = fields.Selection(related='destination_payment_id.state', string='Received')

    _amount_positive = models.Constraint('CHECK(amount > 0)', 'The amount to transfer must be positive.')

    @api.depends('source_journal_id')
    def _compute_payment_method_line_id(self):
        for rec in self:
            lines = rec.source_journal_id.outbound_payment_method_line_ids.sorted(
                lambda line: (line.sequence, line.id))
            if rec.payment_method_line_id not in lines:
                rec.payment_method_line_id = lines[:1]

    @api.depends('destination_journal_id')
    def _compute_destination_payment_method_line_id(self):
        """The first incoming method of the To Journal in the journal's own order; kept when still valid, replaced
        (or emptied) when the To Journal changes."""
        for rec in self:
            lines = rec.destination_journal_id.inbound_payment_method_line_ids.sorted(
                lambda line: (line.sequence, line.id))
            if rec.destination_payment_method_line_id not in lines:
                rec.destination_payment_method_line_id = lines[:1]

    @api.constrains('payment_method_line_id', 'source_journal_id', 'destination_payment_method_line_id',
                    'destination_journal_id')
    def _check_payment_method_line(self):
        for rec in self:
            line = rec.payment_method_line_id
            if line and (line.journal_id != rec.source_journal_id or line.payment_type != 'outbound'):
                raise ValidationError(self.env._(
                    "%(method)s is not an outgoing payment method of %(journal)s.",
                    method=line.display_name, journal=rec.source_journal_id.display_name))
            line = rec.destination_payment_method_line_id
            if line and (line.journal_id != rec.destination_journal_id or line.payment_type != 'inbound'):
                raise ValidationError(self.env._(
                    "%(method)s is not an incoming payment method of %(journal)s.",
                    method=line.display_name, journal=rec.destination_journal_id.display_name))

    @api.depends('source_journal_id', 'destination_journal_id')
    def _compute_currencies(self):
        for rec in self:
            company_currency = rec.source_journal_id.company_id.currency_id
            rec.source_currency_id = rec.source_journal_id.currency_id or company_currency
            rec.destination_currency_id = rec.destination_journal_id.currency_id or company_currency
            rec.multi_currency = (bool(rec.destination_journal_id)
                                  and rec.source_currency_id != rec.destination_currency_id)

    @api.depends('amount', 'fee_amount', 'date', 'source_currency_id', 'destination_currency_id')
    def _compute_destination_amount(self):
        for rec in self:
            net = rec.amount - (rec.fee_amount or 0.0)
            if rec.source_currency_id and rec.destination_currency_id and rec.company_id:
                rec.destination_amount = rec.source_currency_id._convert(
                    net, rec.destination_currency_id, rec.company_id, rec.date or fields.Date.today())
            else:
                rec.destination_amount = net

    def _nc_rate_missing(self):
        """True when a non-company currency of the transfer has no rate on or before its date: Odoo would then
        convert at 1.0, silently. Refused rather than guessed."""
        self.ensure_one()
        Rate = self.env['res.currency.rate'].sudo()
        company = self.company_id
        for currency in (self.source_currency_id | self.destination_currency_id) - company.currency_id:
            if not Rate.search_count([('currency_id', '=', currency.id), ('name', '<=', self.date),
                                      ('company_id', 'in', (company.root_id.id, company.id, False))], limit=1):
                return True
        return False

    @api.depends('amount', 'fee_amount', 'destination_amount', 'date', 'source_currency_id',
                 'destination_currency_id', 'company_id')
    def _compute_exchange_preview(self):
        """The preview the form shows before confirmation. Stored, and frozen once posted (a posted transfer cannot be
        edited, and no rate is a dependency). What was actually BOOKED is fee_move_id / exchange_amount."""
        for rec in self:
            src, dst, company = rec.source_currency_id, rec.destination_currency_id, rec.company_id
            if not (src and dst and company and rec.date and rec.amount):
                rec.update({'accounting_rate': 0.0, 'effective_rate': 0.0, 'expected_amount': 0.0,
                            'difference_amount': 0.0, 'fee_company_amount': 0.0, 'exchange_estimate': 0.0,
                            'show_exchange': False, 'rate_missing': False})
                continue
            ccy = company.currency_id
            rate = src._get_conversion_rate(src, dst, company, rec.date)
            net = rec.amount - (rec.fee_amount or 0.0)
            expected = dst.round(net * rate)
            sent_net_company = src._convert(net, ccy, company, rec.date)
            received_company = dst._convert(rec.destination_amount, ccy, company, rec.date)
            rec.update({
                'accounting_rate': rate,
                'effective_rate': rec.destination_amount / net if net else 0.0,
                'expected_amount': expected,
                'difference_amount': dst.round(rec.destination_amount - expected),
                'fee_company_amount': src._convert(rec.fee_amount or 0.0, ccy, company, rec.date),
                'exchange_estimate': ccy.round(received_company - sent_net_company) if src != dst else 0.0,
                'show_exchange': src != dst or bool(rec.fee_amount),
                'rate_missing': rec._nc_rate_missing(),
            })

    @api.constrains('source_journal_id', 'destination_journal_id', 'destination_amount', 'amount', 'fee_amount')
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
            fee = rec.fee_amount or 0.0
            if fee < 0 or rec.source_currency_id.compare_amounts(fee, rec.amount) >= 0:
                raise ValidationError(self.env._("The bank fee must be zero or more, and less than the amount sent."))
            if (rec.destination_journal_id and not rec.multi_currency
                    and rec.source_currency_id.compare_amounts(rec.destination_amount + fee, rec.amount)):
                raise ValidationError(self.env._(
                    "In one currency the amount received plus the bank fee must equal the amount sent."))

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
        if set(vals) & self._POSTING_FIELDS:
            # Only action_post sets these (through _nc_set_posted); a context flag would be client-controlled.
            raise UserError(self.env._("A transfer is posted with Confirm Transfer only."))
        if set(vals) - {'message_main_attachment_id'} and self.filtered(lambda rec: rec.state != 'draft'):
            raise UserError(self.env._("A posted transfer cannot be edited."))
        return super().write(vals)

    _POSTING_FIELDS = {'state', 'name', 'source_payment_id', 'destination_payment_id', 'fee_move_id',
                       'exchange_move_id', 'exchange_amount'}

    def _nc_set_posted(self, vals):
        """The one writer of the posting fields, called from action_post after both payments exist."""
        return super(InternalTransfer, self).write(vals)  # pylint: disable=super-method-mismatch

    def action_post(self):
        """Create and post both sides once. Locked: a second click or a second user is refused, never doubled."""
        self.ensure_one()
        if not self.env.user.has_group('account.group_account_user'):
            raise AccessError(self.env._("Only an accountant can post an internal transfer."))
        self.check_access('write')
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
        source_method = self.payment_method_line_id
        if not source_method:
            raise UserError(self.env._("Choose the Payment Method of %(journal)s.",
                                       journal=self.source_journal_id.display_name))
        self._check_payment_method_line()
        destination_method = self.destination_payment_method_line_id
        if not destination_method:
            raise UserError(self.env._("Choose the Incoming Payment Method of %(journal)s.",
                                       journal=self.destination_journal_id.display_name))
        if not source_method or not destination_method:
            raise UserError(self.env._(
                "Both journals need a payment method (Outgoing on %(sender)s, Incoming on %(receiver)s).",
                sender=self.source_journal_id.display_name, receiver=self.destination_journal_id.display_name))
        if self._nc_rate_missing():
            raise UserError(self.env._(
                "No exchange rate on or before %(date)s for this transfer's currencies: add the rate first; a "
                "transfer is never converted at a guessed rate.", date=self.date))
        fee_account = company.nc_bank_fee_account_id
        if self.fee_amount and not fee_account:
            raise UserError(self.env._("Set the Bank Fees account in the accounting settings of %(company)s.",
                                       company=company.display_name))
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
        fee_move = self._nc_post_fee_entry(name, memo, fee_account) if self.fee_amount else self.env['account.move']
        moves = source.move_id | destination.move_id | fee_move
        transfer_lines = moves.line_ids.filtered(
            lambda line: line.account_id == company.transfer_account_id and not line.reconciled)
        if len(transfer_lines) != 2 + len(fee_move):
            raise UserError(self.env._(
                "The transfer could not be booked on the Internal Transfer account; nothing was saved."))
        # What is left on the clearing account in company currency after the fee is the GENUINE exchange difference:
        # booked explicitly to Odoo's own gain / loss accounts in its exchange journal (Odoo does not book it by itself
        # when the two sides are in different foreign currencies), then everything is reconciled together.
        residual = company.currency_id.round(sum(transfer_lines.mapped('balance')))
        exchange_move = self.env['account.move']
        # one currency end to end: any residual is conversion rounding of the fee, never an exchange difference;
        # it is absorbed by the fee entry instead (accounting review M3)
        if not self.multi_currency and fee_move and not company.currency_id.is_zero(residual):
            self._nc_absorb_rounding(fee_move, residual)
            residual = 0.0
        if not company.currency_id.is_zero(residual):
            exchange_move = self._nc_post_exchange_entry(name, memo, residual)
            transfer_lines |= exchange_move.line_ids.filtered(
                lambda line: line.account_id == company.transfer_account_id)
        transfer_lines.reconcile()
        if any(not line.reconciled for line in transfer_lines) or len(transfer_lines.full_reconcile_id) != 1:
            raise UserError(self.env._("The transfer lines did not reconcile completely; nothing was saved."))
        exchange = -residual if exchange_move else 0.0     # positive = gain
        self._nc_set_posted({'name': name, 'state': 'posted', 'source_payment_id': source.id,
                             'destination_payment_id': destination.id, 'fee_move_id': fee_move.id,
                             'exchange_move_id': exchange_move.id, 'exchange_amount': exchange})
        self.message_post(body=self.env._(
            "Posted: %(sender)s from %(from)s, %(receiver)s into %(to)s.",
            sender=source.name, receiver=destination.name,
            **{'from': self.source_journal_id.display_name, 'to': self.destination_journal_id.display_name}))
        return {'type': 'ir.actions.client', 'tag': 'display_notification', 'params': {
            'type': 'success', 'title': self.env._('Internal Transfer posted'),
            'message': self.env._("%(name)s: each side completes when its bank confirms it (or with Mark as Paid).",
                                  name=name), 'next': {'type': 'ir.actions.act_window_close'}}}

    def _nc_post_fee_entry(self, name, memo, fee_account):
        """Dr Bank Fees / Cr Internal Transfer account, in the currency sent, on the transfer date, in the company's
        Miscellaneous journal. Created once (action_post is locked) and linked to the transfer."""
        company = self.company_id
        Journal = self.env['account.journal']
        journal = Journal.search([*Journal._check_company_domain(company), ('type', '=', 'general')], limit=1)
        if not journal:
            raise UserError(self.env._("%(company)s has no Miscellaneous journal for the bank fee entry.",
                                       company=company.display_name))
        src = self.source_currency_id
        balance = src._convert(self.fee_amount, company.currency_id, company, self.date)
        label = self.env._("Bank fee %(name)s", name=name)
        move = self.env['account.move'].create({
            'move_type': 'entry', 'journal_id': journal.id, 'date': self.date, 'ref': memo,
            'line_ids': [
                (0, 0, {'account_id': fee_account.id, 'name': label, 'currency_id': src.id,
                        'amount_currency': self.fee_amount, 'balance': balance}),
                (0, 0, {'account_id': company.transfer_account_id.id, 'name': label, 'currency_id': src.id,
                        'amount_currency': -self.fee_amount, 'balance': -balance}),
            ]})
        move.action_post()
        return move

    def _nc_absorb_rounding(self, fee_move, residual):
        """Shift a rounding residual of a one-currency transfer into its fee entry (still draft-free: the entry is
        re-balanced on both of its lines, so it stays balanced and the clearing account nets to zero)."""
        company = self.company_id
        fee_move.button_draft()
        clearing = fee_move.line_ids.filtered(lambda line: line.account_id == company.transfer_account_id)
        other = fee_move.line_ids - clearing
        fee_move.write({'line_ids': [(1, clearing.id, {'balance': clearing.balance - residual}),
                                     (1, other.id, {'balance': other.balance + residual})]})
        fee_move.action_post()

    def _nc_post_exchange_entry(self, name, memo, residual):
        """Close ``residual`` (company currency, debit-positive) left on the transfer account: a loss when the value
        sent net of the fee exceeds the value received, else a gain. Odoo's exchange journal and accounts only."""
        company = self.company_id
        journal = company.currency_exchange_journal_id
        loss = residual > 0
        account = company.expense_currency_exchange_account_id if loss else company.income_currency_exchange_account_id
        if not journal or not account:
            raise UserError(self.env._(
                "Set the exchange difference journal and the exchange gain / loss accounts of %(company)s in the "
                "accounting settings: this transfer has a genuine exchange difference.", company=company.display_name))
        ccy = company.currency_id
        label = self.env._("Exchange difference %(name)s", name=name)
        move = self.env['account.move'].create({
            'move_type': 'entry', 'journal_id': journal.id, 'date': self.date, 'ref': memo,
            'line_ids': [
                (0, 0, {'account_id': company.transfer_account_id.id, 'name': label, 'currency_id': ccy.id,
                        'amount_currency': -residual, 'balance': -residual}),
                (0, 0, {'account_id': account.id, 'name': label, 'currency_id': ccy.id,
                        'amount_currency': residual, 'balance': residual}),
            ]})
        move.action_post()
        return move

    @api.depends('state', 'source_payment_id.state', 'destination_payment_id.state')
    def _compute_progress(self):
        for rec in self:
            rec.nc_status = 'draft' if rec.state != 'posted' else (
                'paid' if rec.source_payment_id.state == 'paid' and rec.destination_payment_id.state == 'paid'
                else 'in_progress')
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
