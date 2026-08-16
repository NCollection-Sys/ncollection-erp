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
    # id order == the order _nc_compute_lines() produced (and PDF/XLSX render) —
    # so all three channels agree. 'level' is for rendering (indent/bold), NEVER
    # a sort key (that would bucket hierarchical reports by level).
    _order = 'id'

    # NOTE: `readonly=True` is a FORM-VIEW hint only — Odoo's write() path gates
    # on field.groups, never on field.readonly, so an RPC call_kw could set
    # these on a row the caller owns. TWO controls now stand behind that, and
    # neither is `readonly`:
    #   1. ir.model.access.csv grants perm_write=0 on this model (#318) — these
    #      rows are only ever created and unlinked, never written, so the
    #      forgery surface is removed rather than merely guarded;
    #   2. action_drill_down below still validates report_model before the
    #      dynamic dispatch (#313), because (1) does not bind a superuser or a
    #      future ACL edit.
    report_model = fields.Char(required=True, readonly=True)   # producing wizard model
    report_res_id = fields.Integer(readonly=True)              # producing wizard id
    account_id = fields.Many2one('account.account', readonly=True)
    partner_id = fields.Many2one('res.partner', readonly=True)  # partner-report drill-down
    label = fields.Char()  # views set their own header ("Account"/"Label")
    debit = fields.Monetary(currency_field='currency_id')
    credit = fields.Monetary(currency_field='currency_id')
    balance = fields.Monetary(currency_field='currency_id')
    # Opening/closing are used by the Trial Balance (nullable for other reports).
    opening_balance = fields.Monetary(currency_field='currency_id')
    closing_balance = fields.Monetary(currency_field='currency_id')
    # F2-T03 comparison columns (FPA §Balance Sheet / §Profit & Loss: Current,
    # Previous, Variance, Variance %). Nullable for reports without a comparison
    # — exactly how opening/closing above are nullable outside the Trial Balance.
    current_amount = fields.Monetary(currency_field='currency_id')
    previous_amount = fields.Monetary(currency_field='currency_id')
    variance = fields.Monetary(currency_field='currency_id')
    # Not Monetary: a percentage is not money, and rendering it with a currency
    # symbol in the list/PDF would be wrong.
    variance_pct = fields.Float(digits=(16, 2))
    # F2-T08: this row's share of a report-defined base (Profitability renders
    # it as "% of Revenue", which is what a margin IS). Same reasoning as
    # variance_pct — a ratio put in a Monetary field would render "AED 70.00"
    # against a row labelled "Net Margin".
    ratio_pct = fields.Float(digits=(16, 2))
    # F2-T06: UAE FTA VAT 201 box identifier and tax values
    box = fields.Char()
    vat_amount = fields.Monetary(currency_field='currency_id', string='VAT Amount')
    recoverable_vat = fields.Monetary(currency_field='currency_id', string='Recoverable VAT')
    currency_id = fields.Many2one('res.currency')
    level = fields.Integer(default=0)

    def action_drill_down(self):
        """Open the journal items behind this line (the report filters + this
        line's account). Odoo record rules still apply → no cross-scope leak."""
        self.ensure_one()
        # SECURITY: `report_model` reaches a dynamic self.env[...] dispatch, and
        # `readonly=True` does NOT stop an RPC write() from setting it to an
        # arbitrary model (Odoo gates write on field.groups, not field.readonly).
        # Unchecked, the .browse().exists() below is a raw SELECT that applies
        # NEITHER ACL NOR ir.rule — an existence oracle over the whole registry
        # ("does res.users id 3 exist?") for any accounting-readonly user.
        # Resolving through the report engine's own class fails closed and needs
        # no hand-maintained list: only models built on the engine can pass.
        model = self.env.get(self.report_model)
        if model is None or not isinstance(
                model, type(self.env['ncollection.account.report'])):
            raise UserError(self.env._(
                "This report line does not point at a financial report."))
        wizard = model.browse(self.report_res_id)
        if not wizard.exists():
            raise UserError(self.env._(
                "The report run expired — re-generate the report to drill down."))
        return {
            'type': 'ir.actions.act_window',
            'name': self.env._("Journal Items — %s", self.label or ''),
            'res_model': 'account.move.line',
            'view_mode': 'list,form',
            'domain': wizard._nc_move_line_domain(
                account=self.account_id, partner=self.partner_id),
            'target': 'current',
        }
