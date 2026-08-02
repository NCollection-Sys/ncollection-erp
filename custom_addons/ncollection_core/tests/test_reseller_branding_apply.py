# -*- coding: utf-8 -*-
"""Tenant-side brand cascade: pushed reseller brand lands on res.company (P10-T09).

This is the TENANT half of the white-label cascade (the platform half — that a
sub-tenant's config-sync payload carries the reseller brand — is covered in
ncollection_reseller). A platform push of brand_* keys through the config-sync
entry point must:
  * apply onto the company's nc_* branding fields when they still hold the
    NCollection default (override-if-default), and
  * NOT clobber a value the tenant has customised (reconcile-safe), and
  * not trip the licensing field whitelist, and still apply standard keys.
"""
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestResellerBrandingApply(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Config = cls.env["ncollection.workspace.config"]
        cls.company = cls.env.company
        cls.Config.create({"plan_code": "STARTER"})

    def test_pushed_brand_overrides_default(self):
        self.Config.sync_from_platform({
            "brand_primary_color": "#AA0000",
            "brand_sidebar_color": "#0000BB",
            "subscription_status": "active",  # standard key alongside
        })
        self.assertEqual(self.company.nc_primary_color, "#AA0000")
        self.assertEqual(self.company.nc_sidebar_color, "#0000BB")
        # standard licensing key still applied
        self.assertEqual(self.Config.get_config().subscription_status, "active")

    def test_customised_colour_not_clobbered(self):
        # Tenant sets its own colour first; a later reconcile push must respect it.
        self.company.nc_primary_color = "#123456"
        self.Config.sync_from_platform({"brand_primary_color": "#AA0000"})
        self.assertEqual(self.company.nc_primary_color, "#123456")

    def test_brand_keys_do_not_trip_whitelist(self):
        # A payload of ONLY brand keys must not raise (they are popped before the
        # non-whitelisted-field guard).
        result = self.Config.sync_from_platform({"brand_secondary_color": "#334455"})
        self.assertTrue(result.get("ok"))
        self.assertEqual(self.company.nc_secondary_color, "#334455")
