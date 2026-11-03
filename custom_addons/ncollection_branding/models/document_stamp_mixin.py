# -*- coding: utf-8 -*-
"""The Document Stamp switch of ONE document (an invoice, a bill, an order...).

The Print Setting of a kind of document (``ncollection.document.type.nc_show_stamp``) is the DEFAULT; a document may
override it: ``default`` follows the Print Setting, ``on`` stamps this document even when its kind is printed without a
stamp, ``off`` prints this document without one. Only the decision lives here: which stamp, where and how is still the
one stamp engine (``ncollection.document.stamp`` + the overlay in ``ir_actions_report.py``); "on" with no stamp
template for the kind draws nothing.

Who may change it is the rule that already decides who may stamp a kind (``manager_group_ids``, Settings
administrators): an ordinary user must not be able to print an unstamped copy of a document that carries an official
stamp, which is why the report context key ``nc_no_stamp`` is honoured only for ``env.su``. Enforced on ``create`` and
``write`` (every RPC path), not only by the form.
"""
from odoo import api, fields, models
from odoo.exceptions import AccessError

STAMP_MODES = [('default', 'Default'), ('on', 'On'), ('off', 'Off')]


class NcDocumentStampMixin(models.AbstractModel):
    _name = 'nc.document.stamp.mixin'
    _description = 'Document Stamp on/off of a printed document'

    nc_stamp_mode = fields.Selection(
        STAMP_MODES, string='Document Stamp', default='default', required=True, copy=False,
        help="Default follows the Print Settings of this kind of document. On or Off applies to this document only. "
             "On draws the stamp chosen for this kind of document; with no stamp configured, nothing is drawn.")
    nc_stamp_default_on = fields.Boolean(
        string='Stamped by default', compute='_compute_nc_stamp_flags',
        help="What Default means for this document: its kind's Print Setting.")
    nc_stamp_mode_editable = fields.Boolean(compute='_compute_nc_stamp_flags')

    @api.depends_context('uid')
    def _compute_nc_stamp_flags(self):
        DocType = self.env['ncollection.document.type']
        for record in self:
            kind = record._nc_stamp_kind() if record.id else DocType.browse()
            record.nc_stamp_default_on = bool(not kind or kind.nc_show_stamp)
            record.nc_stamp_mode_editable = record._nc_may_set_stamp_mode(kind)

    def _nc_stamp_kind(self):
        """The kind this document prints as with ITS report (``_nc_print_report``, nc.document.print.mixin): the
        same answer the stamp overlay gets, so the form's "(stamped)" and the PDF agree."""
        self.ensure_one()
        DocType = self.env['ncollection.document.type'].sudo()
        report = self._nc_print_report() if hasattr(self, '_nc_print_report') else None
        return (report and DocType._nc_types_for(report, self)[:1]) or DocType._nc_kind_of(self)

    def _nc_may_set_stamp_mode(self, kind):
        """The rule of ``ncollection.document.stamp._nc_check_kinds_allowed``, for one document's kind."""
        user = self.env.user
        if self.env.su or user._nc_is_workspace_administrator():
            return True
        return bool(kind and kind.manager_group_ids & user.all_group_ids)

    def _nc_stamp_wanted(self, kind):
        """Whether THIS document is printed with its stamp: its own choice, else its kind's Print Setting."""
        self.ensure_one()
        mode = self.sudo().nc_stamp_mode
        if mode == 'on':
            return True
        if mode == 'off':
            return False
        return bool(not kind or kind.nc_show_stamp)

    def _nc_check_stamp_mode_allowed(self):
        for record in self:
            kind = record._nc_stamp_kind()
            if not record._nc_may_set_stamp_mode(kind):
                raise AccessError(self.env._(
                    "Only a Settings administrator or someone allowed to stamp %(kind)s may turn its Document Stamp "
                    "on or off.", kind=kind.name or record._description))

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        if not self.env.su:
            records.filtered(lambda r: r.nc_stamp_mode != 'default')._nc_check_stamp_mode_allowed()
        return records

    def write(self, vals):
        if 'nc_stamp_mode' in vals and not self.env.su:
            self._nc_check_stamp_mode_allowed()
        return super().write(vals)
