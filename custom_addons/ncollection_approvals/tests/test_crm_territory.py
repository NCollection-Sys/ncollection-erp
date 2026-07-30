# -*- coding: utf-8 -*-
"""P3-T07: CRM leads are auto-assigned to a salesperson by territory."""
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestCrmTerritory(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.ae = cls.env.ref('base.ae')          # United Arab Emirates
        cls.us = cls.env.ref('base.us')
        cls.rep = cls.env['res.users'].create({
            'login': 'nc_crm_rep', 'name': 'Gulf Rep',
            'group_ids': [(6, 0, [cls.env.ref('sales_team.group_sale_salesman').id])]})
        cls.territory = cls.env['ncollection.lead.territory'].create({
            'name': 'Gulf', 'user_id': cls.rep.id,
            'country_ids': [(6, 0, [cls.ae.id])]})

    def test_lead_in_territory_is_auto_assigned(self):
        lead = self.env['crm.lead'].create({
            'name': 'AE Lead', 'country_id': self.ae.id})
        self.assertEqual(lead.nc_territory_id, self.territory)
        self.assertEqual(lead.user_id, self.rep)
        self.assertTrue(lead.activity_ids)  # the assignee is notified

    def test_explicit_salesperson_not_overridden(self):
        other = self.env.ref('base.user_admin')
        lead = self.env['crm.lead'].create({
            'name': 'AE Lead w/ owner', 'country_id': self.ae.id,
            'user_id': other.id})
        self.assertEqual(lead.user_id, other)
        self.assertFalse(lead.nc_territory_id)

    def test_lead_outside_any_territory_unassigned(self):
        lead = self.env['crm.lead'].create({
            'name': 'US Lead', 'country_id': self.us.id})
        self.assertFalse(lead.nc_territory_id)
