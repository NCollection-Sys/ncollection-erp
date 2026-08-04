# -*- coding: utf-8 -*-
"""F2-T03: Native Balance Sheet.

The financial position as of a date (FPA §Balance Sheet): Assets, Liabilities,
Equity, with optional comparison columns. Built on the F2-T01 engine + the
F2-T03 comparison mixin — this module only READS ``account.move.line``; Odoo
owns the numbers (FPA §4/§6).

**Replaces** ``ncollection_mis_templates``' Balance Sheet (`mis_report_bs`). The
groupings below are a deliberate 1:1 transcription of that template's KPI
expressions, so the two produce identical figures during the transition; the
retirement itself is #117 [F2-T07].

Grouping is by ``account_type`` — Odoo-core selection values, identical across
every chart of accounts including ``l10n_ae`` — and NEVER by hardcoded code
prefixes, so it reconciles on the UAE CoA (P3-T05, #45) rather than a generic
numbering.
"""
from odoo import models

# ---------------------------------------------------------------------------
# Parity map — transcribed 1:1 from
#   ncollection_mis_templates/data/mis_report_balance_sheet.xml
# `bale` (cumulative ending balance) == our _nc_closing_balances().
# `sign`: -1 for credit-normal groups, which mis negates to present positive
# figures. tests/test_bs_pl.py holds an INDEPENDENT transcription of this same
# table and asserts the two agree — so a silent edit here fails the build.
# ---------------------------------------------------------------------------
_BS_SECTIONS = [
    ('assets', "ASSETS", [
        ('current_assets', "Current Assets", 1,
         ('asset_receivable', 'asset_cash', 'asset_current', 'asset_prepayments')),
        ('fixed_assets', "Fixed & Other Assets", 1,
         ('asset_non_current', 'asset_fixed')),
    ], "TOTAL ASSETS"),
    ('liabilities_equity', "LIABILITIES & EQUITY", [
        ('current_liabilities', "Current Liabilities", -1,
         ('liability_payable', 'liability_credit_card', 'liability_current')),
        ('long_term_liabilities', "Long Term Liabilities", -1,
         ('liability_non_current',)),
        ('equity', "Equity", -1,
         ('equity', 'equity_unaffected')),
        # The current + prior years' net result. Odoo COMPUTES this rather than
        # posting it, so without this bucket the statement would not balance.
        ('accumulated_earnings', "Accumulated Earnings", -1,
         ('income', 'income_other', 'expense', 'expense_direct_cost',
          'expense_depreciation')),
    ], "TOTAL LIABILITIES & EQUITY"),
]


class NcollectionBalanceSheet(models.TransientModel):
    _name = 'ncollection.account.report.balance.sheet'
    # Comparison mixin first: it supplies _nc_columns()/_nc_list_view_ref(),
    # which the engine declares abstract.
    _inherit = ['ncollection.account.report.comparison', 'ncollection.account.report']
    _description = 'NCollection Balance Sheet'

    def _nc_report_title(self):
        return self.env._("Balance Sheet")

    def _nc_report_action_ref(self):
        # Unique report_name per action — sharing one renders every wizard
        # against the FIRST action's model (see report_templates.xml).
        return 'ncollection_account_reports.action_report_balance_sheet'

    # ---- computation -----------------------------------------------------

    def _nc_bucket_totals(self, record):
        """``(totals, details, raw)`` for one period, using cumulative closing
        balances (a position, not a flow):

        * ``totals``  — ``{bucket_key: signed total}``
        * ``details`` — ``{bucket_key: [(account, signed balance)]}``
        * ``raw``     — ``{account_id: signed balance}``, for per-account
          comparison lookups without a second query.
        """
        balances = record._nc_closing_balances()
        # active_test=False: an ARCHIVED account can still carry a balance;
        # dropping it would silently understate the section (and unbalance the
        # statement). Mirrors the engine's _nc_opening_balances.
        accounts = self.env['account.account'].with_context(
            active_test=False).browse(list(balances))
        totals, details, raw = {}, {}, {}
        for _section, _label, buckets, _total_label in _BS_SECTIONS:
            for key, _blabel, sign, types in buckets:
                rows = [(a, balances[a.id] * sign)
                        for a in accounts if a.account_type in types]
                totals[key] = sum(balance for _a, balance in rows)
                details[key] = sorted(rows, key=lambda r: r[0].code or '')
                raw.update({a.id: balance for a, balance in rows})
        return totals, details, raw

    def _nc_compute_lines(self):
        """Section headers, per-account detail (drill-down), section subtotals
        and the two grand totals — which are equal when the books balance."""
        self.ensure_one()
        current, detail, _raw = self._nc_bucket_totals(self)
        comparison = self._nc_comparison_record()
        # ONE extra aggregate query for the whole comparison period — never one
        # per account row.
        previous, _pdetail, previous_by_account = (
            self._nc_bucket_totals(comparison) if comparison is not None
            else ({}, {}, {}))

        rows = []
        for _section, section_label, buckets, total_label in _BS_SECTIONS:
            rows.append(self._nc_comparison_row(section_label, 0.0, 0.0, level=0))
            section_current = section_previous = 0.0
            for key, bucket_label, _sign, _types in buckets:
                cur, prev = current.get(key, 0.0), previous.get(key, 0.0)
                section_current += cur
                section_previous += prev
                rows.append(self._nc_comparison_row(bucket_label, cur, prev, level=1))
                for account, balance in detail.get(key, ()):
                    rows.append(self._nc_comparison_row(
                        account.display_name, balance,
                        previous_by_account.get(account.id, 0.0),
                        level=2, account_id=account.id))
            rows.append(self._nc_comparison_row(
                total_label, section_current, section_previous, level=0))
        return rows

    def _nc_totals(self):
        """``(total_assets, total_liabilities_and_equity)`` — the balancing
        identity, exposed for tests and for F3 dashboards that want the two
        headline figures without re-deriving them from rendered rows."""
        self.ensure_one()
        totals = self._nc_bucket_totals(self)[0]
        return (sum(totals[k] for k, _l, _s, _t in _BS_SECTIONS[0][2]),
                sum(totals[k] for k, _l, _s, _t in _BS_SECTIONS[1][2]))
