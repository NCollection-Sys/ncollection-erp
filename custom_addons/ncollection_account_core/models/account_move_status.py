# -*- coding: utf-8 -*-
"""The payment status of an invoice / bill / credit note / receipt, read from Odoo's own accounting.

Nothing the user SEES here is stored and nothing is maintained by hand: Paid Amount, Balance Due and Settlement Status
are computed, at read time, from the native fields Odoo's reconciliation already keeps up to date -- ``amount_total``,
``amount_residual``, ``invoice_date_due`` and the partial reconciliations (``account.partial.reconcile``) of the
document's receivable / payable lines. The ONE stored figure is the invoice-specific excess (``nc_overpaid_amount``,
below): it is what makes "Overpaid" filterable and sortable in the database, and it is recomputed from the same native
facts whenever they change. Odoo's own ``amount_residual`` and every journal entry are never touched.

    Overdue         posted, an amount still due and the due date has passed (whether or not part was paid)
    Unpaid          posted, an amount is due, nothing applied yet, not past due
    Partially Paid  posted, something applied, an amount still due, not past due
    Paid in Full    posted, nothing left due, no excess
    Overpaid        posted, nothing left due, and the payment / credit that settled it was applied to THIS document
                    alone and still has an unapplied remainder: that remainder is the document's excess

The order above is the Settlement Status sort order (most urgent first, most settled last; a document with no status
last), decided by a rank in SQL -- never by the translated label.

THE EXCESS, EXACTLY. A payment line that settled this document and still has an unapplied remainder is attributed to
the document only when that line settled NOTHING ELSE. A payment of 1000 applied to invoice A (800) alone leaves 200
that belongs to A. A payment of 1000 applied to A (300) and B (400) leaves 300 that belongs to neither: it is customer
credit, and shown on both it would be counted twice. (Before this attribution, both A and B read "Overpaid".) The
remainder is read in the document's currency (the line's own, or the company-currency remainder converted at the
document date).

Because the excess is real money already received, ``Paid Amount = total - amount due + excess`` and ``Balance Due =
amount due - excess``; the two always add up to the total (1000 paid + (-200) due = 800).

A draft, a cancelled document, a reversed one and a plain journal entry have no payment status (empty).
"""
from odoo import api, fields, models
from odoo.tools import SQL

NC_PAYMENT_STATUSES = [
    ('overdue', 'Overdue'),
    ('unpaid', 'Unpaid'),
    ('partial', 'Partially Paid'),
    ('paid', 'Paid in Full'),
    ('overpaid', 'Overpaid'),
]
_PAY_TERM_TYPES = ('asset_receivable', 'liability_payable')
_INVOICE_TYPES = ('out_invoice', 'out_refund', 'in_invoice', 'in_refund', 'out_receipt', 'in_receipt')
_SORTED_FIELDS = ('nc_amount_paid', 'nc_amount_due', 'nc_payment_status')
# Tax groups of the UAE chart whose supplies are NOT taxable supplies (matched by the external id's suffix, which is
# the same in every company and every language; the group name is translatable)
_NON_TAXABLE_GROUP_SUFFIXES = ('ae_tax_group_exempted', 'ae_tax_group_out_of_scope')


class AccountMove(models.Model):
    _inherit = 'account.move'

    nc_overpaid_amount = fields.Monetary(
        string='Overpaid Amount', compute='_compute_nc_overpaid_amount', store=True, currency_field='currency_id',
        help="The excess already received for THIS document: what the payment or credit that settled it brought "
             "beyond the document's total, when that payment was applied to this document alone (see "
             "models/account_move_status.py). Zero otherwise.")
    nc_amount_paid = fields.Monetary(
        string='Paid Amount', compute='_compute_nc_payment_status', currency_field='currency_id',
        help="What has been received for this document, in its own currency: the amount applied (total minus amount "
             "due) plus any excess. Paid Amount + Balance Due = total.")
    nc_amount_due = fields.Monetary(
        string='Balance Due', compute='_compute_nc_payment_status', currency_field='currency_id',
        help="What is still due, in its own currency (the native residual). NEGATIVE when the document is overpaid: "
             "the excess already received, shown from the settlement's point of view (the native amount due and the "
             "accounting are untouched).")
    nc_payment_status = fields.Selection(
        NC_PAYMENT_STATUSES, string='Settlement Status', compute='_compute_nc_payment_status',
        help="Derived from the accounting (residual, due date and reconciliations); never set by hand. Sorted: "
             "Overdue, Unpaid, Partially Paid, Paid in Full, Overpaid.")
    nc_is_taxable = fields.Boolean(
        string='Taxable', compute='_compute_nc_is_taxable', search='_search_nc_is_taxable',
        help="At least one line carries a tax that makes it a taxable supply: standard-rated or zero-rated VAT. "
             "Exempt, out-of-scope and untaxed lines are not taxable. A zero VAT amount alone does not decide it.")

    # ------------------------------------------------------------------ the excess
    @api.depends('state', 'move_type', 'payment_state', 'currency_id', 'amount_residual', 'amount_total',
                 'line_ids.reconciled', 'line_ids.matched_debit_ids', 'line_ids.matched_credit_ids',
                 'line_ids.matched_debit_ids.debit_move_id.amount_residual',
                 'line_ids.matched_debit_ids.debit_move_id.amount_residual_currency',
                 'line_ids.matched_debit_ids.debit_move_id.reconciled',
                 'line_ids.matched_debit_ids.debit_move_id.matched_credit_ids',
                 'line_ids.matched_credit_ids.credit_move_id.amount_residual',
                 'line_ids.matched_credit_ids.credit_move_id.amount_residual_currency',
                 'line_ids.matched_credit_ids.credit_move_id.reconciled',
                 'line_ids.matched_credit_ids.credit_move_id.matched_debit_ids')
    def _compute_nc_overpaid_amount(self):
        for move in self:
            move.nc_overpaid_amount = move._nc_attributed_excess()

    def _nc_attributed_excess(self):
        """See the module docstring. Read as the superuser: reconciliations are accounting facts, and a stored
        value must not depend on who triggered the recompute."""
        self.ensure_one()
        if (self.state != 'posted' or self.move_type not in _INVOICE_TYPES
                or self.payment_state in ('reversed', 'invoicing_legacy')
                or not self.currency_id.is_zero(self.amount_residual)):
            return 0.0
        move = self.sudo()
        lines = move.line_ids.filtered(lambda line: line.account_id.account_type in _PAY_TERM_TYPES)
        partials = lines.matched_debit_ids | lines.matched_credit_ids
        counterparts = (partials.debit_move_id | partials.credit_move_id) - lines
        excess = 0.0
        for line in counterparts:
            if line.reconciled or (line.company_currency_id.is_zero(line.amount_residual)
                                   and line.currency_id.is_zero(line.amount_residual_currency)):
                continue
            settled = line.matched_debit_ids | line.matched_credit_ids
            if ((settled.debit_move_id | settled.credit_move_id) - line) - lines:
                continue                 # it settled another document too: customer credit, not this one's excess
            if line.currency_id == move.currency_id:
                excess += abs(line.amount_residual_currency)
            else:
                excess += move.company_currency_id._convert(
                    abs(line.amount_residual), move.currency_id, move.company_id, move.invoice_date or move.date)
        return move.currency_id.round(excess)

    # ------------------------------------------------------------------ what the user sees (never stored)
    @api.depends('state', 'move_type', 'amount_total', 'amount_residual', 'invoice_date_due', 'payment_state',
                 'nc_overpaid_amount')
    @api.depends_context('tz')                      # "today" (for Overdue) is the user's day
    def _compute_nc_payment_status(self):
        today = fields.Date.context_today(self)
        for move in self:
            invoice = move.is_invoice(include_receipts=True)
            move.nc_amount_due = move.amount_residual - move.nc_overpaid_amount if invoice else 0.0
            move.nc_amount_paid = (move.amount_total - move.amount_residual + move.nc_overpaid_amount
                                   if invoice else 0.0)
            move.nc_payment_status = move._nc_payment_status_value(today)

    def _nc_payment_status_value(self, today):
        self.ensure_one()
        if (self.state != 'posted' or not self.is_invoice(include_receipts=True)
                or self.payment_state in ('reversed', 'invoicing_legacy')):
            return False
        currency = self.currency_id
        if not currency.is_zero(self.amount_residual):
            if self.invoice_date_due and self.invoice_date_due < today:
                return 'overdue'
            return 'partial' if currency.compare_amounts(self.amount_residual, self.amount_total) < 0 else 'unpaid'
        return 'overpaid' if self.nc_overpaid_amount > 0 else 'paid'

    # ------------------------------------------------------------------ sorting: in the database, from the same facts
    @api.model
    def _order_field_to_sql(self, alias, field_name, direction, nulls, query):
        """Paid Amount, Balance Due and Settlement Status are not columns, so Odoo would refuse to sort them. They are
        expressed here from stored columns only, with the SAME rules as the Python above (the tests compare them), so
        the header sorts the whole filtered result set, page after page, and never the loaded page alone."""
        if field_name not in _SORTED_FIELDS:
            return super()._order_field_to_sql(alias, field_name, direction, nulls, query)
        invoice = SQL("%s.move_type IN %s", SQL.identifier(alias), _INVOICE_TYPES)
        a = SQL.identifier(alias)
        if field_name == 'nc_amount_paid':
            expr = SQL("CASE WHEN %s THEN %s.amount_total - %s.amount_residual + COALESCE(%s.nc_overpaid_amount, 0) "
                       "ELSE 0 END", invoice, a, a, a)
        elif field_name == 'nc_amount_due':
            expr = SQL("CASE WHEN %s THEN %s.amount_residual - COALESCE(%s.nc_overpaid_amount, 0) ELSE 0 END",
                       invoice, a, a)
        else:
            half = SQL("(SELECT COALESCE(c.rounding, 0.01) / 2 FROM res_currency c WHERE c.id = %s.currency_id)", a)
            tz = self.env.context.get('tz') or self.env.user.tz or 'UTC'
            today = SQL("(NOW() AT TIME ZONE %s)::date", tz)
            expr = SQL(
                "CASE WHEN %(a)s.state <> 'posted' OR NOT (%(invoice)s) "
                "          OR %(a)s.payment_state IN ('reversed', 'invoicing_legacy') THEN 9 "
                "     WHEN ABS(%(a)s.amount_residual) >= %(half)s THEN "
                "          CASE WHEN %(a)s.invoice_date_due < %(today)s THEN 0 "
                "               WHEN %(a)s.amount_residual < %(a)s.amount_total - %(half)s THEN 2 ELSE 1 END "
                "     WHEN COALESCE(%(a)s.nc_overpaid_amount, 0) > 0 THEN 4 ELSE 3 END",
                a=a, invoice=invoice, half=half, today=today)
        return SQL("%s %s %s", expr, direction, nulls)

    # ------------------------------------------------------------------ taxable / non-taxable
    def _nc_non_taxable_group_ids(self):
        data = self.env['ir.model.data'].sudo().search([('model', '=', 'account.tax.group')])
        return [rec.res_id for rec in data if rec.name.endswith(_NON_TAXABLE_GROUP_SUFFIXES)]

    def _nc_taxable_line_domain(self):
        groups = self._nc_non_taxable_group_ids()
        tax_domain = [('tax_group_id', 'not in', groups)] if groups else []
        return [('display_type', '=', 'product'), ('tax_ids', 'any', tax_domain)]

    @api.depends('invoice_line_ids.tax_ids.tax_group_id')
    def _compute_nc_is_taxable(self):
        excluded = set(self._nc_non_taxable_group_ids())
        for move in self:
            move.nc_is_taxable = any(
                tax.tax_group_id.id not in excluded
                for line in move.invoice_line_ids if line.display_type == 'product' for tax in line.tax_ids)

    @api.model
    def _search_nc_is_taxable(self, operator, value):
        if operator not in ('=', '!='):
            raise NotImplementedError(self.env._("Taxable can only be searched with Yes or No."))
        taxable = bool(value) == (operator == '=')
        domain = [('move_type', 'in', _INVOICE_TYPES),
                  ('invoice_line_ids', 'any', self._nc_taxable_line_domain())]
        if taxable:
            return domain
        return [('move_type', 'in', _INVOICE_TYPES), '!', ('invoice_line_ids', 'any', self._nc_taxable_line_domain())]
