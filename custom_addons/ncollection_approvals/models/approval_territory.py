# -*- coding: utf-8 -*-
from odoo import fields, models


class LeadTerritory(models.Model):
    """P3-T07: a territory-based CRM lead-assignment rule — leads whose country
    or state matches are auto-assigned to the rule's salesperson/team."""
    _name = 'ncollection.lead.territory'
    _description = 'CRM Lead Territory (auto-assignment rule)'
    _order = 'sequence, id'

    name = fields.Char(required=True)
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    # A rule matches a lead by state first (more specific), then country.
    country_ids = fields.Many2many('res.country', string='Countries')
    state_ids = fields.Many2many('res.country.state', string='States/Regions')
    user_id = fields.Many2one(
        'res.users', string='Salesperson', required=True,
        help='Leads in this territory are assigned to this salesperson.')
    team_id = fields.Many2one('crm.team', string='Sales Team')
