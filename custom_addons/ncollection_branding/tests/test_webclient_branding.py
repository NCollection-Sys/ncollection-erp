# -*- coding: utf-8 -*-
"""Web client white-label branding (P1-T13).

Verifies the Odoo-branding surfaces are overridden. Where a template can be
rendered without an HTTP request (brand_promotion) we assert the rendered
output; for request-bound frontend templates (login, 404, offline) we assert
the inheriting view exists and its own arch carries the replacement — and
rely on the fact that module install FAILS if any inheritance xpath does not
match, so a green install already proves the anchors resolved.
"""

import os

from odoo.tests import HttpCase, TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestWebclientBrandingHttp(HttpCase):

    def test_backend_page_title_branded(self):
        """End-to-end: the web client root serves a branded <title> (proves
        the web.layout override renders) and returns 200 (proves the backend
        asset bundle — including white_label.js — compiles and serves)."""
        self.authenticate("admin", "admin")
        res = self.url_open("/odoo")
        self.assertEqual(res.status_code, 200)
        self.assertIn("NCollection ERP", res.text)
        # the server-rendered <title> must not be the bare Odoo default
        self.assertNotIn("<title>Odoo</title>", res.text)


@tagged("post_install", "-at_install")
class TestWebclientBranding(TransactionCase):

    def _arch(self, xmlid):
        return str(self.env.ref(xmlid).arch)

    # ---------------- rendered (no request needed) ----------------

    def test_brand_promotion_deodoo(self):
        html = str(self.env["ir.qweb"]._render(
            "web.brand_promotion_message", {"_message": "", "_utm_medium": "portal"}
        ))
        self.assertNotIn("odoo.com", html.lower())
        self.assertNotIn("odoo_logo_tiny", html)
        self.assertIn("NCollection ERP", html)

    # ---------------- inheriting views present + branded ----------------

    def test_login_footer_override_present(self):
        arch = self._arch("ncollection_branding.login_layout_branded")
        self.assertIn("NCollection ERP", arch)
        # the two Odoo links are removed by this override
        self.assertIn("position=\"replace\"", arch)

    def test_bootstrap_theme_and_icon_override(self):
        arch = self._arch("ncollection_branding.webclient_bootstrap_branded")
        self.assertIn("#17375E", arch)
        self.assertIn("apple-touch-icon.png", arch)
        self.assertNotIn("#71639e", arch)
        self.assertNotIn("odoo-icon-ios", arch)

    def test_offline_override_present(self):
        arch = self._arch("ncollection_branding.webclient_offline_branded")
        self.assertIn("NCollection ERP will load", arch)
        self.assertIn("ncollection_branding/static/src/img/logo.png", arch)

    def test_404_override_present(self):
        arch = self._arch("ncollection_branding.http_404_branded")
        self.assertIn("ncollection_branding.ncollection_error_layout", arch)
        self.assertIn("workspace administrator", arch)

    def test_403_override_present(self):
        arch = self._arch("ncollection_branding.http_403_branded")
        self.assertIn("ncollection_branding.ncollection_error_layout", arch)
        self.assertIn("403 Access Denied", arch)

    def test_uniform_error_layout_render(self):
        """Uniform error layout renders standalone with branding, incident copy badge, and recovery CTAs."""
        html = str(self.env["ir.qweb"]._render(
            "ncollection_branding.ncollection_error_layout", {
                "error_code": "500 Internal Error",
                "error_type": "500",
                "error_title": "Server Incident",
                "error_message": "An unexpected error occurred.",
                "incident_id": "ERR-99A1F0-8A2D",
            }
        ))
        self.assertIn("NCollection", html)
        self.assertIn("/ncollection_branding/static/src/img/logo.png", html)
        self.assertIn("ERR-99A1F0-8A2D", html)
        self.assertIn("Return to Workspace", html)
        self.assertIn("ncCopyIncident", html)
        self.assertNotIn("Odoo", html)

    def test_settings_about_override_present(self):
        arch = self._arch("ncollection_branding.res_config_settings_view_form_branded")
        self.assertIn("NCollection ERP", arch)
        # the widget is replaced away
        self.assertIn("res_config_edition", arch)  # in the xpath selector

    # ---------------- JS asset registered ----------------

    def test_white_label_js_in_backend_bundle(self):
        """The white-label JS/OWL assets declared in web.assets_backend exist
        on disk (the manifest reference is the source of truth for what
        actually ships; this catches a typo'd path)."""
        module = self.env["ir.module.module"].search([("name", "=", "ncollection_branding")])
        self.assertTrue(module)
        module_dir = os.path.dirname(os.path.dirname(__file__))
        for rel_path in ("static/src/js/white_label.js", "static/src/xml/error_dialogs_patch.xml"):
            self.assertTrue(
                os.path.exists(os.path.join(module_dir, *rel_path.split("/"))),
                "%s must exist (declared in web.assets_backend)" % rel_path,
            )


@tagged('post_install', '-at_install')
class TestOdooTourIsOff(TransactionCase):
    """The Odoo onboarding tour feature is disabled (#472).

    Two halves, both asserted, because either alone is a half-fix: the server
    flag is what stops tours being SERVED (web_tour/models/tour.py gates on
    `tour_enabled`), and the frontend removal is what takes the Odoo-branded
    "Onboarding" control out of the UI.
    """

    def test_tours_are_disabled_for_an_admin(self):
        """web_tour enables them for exactly this user — an admin on a
        database with no demo data, which is every NCollection platform and
        every provisioned tenant."""
        user = self.env.ref('base.user_admin')
        self.assertIn('tour_enabled', user._fields,
                      'control: web_tour is not installed, so this proves nothing')
        user.invalidate_recordset(['tour_enabled'])
        self.assertFalse(user.tour_enabled)

    def test_a_new_user_gets_tours_disabled_too(self):
        user = self.env['res.users'].create({
            'name': 'Tourless', 'login': 'tourless@example.test',
            'group_ids': [(6, 0, [self.env.ref('base.group_user').id])]})
        self.assertFalse(user.tour_enabled)

    def test_the_onboarding_control_is_removed_in_the_client(self):
        """The registry entry web_tour's service adds. Asserted on the asset
        source because the repo has no JS unit runner — the browser behaviour
        is covered by the live verification in the PR."""
        path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            'static', 'src', 'js', 'white_label.js')
        with open(path, encoding='utf-8') as handle:
            source = handle.read()
        self.assertIn('debugDefaultRegistry.remove(ONBOARDING_ITEM)', source)
        self.assertIn('addEventListener("UPDATE"', source,
                      'a one-shot remove() would run before the tour service '
                      'registers the item and so do nothing at all')
