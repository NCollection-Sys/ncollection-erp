"""Match a bank transaction with a payment batch (opened from the matching workspace)."""
from odoo import api, fields, models


class BatchMatch(models.TransientModel):
    _name = 'ncollection.bank.batch.match'
    _description = 'Match a Payment Batch'

    statement_line_id = fields.Many2one('account.bank.statement.line', required=True, readonly=True)
    journal_id = fields.Many2one(related='statement_line_id.journal_id')
    batch_id = fields.Many2one('ncollection.bank.payment.batch', required=True,
                               domain="[('journal_id', '=', journal_id), ('state', '=', 'open')]")
    batch_amount = fields.Monetary(related='batch_id.amount', currency_field='currency_id')
    transaction_amount = fields.Monetary(related='statement_line_id.amount', currency_field='currency_id')
    currency_id = fields.Many2one(related='statement_line_id.currency_id')

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        ctx = self.env.context
        line_id = ctx.get('default_statement_line_id') or (
            ctx.get('active_model') == 'account.bank.statement.line' and ctx.get('active_id'))
        if line_id:
            res['statement_line_id'] = line_id
        return res

    def action_match(self):
        self.ensure_one()
        self.statement_line_id.action_nc_match_batch(self.batch_id)
        return {'type': 'ir.actions.act_window_close'}
