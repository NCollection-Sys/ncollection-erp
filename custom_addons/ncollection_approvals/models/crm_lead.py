# -*- coding: utf-8 -*-
from odoo import api, fields, models

_TODO_ACT = 'mail.mail_activity_data_todo'


class CrmLead(models.Model):
    _inherit = 'crm.lead'

    # P3-T07: the territory rule a lead was auto-assigned through (audit trail).
    nc_territory_id = fields.Many2one(
        'ncollection.lead.territory', string='Territory', copy=False, index=True)

    @api.model_create_multi
    def create(self, vals_list):
        leads = super().create(vals_list)
        # Only auto-assign when the caller didn't set a salesperson explicitly —
        # crm.lead assigns a default user_id on create, so we gate on the vals,
        # not on the (already-populated) user_id field.
        for lead, vals in zip(leads, vals_list):
            if not vals.get('user_id'):
                lead._nc_assign_territory()
        return leads

    def _nc_match_territory(self):
        """Return the first territory rule matching this lead — state before
        country (more specific wins), rules ordered by sequence."""
        self.ensure_one()
        Territory = self.env['ncollection.lead.territory']
        if self.state_id:
            rule = Territory.search([('state_ids', 'in', self.state_id.id)], limit=1)
            if rule:
                return rule
        if self.country_id:
            # Country tier: only rules that are NOT state-specific. A rule that
            # constrains states is matched via the state tier alone, so it never
            # silently becomes a country-wide catch-all for other states.
            rule = Territory.search([
                ('country_ids', 'in', self.country_id.id),
                ('state_ids', '=', False)], limit=1)
            if rule:
                return rule
        return Territory.browse()

    def _nc_assign_territory(self):
        """Assign the lead to its territory's salesperson/team and notify them.
        Called only when no explicit salesperson was given (see create)."""
        self.ensure_one()
        rule = self._nc_match_territory()
        if not rule:
            return
        vals = {'nc_territory_id': rule.id, 'user_id': rule.user_id.id}
        if rule.team_id:
            vals['team_id'] = rule.team_id.id
        # System routing: write under sudo so a low-privileged creator (who may
        # lack write access to a lead core has already assigned to someone else)
        # cannot block it. Values come only from manager-owned territory rules.
        lead = self.sudo()
        lead.write(vals)
        lead.activity_schedule(
            _TODO_ACT,
            summary=self.env._("New lead assigned: %s", self.name or ''),
            note=self.env._("Auto-assigned from territory '%s'.", rule.name),
            user_id=rule.user_id.id)
