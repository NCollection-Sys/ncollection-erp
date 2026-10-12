# -*- coding: utf-8 -*-
"""Trial Balance computed by the official OCA ``account_financial_report`` engine,
presented through the NCollection report contract.

THE ADAPTER IS AN ENGINE, NOT A SCREEN. It inherits the NCollection report base
(``ncollection.account.report``) and implements the same two methods every
NCollection report implements - ``_nc_columns()`` and ``_nc_compute_lines()`` -
by running OCA's ``report.account_financial_report.trial_balance`` and mapping
what it returns onto the NCollection row contract. Everything downstream is then
the code NCollection already owns and tests: the viewer (state in the URL,
RTL/LTR, drill-down), ``_nc_export_payload`` (PDF and XLSX), the per-run privacy
rule, the single-fiscal-year guard. No OCA screen, template or wizard form is
ever shown.

What is normalised
------------------
* columns: Account / Opening / Debit / Credit / Closing - identical keys and
  labels to the native report, so the two are interchangeable to the viewer;
* rows: one per account (``account_id`` = the real account, so the viewer can
  link to it), optionally preceded by OCA's account-group rows (``account_id``
  False - a group is not an account and gets no link), with the hierarchy
  carried in ``level``; a final Total row;
* filters: company, date range, target moves, journals, accounts, partners map
  one-to-one; ``hide_zero`` and ``show_hierarchy`` are the OCA options worth
  exposing and appear in the viewer as ordinary filters.

Not mapped (stated, not hidden): foreign-currency columns and the analytic
"grouped by" mode - OCA supports both, the NCollection viewer has no column
contract for them yet.

Security: OCA runs as the CURRENT user (it never calls ``sudo``), so Odoo's own
record rules and company rules apply to every figure. Its wizard row is deleted
as soon as the figures are read, because OCA's wizard model has no per-user
rule - leaving it behind would leave filter values readable by other users.
"""
from odoo import fields, models
from odoo.exceptions import AccessError


class NcTrialBalanceOca(models.TransientModel):
    _name = 'ncollection.account.report.tb.oca'
    _inherit = ['ncollection.account.report']
    _description = 'NCollection Trial Balance (OCA engine)'

    hide_zero = fields.Boolean(
        string='Exclude Zero Balances', default=True,
        help="Leave out accounts with no opening balance and no movement.")
    show_hierarchy = fields.Boolean(
        string='Show account groups',
        help="Group accounts under their account-group hierarchy.")

    def _nc_report_title(self):
        return self.env._("Trial Balance")

    def _nc_report_action_ref(self):
        return 'ncollection_account_reports_oca.action_report_trial_balance_oca'

    def _nc_columns(self):
        return [
            {'key': 'label', 'label': self.env._("Account"), 'type': 'char'},
            {'key': 'opening_balance', 'label': self.env._("Opening"), 'type': 'monetary'},
            {'key': 'debit', 'label': self.env._("Debit"), 'type': 'monetary'},
            {'key': 'credit', 'label': self.env._("Credit"), 'type': 'monetary'},
            {'key': 'closing_balance', 'label': self.env._("Closing"), 'type': 'monetary'},
        ]

    # ------------------------------------------------------------------ OCA
    def _nc_oca_values(self):
        """Run OCA's report for these filters and return its value dict."""
        self.ensure_one()
        # OCA creates the company's earnings account on demand (as the system), so a
        # run against a company the caller may not use must be refused BEFORE it
        # reaches OCA - otherwise asking for a foreign company has a side effect there.
        if self.company_id not in self.env.user.company_ids:
            raise AccessError(self.env._("You are not allowed to report on this company."))
        # NCollection policy, applied here too so both engines refuse the same
        # input: a period must lie inside ONE fiscal year (a wrong number is worse
        # than an error for a financial report).
        self._nc_assert_single_fiscal_year()
        wizard = self.env['trial.balance.report.wizard'].create({
            'company_id': self.company_id.id,
            'date_from': self.date_from,
            'date_to': self.date_to,
            'target_move': self.target_move,
            'hide_account_at_0': self.hide_zero,
            'show_hierarchy': self.show_hierarchy,
            'foreign_currency': False,
            'account_ids': [(6, 0, self.account_ids.ids)],
            'journal_ids': [(6, 0, self.journal_ids.ids)],
            'partner_ids': [(6, 0, self.partner_ids.ids)],
        })
        try:
            data = wizard._prepare_report_data()
            return self.env['report.account_financial_report.trial_balance']._get_report_values(
                [wizard.id], data)
        finally:
            wizard.unlink()

    def _nc_compute_lines(self):
        self.ensure_one()
        values = self._nc_oca_values()
        rows = []
        total = dict.fromkeys(('opening_balance', 'debit', 'credit', 'closing_balance'), 0.0)
        for item in values['trial_balance']:
            is_group = item.get('type') == 'group_type'
            label = ('%s %s' % (item['code'], item['name'])).strip() if item.get('code') \
                else item['name']
            row = {
                'label': label,
                'account_id': False if is_group else item['id'],
                'opening_balance': item['initial_balance'],
                'debit': item['debit'],
                'credit': item['credit'],
                'closing_balance': item['ending_balance'],
                'balance': item['debit'] - item['credit'],
                # the Total row is level 0; accounts (and, when shown, groups
                # and the accounts inside them) nest below it
                'level': item.get('level', 0) + 1 if self.show_hierarchy else 1,
            }
            rows.append(row)
            if not is_group:
                total['opening_balance'] += row['opening_balance']
                total['debit'] += row['debit']
                total['credit'] += row['credit']
                total['closing_balance'] += row['closing_balance']
        rows.append(dict(
            total, label=self.env._("Total"), account_id=False,
            balance=total['debit'] - total['credit'], level=0))
        return rows
