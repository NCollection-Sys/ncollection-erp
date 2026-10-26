# -*- coding: utf-8 -*-
"""The platform pushes its System Branding and the company's contact details to its tenants (config-sync).

The keys are sent only to a tenant that advertised it accepts them (an older tenant's whitelist would refuse the
whole push, licensing included); a failed push drops the capability so the next attempt is the known-good payload;
a change to the platform's System Branding re-syncs every ready tenant through the one queue. The release gate's new
``absent`` list is shown on the tenant form and its action names the install.
"""
from unittest.mock import patch

from odoo.tests import TransactionCase, tagged

from odoo.addons.ncollection_saas.models.tenant_release import TenantRelease


@tagged('post_install', '-at_install')
class TestSystemBrandingSync(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env['ir.config_parameter'].sudo().set_param('ncollection_saas.base_domain', 'example.com')
        cls.tenant = cls.env['ncollection.tenant'].with_user(cls.env.ref('base.user_root')).create({
            'company_name': 'Sync Co', 'database_name': 'syncbrand', 'database_status': 'ready', 'status': 'active',
            'email': 'owner@sync.example'})

    def test_nothing_is_sent_before_the_tenant_advertises_it(self):
        vals = self.tenant._config_sync_vals()
        self.assertNotIn('nc_system_branding', vals)
        self.assertNotIn('nc_company_identity', vals)

    def test_once_advertised_the_platform_identity_and_the_company_details_ride_the_push(self):
        self.tenant._record_sync_capabilities({'accepts_rate': True, 'accepts_system_branding': True})
        self.assertTrue(self.tenant.config_sync_accepts_system_branding)
        vals = self.tenant._config_sync_vals()
        brand = self.env['ncollection.product.branding'].nc_brand()
        self.assertEqual(vals['nc_system_branding']['name'], brand['name'])
        self.assertEqual(set(vals['nc_system_branding']),
                         {'name', 'short_name', 'browser_title', 'copyright_text', 'primary_color',
                          'secondary_color', 'logo', 'login_logo', 'admin_logo', 'favicon'})
        self.assertEqual(vals['nc_company_identity'], {'email': 'owner@sync.example', 'phone': '',
                                                       'website': self.tenant.tenant_url},
                         'nothing is invented: no phone was entered, so it is sent empty')

    def test_a_failed_push_drops_the_capability(self):
        self.tenant._record_sync_capabilities({'accepts_system_branding': True})
        self.tenant._config_sync_record('transient', error='boom')
        self.assertFalse(self.tenant.config_sync_accepts_system_branding)

    def test_a_new_workspace_gets_the_identity_on_its_first_sync_not_hours_later(self):
        pushed = []

        def push(tenant, db, vals):
            pushed.append(sorted(vals))
            tenant._record_sync_capabilities({'accepts_system_branding': True})
            return True

        with patch.object(type(self.tenant), '_config_sync_push', autospec=True, side_effect=push):
            self.tenant.sync_workspace_config()
            self.assertEqual(len(pushed), 2, 'the advertised capability is used at once')
            self.assertNotIn('nc_system_branding', pushed[0])
            self.assertIn('nc_system_branding', pushed[1])
            self.tenant.sync_workspace_config()
            self.assertEqual(len(pushed), 3, 'one push once the capability is known: never a loop')

    def test_a_change_of_the_platform_identity_resyncs_every_ready_tenant(self):
        with patch.object(type(self.tenant), '_config_sync_enqueue', autospec=True) as enqueue:
            self.env['ncollection.product.branding'].with_user(self.env.ref('base.user_root'))._nc_record().write(
                {'short_name': 'Renamed'})
        self.assertTrue(enqueue.called)
        self.assertIn(self.tenant, enqueue.call_args[0][0])

    def test_the_tenant_form_names_the_modules_to_install(self):
        body = {'state': 'upgrade_required', 'behind': [], 'required': [],
                'absent': ['ncollection_purchase_documents', 'ncollection_sale_documents'], 'missing': [],
                'transitional': [], 'schema_gap': False, 'hold': '', 'hold_reason': '', 'db_fingerprint': 'a' * 64,
                'release_fingerprint': 'b' * 64, 'compatible': False}
        with patch.object(TenantRelease, '_nc_release_call', return_value=(200, body)):
            self.tenant._nc_release_record(200, body)
        self.assertEqual(self.tenant.nc_release_state, 'upgrade_required')
        self.assertIn('ncollection_sale_documents: auto-installed by the code, not installed here',
                      self.tenant.nc_release_detail)
        self.assertIn('ncollection_sale_documents', self.tenant.nc_release_required)
        self.assertIn('installs ncollection_purchase_documents, ncollection_sale_documents',
                      self.tenant._nc_release_action_text('upgrade_required', body))
