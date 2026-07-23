# -*- coding: utf-8 -*-
"""P2-T12 lifecycle side effects in the SaaS layer.

The subscription state machine lives in ncollection_subscription; here we prove
the SaaS-layer reactions: suspension/reactivation/termination drive the tenant
lifecycle status (which config-sync projects into the tenant DB, where the
P2-T03 interstitial gates access), and starting a trial provisions the tenant.
"""
from unittest.mock import patch

from odoo.tests import TransactionCase, tagged

ENGINE = 'odoo.addons.ncollection_saas.models.provisioning_job.ProvisioningJob'


@tagged('post_install', '-at_install')
class TestLifecycleEffects(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.plan = cls.env['ncollection.subscription.plan'].create({
            'name': 'Effects Plan', 'code': 'EFFECTS', 'max_users': 5, 'trial_days': 14})

    def _tenant(self, **kw):
        vals = {'company_name': 'Effects Co', 'plan_id': self.plan.id}
        vals.update(kw)
        return self.env['ncollection.tenant'].create(vals)

    def _sub(self, tenant, status):
        sub = self.env['ncollection.subscription'].create({
            'name': 'SUB-FX', 'tenant_id': tenant.id, 'plan_id': self.plan.id, 'status': status})
        tenant.subscription_id = sub.id
        return sub

    def test_suspend_drives_tenant_suspended(self):
        tenant = self._tenant(status='active')
        sub = self._sub(tenant, 'active')
        sub.action_suspend()
        self.assertEqual(sub.status, 'suspended')
        self.assertEqual(tenant.status, 'suspended',
                         "suspending the subscription must suspend the tenant (blocks access)")

    def test_reactivate_drives_tenant_active(self):
        tenant = self._tenant(status='suspended')
        sub = self._sub(tenant, 'suspended')
        sub.action_reactivate()
        self.assertEqual(sub.status, 'active')
        self.assertEqual(tenant.status, 'active', "reactivation must restore tenant access")

    def test_terminate_drives_tenant_expired(self):
        tenant = self._tenant(status='suspended')
        sub = self._sub(tenant, 'suspended')
        sub.action_terminate()
        self.assertEqual(sub.status, 'terminated')
        self.assertEqual(tenant.status, 'expired', "termination leaves the tenant access-blocked")

    def test_start_trial_provisions_tenant(self):
        tenant = self._tenant(status='trial', database_status='not_provisioned')
        sub = self._sub(tenant, 'draft')
        with patch('%s.action_run' % ENGINE):   # don't spawn a real database
            sub.action_start_trial()
        self.assertEqual(sub.status, 'trial')
        job = self.env['ncollection.provisioning.job'].search([('tenant_id', '=', tenant.id)])
        self.assertTrue(job, "starting a trial must provision the tenant (full plan access)")
