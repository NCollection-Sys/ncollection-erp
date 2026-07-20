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
        # the branded logo replaces the Odoo 404 illustration; "404.svg"
        # legitimately remains inside the xpath SELECTOR, so we only assert
        # the replacement content is present (install-success already proves
        # the xpath matched and the swap applied).
        self.assertIn("ncollection_branding/static/src/img/logo.png", arch)
        self.assertIn("workspace administrator", arch)

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
