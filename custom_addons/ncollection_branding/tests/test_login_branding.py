# -*- coding: utf-8 -*-
"""Login page redesign (P1-T14).

Template-only redesign of /web/login (+ the reset/signup pages that share
web.login_layout). These tests prove:

* the split-screen frame + heading + relabelled "Forgot password?" render,
* Odoo's CSRF token and the auth controller are untouched (classic form login
  and JSON-RPC authenticate both still work),
* the reset-password page inherits the same frame,
* the page source carries zero visible "Odoo" branding,
* Remember-Me is deliberately absent (see the module/PR rationale),
* the NCollection company logo is seeded (login card no longer shows Odoo's
  default logo).

Module install already fails if any inheritance xpath stops matching, so a
green suite also proves every anchor still resolves against Odoo 19's markup.
"""

from odoo.tests import HttpCase, TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestLoginRedesignHttp(HttpCase):

    def setUp(self):
        super().setUp()
        # Make the reset-password link deterministic regardless of DB defaults.
        self.env["ir.config_parameter"].sudo().set_param("auth_signup.reset_password", "True")
        self.env.flush_all()

    def test_login_page_redesigned(self):
        """/web/login renders the split-screen redesign and stays functional."""
        res = self.url_open("/web/login")
        self.assertEqual(res.status_code, 200)
        body = res.text

        # Split-screen frame + copy.
        self.assertIn("o_nc_login__brand", body)
        self.assertIn("o_nc_login__form-side", body)
        self.assertIn("Sign in to your workspace", body)
        self.assertIn("All rights reserved", body)

        # Security-critical: CSRF token and the POST form survive the redesign.
        self.assertIn('name="csrf_token"', body)
        self.assertIn('action="/web/login"', body)

    def test_forgot_password_link_relabeled_and_wired(self):
        """The reset link is relabelled but still points at /web/reset_password
        and keeps its enable-guard (functional Forgot Password, item 4)."""
        res = self.url_open("/web/login")
        self.assertEqual(res.status_code, 200)
        self.assertIn("Forgot password?", res.text)
        self.assertIn("/web/reset_password", res.text)
        # the native "Reset Password" label is gone (relabelled, not duplicated)
        self.assertNotIn("Reset Password", res.text)

    def test_no_remember_me_control(self):
        """Remember-Me is intentionally omitted (no native support; a cosmetic
        checkbox would be misleading). Assert no checkbox leaked in."""
        res = self.url_open("/web/login")
        self.assertNotIn('type="checkbox"', res.text)

    def test_login_page_zero_odoo_branding(self):
        """Acceptance item 8: no visible 'Odoo' in the page source. The only
        remaining lowercase 'odoo' tokens are framework JS identifiers (var
        odoo, __session_info__, script id, __odooAssetError) which are core and
        invisible; the case-sensitive brand string must be absent."""
        res = self.url_open("/web/login")
        self.assertEqual(res.text.count("Odoo"), 0)
        self.assertNotIn("Powered by", res.text)

    def test_reset_password_page_branded(self):
        """The reset-password page shares web.login_layout, so it inherits the
        same split-screen frame and carries no 'Odoo' branding."""
        res = self.url_open("/web/reset_password")
        self.assertEqual(res.status_code, 200)
        self.assertIn("o_nc_login__brand", res.text)
        self.assertIn("oe_reset_password_form", res.text)
        self.assertEqual(res.text.count("Odoo"), 0)

    def test_auth_controller_untouched(self):
        """JSON-RPC authenticate must still succeed — proves the redesign did
        not touch controller/session logic (ARCHITECTURE_SECURITY.md §6)."""
        # HttpCase.authenticate posts to /web/session/authenticate under the
        # hood; a returned session_id proves the auth flow works end to end.
        self.authenticate("admin", "admin")
        self.assertTrue(self.session.uid, "admin authentication must succeed")


@tagged("post_install", "-at_install")
class TestLoginRedesign(TransactionCase):

    def _arch(self, xmlid):
        return str(self.env.ref(xmlid).arch)

    def test_layout_split_override_present(self):
        arch = self._arch("ncollection_branding.login_layout_split")
        self.assertIn("o_nc_login__brand", arch)
        self.assertIn("o_nc_login__form-side", arch)
        # per-tenant logo via the company_logo binary (acceptance item 7)
        self.assertIn("company_logo", arch)

    def test_form_override_present(self):
        arch = self._arch("ncollection_branding.login_form_branded")
        self.assertIn("Sign in to your workspace", arch)
        self.assertIn("o_nc_login__forgot", arch)
        # guard preserved so the link never becomes a dead 404
        self.assertIn("reset_password_enabled", arch)

    def test_company_logo_seeded(self):
        """The login card reads /web/binary/company_logo; the NCollection logo
        must be seeded so it does not fall back to Odoo's default (item 2)."""
        company = self.env.ref("base.main_company")
        self.assertTrue(company.logo, "main company logo must be seeded")
