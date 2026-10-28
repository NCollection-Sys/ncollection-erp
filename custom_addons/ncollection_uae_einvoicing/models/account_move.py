# -*- coding: utf-8 -*-
"""A document whose e-invoice has been submitted is final: it can no longer be reset to draft or cancelled.

The correction is a credit note or a new invoice (MD 243 Art 6(2)). Until submission exists (``locked`` is set when a
document leaves ``not_submitted``) nothing here changes how accounting behaves; the e-invoice is rebuilt from the books
and ``action_generate_xml`` refuses a stale payload."""

from odoo import fields, models
from odoo.exceptions import AccessError, UserError

USER_GROUP = 'ncollection_uae_einvoicing.group_einvoice_user'


class AccountMove(models.Model):
    _inherit = 'account.move'

    nc_einvoice_ids = fields.One2many('ncollection.einvoice', 'move_id', string='UAE e-invoices', groups=USER_GROUP)
    nc_einvoice_count = fields.Integer(compute='_compute_nc_einvoice_count', groups=USER_GROUP)

    def _compute_nc_einvoice_count(self):
        for move in self:
            move.nc_einvoice_count = len(move.nc_einvoice_ids)

    def action_nc_open_einvoice(self):
        """Open this document's e-invoice, creating it on first use. The right is checked here, at the ORM, not only
        by the button's visibility: the e-invoice model's own create guards (licence, posted customer document, the
        document's company) then apply as for any other caller."""
        self.ensure_one()
        if not self.env.user.has_group(USER_GROUP):
            raise AccessError(self.env._("Only a UAE e-invoicing user can open the e-invoice of a document."))
        record = self.env['ncollection.einvoice'].search([('move_id', '=', self.id)], order='revision desc', limit=1)
        if not record:
            record = self.env['ncollection.einvoice'].create({'move_id': self.id})
        return {
            'type': 'ir.actions.act_window',
            'name': self.env._('UAE e-invoice'),
            'res_model': 'ncollection.einvoice',
            'res_id': record.id,
            'view_mode': 'form',
            'target': 'current',
        }

    def _nc_refuse_if_einvoice_locked(self):
        if self.ids and self.env['ncollection.einvoice'].sudo().search_count(
            [('move_id', 'in', self.ids), ('locked', '=', True)]
        ):
            raise UserError(
                self.env._(
                    "This document has been submitted as a UAE e-invoice and is final. Issue a credit note or a new "
                    "invoice instead."
                )
            )

    def button_draft(self):
        self._nc_refuse_if_einvoice_locked()
        return super().button_draft()

    def button_cancel(self):
        self._nc_refuse_if_einvoice_locked()
        return super().button_cancel()
