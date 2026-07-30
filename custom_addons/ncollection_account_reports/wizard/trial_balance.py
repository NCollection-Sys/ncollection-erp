# -*- coding: utf-8 -*-
"""F2-T02: Native Trial Balance.

Per-account opening / debit / credit / closing, computed purely from
``account.move.line`` aggregates (Odoo owns the numbers). Opening balance resets
at the fiscal-year start for P&L accounts and carries over for balance-sheet
accounts; **closing = opening + debit − credit**, which reconciles with the
General Ledger's ending running balance. Built on the F2-T01 engine.
"""
from odoo import models


class NcollectionTrialBalance(models.TransientModel):
    _name = 'ncollection.account.report.trial.balance'
    _inherit = ['ncollection.account.report']
    _description = 'NCollection Trial Balance'

    def _nc_report_title(self):
        return self.env._("Trial Balance")

    def _nc_list_view_ref(self):
        return 'ncollection_account_reports.view_report_line_tb_list'

    def _nc_report_action_ref(self):
        # Own report action (own report_name) — the engine default points at the
        # reference report; without this override Export-PDF would render a TB
        # against the reference model and raise MissingError.
        return 'ncollection_account_reports.action_report_trial_balance'

    def _nc_columns(self):
        return [
            {'key': 'label', 'label': self.env._("Account"), 'type': 'char'},
            {'key': 'opening_balance', 'label': self.env._("Opening"), 'type': 'monetary'},
            {'key': 'debit', 'label': self.env._("Debit"), 'type': 'monetary'},
            {'key': 'credit', 'label': self.env._("Credit"), 'type': 'monetary'},
            {'key': 'closing_balance', 'label': self.env._("Closing"), 'type': 'monetary'},
        ]

    def _nc_compute_lines(self):
        """Aggregate _read_group queries (period + the shared opening helper) —
        never a per-account loop. closing = opening + debit - credit reconciles
        with the General Ledger's ending running balance."""
        self.ensure_one()
        AML = self.env['account.move.line']
        base = self._nc_filter_domain()

        period = {}
        for account, debit, credit in AML._read_group(
                base + [('date', '>=', self.date_from), ('date', '<=', self.date_to)],
                groupby=['account_id'], aggregates=['debit:sum', 'credit:sum']):
            if account:
                period[account.id] = (debit, credit)

        opening = self._nc_opening_balances()

        accounts = self.env['account.account'].browse(
            list(set(opening) | set(period))).sorted(lambda a: a.code or '')
        rows = []
        t_open = t_deb = t_cred = t_close = 0.0
        for account in accounts:
            opn = opening.get(account.id, 0.0)
            deb, cred = period.get(account.id, (0.0, 0.0))
            close = opn + deb - cred
            t_open += opn
            t_deb += deb
            t_cred += cred
            t_close += close
            rows.append({
                'label': account.display_name, 'account_id': account.id,
                'opening_balance': opn, 'debit': deb, 'credit': cred,
                'closing_balance': close, 'balance': deb - cred, 'level': 1})
        rows.append({
            'label': self.env._("Total"), 'account_id': False,
            'opening_balance': t_open, 'debit': t_deb, 'credit': t_cred,
            'closing_balance': t_close, 'balance': t_deb - t_cred, 'level': 0})
        return rows
