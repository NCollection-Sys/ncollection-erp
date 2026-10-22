# -*- coding: utf-8 -*-
"""The "Files" of a business document, for the list-view paperclip and its viewer.

Nothing new is stored. The files are Odoo's own ``ir.attachment`` rows of the record (``res_model`` = the model,
``res_field`` empty, so a binary field kept as an attachment is not a "file") and are read AS THE CALLING USER, so
Odoo's own rules decide what is visible: a user who cannot read the document cannot list, count or open its files,
in the same company or any other. The list only ever loads a COUNT (one grouped query for the page); the metadata is
fetched when the paperclip is clicked, and the content only when the viewer asks the standard ``/web/content`` /
``/web/image`` routes for it.

A model opts in by inheriting ``nc.attachment.mixin`` and putting ``nc_attachment_count`` with the
``nc_attachment_count`` widget in its list (ncollection_core/static/src/attachments).
"""
from odoo import api, fields, models


class NcAttachmentMixin(models.AbstractModel):
    _name = 'nc.attachment.mixin'
    _description = 'Files of a business document'

    nc_attachment_count = fields.Integer(
        string='Files', compute='_compute_nc_attachment_count',
        help="Number of files attached to this document (not the binary fields stored as attachments).")

    @api.depends()
    def _compute_nc_attachment_count(self):
        counts = dict(self.env['ir.attachment']._read_group(
            [('res_model', '=', self._name), ('res_id', 'in', self.ids), ('res_field', '=', False)],
            ['res_id'], ['__count']))
        for record in self:
            record.nc_attachment_count = counts.get(record.id, 0)

    def nc_get_attachments(self):
        """Metadata (never content) of this document's files, for the viewer, oldest first."""
        self.ensure_one()
        self.check_access('read')
        attachments = self.env['ir.attachment'].search([
            ('res_model', '=', self._name), ('res_id', '=', self.id), ('res_field', '=', False),
        ], order='id')
        return [{
            'id': attachment.id,
            'name': attachment.name or '',
            'mimetype': attachment.mimetype or '',
            'checksum': attachment.checksum or '',
            'type': attachment.type,
            'url': attachment.url if attachment.type == 'url' else '',
        } for attachment in attachments]
