"""Matching workflows the OCA workspace does not have: early-payment discounts, payment batches, duplicate review.

Early-payment discount
    Odoo core computes the discount entries for a payment-term line
    (``_get_invoice_counterpart_amls_for_early_payment_discount``: base / tax / term / exchange lines, honouring the
    payment term's tax treatment). The engine never calls
    it. Here: when every matched document is still inside its discount window at the transaction date, is in the
    transaction's currency, and the transaction pays exactly the discounted total, the workspace offers "Apply early
    payment discount", which books the documents in FULL and adds Odoo's own discount lines. Nothing is computed here
    except the eligibility test; a document outside its window, in another currency or a different amount is never
    discounted (no invented discounts).

Payment batch
    ``ncollection.bank.payment.batch`` groups posted payments that the bank books as ONE transaction. Matching a batch
    adds the outstanding journal items of its payments to the transaction (the engine's own ``add_multiple_lines``);
    payments already reconciled with the bank are refused, never matched twice.

Duplicate review
    Read-only detection with explanations (``ncollection.bank.duplicate.review``); see that model.
"""
import json

from odoo import api, fields, models
from odoo.exceptions import UserError


class AccountBankStatementLine(models.Model):
    _inherit = 'account.bank.statement.line'

    nc_early_discount_available = fields.Boolean(
        string='Early payment discount applies', compute='_compute_nc_early_discount_available',
        help='The matched documents are inside their early-payment discount window and the transaction pays exactly '
             'their discounted total.')
    nc_duplicate_reviewed = fields.Boolean(
        string='Reviewed: not a duplicate', copy=False, readonly=True,
        help='Set by an accountant in the duplicate review; the line is no longer proposed as a duplicate.')

    # ------------------------------------------------------- early discount
    def _nc_discount_candidates(self):
        """(payment-term items, currency) when the pending match qualifies for an early-payment discount, else None."""
        self.ensure_one()
        if self.is_reconciled:
            return None
        # On a transaction's FIRST opening there is no stored match yet: computing reconcile_data_info runs the
        # engine's auto-matching, which STORES its proposal in reconcile_data. Read the info first (that triggers it),
        # then the stored proposal, so the very first opening already sees the proposed documents.
        info = self.reconcile_data_info or {}
        stored = self.reconcile_data
        if isinstance(stored, str):
            stored = json.loads(stored)
        data = ((stored or {}).get('data') if stored else None) or info.get('data', [])
        others = [d for d in data if d.get('kind') == 'other']
        if not others or any(not d.get('id') for d in others):
            return None      # a manual line is already in the match: let the user decide
        items = self.env['account.move.line'].browse([d['id'] for d in others]).exists()
        currency = self.foreign_currency_id or self.currency_id
        if len(items) != len(others) or any(ml.display_type != 'payment_term' for ml in items):
            return None
        for ml in items:
            invoice = ml.move_id
            if not invoice._is_eligible_for_early_payment_discount(currency, self.date):
                return None
            if ml.company_id != self.company_id or ml.reconciled:
                return None
            if not ml.currency_id.is_zero(ml.amount_residual_currency - ml.amount_currency):
                return None  # already partly paid: the discount is only for a payment of the whole term line
        amount = self.amount_currency if self.foreign_currency_id else self.amount
        discounted = sum(items.mapped('discount_amount_currency'))
        if currency.compare_amounts(abs(amount), abs(discounted)) != 0:
            return None
        if currency.compare_amounts(abs(discounted), abs(sum(items.mapped('amount_residual_currency')))) == 0:
            return None      # no discount to apply
        return items, currency

    @api.depends('reconcile_data', 'reconcile_data_info', 'is_reconciled', 'amount', 'date')
    def _compute_nc_early_discount_available(self):
        for st_line in self:
            try:
                st_line.nc_early_discount_available = bool(st_line.id and st_line._nc_discount_candidates())
            except (KeyError, TypeError):
                st_line.nc_early_discount_available = False

    def _nc_data_line(self, vals, reference, currency):
        """One workspace data line (the engine's own format) from Odoo's counterpart values."""
        account = self.env['account.account'].browse(vals['account_id'])
        partner = self.env['res.partner'].browse(vals.get('partner_id')).exists()
        balance = self.company_id.currency_id.round(vals['balance'])
        line_currency = self.env['res.currency'].browse(vals.get('currency_id')) or currency
        return {
            'reference': reference, 'id': False, 'kind': 'other',
            'account_id': [account.id, account.display_name],
            'partner_id': [partner.id, partner.display_name] if partner else False,
            'date': fields.Date.to_string(self.date), 'name': vals.get('name') or self.payment_ref,
            'amount': balance, 'net_amount': balance,
            'debit': balance if balance > 0 else 0.0, 'credit': -balance if balance < 0 else 0.0,
            'currency_id': self.company_id.currency_id.id, 'line_currency_id': line_currency.id,
            'currency_amount': vals.get('amount_currency', 0.0),
            'tax_ids': vals.get('tax_ids', []), 'tax_tag_ids': vals.get('tax_tag_ids', []),
            'tax_repartition_line_id': vals.get('tax_repartition_line_id'), 'group_tax_id': vals.get('group_tax_id'),
            'analytic_distribution': vals.get('analytic_distribution'),
            'nc_early_discount': True,
        }

    def action_nc_apply_early_discount(self):
        self.ensure_one()
        self.check_access('write')
        found = self._nc_discount_candidates()
        if not found:
            raise UserError(self.env._(
                "No early payment discount applies: the matched documents must still be inside their discount period "
                "on %(date)s, be in the transaction's currency, and the transaction must pay exactly their discounted "
                "total.", date=self.date))
        items, currency = found
        info = dict(self.reconcile_data_info)
        aux = info.get('reconcile_auxiliary_id', 1)
        data = []
        for line in info['data']:
            if line.get('kind') == 'other' and line.get('id') in items.ids:
                ml = items.browse(line['id'])
                full = -ml.amount_residual
                line = dict(line, amount=full, net_amount=full, debit=full if full > 0 else 0.0,
                            credit=-full if full < 0 else 0.0, currency_amount=-ml.amount_residual_currency)
                line.pop('original_amount', None)
                line.pop('original_amount_unsigned', None)
            if line.get('kind') != 'suspense':
                data.append(line)
        liquidity_balance = sum(d['amount'] for d in data if d.get('kind') == 'liquidity')
        open_balance = sum(items.mapped('amount_residual')) - liquidity_balance
        values = items.move_id._get_invoice_counterpart_amls_for_early_payment_discount(
            [{'aml': ml, 'amount_currency': ml.amount_residual_currency, 'balance': ml.amount_residual}
             for ml in items],
            open_balance)
        for key in ('term_lines', 'tax_lines', 'base_lines', 'exchange_lines'):
            for vals in values[key]:
                data.append(self._nc_data_line(vals, f'reconcile_auxiliary;{aux}', currency))
                aux += 1
        self.reconcile_data_info = self._recompute_suspense_line(data, aux, info.get('manual_reference'))
        self.can_reconcile = self.reconcile_data_info.get('can_reconcile', False)
        return True

    def _do_auto_reconcile(self, reconcile_if_possible=True):
        """The engine proposes a document only when its FULL open amount equals the payment. Odoo Enterprise also
        proposes a DISCOUNTED match (documentation: "exact and discounted matches"): a customer paying 1,029 for a
        1,050 invoice with 2% early-payment discount. When the engine found nothing and the transaction has a partner,
        propose
        that partner's one open document whose discounted total equals the payment, if it is still inside its discount
        window at the transaction date. Exactly one candidate or nothing (never an ambiguous guess), and it is only a
        PROPOSAL: nothing is posted here, the user applies the discount and validates."""
        res = super()._do_auto_reconcile(reconcile_if_possible=reconcile_if_possible)
        for st_line in self.filtered(lambda line: not line.is_reconciled and line.partner_id):
            data = (st_line.reconcile_data_info or {}).get('data', [])
            if any(d.get('kind') == 'other' for d in data):
                continue
            candidate = st_line._nc_discounted_candidate()
            if candidate:
                st_line._add_account_move_line(candidate)
        return res

    def _nc_discounted_candidate(self):
        self.ensure_one()
        currency = self.foreign_currency_id or self.currency_id
        amount = self.amount_currency if self.foreign_currency_id else self.amount
        if currency.is_zero(amount):
            return self.env['account.move.line']
        items = self.env['account.move.line'].search([
            ('partner_id', 'child_of', self.partner_id.commercial_partner_id.id),
            ('company_id', '=', self.company_id.id),
            ('display_type', '=', 'payment_term'),
            ('parent_state', '=', 'posted'),
            ('reconciled', '=', False),
            ('currency_id', '=', currency.id),
            ('discount_date', '>=', self.date),
        ])
        matches = items.filtered(
            lambda ml: (ml.amount_currency > 0) == (amount > 0)
            and currency.compare_amounts(abs(ml.discount_amount_currency), abs(amount)) == 0
            and currency.is_zero(ml.amount_residual_currency - ml.amount_currency)
            and ml.move_id._is_eligible_for_early_payment_discount(currency, self.date))
        return matches if len(matches) == 1 else self.env['account.move.line']

    # ---------------------------------------------------------------- batch
    def action_nc_match_batch(self, batch):
        """Add every outstanding item of the batch's payments to this transaction (the engine's own multi-add)."""
        self.ensure_one()
        self.check_access('write')
        batch.ensure_one()
        batch.check_access('read')
        if self.is_reconciled:
            raise UserError(self.env._("The bank transaction %(name)s is already reconciled.", name=self.display_name))
        if batch.journal_id != self.journal_id or batch.company_id != self.company_id:
            raise UserError(self.env._("The batch %(batch)s belongs to another journal or company.", batch=batch.name))
        items = batch._nc_outstanding_items()
        if not items:
            raise UserError(self.env._(
                "The payments of %(batch)s have nothing left to match: they are already reconciled with the bank, or "
                "they were posted straight to the bank account.", batch=batch.name))
        self.add_multiple_lines([('id', 'in', items.ids)])
        return True
