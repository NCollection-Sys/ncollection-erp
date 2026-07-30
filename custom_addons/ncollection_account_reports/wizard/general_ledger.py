# -*- coding: utf-8 -*-
"""F2-T02: Native General Ledger.

Per-journal-item rows grouped by account, each account opened with its opening
balance and then its items in date order carrying a **running balance**. Built
on the F2-T01 engine (filters, PDF/XLSX, drill-down). GL has a different row
shape than the per-account reports, so it uses its own line model + list view
(as the F2-T01 README anticipates). Odoo owns the numbers — this only reads
``account.move.line``.
"""
from odoo import fields, models
from odoo.exceptions import UserError


class NcollectionGLLine(models.TransientModel):
    _name = 'ncollection.account.report.gl.line'
    _description = 'NCollection General Ledger Line'
    _order = 'id'

    report_res_id = fields.Integer(readonly=True)   # producing GL wizard id (drill-down)
    account_id = fields.Many2one('account.account', readonly=True)
    date = fields.Date(readonly=True)
    move_name = fields.Char(string='Entry', readonly=True)
    journal_id = fields.Many2one('account.journal', readonly=True)
    partner_id = fields.Many2one('res.partner', readonly=True)
    label = fields.Char(readonly=True)
    debit = fields.Monetary(currency_field='currency_id', readonly=True)
    credit = fields.Monetary(currency_field='currency_id', readonly=True)
    running_balance = fields.Monetary(currency_field='currency_id', readonly=True)
    currency_id = fields.Many2one('res.currency', readonly=True)
    is_initial = fields.Boolean(readonly=True)      # the per-account opening row

    def action_drill_down(self):
        """Open the account's journal items for the period (record rules apply)."""
        self.ensure_one()
        wizard = self.env['ncollection.account.report.general.ledger'].browse(
            self.report_res_id)
        if not wizard.exists():
            raise UserError(self.env._(
                "The report run expired — re-generate the report to drill down."))
        return {
            'type': 'ir.actions.act_window',
            'name': self.env._("Journal Items — %s", self.account_id.display_name),
            'res_model': 'account.move.line',
            'view_mode': 'list,form',
            'domain': wizard._nc_move_line_domain(account=self.account_id),
            'target': 'current',
        }


class NcollectionGeneralLedger(models.TransientModel):
    _name = 'ncollection.account.report.general.ledger'
    _inherit = ['ncollection.account.report']
    _description = 'NCollection General Ledger'

    def _nc_report_title(self):
        return self.env._("General Ledger")

    def _nc_report_action_ref(self):
        return 'ncollection_account_reports.action_report_general_ledger'

    def _nc_columns(self):
        return [
            {'key': 'date', 'label': self.env._("Date"), 'type': 'char'},
            {'key': 'move_name', 'label': self.env._("Entry"), 'type': 'char'},
            {'key': 'journal_name', 'label': self.env._("Journal"), 'type': 'char'},
            {'key': 'label', 'label': self.env._("Label"), 'type': 'char'},
            {'key': 'partner_name', 'label': self.env._("Partner"), 'type': 'char'},
            {'key': 'debit', 'label': self.env._("Debit"), 'type': 'monetary'},
            {'key': 'credit', 'label': self.env._("Credit"), 'type': 'monetary'},
            {'key': 'running_balance', 'label': self.env._("Balance"), 'type': 'monetary'},
        ]

    def _nc_gl_rows(self):
        """Ordered rows: per account (sorted by code) an opening row then its
        period items in date order, each carrying the cumulative running balance.
        The opening reuses the shared, accounting-correct opening helper."""
        self.ensure_one()
        AML = self.env['account.move.line']
        opening = self._nc_opening_balances()
        # Already ordered by (account_id, date, id) — one pass groups AND keeps
        # date order per account (no recordset unions, no per-account re-sort).
        period = AML.search(
            self._nc_move_line_domain(), order='account_id, date, id')
        by_account = {}
        for line in period:
            by_account.setdefault(line.account_id.id, []).append(line)

        account_ids = set(opening) | set(by_account)
        accounts = self.env['account.account'].browse(
            list(account_ids)).sorted(lambda a: a.code or '')
        rows = []
        for account in accounts:
            running = opening.get(account.id, 0.0)
            rows.append({
                'is_initial': True, 'account_id': account.id,
                'label': self.env._("Opening Balance — %s", account.display_name),
                'date': False, 'move_name': '', 'journal_id': False,
                'journal_name': '', 'partner_id': False, 'partner_name': '',
                'debit': 0.0, 'credit': 0.0, 'running_balance': running})
            for line in by_account.get(account.id, []):
                running += line.balance
                rows.append({
                    'is_initial': False, 'account_id': account.id,
                    'label': line.name or line.move_id.ref or '',
                    'date': line.date, 'move_name': line.move_id.name,
                    'journal_id': line.journal_id.id,
                    'journal_name': line.journal_id.name,
                    'partner_id': line.partner_id.id or False,
                    'partner_name': line.partner_id.display_name or '',
                    'debit': line.debit, 'credit': line.credit,
                    'running_balance': running})
        return rows

    def _nc_compute_lines(self):
        # The PDF/XLSX render channel — same rows the on-screen list shows.
        return self._nc_gl_rows()

    def action_view(self):
        self.ensure_one()
        GLLine = self.env['ncollection.account.report.gl.line']
        GLLine.search([('create_uid', '=', self.env.uid)]).unlink()
        currency_id = self.company_id.currency_id.id
        created = GLLine.create([{
            'report_res_id': self.id,
            'account_id': row['account_id'],
            'date': row.get('date') or False,
            'move_name': row.get('move_name') or '',
            'journal_id': row.get('journal_id') or False,
            'partner_id': row.get('partner_id') or False,
            'label': row.get('label') or '',
            'debit': row.get('debit', 0.0),
            'credit': row.get('credit', 0.0),
            'running_balance': row.get('running_balance', 0.0),
            'is_initial': row.get('is_initial', False),
            'currency_id': currency_id,
        } for row in self._nc_gl_rows()])
        return {
            'type': 'ir.actions.act_window',
            'name': self._nc_report_title(),
            'res_model': 'ncollection.account.report.gl.line',
            'view_mode': 'list',
            # Pin the list view explicitly (like the base engine) — don't rely on
            # this model having exactly one list view.
            'views': [(self.env.ref(
                'ncollection_account_reports.view_report_gl_line_list').id, 'list')],
            'domain': [('id', 'in', created.ids)],
            'target': 'current',
        }
