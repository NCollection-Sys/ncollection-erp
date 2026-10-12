# -*- coding: utf-8 -*-
"""The "Exclude Zero Balances" option, defined once.

A SEPARATE ``AbstractModel`` (the comparison-mixin pattern): only the reports for which "a row with nothing in it" has
a meaning inherit it, so Cash Flow, the UAE VAT 201 and the executive summaries (fixed statement layouts that must
always print every line and reconcile) never grow a no-op checkbox. The viewer lists every stored Boolean on a report
as a filter, and the printed filter echo (PDF and XLSX) reads ``_nc_export_filters``, so declaring the field here is
all the client needs.

THE RULE, one sentence: a row is dropped only when EVERY amount column on it is zero (to the company currency's
rounding). Activity is never hidden: an account that moved and netted to nothing keeps its row, because that is real
information on a ledger. Totals and section subtotals are never recomputed (a dropped row contributes nothing to
them), hierarchy headers and Total rows stay, and with the option OFF nothing changes at all.
"""
from odoo import fields, models
from odoo.tools import float_is_zero


class NcollectionAccountReportZero(models.AbstractModel):
    _name = 'ncollection.account.report.zero'
    _description = 'NCollection Financial Report Zero-Balance Option'

    exclude_zero = fields.Boolean(
        string='Exclude Zero Balances', default=False,
        help="Leave out rows whose every amount is zero. Rows that show any movement or balance, and all totals, "
             "are kept.")

    def _nc_is_zero(self, *amounts):
        """True when every amount is zero to the company currency's rounding."""
        self.ensure_one()
        rounding = self.company_id.currency_id.rounding or 0.01
        return all(float_is_zero(amount or 0.0, precision_rounding=rounding) for amount in amounts)

    def _nc_skip_zero(self, *amounts):
        """True when this row should be left out: the option is on AND every amount is zero."""
        return bool(self.exclude_zero) and self._nc_is_zero(*amounts)
