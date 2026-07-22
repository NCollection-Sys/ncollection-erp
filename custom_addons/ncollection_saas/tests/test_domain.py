# -*- coding: utf-8 -*-
"""Tenant domain & SSL tracking (P2-T06) — CI-safe unit tests.

Cover the pure platform-side logic without any nginx/cert round-trip: the FQDN
derivation, idempotent per-tenant sync, wildcard-expiry propagation, the
ssl_status computation across the 14-day lead window, and the weekly
reconciliation cron (backfill + throttled expiry alert). Live HTTPS reachability
over the wildcard cert is proven on the real edge (staging), not in CI.
"""
from datetime import timedelta

from odoo import fields
from odoo.tests import TransactionCase, tagged

from odoo.addons.ncollection_saas.models.domain import (
    SSL_ALERT_LEAD_DAYS,
    _WILDCARD_EXPIRY_PARAM,
)


@tagged('post_install', '-at_install')
class TestDomain(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        params = cls.env['ir.config_parameter'].sudo()
        params.set_param('ncollection_saas.provisioning_quota_per_hour', '0')
        params.set_param('ncollection_saas.base_domain', 'ncollectionerp.com')
        cls.plan = cls.env['ncollection.subscription.plan'].create({
            'name': 'Growth', 'code': 'GROWTH',
            'allowed_module_names': 'crm', 'max_users': 5})
        cls.Domain = cls.env['ncollection.domain']

    def _tenant(self, **kw):
        vals = {'company_name': 'Acme', 'plan_id': self.plan.id, 'status': 'active',
                'database_status': 'ready', 'database_name': 'acme'}
        vals.update(kw)
        return self.env['ncollection.tenant'].create(vals)

    def _set_wildcard_expiry(self, value):
        self.env['ir.config_parameter'].sudo().set_param(
            _WILDCARD_EXPIRY_PARAM, value)

    # ---- FQDN derivation -------------------------------------------------

    def test_fqdn_from_database_name(self):
        tenant = self._tenant(database_name='acme')
        self.assertEqual(self.Domain._fqdn_for_tenant(tenant), 'acme.ncollectionerp.com')

    def test_fqdn_empty_without_database_name(self):
        tenant = self._tenant(database_name=False, database_status='not_provisioned')
        self.assertFalse(self.Domain._fqdn_for_tenant(tenant))

    # ---- per-tenant sync (provisioning hook + reconcile) -----------------

    def test_sync_creates_subdomain_record(self):
        tenant = self._tenant(database_name='acme')
        rec = self.Domain._sync_for_tenant(tenant)
        self.assertEqual(rec.fqdn, 'acme.ncollectionerp.com')
        self.assertEqual(rec.domain_type, 'subdomain')
        self.assertTrue(rec.is_wildcard)
        self.assertEqual(rec.tenant_id, tenant)

    def test_sync_is_idempotent(self):
        tenant = self._tenant(database_name='acme')
        first = self.Domain._sync_for_tenant(tenant)
        second = self.Domain._sync_for_tenant(tenant)
        self.assertEqual(first, second, "second sync updates, does not duplicate")
        self.assertEqual(
            self.Domain.search_count([('tenant_id', '=', tenant.id)]), 1)

    def test_sync_propagates_wildcard_expiry(self):
        expiry = fields.Date.to_string(fields.Date.today() + timedelta(days=60))
        self._set_wildcard_expiry(expiry)
        rec = self.Domain._sync_for_tenant(self._tenant(database_name='acme'))
        self.assertEqual(fields.Date.to_string(rec.ssl_expiry), expiry)

    # ---- ssl_status computation ------------------------------------------

    def _domain(self, days_to_expiry=None):
        tenant = self._tenant(database_name='acme')
        expiry = (fields.Date.today() + timedelta(days=days_to_expiry)
                  if days_to_expiry is not None else False)
        return self.Domain.create({
            'tenant_id': tenant.id, 'fqdn': 'acme.ncollectionerp.com',
            'ssl_expiry': expiry})

    def test_ssl_status_unknown_without_expiry(self):
        self.assertEqual(self._domain(None).ssl_status, 'unknown')

    def test_ssl_status_valid_far_out(self):
        self.assertEqual(self._domain(SSL_ALERT_LEAD_DAYS + 30).ssl_status, 'valid')

    def test_ssl_status_expiring_within_window(self):
        self.assertEqual(self._domain(SSL_ALERT_LEAD_DAYS - 1).ssl_status, 'expiring')

    def test_ssl_status_expired(self):
        self.assertEqual(self._domain(-1).ssl_status, 'expired')

    # ---- weekly reconciliation cron --------------------------------------

    def test_cron_backfills_ready_tenants(self):
        tenant = self._tenant(database_name='acme')
        self.assertEqual(self.Domain.search_count([('tenant_id', '=', tenant.id)]), 0)
        self.Domain._cron_scan_ssl_expiry()
        self.assertEqual(self.Domain.search_count([('tenant_id', '=', tenant.id)]), 1)

    def test_cron_alerts_and_throttles(self):
        domain = self._domain(days_to_expiry=-1)  # expired -> must alert
        activities_before = len(domain.activity_ids)
        domain._alert_if_expiring()
        self.assertEqual(len(domain.activity_ids), activities_before + 1,
                         "expiry schedules a renewal activity")
        self.assertEqual(domain.last_alert_date, fields.Date.context_today(domain))
        # Second pass on the same day is throttled — no duplicate activity.
        domain._alert_if_expiring()
        self.assertEqual(len(domain.activity_ids), activities_before + 1,
                         "re-alert throttled within SSL_REALERT_DAYS")

    def test_cron_does_not_alert_valid(self):
        domain = self._domain(days_to_expiry=SSL_ALERT_LEAD_DAYS + 30)
        domain._alert_if_expiring()
        self.assertFalse(domain.last_alert_date, "valid cert raises no alert")
