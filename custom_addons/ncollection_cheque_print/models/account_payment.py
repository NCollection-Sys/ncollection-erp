# -*- coding: utf-8 -*-
from odoo import api, fields, models


class AccountPayment(models.Model):
    """Cheque printing is an ADDITIONAL capability over the payment. Nothing here changes how a payment is created,
    posted, reconciled or cancelled, and no print job ever writes to the payment."""
    _inherit = 'account.payment'

    nc_cheque_job_ids = fields.One2many(
        'ncollection.cheque.print.job', inverse_name='payment_id', string='Cheque print jobs', readonly=True)
    nc_cheque_job_count = fields.Integer(compute='_compute_nc_cheque_job_count', string='Cheques')

    @api.depends('nc_cheque_job_ids')
    def _compute_nc_cheque_job_count(self):
        for payment in self:
            payment.nc_cheque_job_count = len(payment.nc_cheque_job_ids)

    def action_nc_print_cheque(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window', 'name': self.env._("Print cheque"),
            'res_model': 'ncollection.cheque.print.wizard', 'view_mode': 'form', 'target': 'new',
            'context': {'default_payment_id': self.id},
        }

    def action_nc_open_cheque_jobs(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window', 'name': self.env._("Cheques"),
            'res_model': 'ncollection.cheque.print.job', 'view_mode': 'list,form',
            'domain': [('payment_id', '=', self.id)],
        }
