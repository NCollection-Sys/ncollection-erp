# -*- coding: utf-8 -*-
"""F2-T01 reference report: a Trial-Balance-style per-account balance summary
that PROVES the report engine end-to-end (filters → drill-down → PDF → XLSX,
< 2s). It is deliberately a small generic report — the full General Ledger /
Trial Balance / Balance Sheet / P&L land in F2-T02+ on this same engine.
"""
from odoo import models


class NcollectionAccountReportReference(models.TransientModel):
    _name = 'ncollection.account.report.reference'
    _inherit = ['ncollection.account.report']
    _description = 'NCollection Reference Report (Account Balances)'

    def _nc_report_title(self):
        return self.env._("Account Balances (reference)")

    def _nc_columns(self):
        return [
            {'key': 'label', 'label': self.env._("Account"), 'type': 'char'},
            {'key': 'debit', 'label': self.env._("Debit"), 'type': 'monetary'},
            {'key': 'credit', 'label': self.env._("Credit"), 'type': 'monetary'},
            {'key': 'balance', 'label': self.env._("Balance"), 'type': 'monetary'},
        ]

    def _nc_compute_lines(self):
        """One aggregate query over the filtered journal items → per-account
        debit/credit/balance (never a per-account loop — the < 2s perf target)."""
        self.ensure_one()
        total_debit = total_credit = total_balance = 0.0
        lines = []
        for account, debit, credit, balance in self.env['account.move.line']._read_group(
                domain=self._nc_move_line_domain(),
                groupby=['account_id'],
                aggregates=['debit:sum', 'credit:sum', 'balance:sum']):
            if not account:
                continue
            total_debit += debit
            total_credit += credit
            total_balance += balance
            lines.append({
                'label': account.display_name, 'account_id': account.id,
                'debit': debit, 'credit': credit, 'balance': balance, 'level': 1})
        lines.sort(key=lambda row: row['label'])
        lines.append({
            'label': self.env._("Total"), 'account_id': False,
            'debit': total_debit, 'credit': total_credit,
            'balance': total_balance, 'level': 0})
        return lines
