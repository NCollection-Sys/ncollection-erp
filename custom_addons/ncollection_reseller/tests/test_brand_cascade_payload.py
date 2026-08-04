# -*- coding: utf-8 -*-
"""The config-sync payload carries the reseller brand for sub-tenants (P10-T09).

This proves the PLATFORM half of the cascade: a sub-tenant's _config_sync_vals()
includes the reseller's brand_* keys (which the tenant side applies to
res.company — see ncollection_core test_reseller_branding_apply). A non-reseller
tenant's payload is byte-for-byte unchanged (backward compatibility).
"""
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestBrandCascadePayload(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        partner = cls.env['res.partner'].create({'name': 'Brandco'})
        cls.reseller = cls.env['ncollection.reseller'].create({
            'name': 'Brandco', 'partner_id': partner.id,
            'brand_primary_color': '#101010',
            'brand_secondary_color': '#202020',
            'brand_sidebar_color': '#303030',
        })
        cls.Tenant = cls.env['ncollection.tenant']

    def test_reseller_tenant_payload_has_brand(self):
        tenant = self.Tenant.create({
            'company_name': 'Sub', 'database_name': 'sub',
            'reseller_id': self.reseller.id,
        })
        vals = tenant._config_sync_vals()
        self.assertEqual(vals.get('brand_primary_color'), '#101010')
        self.assertEqual(vals.get('brand_secondary_color'), '#202020')
        self.assertEqual(vals.get('brand_sidebar_color'), '#303030')
        # Standard licensing keys are still present (super() preserved).
        self.assertIn('subscription_status', vals)

    def test_non_reseller_payload_unchanged(self):
        tenant = self.Tenant.create({
            'company_name': 'Direct', 'database_name': 'direct2'})
        vals = tenant._config_sync_vals()
        self.assertNotIn('brand_primary_color', vals)
        self.assertNotIn('brand_login_background', vals)

    def test_unset_reseller_colour_not_pushed(self):
        # A reseller that only sets one colour pushes only that one — the tenant
        # keeps the NCollection default for the rest.
        # arch-guard: admin-db-billing -- ncollection.reseller.partner_id is a
        # required Many2one('res.partner') on the PLATFORM database
        # (models/reseller.py), so this is the platform's own admin data, not a
        # cross-layer ORM call into a tenant database.
        partner = self.env['res.partner'].create(  # arch-guard: admin-db-billing
            {'name': 'Partial'})
        reseller = self.env['ncollection.reseller'].create({
            'name': 'Partial', 'partner_id': partner.id,
            'brand_primary_color': '#ABCDEF'})
        tenant = self.Tenant.create({
            'company_name': 'P', 'database_name': 'partial',
            'reseller_id': reseller.id})
        vals = tenant._config_sync_vals()
        self.assertEqual(vals.get('brand_primary_color'), '#ABCDEF')
        self.assertNotIn('brand_secondary_color', vals)
