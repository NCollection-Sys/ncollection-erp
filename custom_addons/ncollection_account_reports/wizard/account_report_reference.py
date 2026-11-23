# -*- coding: utf-8 -*-
"""F2-T01 reference report: a Trial-Balance-style per-account balance summary
that PROVES the report engine end-to-end (filters → drill-down → PDF → XLSX,
< 2s). It is deliberately a small generic report — the full General Ledger /
Trial Balance / Balance Sheet / P&L land in F2-T02+ on this same engine.
"""
from odoo import models


class NcollectionAccountReportReference(models.TransientModel):
    _name = 'ncollection.account.report.reference'
    _inherit = ['ncollection.account.report.zero', 'ncollection.account.report']
    _description = 'NCollection Reference Report (Account Balances)'

    def _nc_report_title(self):
        return self.env._("Account Balances")

    def _nc_columns(self):
        return [
            {'key': 'label', 'label': self.env._("Account"), 'type': 'char'},
            {'key': 'opening', 'label': self.env._("Opening Balance"), 'type': 'monetary'},
            {'key': 'debit', 'label': self.env._("Debit"), 'type': 'monetary'},
            {'key': 'credit', 'label': self.env._("Credit"), 'type': 'monetary'},
            {'key': 'balance', 'label': self.env._("Closing Balance"), 'type': 'monetary'},
        ]

    def _nc_compute_lines(self):
        """Per account: Opening (the shared, accounting-correct opening of the Trial Balance / General Ledger),
        the period's Debit and Credit, and Closing = Opening + Debit - Credit.

        Before 19.0 (fin-ux-14) the "Balance" column was the PERIOD MOVEMENT only (no opening), so an account with
        history before the period showed a balance that did not accumulate. One aggregate query for the period plus
        the opening helper's grouped queries; never a per-account loop."""
        self.ensure_one()
        fy_from = self.company_id.compute_fiscalyear_dates(self.date_from)['date_from']
        fy_to = self.company_id.compute_fiscalyear_dates(self.date_to)['date_from']
        if fy_from == fy_to:
            opening = self._nc_opening_balances()
        else:
            # across fiscal years there is no single P&L reset date: every account carries all prior activity
            opening = {account.id: balance for account, balance in self.env['account.move.line']._read_group(
                self._nc_filter_domain() + [('date', '<', self.date_from)],
                groupby=['account_id'], aggregates=['balance:sum']) if account}
        if self.account_ids:
            opening = {k: v for k, v in opening.items() if k in self.account_ids.ids}
        period = {}
        for account, debit, credit in self.env['account.move.line']._read_group(
                domain=self._nc_move_line_domain(),
                groupby=['account_id'],
                aggregates=['debit:sum', 'credit:sum']):
            if account:
                period[account.id] = (debit, credit)
        accounts = self.env['account.account'].with_context(active_test=False).browse(set(opening) | set(period))
        totals = dict.fromkeys(('opening', 'debit', 'credit', 'balance'), 0.0)
        lines = []
        for account in accounts:
            debit, credit = period.get(account.id, (0.0, 0.0))
            start = opening.get(account.id, 0.0)
            closing = self.company_id.currency_id.round(start + debit - credit)
            if self._nc_skip_zero(start, debit, credit, closing):
                continue
            row = {'label': account.display_name, 'account_id': account.id, 'code': account.code or '',
                   'opening': start, 'debit': debit, 'credit': credit, 'balance': closing, 'level': 1}
            for key in totals:
                totals[key] += row[key]
            lines.append(row)
        lines.sort(key=lambda row: (row['code'], row['label']))
        lines.append({'label': self.env._("Total"), 'account_id': False, **totals, 'level': 0})
        return lines

    def _nc_drill_action(self, account):
        """A row opens the account's LEDGER for the same run (General Ledger viewer: opening row, every item in
        date order, running balance, closing), not a bare journal-items list whose per-line Balance never
        accumulates. Same company / period / posted filter / journals / partners as this run."""
        self.ensure_one()
        filters = {
            'company_id': self.company_id.id, 'date_from': str(self.date_from), 'date_to': str(self.date_to),
            'target_move': self.target_move, 'account_ids': [account.id],
            'journal_ids': self.journal_ids.ids, 'partner_ids': self.partner_ids.ids,
        }
        return {
            'type': 'ir.actions.client', 'tag': 'ncollection_account_reports.report_viewer',
            'name': self.env._("Ledger — %s", account.display_name),
            'context': {'nc_report_model': 'ncollection.account.report.general.ledger',
                        'nc_report_filters': filters},
        }
