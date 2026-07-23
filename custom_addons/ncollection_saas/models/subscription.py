# -*- coding: utf-8 -*-
"""Auto-provisioning trigger (P2-T02).

Activating a subscription must yield a working tenant with zero manual steps.
The trigger lives here (ncollection_saas), not in ncollection_subscription,
because only this layer may depend on queue_job / the provisioning engine
(two-layer separation, Rule 3): the subscription module owns the lifecycle
state machine; the SaaS layer reacts to it.
"""

import logging

from odoo import models

_logger = logging.getLogger(__name__)


class Subscription(models.Model):
    _inherit = 'ncollection.subscription'

    # Subscription status -> the tenant lifecycle status that mirrors it. The
    # tenant status is what config-sync projects into the tenant DB, where the
    # P2-T03 interstitial gates access — so the subscription lifecycle must
    # drive it. 'trial' needs no tenant move (the tenant is already 'trial').
    _TENANT_STATUS_METHOD = {
        'active': 'action_activate',
        'suspended': 'action_suspend',
        'expired': 'action_expire',
    }

    def _drive_tenant_status(self, target):
        """Move the tenant lifecycle to mirror the subscription, going through
        the tenant's own guarded transition (which enqueues config-sync so the
        interstitial updates). Fail-soft: a tenant-state guard never blocks the
        subscription transition."""
        self.ensure_one()
        tenant = self.tenant_id
        if not tenant or tenant.status == target:
            return
        method = self._TENANT_STATUS_METHOD.get(target)
        if method and target in tenant._ALLOWED_TRANSITIONS.get(tenant.status, set()):
            getattr(tenant, method)()

    def action_activate(self):
        """draft/trial -> active: auto-provision the tenant, then mirror the
        tenant to 'active' (also the trial-conversion path)."""
        res = super().action_activate()
        for sub in self:
            sub._trigger_provisioning()
            sub._drive_tenant_status('active')
        return res

    def action_start_trial(self):
        """draft -> trial: provision so the trial has full plan access; no
        invoice is raised (billing does not hook the trial)."""
        res = super().action_start_trial()
        for sub in self:
            sub._trigger_provisioning()
        return res

    def action_suspend(self):
        """-> suspended: block tenant access via the P2-T03 interstitial."""
        res = super().action_suspend()
        for sub in self:
            sub._drive_tenant_status('suspended')
        return res

    def action_reactivate(self):
        """-> active: restore tenant access (reactivation, incl. during grace)."""
        res = super().action_reactivate()
        for sub in self:
            sub._drive_tenant_status('active')
        return res

    def action_terminate(self):
        """-> terminated: project a terminal, access-blocked tenant."""
        res = super().action_terminate()
        for sub in self:
            sub._drive_tenant_status('expired')
        return res

    def write(self, vals):
        """A plan change on a subscription (upgrade/downgrade) propagates to the
        tenant: it keeps tenant.plan_id (the config source of truth, matching
        the provisioning seed) in step, then pushes the new module set / limits
        into the tenant workspace (P2-T03)."""
        res = super().write(vals)
        if 'plan_id' in vals:
            to_sync = self.env['ncollection.tenant']
            for sub in self:
                tenant = sub.tenant_id
                # only the tenant's CURRENT subscription drives its plan
                if tenant and tenant.subscription_id == sub and tenant.plan_id != sub.plan_id:
                    tenant.plan_id = sub.plan_id
                if tenant:
                    to_sync |= tenant
            to_sync._config_sync_enqueue()
        return res

    def _trigger_provisioning(self):
        """Create + enqueue a provisioning job for this subscription's tenant.

        Idempotent and safe to call repeatedly:
        - only fires for a tenant not already provisioned/provisioning,
        - never creates a second job while one is queued/running/done,
        - generates a routable DB name if the tenant lacks one.
        Enqueues on the dedicated runner (async); the manual "Provision" button
        remains available on the job for retries.
        """
        self.ensure_one()
        tenant = self.tenant_id
        if not tenant or tenant.database_status not in ('not_provisioned', 'error'):
            return
        Job = self.env['ncollection.provisioning.job']
        live = Job.search([
            ('tenant_id', '=', tenant.id),
            ('status', 'in', ('queued', 'running', 'done')),
        ], limit=1)
        if live:
            return  # a job is already pending / running / succeeded
        db_name = tenant._ensure_database_name()
        job = Job.create({'tenant_id': tenant.id, 'database_name': db_name})
        _logger.info("Auto-provisioning tenant %s -> database '%s' (job %s)",
                     tenant.company_name, db_name, job.id)
        job.action_run()
