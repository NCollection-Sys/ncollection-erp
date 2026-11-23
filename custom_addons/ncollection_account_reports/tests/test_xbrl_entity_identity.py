# -*- coding: utf-8 -*-
"""Request 10: an XBRL instance identifies the REPORTING tenant, never NCollection: the scheme is the tenant's own
address (or the one the filing profile mandates), the identifier its TRN or a stable company id."""
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestXbrlEntityIdentity(TransactionCase):

    def setUp(self):
        super().setUp()
        self.params = self.env['ir.config_parameter'].sudo()
        self.Xbrl = self.env['ncollection.account.report.xbrl']

    def test_the_tenant_domain_is_the_scheme(self):
        self.params.set_param('web.base.url', 'https://ahmed.balancecloud.ae')
        self.assertEqual(self.Xbrl._nc_entity_scheme(), 'https://ahmed.balancecloud.ae/xbrl/entity')
        self.params.set_param('web.base.url', 'https://erp.customer-example.com')
        self.assertEqual(self.Xbrl._nc_entity_scheme(), 'https://erp.customer-example.com/xbrl/entity')

    def test_never_ncollection_and_a_mandated_scheme_wins(self):
        self.params.set_param('web.base.url', 'http://ncollection.localhost:8069')
        scheme = self.Xbrl._nc_entity_scheme()
        self.assertNotIn('ncollection.com', scheme)
        self.assertTrue(scheme.startswith('urn:x-tenant:'))
        self.params.set_param('ncollection.xbrl_entity_scheme', 'http://standards.iso.org/iso/17442')
        self.assertEqual(self.Xbrl._nc_entity_scheme(), 'http://standards.iso.org/iso/17442')
        self.params.set_param('ncollection.xbrl_entity_scheme', 'not a uri')
        self.assertTrue(self.Xbrl._nc_entity_scheme().startswith('urn:x-tenant:'))

    def test_identifier_is_the_company_s_own(self):
        draft = self.env['res.company'].new({'name': 'X', 'vat': 'AE-100 123 456 700 003'})
        self.assertEqual(self.Xbrl._nc_entity_identifier(draft), 'AE-100123456700003')
        company = self.env['res.company'].create({'name': 'XBRL No TRN'})
        self.assertEqual(self.Xbrl._nc_entity_identifier(company), 'company-%d' % company.id)
