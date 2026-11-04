# -*- coding: utf-8 -*-
"""DASH-02: what customers OWE, what we hold as customer credit, what we OWE, what we have paid ahead.

The Balance Sheet nets a partner ledger into one figure ("Receivables 60" = 100 invoiced - 40 advance). That is the
right presentation of the ledger and the wrong answer to "how much do customers owe us": the 40 is money we hold for
the customer, not a reduction of the 100 they owe. The dashboards therefore show the open items on each side
separately, and the difference between the two sides is the ledger balance (tested).

The position is **as of a date** (the reporting date), not as of today: a line counts as open on ``date_to`` when it
existed by then and had not yet been reconciled by then (a partial reconciliation after ``date_to`` does not reduce
it). Amounts are in the company currency; Odoo's own partial reconciliations carry the company-currency amount.

Vendor side: a **prepayment** is an unallocated debit that originates from a PAYMENT. A supplier credit note left
unapplied is a different thing (a claim on the supplier arising from a return or a correction), so it is reported
separately and never counted as an advance.
"""
from odoo import api, models

_RECEIVABLE, _PAYABLE = 'asset_receivable', 'liability_payable'


class NcollectionOpenItems(models.AbstractModel):
    _name = 'ncollection.account.open.items'
    _description = 'NCollection Open Receivable / Payable Items As Of A Date'

    @api.model
    def _residuals(self, account_type, date_to, company, include_draft=False):
        """``[(line, signed residual as of date_to)]`` for the open lines of one account type, debit positive."""
        Line = self.env['account.move.line']
        states = ('posted', 'draft') if include_draft else ('posted',)
        domain = [
            ('company_id', '=', company.id),
            ('account_id.account_type', '=', account_type),
            ('parent_state', 'in', states),
            ('date', '<=', date_to),
            '|', ('reconciled', '=', False),
            '|', ('matched_debit_ids.max_date', '>', date_to), ('matched_credit_ids.max_date', '>', date_to),
        ]
        rows = []
        for line in Line.search(domain):
            residual = line.balance
            # partials where this line is the debit side reduce it; where it is the credit side they shrink the credit
            residual -= sum(p.amount for p in line.matched_credit_ids if p.max_date <= date_to)
            residual += sum(p.amount for p in line.matched_debit_ids if p.max_date <= date_to)
            if abs(residual) > 0.00001:
                rows.append((line, residual))
        return rows

    @api.model
    def position(self, account_type, date_to, company, include_draft=False):
        """``(owed, credit)`` of one side as of ``date_to``, both positive, in the company currency.

        Receivable: owed = open invoices, credit = unallocated payments and credit notes held for customers.
        Payable: owed = open bills, credit = unallocated debits (see :meth:`payable_position` for their origin)."""
        rows = self._residuals(account_type, date_to, company, include_draft)
        if account_type == _RECEIVABLE:
            owed = sum(r for _l, r in rows if r > 0)
            credit = -sum(r for _l, r in rows if r < 0)
        else:
            owed = -sum(r for _l, r in rows if r < 0)
            credit = sum(r for _l, r in rows if r > 0)
        return owed, credit

    @api.model
    def payable_position(self, date_to, company, include_draft=False):
        """``(owed, prepayments, credit_notes)``: what we owe suppliers, what we paid ahead, and what suppliers owe us
        on credit notes still unapplied."""
        rows = self._residuals(_PAYABLE, date_to, company, include_draft)
        owed = -sum(r for _l, r in rows if r < 0)
        prepayments = sum(r for line, r in rows if r > 0 and line.payment_id)
        credit_notes = sum(r for line, r in rows if r > 0 and not line.payment_id)
        return owed, prepayments, credit_notes
