# -*- coding: utf-8 -*-
"""F2-T01: the transient rows that back the on-screen native list view.

A generic financial-report line (label + debit/credit/balance) with drill-down to
the underlying journal items. Reports compute these via the engine's
``_nc_compute_lines``; the engine stores them here for the list view.
"""
from odoo import fields, models
from odoo.exceptions import UserError


class NcollectionAccountReportLine(models.TransientModel):
    _name = 'ncollection.account.report.line'
    _description = 'NCollection Financial Report Line'
    _order = 'level, id'

    report_model = fields.Char(required=True)   # the wizard model that produced it
    report_res_id = fields.Integer()            # the wizard record id (for drill-down)
    account_id = fields.Many2one('account.account')
    label = fields.Char(string='Account')
    debit = fields.Monetary(currency_field='currency_id')
    credit = fields.Monetary(currency_field='currency_id')
    balance = fields.Monetary(currency_field='currency_id')
    currency_id = fields.Many2one('res.currency')
    level = fields.Integer(default=0)

    def action_drill_down(self):
        """Open the journal items behind this line (the report filters + this
        line's account). Odoo record rules still apply → no cross-scope leak."""
        self.ensure_one()
        wizard = self.env[self.report_model].browse(self.report_res_id)
        if not wizard.exists():
            raise UserError(self.env._(
                "The report run expired — re-generate the report to drill down."))
        return {
            'type': 'ir.actions.act_window',
            'name': self.env._("Journal Items — %s", self.label or ''),
            'res_model': 'account.move.line',
            'view_mode': 'list,form',
            'domain': wizard._nc_move_line_domain(account=self.account_id),
            'target': 'current',
        }
