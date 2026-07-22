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

    def action_activate(self):
        """draft -> active, then auto-provision the tenant if it has no DB yet."""
        res = super().action_activate()
        for sub in self:
            sub._trigger_provisioning()
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
