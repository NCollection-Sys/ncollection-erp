# -*- coding: utf-8 -*-
"""Per-tenant dynamic branding (P1-T16).

Covers the three moving parts: the res.company colour/background fields with
their defaults, the strict-hex ORM constraint (the security control for the CSS
injection), and the runtime :root{--nc-*} injection on both the backend and the
login page — including that changing a colour changes what is emitted.
"""

from odoo.exceptions import ValidationError
from odoo.tests import HttpCase, TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestDynamicBranding(TransactionCase):

    def setUp(self):
        super().setUp()
        self.company = self.env.ref("base.main_company")

    def test_fields_exist_with_defaults(self):
        for fname in ("nc_primary_color", "nc_secondary_color",
                      "nc_sidebar_color", "nc_login_background"):
            self.assertIn(fname, self.company._fields)
        # a fresh company carries the NCollection defaults (field-level, so a
        # module upgrade never clobbers a tenant's chosen colours)
        fresh = self.env["res.company"].create({"name": "T16 Co"})
        self.assertEqual(fresh.nc_primary_color, "#1F5F8F")
        self.assertEqual(fresh.nc_secondary_color, "#2D7AB7")
        self.assertEqual(fresh.nc_sidebar_color, "#12233A")

    def test_hex_constraint_rejects_bad_values(self):
        # includes a CSS-injection attempt (#123;}) — must be rejected
        for bad in ("red", "#12", "#GGGGGG", "#1F5F8", "#1F5F8FF", "#123;}",
                    "javascript:alert(1)", "url(x)"):
            with self.assertRaises(ValidationError, msg="accepted %r" % bad):
                self.company.write({"nc_primary_color": bad})
                self.company.flush_recordset()

    def test_hex_constraint_accepts_valid_and_empty(self):
        # valid 6-digit hex, both cases
        self.company.write({"nc_secondary_color": "#aB12Cd"})
        self.company.flush_recordset()
        self.assertEqual(self.company.nc_secondary_color, "#aB12Cd")
        # empty means "use the NCollection default" and must be allowed
        self.company.write({"nc_secondary_color": False})
        self.company.flush_recordset()
        self.assertFalse(self.company.nc_secondary_color)


@tagged("post_install", "-at_install")
class TestDynamicBrandingHttp(HttpCase):

    def test_backend_injects_tenant_css_vars(self):
        """The backend web client head carries the per-tenant :root block.

        HttpCase runs the request in the test transaction, so a write here (once
        flushed) is visible to the QWeb render without a commit."""
        self.env.ref("base.main_company").write({"nc_primary_color": "#AB12CD"})
        self.env.flush_all()
        self.authenticate("admin", "admin")
        res = self.url_open("/odoo")
        self.assertEqual(res.status_code, 200)
        self.assertIn("nc_tenant_theme", res.text)
        # canonical --nc-<category>-<name> token (UI-T01/#128)
        self.assertIn("--nc-color-primary: #AB12CD", res.text)

    def test_login_injects_tenant_css_vars(self):
        """The public login page also carries the :root block (colours theme
        the login form even before authentication)."""
        self.env.ref("base.main_company").write({"nc_secondary_color": "#33EE99"})
        self.env.flush_all()
        res = self.url_open("/web/login")
        self.assertEqual(res.status_code, 200)
        self.assertIn("nc_tenant_theme", res.text)
        # canonical --nc-<category>-<name> token (UI-T01/#128)
        self.assertIn("--nc-color-secondary: #33EE99", res.text)
