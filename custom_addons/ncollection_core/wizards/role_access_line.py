# -*- coding: utf-8 -*-
"""What a role really allows, one line per model, read from ``ir.model.access`` when the dialog is opened.

Nothing here is kept: the lines are rebuilt from Odoo's own access rows every time, so the list can never disagree with
what is enforced, and there is no checkbox that changes anything (a role's rights come from its groups).
"""
from odoo import fields, models


class RoleAccessLine(models.TransientModel):
    _name = 'ncollection.role.access.line'
    _description = 'Access of a role to a model'
    _order = 'model_label, id'

    role_id = fields.Many2one('ncollection.role', required=True, ondelete='cascade', index=True)
    model_name = fields.Char(string='Model')
    model_label = fields.Char(string='Data')
    can_read = fields.Boolean(string='View')
    can_write = fields.Boolean(string='Edit')
    can_create = fields.Boolean(string='Create')
    can_unlink = fields.Boolean(string='Delete')

    def _compute_display_name(self):
        for line in self:
            line.display_name = line.model_label or line.model_name or ''
