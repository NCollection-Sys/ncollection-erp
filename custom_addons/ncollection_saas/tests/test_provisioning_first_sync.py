# -*- coding: utf-8 -*-
"""A tenant is synced as soon as its provisioning is done (and therefore receives the platform's System Branding).

Measured in production: First Company was created ACTIVE, provisioned, and showed the shipped default branding
("NCollection ERP", the shipped logo) because nothing queued its first config push: ``_mark_done`` only reached a sync
through ``action_activate``, i.e. for a TRIAL tenant. The first push is the one whose response advertises the tenant's
capabilities, after which the same job sends the platform's System Branding (system_branding_sync.py).
"""
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestProvisioningFirstSync(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env['ir.config_parameter'].sudo().set_param('ncollection_saas.provisioning_quota_per_hour', '0')

    def _provisioned(self, status, db):
        tenant = self.env['ncollection.tenant'].create({
            'company_name': 'Sync %s' % db, 'database_name': db, 'status': status,
            'database_status': 'provisioning', 'email': 'owner@%s.example' % db})
        job = self.env['ncollection.provisioning.job'].create({
            'tenant_id': tenant.id, 'database_name': db, 'status': 'running'})
        return tenant, job

    def _sync_jobs(self, tenant):
        return self.env['queue.job'].search([
            ('method_name', '=', 'sync_workspace_config'),
            ('identity_key', '=', 'nc-config-sync-%s' % tenant.id)])

    def test_an_active_tenant_is_synced_when_its_provisioning_is_done(self):
        tenant, job = self._provisioned('active', 'firstsyncact')
        self.assertFalse(self._sync_jobs(tenant))
        job._mark_done()
        self.assertEqual(tenant.database_status, 'ready')
        jobs = self._sync_jobs(tenant)
        self.assertEqual(len(jobs), 1, 'exactly one config-sync job, on the existing queue')
        self.assertEqual(jobs.model_name, 'ncollection.tenant')
        self.assertEqual(jobs.records, tenant)

    def test_a_trial_tenant_is_not_given_a_second_sync_job_by_its_activation(self):
        tenant, job = self._provisioned('trial', 'firstsynctrial')
        job._mark_done()
        self.assertEqual(tenant.status, 'active', 'the trial was activated by _mark_done')
        self.assertEqual(len(self._sync_jobs(tenant)), 1,
                         'the provisioning sync and the activation sync collapse on the existing identity key')

    def test_the_tenant_company_identity_is_not_part_of_the_push(self):
        """The push carries System Branding and contact details; never the tenant company's name or logo."""
        tenant, job = self._provisioned('active', 'firstsyncid')
        job._mark_done()
        vals = tenant._config_sync_vals()
        for key in vals.get('nc_company_identity', {}):
            self.assertIn(key, ('email', 'phone', 'website'))
        self.assertNotIn('company_name', vals)
        self.assertNotIn('logo', vals)
