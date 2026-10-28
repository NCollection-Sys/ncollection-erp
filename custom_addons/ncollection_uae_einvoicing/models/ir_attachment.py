# -*- coding: utf-8 -*-
"""The e-invoice archive cannot be altered or deleted through the ORM, by anyone.

An attachment whose parent is a ``ncollection.einvoice`` (the generated XML and the provider responses) keeps its
content, name, owner and checksum for good, and:

* it can be CREATED only by the engine (``sudo``): a user cannot drop a file into the archive, where it could never
  be removed again, nor move an existing file into it;
* it can never be made public, and no access token can be minted for it by a user: the XML carries both parties'
  identifiers, so the only way to read it is through the record, with its company rule.

Together with the append-only history and the refusal to delete a record that holds an XML, this is the tenant-side
half of the retention rule; deleting the database or the filestore is a separate, infrastructure-level matter (see
retention.py).
"""

from odoo import api, models
from odoo.exceptions import UserError

_ARCHIVE_MODEL = 'ncollection.einvoice'
_IMMUTABLE = {
    'datas',
    'raw',
    'db_datas',
    'store_fname',
    'checksum',
    'name',
    'mimetype',
    'res_model',
    'res_id',
    'res_field',
    'type',
    'url',
    'file_size',
    'company_id',
    'description',
    'index_content',
}


class IrAttachment(models.Model):
    _inherit = 'ir.attachment'

    def _nc_in_einvoice_archive(self):
        return self.filtered(lambda attachment: attachment.res_model == _ARCHIVE_MODEL)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('res_model') == _ARCHIVE_MODEL:
                if not self.env.su:
                    raise UserError(self.env._("Files in the e-invoice archive are written by the engine only."))
                if vals.get('public') or vals.get('access_token'):
                    raise UserError(self.env._("An e-invoice archive file is never public."))
        records = super().create(vals_list)
        # Odoo also fills ``res_model``, ``res_id`` and ``public`` from ``default_<field>`` context keys AFTER the
        # checks above, so the result is checked too: nobody but the engine may have put a file in the archive, and
        # no file in it is ever public or tokenised
        archive = records._nc_in_einvoice_archive()
        if archive:
            if not self.env.su:
                raise UserError(self.env._("Files in the e-invoice archive are written by the engine only."))
            if any(attachment.public or attachment.access_token for attachment in archive):
                raise UserError(self.env._("An e-invoice archive file is never public."))
        return records

    def write(self, vals):
        keys = set(vals)
        if vals.get('res_model') == _ARCHIVE_MODEL and not self.env.su:
            raise UserError(self.env._("Files in the e-invoice archive are written by the engine only."))
        if self._nc_in_einvoice_archive():
            if _IMMUTABLE & keys:
                raise UserError(self.env._("An e-invoice archive file cannot be modified."))
            if vals.get('public') or vals.get('access_token'):
                raise UserError(self.env._("An e-invoice archive file is never public."))
        return super().write(vals)

    @api.ondelete(at_uninstall=False)
    def _nc_protect_einvoice_archive(self):
        if self._nc_in_einvoice_archive():
            raise UserError(self.env._("An e-invoice archive file cannot be deleted."))
