from odoo import api, fields, models


class PaymentSettle(models.TransientModel):
    """Mark as Paid: shows exactly what will be booked, then calls ``account.payment._nc_mark_as_paid``."""
    _name = 'ncollection.payment.settle'
    _description = 'Mark a Payment as Paid'

    payment_id = fields.Many2one('account.payment', required=True, readonly=True, ondelete='cascade')
    date = fields.Date(required=True, default=fields.Date.context_today,
                       help="The day the money actually reached (or left) the bank or the cash box.")
    currency_id = fields.Many2one(related='payment_id.currency_id')
    amount = fields.Monetary(compute='_compute_preview', currency_field='currency_id')
    from_account_id = fields.Many2one('account.account', string='From', compute='_compute_preview')
    to_account_id = fields.Many2one('account.account', string='To', compute='_compute_preview')
    direction = fields.Selection([('in', 'Receipt'), ('out', 'Payment')], compute='_compute_preview')

    @api.depends('payment_id')
    def _compute_preview(self):
        for wiz in self:
            pay = wiz.payment_id
            lines = pay._nc_open_liquidity_lines()
            wiz.amount = abs(pay.nc_pending_amount)
            wiz.direction = 'in' if pay.payment_type == 'inbound' else 'out'
            outstanding = lines.account_id[:1]
            liquidity = pay.journal_id.default_account_id
            if wiz.direction == 'in':
                wiz.from_account_id, wiz.to_account_id = outstanding, liquidity
            else:
                wiz.from_account_id, wiz.to_account_id = liquidity, outstanding

    def action_confirm(self):
        self.ensure_one()
        payment = self.payment_id
        st_line = payment._nc_mark_as_paid(self.date)
        return {'type': 'ir.actions.client', 'tag': 'display_notification', 'params': {
            'type': 'success', 'title': self.env._('Marked as Paid'),
            'message': self.env._("%(payment)s is now %(state)s (bank transaction %(line)s).", payment=payment.name,
                                  state=dict(payment._fields['state']._description_selection(self.env))[payment.state],
                                  line=st_line.display_name),
            'next': {'type': 'ir.actions.act_window_close', 'infos': {'reload': True}}}}
