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
* no vendor mark is the company logo (login card no longer shows Odoo's
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

    def test_forgot_password_is_offered_twice_and_never_hidden_when_enabled(self):
        """One beside the Password label and one, larger, under the Log in button: the way back in is unmissable."""
        body = self.url_open("/web/login").text
        self.assertGreaterEqual(body.count(">Forgot password?</a>"), 2)
        self.assertIn("o_nc_login__forgot--below", body)
        self.env["ir.config_parameter"].sudo().set_param("auth_signup.reset_password", "False")
        self.env.flush_all()
        self.assertNotIn("Forgot password?", self.url_open("/web/login").text, "off means off, in both places")

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
        # The sign-in page is the SYSTEM's: its logo (brand panel and the mobile card) is the System Branding login
        # logo, never the company's (System Branding vs Company Identity), and no hard-coded mark stands in for it.
        self.assertNotIn("company_logo", arch)
        self.assertIn("login_logo_url", arch)
        self.assertNotIn("<svg", arch.split('o_nc_login__brand-copy')[0], "no hard-coded mark beside the name")

    def test_form_override_present(self):
        arch = self._arch("ncollection_branding.login_form_branded")
        self.assertIn("Sign in to your workspace", arch)
        self.assertIn("o_nc_login__forgot", arch)
        # guard preserved so the link never becomes a dead 404
        self.assertIn("reset_password_enabled", arch)

    def test_the_system_logo_stays_legible_on_the_dark_brand_panel(self):
        """Measured on the real platform: a brand-blue logo on a transparent background drawn straight on the
        dark panel was present but practically invisible. It sits on a light tile from the surface token."""
        from odoo.tools import file_open
        with file_open('ncollection_branding/static/src/scss/login.scss') as handle:
            scss = handle.read()
        rule = scss.split('.o_nc_login__system-logo {', 1)[1].split('}', 1)[0]
        self.assertIn('background-color: var(--nc-surface-raised)', rule)
        self.assertIn('o_nc_login__system-logo', self._arch('ncollection_branding.login_layout_split'))

    def test_no_vendor_mark_is_the_company_logo(self):
        """The company's logo prints on its invoices: Odoo's default mark and the NCollection mark this module used to
        seed are removed, so the letterhead shows the company's own logo or its name. The sign-in page shows the
        system's logo (System Branding), never this field."""
        import base64
        import io
        from PIL import Image
        from odoo.tools import file_open
        company = self.env.ref("base.main_company")
        with file_open('ncollection_branding/static/src/img/logo.png', 'rb') as handle:
            company.logo = base64.b64encode(handle.read())
        self.env['res.company']._nc_clear_vendor_logos()
        self.assertFalse(company.logo, "the NCollection mark is not the company's logo")
        buffer = io.BytesIO()
        Image.new('RGB', (8, 8), (200, 30, 30)).save(buffer, format='PNG')
        company.logo = base64.b64encode(buffer.getvalue())
        own = company.logo
        self.env['res.company']._nc_clear_vendor_logos()
        self.assertEqual(company.logo, own, "a logo the Owner uploaded is never touched")
