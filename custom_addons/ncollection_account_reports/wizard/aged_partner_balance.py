# -*- coding: utf-8 -*-
"""F2-T05: Aged Receivable / Aged Payable.

Open (unreconciled) receivable or payable amounts, bucketed by how overdue they
are as at ``date_to``. Buckets follow FINANCIAL_PLATFORM_ARCHITECTURE.md
§"Aged Receivable": Current · 1-30 · 31-60 · 61-90 · 91-120 · Over 120.

CONFIGURABLE, per the ticket's "buckets configurable (30/60/90 default)":
``period_length`` (default 30) sets the step, giving boundaries at 30/60/90/120
— which reproduces the FPA buckets exactly at its default and lets a tenant
using 15- or 45-day terms re-cut them without code. Odoo's own aged reporting
uses the same single-step convention, so this is the least surprising shape.

WRITTEN CLEAN-ROOM. OCA's `account_financial_report` ships an equivalent report,
but it is AGPL-3 while this module is LGPL-3, so its code cannot be copied here.
The bucketing below is the standard technique — compare a line's due date to an
as-of date and drop the residual into a slot — implemented from the FPA spec
against this repo's own engine.

WHY RESIDUAL AND NOT BALANCE. An aged report answers "what is still owed", so it
reads ``amount_residual`` over unreconciled lines. That is also what makes the
FPA acceptance criterion — "outstanding balances match customer ledger" —
a real check rather than a tautology: the ledger sums every posted line's
balance, this sums open residuals, and they must agree.
"""
from odoo import fields, models
from odoo.exceptions import UserError

RECEIVABLE = 'asset_receivable'
PAYABLE = 'liability_payable'


class NcollectionAgedLine(models.TransientModel):
    _name = 'ncollection.account.report.aged.line'
    _description = 'NCollection Aged Partner Balance Line'
    _order = 'id'

    report_res_id = fields.Integer(readonly=True)
    partner_id = fields.Many2one('res.partner', readonly=True)
    bucket_current = fields.Monetary(currency_field='currency_id', readonly=True)
    bucket_1 = fields.Monetary(string='1-30', currency_field='currency_id', readonly=True)
    bucket_2 = fields.Monetary(string='31-60', currency_field='currency_id', readonly=True)
    bucket_3 = fields.Monetary(string='61-90', currency_field='currency_id', readonly=True)
    bucket_4 = fields.Monetary(string='91-120', currency_field='currency_id', readonly=True)
    bucket_over = fields.Monetary(string='Over 120', currency_field='currency_id',
                                  readonly=True)
    total = fields.Monetary(currency_field='currency_id', readonly=True)
    currency_id = fields.Many2one('res.currency', readonly=True)

    def action_drill_down(self):
        """Open this partner's OPEN items — the lines this row is made of."""
        self.ensure_one()
        wizard = self.env['ncollection.account.report.aged.partner'].browse(
            self.report_res_id)
        if not wizard.exists():
            raise UserError(self.env._(
                "The report run expired — re-generate the report to drill down."))
        return {
            'type': 'ir.actions.act_window',
            'name': self.env._("Open Items — %s", self.partner_id.display_name),
            'res_model': 'account.move.line',
            'view_mode': 'list,form',
            'domain': wizard._nc_open_line_domain() + [
                ('partner_id', '=', self.partner_id.id)],
            'target': 'current',
        }


class NcollectionAgedPartnerBalance(models.TransientModel):
    _name = 'ncollection.account.report.aged.partner'
    _inherit = ['ncollection.account.report']
    _description = 'NCollection Aged Partner Balance'

    partner_scope = fields.Selection(
        [('receivable', 'Aged Receivable'), ('payable', 'Aged Payable')],
        default='receivable', required=True, string='Report')
    period_length = fields.Integer(
        default=30, required=True, string='Bucket size (days)',
        help="Step between ageing buckets. 30 gives the standard "
             "Current / 1-30 / 31-60 / 61-90 / 91-120 / Over 120 columns.")

    def _nc_account_types(self):
        self.ensure_one()
        return [RECEIVABLE if self.partner_scope == 'receivable' else PAYABLE]

    def _nc_report_title(self):
        return (self.env._("Aged Receivable") if self.partner_scope == 'receivable'
                else self.env._("Aged Payable"))

    def _nc_report_action_ref(self):
        return 'ncollection_account_reports.action_report_aged_partner'

    def _nc_bucket_labels(self):
        """Column headings, derived from period_length so they never lie.

        Hardcoding "1-30" while period_length is 45 would produce a report whose
        headings contradict its own arithmetic.
        """
        self.ensure_one()
        step = self.period_length
        return [
            self.env._("Current"),
            "1-%d" % step,
            "%d-%d" % (step + 1, step * 2),
            "%d-%d" % (step * 2 + 1, step * 3),
            "%d-%d" % (step * 3 + 1, step * 4),
            self.env._("Over %d", step * 4),
        ]

    def _nc_columns(self):
        labels = self._nc_bucket_labels()
        cols = [{'key': 'partner_name', 'label': self.env._("Partner"), 'type': 'char'}]
        for key, label in zip(
                ('bucket_current', 'bucket_1', 'bucket_2', 'bucket_3',
                 'bucket_4', 'bucket_over'), labels):
            cols.append({'key': key, 'label': label, 'type': 'monetary'})
        cols.append({'key': 'total', 'label': self.env._("Total"), 'type': 'monetary'})
        return cols

    def _nc_open_line_domain(self):
        """Posted, unreconciled receivable/payable lines as at date_to."""
        self.ensure_one()
        domain = [
            ('parent_state', '=', 'posted'),
            ('company_id', '=', self.company_id.id),
            ('date', '<=', self.date_to),
            ('account_id.account_type', 'in', self._nc_account_types()),
            ('partner_id', '!=', False),
            ('amount_residual', '!=', 0.0),
        ]
        if self.partner_ids:
            domain.append(('partner_id', 'in', self.partner_ids.ids))
        if self.journal_ids:
            domain.append(('journal_id', 'in', self.journal_ids.ids))
        return domain

    def _nc_bucket_index(self, line):
        """Which bucket a line falls in: 0 = current, 5 = over 4×period.

        Ageing is measured from the DUE date, falling back to the accounting
        date when a line has no maturity — an invoice with no due date is due
        immediately, so treating it as current would under-report the debt.
        """
        self.ensure_one()
        due = line.date_maturity or line.date
        if due >= self.date_to:
            return 0
        overdue = (self.date_to - due).days
        step = self.period_length
        for index in range(1, 5):
            if overdue <= step * index:
                return index
        return 5

    def _nc_aged_rows(self):
        self.ensure_one()
        if self.period_length <= 0:
            raise UserError(self.env._(
                "Bucket size must be a positive number of days."))
        keys = ('bucket_current', 'bucket_1', 'bucket_2', 'bucket_3',
                'bucket_4', 'bucket_over')
        lines = self.env['account.move.line'].search(self._nc_open_line_domain())
        by_partner = {}
        for line in lines:
            row = by_partner.setdefault(line.partner_id, dict.fromkeys(keys, 0.0))
            row[keys[self._nc_bucket_index(line)]] += line.amount_residual

        rows = []
        for partner in sorted(by_partner, key=lambda p: p.display_name or ''):
            row = by_partner[partner]
            rows.append({
                'partner_id': partner.id,
                'partner_name': partner.display_name,
                **row,
                'total': sum(row.values()),
            })
        return rows

    def _nc_compute_lines(self):
        return self._nc_aged_rows()

    def action_view(self):
        self.ensure_one()
        Line = self.env['ncollection.account.report.aged.line']
        Line.search([('create_uid', '=', self.env.uid)]).unlink()
        currency_id = self.company_id.currency_id.id
        created = Line.create([{
            'report_res_id': self.id,
            'partner_id': row['partner_id'],
            'bucket_current': row['bucket_current'],
            'bucket_1': row['bucket_1'],
            'bucket_2': row['bucket_2'],
            'bucket_3': row['bucket_3'],
            'bucket_4': row['bucket_4'],
            'bucket_over': row['bucket_over'],
            'total': row['total'],
            'currency_id': currency_id,
        } for row in self._nc_aged_rows()])
        return {
            'type': 'ir.actions.act_window',
            'name': self._nc_report_title(),
            'res_model': 'ncollection.account.report.aged.line',
            'view_mode': 'list',
            'views': [(self.env.ref(
                'ncollection_account_reports.view_report_aged_line_list').id,
                'list')],
            'domain': [('id', 'in', created.ids)],
            'target': 'current',
        }
