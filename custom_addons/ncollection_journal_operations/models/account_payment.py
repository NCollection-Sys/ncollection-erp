"""Payment / Receipt / Internal Transfer side of an ``account.payment`` opened from a bank or cash journal card, and
Mark as Paid. Design: docs/markdown/JOURNAL_OPERATIONS.md.

Nothing here posts, reconciles or changes a state by itself: the payment is posted by Odoo's ``action_post``, the
settlement is an Odoo statement line, the match is ``account.move.line.reconcile()``, and Odoo recomputes ``state``
from the residual. ``_synchronize_to_moves`` (engine-guarded) is not touched.
"""
from psycopg2 import errors

from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

OPERATION_DIRECTION = {'payment': 'outbound', 'receipt': 'inbound'}
SETTLEMENT_PREFIX = 'nc-settle-'
# Receivable / payable go through Customer / Supplier; liquidity and credit cards through Internal Transfer.
BLOCKED_ACCOUNT_TYPES = ('asset_receivable', 'liability_payable', 'asset_cash', 'liability_credit_card', 'off_balance')


class AccountPayment(models.Model):
    _inherit = 'account.payment'

    nc_operation = fields.Selection(
        [('payment', 'Payment'), ('receipt', 'Receipt'), ('transfer', 'Internal Transfer')],
        string='Operation', copy=False, readonly=True,
        help="Set when the record is created from a bank / cash journal card. It fixes the direction.")
    nc_source_journal_id = fields.Many2one(
        'account.journal', string='Card Journal', copy=False, readonly=True, index='btree_not_null',
        help="The journal card the operation was opened from. The payment can only ever be in this journal.")
    nc_counterpart_type = fields.Selection(
        [('customer', 'Customer'), ('supplier', 'Supplier'), ('account', 'Account')],
        string='Type', copy=False)
    nc_counterpart_account_id = fields.Many2one(
        'account.account', string='Payee Account', copy=False, check_company=True,
        domain="[('id', 'in', nc_allowed_account_ids)]")
    nc_allowed_account_ids = fields.Many2many(
        'account.account', compute='_compute_nc_allowed_account_ids')
    nc_analytic_distribution = fields.Json(string='Analytic', copy=False)
    analytic_precision = fields.Integer(
        store=False, default=lambda self: self.env['decimal.precision'].precision_get('Percentage Analytic'))
    nc_transfer_id = fields.Many2one('ncollection.internal.transfer', string='Internal Transfer', copy=False,
                                     readonly=True, index='btree_not_null', ondelete='restrict')
    nc_pending_amount = fields.Monetary(
        string='Pending Amount', compute='_compute_nc_pending', currency_field='currency_id',
        help="What still waits on the outstanding account (signed: receipts positive, payments negative).")
    nc_can_mark_paid = fields.Boolean(compute='_compute_nc_pending')
    nc_settlement_line_ids = fields.One2many('account.bank.statement.line', 'nc_settlement_payment_id',
                                             string='Settlements', readonly=True)

    # ------------------------------------------------------------------ computes
    def _nc_forbidden_counterpart_accounts(self):
        """Accounts a direct Account operation may not use: they would self-offset or bypass the proper flow."""
        self.ensure_one()
        journal = self.journal_id
        lines = journal.inbound_payment_method_line_ids | journal.outbound_payment_method_line_ids
        return (journal.default_account_id | lines.payment_account_id | journal.suspense_account_id
                | journal.company_id.transfer_account_id)

    @api.depends('journal_id', 'company_id', 'nc_counterpart_type')
    def _compute_nc_allowed_account_ids(self):
        Account = self.env['account.account']
        for pay in self:
            if pay.nc_counterpart_type != 'account' or not pay.company_id:
                pay.nc_allowed_account_ids = False
                continue
            pay.nc_allowed_account_ids = Account.search([
                *Account._check_company_domain(pay.company_id),
                ('active', '=', True),
                ('account_type', 'not in', BLOCKED_ACCOUNT_TYPES),
                ('id', 'not in', pay._nc_forbidden_counterpart_accounts().ids),
            ])

    def _nc_open_liquidity_lines(self):
        """Posted liquidity lines still waiting on an account other than the journal's own liquidity account.

        By accounting, not by label: Community sets ``paid`` as soon as the reconciled invoices are paid even though
        the money is still on Outstanding Receipts / Payments, so ``paid`` with an open outstanding line is pending
        for the bank too."""
        self.ensure_one()
        if self.state not in ('in_process', 'paid') or not self.move_id or self.move_id.state != 'posted':
            return self.env['account.move.line']
        liquidity, _counterpart, _writeoff = self._seek_for_lines()
        default = self.journal_id.default_account_id
        return liquidity.filtered(lambda line: line.account_id != default and line.account_id.reconcile
                                  and not line.reconciled
                                  and not line.company_currency_id.is_zero(line.amount_residual))

    @api.depends('state', 'move_id.line_ids.amount_residual', 'move_id.line_ids.amount_residual_currency')
    def _compute_nc_pending(self):
        for pay in self:
            lines = pay._nc_open_liquidity_lines()
            same = lines.filtered(lambda line: line.currency_id == pay.currency_id)
            if lines and same == lines:
                pay.nc_pending_amount = sum(lines.mapped('amount_residual_currency'))
            else:
                pay.nc_pending_amount = pay.company_currency_id._convert(
                    sum(lines.mapped('amount_residual')), pay.currency_id, pay.company_id,
                    pay.date or fields.Date.today()) if lines else 0.0
            pay.nc_can_mark_paid = bool(lines) and pay.company_id in self.env.companies

    @api.depends('nc_counterpart_type', 'nc_counterpart_account_id', 'nc_operation')
    def _compute_destination_account_id(self):
        res = super()._compute_destination_account_id()
        for pay in self:
            if pay.nc_operation == 'transfer':
                pay.destination_account_id = pay.company_id.transfer_account_id
            elif pay.nc_counterpart_type == 'account':
                pay.destination_account_id = pay.nc_counterpart_account_id
        return res

    @api.onchange('nc_counterpart_type')
    def _onchange_nc_counterpart_type(self):
        for pay in self:
            if pay.nc_counterpart_type in ('customer', 'supplier'):
                pay.partner_type = pay.nc_counterpart_type
                pay.nc_counterpart_account_id = False
                partner = pay.partner_id
                rank = 'customer_rank' if pay.nc_counterpart_type == 'customer' else 'supplier_rank'
                if partner and not partner[rank]:
                    pay.partner_id = False
            elif pay.nc_counterpart_type == 'account':
                pay.partner_id = False

    def _prepare_move_counterpart_lines(self, default_values):
        lines = super()._prepare_move_counterpart_lines(default_values)
        if self.nc_counterpart_type == 'account' and self.nc_analytic_distribution:
            for line in lines:
                line['analytic_distribution'] = self.nc_analytic_distribution
        return lines

    # --------------------------------------------------------------- integrity
    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            operation = vals.get('nc_operation')
            if not operation:
                continue
            source = vals.get('nc_source_journal_id')
            if not source:
                raise ValidationError(self.env._("An operation from a journal card needs its card journal."))
            vals['journal_id'] = source  # the card decides, whatever the context or the client sent
            if operation in OPERATION_DIRECTION:
                vals['payment_type'] = OPERATION_DIRECTION[operation]
            kind = vals.get('nc_counterpart_type')
            if kind in ('customer', 'supplier'):
                vals['partner_type'] = kind
            elif kind == 'account':
                vals['partner_id'] = False
        payments = super().create(vals_list)
        payments._nc_operations()._nc_check_operation()
        return payments

    _IMMUTABLE = ('nc_operation', 'nc_source_journal_id', 'nc_transfer_id')

    def _nc_operations(self):
        """Every record the operation rules apply to, including a crafted Account type without an operation."""
        return self.filtered(lambda pay: pay.nc_operation or pay.nc_counterpart_type == 'account')

    def write(self, vals):
        # Set at creation only: never added to, moved on or removed from an existing payment, whatever its origin.
        for name in (name for name in self._IMMUTABLE if name in vals):
            for pay in self:
                old = pay[name].id if pay._fields[name].type == 'many2one' else pay[name]
                if (vals[name] or False) != (old or False):
                    raise ValidationError(self.env._("%(field)s cannot be changed on %(payment)s.",
                                                     field=pay._fields[name].string, payment=pay.display_name))
        res = super().write(vals)
        if {'journal_id', 'payment_type', 'partner_id', 'partner_type', 'nc_counterpart_type',
                'nc_counterpart_account_id', 'destination_account_id'} & set(vals):
            self._nc_operations()._nc_check_operation()
        return res

    def _nc_check_operation(self):
        for pay in self:
            if not pay.nc_operation:
                raise ValidationError(self.env._(
                    "An Account payment or receipt is created from a bank or cash journal card only."))
            if pay.journal_id != pay.nc_source_journal_id:
                raise ValidationError(self.env._(
                    "%(payment)s must stay in %(journal)s, the journal it was created from.",
                    payment=pay.display_name, journal=pay.nc_source_journal_id.display_name))
            if pay.journal_id.type not in ('bank', 'cash'):
                raise ValidationError(self.env._("Journal card operations exist only for bank and cash journals."))
            expected = OPERATION_DIRECTION.get(pay.nc_operation)
            if expected and pay.payment_type != expected:
                operation = dict(pay._fields['nc_operation'].selection)[pay.nc_operation]
                raise ValidationError(self.env._("A %(operation)s cannot change direction.", operation=operation))
            if pay.nc_operation == 'transfer':
                if not pay.nc_transfer_id:
                    raise ValidationError(self.env._("An internal transfer payment is created by its transfer only."))
                continue
            kind = pay.nc_counterpart_type
            if not kind:
                raise ValidationError(self.env._("Choose the Type: Customer, Supplier or Account."))
            if kind == 'account':
                account = pay.nc_counterpart_account_id
                if pay.partner_id:
                    raise ValidationError(self.env._("An Account operation has no partner."))
                if not account:
                    raise ValidationError(self.env._("Choose the account of this operation."))
                if (account.account_type in BLOCKED_ACCOUNT_TYPES
                        or account in pay._nc_forbidden_counterpart_accounts()
                        or account.company_ids and pay.company_id not in account.company_ids
                        or not account.active):
                    raise ValidationError(self.env._(
                        "%(account)s cannot be used directly: use Customer or Supplier for receivables and payables, "
                        "Internal Transfer for another bank or cash account.", account=account.display_name))
                if pay.destination_account_id != account:
                    raise ValidationError(self.env._("The operation must post against %(account)s.",
                                                     account=account.display_name))
            elif pay.partner_type != kind:
                raise ValidationError(self.env._("The partner type must match the Type."))

    # ------------------------------------------------------------ mark as paid
    def action_nc_mark_as_paid(self):
        """Open the confirmation (date + what will be booked); the settlement itself is ``_nc_mark_as_paid``."""
        self.ensure_one()
        self._nc_check_can_settle()
        return {
            'type': 'ir.actions.act_window',
            'name': self.env._('Mark as Paid'),
            'res_model': 'ncollection.payment.settle',
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_payment_id': self.id},
        }

    def _nc_check_can_settle(self):
        self.ensure_one()
        if not self.env.user.has_group('account.group_account_user'):
            raise AccessError(self.env._("Only an accountant can mark a payment as paid."))
        self.check_access('write')
        self.env['account.bank.statement.line'].check_access('create')
        if self.company_id not in self.env.companies:
            raise AccessError(self.env._("%(payment)s belongs to another company.", payment=self.display_name))
        if self.state not in ('in_process', 'paid'):
            state = dict(self._fields['state']._description_selection(self.env))[self.state]
            raise UserError(self.env._("Only a posted payment can be marked as paid (%(payment)s is %(state)s).",
                                       payment=self.display_name, state=state))
        if not self._nc_open_liquidity_lines():
            raise UserError(self.env._("%(payment)s has nothing left on an outstanding account.",
                                       payment=self.display_name))

    def _nc_lock(self):
        try:
            self.env.cr.execute('SELECT id FROM account_payment WHERE id IN %s FOR UPDATE NOWAIT', [tuple(self.ids)])
        except errors.LockNotAvailable:
            raise UserError(self.env._(
                "This payment is being completed by another user. Reload it in a moment.")) from None
        self.invalidate_recordset()
        self.move_id.line_ids.invalidate_recordset()

    def _nc_settlement_amounts(self, lines, date):
        """(journal-currency amount, foreign currency, foreign amount) of what is left on ``lines``."""
        self.ensure_one()
        journal_currency = self.journal_id.currency_id or self.company_id.currency_id
        currencies = lines.currency_id
        residual_company = sum(lines.mapped('amount_residual'))
        residual_currency = sum(lines.mapped('amount_residual_currency'))
        if len(currencies) == 1 and currencies == journal_currency:
            return residual_currency, False, 0.0
        if journal_currency == self.company_id.currency_id:
            amount = residual_company
        else:
            amount = self.company_id.currency_id._convert(residual_company, journal_currency, self.company_id, date)
        if len(currencies) == 1 and currencies != journal_currency:
            return amount, currencies, residual_currency
        return amount, False, 0.0

    def _nc_mark_as_paid(self, date=None):
        """Complete an In Process payment / receipt: one statement line in its own journal that moves the residual from
        the outstanding account (read from the POSTED line) to the journal's liquidity account, reconciled with the
        payment's outstanding line. Atomic, locked, once per payment (unique_import_id ``nc-settle-<id>``)."""
        self.ensure_one()
        self._nc_lock()
        self._nc_check_can_settle()
        date = date or fields.Date.context_today(self)
        lines = self._nc_open_liquidity_lines()
        if len(lines.account_id) != 1:
            raise UserError(self.env._("%(payment)s waits on several accounts; reconcile it from Reconcile instead.",
                                       payment=self.display_name))
        journal = self.journal_id
        amount, foreign_currency, foreign_amount = self._nc_settlement_amounts(lines, date)
        journal_currency = journal.currency_id or self.company_id.currency_id
        if journal_currency.is_zero(amount):
            raise UserError(self.env._("%(payment)s has nothing left to settle.", payment=self.display_name))
        StLine = self.env['account.bank.statement.line']
        waiting = StLine.search([
            ('journal_id', '=', journal.id), ('is_reconciled', '=', False), ('move_id.state', '=', 'posted'),
            ('amount', '=', amount), ('nc_settlement_payment_id', '=', False)], limit=3)
        if waiting:
            raise UserError(self.env._(
                "The bank already reported %(count)s unmatched transaction(s) of this amount in %(journal)s "
                "(%(refs)s). Match the payment with it in Reconcile, so it is not counted twice.",
                count=len(waiting), journal=journal.display_name,
                refs=', '.join(waiting.mapped(lambda st: st.payment_ref or st.display_name))))
        if StLine.search_count([('unique_import_id', '=', f'{SETTLEMENT_PREFIX}{self.id}')]):
            raise UserError(self.env._("%(payment)s was already marked as paid.", payment=self.display_name))
        label = self.env._("Mark as Paid: %(payment)s", payment=self.name)
        vals = {
            'journal_id': journal.id,
            'date': date,
            'payment_ref': f'{label} — {self.memo}' if self.memo else label,
            'partner_id': self.partner_id.id,
            'amount': amount,
            'counterpart_account_id': lines.account_id.id,
            'unique_import_id': f'{SETTLEMENT_PREFIX}{self.id}',
            'nc_settlement_payment_id': self.id,
        }
        if foreign_currency:
            vals.update(foreign_currency_id=foreign_currency.id, amount_currency=foreign_amount)
        st_line = StLine.create(vals)
        if st_line.move_id.state != 'posted':
            st_line.move_id.action_post()
        counterpart = st_line.move_id.line_ids.filtered(
            lambda line: line.account_id == lines.account_id and not line.reconciled)
        (counterpart | lines).reconcile()
        st_line.move_id.checked = True
        self.invalidate_recordset(['state', 'is_matched', 'is_reconciled'])
        if self._nc_open_liquidity_lines():
            raise UserError(self.env._("%(payment)s could not be fully settled; nothing was saved.",
                                       payment=self.display_name))
        liquidity = st_line.move_id.line_ids.filtered(lambda line: line.account_id == journal.default_account_id)
        self.message_post(body=self.env._(
            "Marked as Paid on %(date)s by %(user)s: %(amount)s moved from %(sender)s to %(target)s "
            "(statement line %(line)s).",
            date=date, user=self.env.user.name, amount=journal_currency.format(abs(amount)),
            sender=lines.account_id.display_name, target=liquidity.account_id.display_name,
            line=st_line.display_name))
        return st_line
