# -*- coding: utf-8 -*-
"""No breadcrumb shows a technical identifier: a NEW record of a model without a name is titled by its model.

A window action that opens a form on a fresh record puts that record's ``display_name`` in the breadcrumb. For a model
with no name of its own (no ``_rec_name``), Odoo's default ``display_name`` is ``"<model>,<id>"``: for a fresh record
``ncollection.subscription.dashboard,NewId_0x...``, and measured on the real platform also Inventory's "Adjust
Valuation" (``product.value``) and "My Dashboard" (``board.board``). The fix belongs to the data source, once for every
model: such a NEW record is titled by its model's description (``ir.model``, in the user's language). Saved records,
and every model that has a name, keep Odoo's own ``display_name``; a model with its own title (the SaaS Summary)
overrides this as before.
"""
from odoo import models
from odoo.orm.identifiers import NewId


class Base(models.AbstractModel):
    _inherit = 'base'

    def _compute_display_name(self):
        super()._compute_display_name()
        if self._rec_name:
            return
        fresh = self.filtered(lambda record: isinstance(record.id, NewId) and not record.id.origin)
        if fresh:
            title = self.env['ir.model']._get(self._name).name or self._description or self._name
            for record in fresh:
                record.display_name = title
