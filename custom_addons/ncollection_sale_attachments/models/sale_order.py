# -*- coding: utf-8 -*-
"""Attachments of a quotation / sales order, for the list-view viewer.

Nothing new is stored. The attachments are Odoo's own ``ir.attachment`` rows (``res_model='sale.order'``) and
are read AS THE CALLING USER, so Odoo's own rules decide what is visible: a user who cannot read an order cannot
list, count or open its attachments, in the same company or any other. The list view only ever loads a COUNT
(one grouped query for the whole page); the file metadata is fetched when the icon is clicked, and the file
content only when the viewer asks the standard ``/web/content`` / ``/web/image`` routes for it.
"""
from odoo import api, fields, models


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    nc_attachment_count = fields.Integer(
        string='Attachments', compute='_compute_nc_attachment_count',
        help="Number of files attached to this document (not the binary fields stored as attachments).")

    @api.depends()
    def _compute_nc_attachment_count(self):
        counts = dict(self.env['ir.attachment']._read_group(
            [('res_model', '=', 'sale.order'), ('res_id', 'in', self.ids), ('res_field', '=', False)],
            ['res_id'], ['__count']))
        for order in self:
            order.nc_attachment_count = counts.get(order.id, 0)

    def nc_get_attachments(self):
        """Metadata (never content) of this document's attachments, for the viewer, oldest first."""
        self.ensure_one()
        self.check_access('read')
        attachments = self.env['ir.attachment'].search([
            ('res_model', '=', 'sale.order'), ('res_id', '=', self.id), ('res_field', '=', False),
        ], order='id')
        return [{
            'id': attachment.id,
            'name': attachment.name or '',
            'mimetype': attachment.mimetype or '',
            'checksum': attachment.checksum or '',
            'type': attachment.type,
            'url': attachment.url if attachment.type == 'url' else '',
        } for attachment in attachments]
