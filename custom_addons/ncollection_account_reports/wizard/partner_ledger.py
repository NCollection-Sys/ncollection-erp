# -*- coding: utf-8 -*-
"""F2-T05: Native Partner Ledger.

Per-journal-item rows grouped by PARTNER — the receivable/payable mirror of the
F2-T02 General Ledger, which groups by account. Each partner opens with its
opening balance and then its items in date order carrying a running balance.

WHY IT IS NOT JUST THE GL WITH A DIFFERENT GROUP KEY, stated because the
similarity invites exactly that shortcut:

  * A partner ledger is meaningful ONLY over receivable/payable accounts.
    Grouping every account by partner would mix a customer's invoices with the
    revenue lines that back them and double-count the story. So this restricts
    to ``asset_receivable`` / ``liability_payable`` (Odoo's own selection
    values, account_account.py:46,52) and offers a customer/vendor/both switch.
  * The opening balance therefore cannot reuse ``_nc_opening_balances()``
    unchanged: that helper keys by ACCOUNT and applies the fiscal-year reset
    rule for P&L accounts. Receivable and payable are balance-sheet accounts,
    so they carry across years — but the opening still has to be summed PER
    PARTNER, which the account-keyed helper cannot express.

Odoo owns the numbers — this only reads ``account.move.line``.
"""
from odoo import fields, models
from odoo.exceptions import UserError

# Odoo core's own selection values — account/models/account_account.py.
RECEIVABLE = 'asset_receivable'
PAYABLE = 'liability_payable'


class NcollectionPartnerLedgerLine(models.TransientModel):
    _name = 'ncollection.account.report.partner.line'
    _description = 'NCollection Partner Ledger Line'
    _order = 'id'

    report_res_id = fields.Integer(readonly=True)   # producing wizard id (drill-down)
    partner_id = fields.Many2one('res.partner', readonly=True)
    account_id = fields.Many2one('account.account', readonly=True)
    date = fields.Date(readonly=True)
    move_name = fields.Char(string='Entry', readonly=True)
    journal_id = fields.Many2one('account.journal', readonly=True)
    label = fields.Char(readonly=True)
    debit = fields.Monetary(currency_field='currency_id', readonly=True)
    credit = fields.Monetary(currency_field='currency_id', readonly=True)
    running_balance = fields.Monetary(currency_field='currency_id', readonly=True)
    currency_id = fields.Many2one('res.currency', readonly=True)
    is_initial = fields.Boolean(readonly=True)      # the per-partner opening row

    def action_drill_down(self):
        """Open this partner's journal items for the period (record rules apply)."""
        self.ensure_one()
        wizard = self.env['ncollection.account.report.partner.ledger'].browse(
            self.report_res_id)
        if not wizard.exists():
            raise UserError(self.env._(
                "The report run expired — re-generate the report to drill down."))
        domain = wizard._nc_move_line_domain(partner=self.partner_id)
        return {
            'type': 'ir.actions.act_window',
            'name': self.env._("Journal Items — %s", self.partner_id.display_name),
            'res_model': 'account.move.line',
            'view_mode': 'list,form',
            # The wizard domain is partner-scoped but NOT account-type scoped;
            # add that here so the drill-down shows the same rows the report
            # counted rather than every move the partner appears on.
            'domain': domain + [('account_id.account_type', 'in',
                                 wizard._nc_account_types())],
            'target': 'current',
        }


class NcollectionPartnerLedger(models.TransientModel):
    _name = 'ncollection.account.report.partner.ledger'
    _inherit = ['ncollection.account.report']
    _description = 'NCollection Partner Ledger'

    partner_scope = fields.Selection(
        [('receivable', 'Customers'),
         ('payable', 'Vendors'),
         ('both', 'Both')],
        default='receivable', required=True, string='Scope',
        help="Which side of the ledger to report. A partner ledger is only "
             "meaningful over receivable/payable accounts.")

    def _nc_account_types(self):
        """The account types in scope — never 'every account'."""
        self.ensure_one()
        if self.partner_scope == 'receivable':
            return [RECEIVABLE]
        if self.partner_scope == 'payable':
            return [PAYABLE]
        return [RECEIVABLE, PAYABLE]

    def _nc_report_title(self):
        return {
            'receivable': self.env._("Partner Ledger — Customers"),
            'payable': self.env._("Partner Ledger — Vendors"),
        }.get(self.partner_scope, self.env._("Partner Ledger"))

    def _nc_report_action_ref(self):
        return 'ncollection_account_reports.action_report_partner_ledger'

    def _nc_columns(self):
        return [
            {'key': 'date', 'label': self.env._("Date"), 'type': 'char'},
            {'key': 'move_name', 'label': self.env._("Entry"), 'type': 'char'},
            {'key': 'journal_name', 'label': self.env._("Journal"), 'type': 'char'},
            {'key': 'account_name', 'label': self.env._("Account"), 'type': 'char'},
            {'key': 'label', 'label': self.env._("Label"), 'type': 'char'},
            {'key': 'debit', 'label': self.env._("Debit"), 'type': 'monetary'},
            {'key': 'credit', 'label': self.env._("Credit"), 'type': 'monetary'},
            {'key': 'running_balance', 'label': self.env._("Balance"), 'type': 'monetary'},
        ]

    def _nc_partner_opening(self):
        """``{partner_id: balance before date_from}`` over the scoped accounts.

        Summed PER PARTNER, which is why the account-keyed
        ``_nc_opening_balances()`` cannot be reused here. Receivable/payable are
        balance-sheet accounts, so there is no fiscal-year reset to apply —
        every earlier posted line counts, which is what makes the ledger's
        closing balance equal the partner's real outstanding position.
        """
        self.ensure_one()
        domain = [
            ('parent_state', '=', 'posted'),
            ('company_id', '=', self.company_id.id),
            ('date', '<', self.date_from),
            ('account_id.account_type', 'in', self._nc_account_types()),
        ]
        if self.partner_ids:
            domain.append(('partner_id', 'in', self.partner_ids.ids))
        if self.journal_ids:
            domain.append(('journal_id', 'in', self.journal_ids.ids))
        opening = {}
        grouped = self.env['account.move.line']._read_group(
            domain, groupby=['partner_id'], aggregates=['balance:sum'])
        for partner, balance in grouped:
            if partner:
                opening[partner.id] = balance
        return opening

    def _nc_partner_rows(self):
        """Ordered rows: per partner an opening row then its period items."""
        self.ensure_one()
        AML = self.env['account.move.line']
        opening = self._nc_partner_opening()
        domain = self._nc_move_line_domain() + [
            ('account_id.account_type', 'in', self._nc_account_types()),
            ('partner_id', '!=', False),
        ]
        period = AML.search(domain, order='partner_id, date, id')
        by_partner = {}
        for line in period:
            by_partner.setdefault(line.partner_id.id, []).append(line)

        partner_ids = set(opening) | set(by_partner)
        partners = self.env['res.partner'].browse(
            list(partner_ids)).sorted(lambda p: p.display_name or '')
        rows = []
        for partner in partners:
            running = opening.get(partner.id, 0.0)
            rows.append({
                'is_initial': True, 'partner_id': partner.id,
                'label': self.env._("Opening Balance — %s", partner.display_name),
                'date': False, 'move_name': '', 'journal_id': False,
                'journal_name': '', 'account_id': False, 'account_name': '',
                'debit': 0.0, 'credit': 0.0, 'running_balance': running})
            for line in by_partner.get(partner.id, []):
                running += line.balance
                rows.append({
                    'is_initial': False, 'partner_id': partner.id,
                    'label': line.name or line.move_id.ref or '',
                    'date': line.date, 'move_name': line.move_id.name,
                    'journal_id': line.journal_id.id,
                    'journal_name': line.journal_id.name,
                    'account_id': line.account_id.id,
                    'account_name': line.account_id.display_name,
                    'debit': line.debit, 'credit': line.credit,
                    'running_balance': running})
        return rows

    def _nc_compute_lines(self):
        # The PDF/XLSX render channel — same rows the on-screen list shows.
        return self._nc_partner_rows()

    def action_view(self):
        self.ensure_one()
        Line = self.env['ncollection.account.report.partner.line']
        Line.search([('create_uid', '=', self.env.uid)]).unlink()
        currency_id = self.company_id.currency_id.id
        created = Line.create([{
            'report_res_id': self.id,
            'partner_id': row['partner_id'],
            'account_id': row.get('account_id') or False,
            'date': row.get('date') or False,
            'move_name': row.get('move_name') or '',
            'journal_id': row.get('journal_id') or False,
            'label': row.get('label') or '',
            'debit': row.get('debit', 0.0),
            'credit': row.get('credit', 0.0),
            'running_balance': row.get('running_balance', 0.0),
            'is_initial': row.get('is_initial', False),
            'currency_id': currency_id,
        } for row in self._nc_partner_rows()])
        return {
            'type': 'ir.actions.act_window',
            'name': self._nc_report_title(),
            'res_model': 'ncollection.account.report.partner.line',
            'view_mode': 'list',
            # Pinned explicitly, like the base engine — do not rely on this
            # model having exactly one list view.
            'views': [(self.env.ref(
                'ncollection_account_reports.view_report_partner_line_list').id,
                'list')],
            'domain': [('id', 'in', created.ids)],
            'target': 'current',
        }
