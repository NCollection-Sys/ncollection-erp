# -*- coding: utf-8 -*-
from odoo import fields, models


class ChequeReasonWizard(models.TransientModel):
    _name = 'ncollection.cheque.reason.wizard'
    _description = 'Reprint or void a cheque'

    job_id = fields.Many2one('ncollection.cheque.print.job', required=True, readonly=True)
    kind = fields.Selection([('reprint', 'Reprint'), ('void', 'Void')], required=True, readonly=True)
    reason = fields.Text(
        required=True, help="Why is this cheque being reprinted or voided? It is kept in the history.")

    def action_confirm(self):
        self.ensure_one()
        if self.kind == 'reprint':
            return self.job_id.action_reprint(self.reason)
        self.job_id.action_void(self.reason)
        return {'type': 'ir.actions.act_window_close'}
