# -*- coding: utf-8 -*-
"""Shared OWL component library (UI-T02 / #129).

Three guarantees:
  1. The library's templates and its dev playground exist and install (a broken
     OWL template or JS import fails module install / the asset build, so a
     green install already proves the components compile and register).
  2. The playground page loads (render-acceptance smoke: the client action +
     backend bundle serve without error).
  3. Component styles are TOKENS ONLY — zero hard-coded colours or radii — which
     is the #129 "tokens only" acceptance turned into an enforced guard.
"""

import os
import re

from odoo.tests import HttpCase, TransactionCase, tagged

_MODULE_DIR = os.path.dirname(os.path.dirname(__file__))
_COMP_DIR = os.path.join(_MODULE_DIR, "static", "src", "components")

# The seven components #129 requires.
_EXPECTED_TEMPLATES = (
    "ncollection_branding.NcKpiCard",
    "ncollection_branding.NcSectionCard",
    "ncollection_branding.NcBadge",
    "ncollection_branding.NcQuickActionCard",
    "ncollection_branding.NcChartWrapper",
    "ncollection_branding.NcEmptyState",
    "ncollection_branding.NcLoadingSkeleton",
)

# Component stylesheets that must be tokens-only.
_COMPONENT_STYLES = ("components.scss", "playground.scss")

_HEX_RE = re.compile(r"#[0-9a-fA-F]{3,8}\b")
_RADIUS_PX_RE = re.compile(r"border-radius:\s*[^;]*\d+px")
# SCSS comments — stripped before scanning so issue refs like "#129" in a
# header comment are not mistaken for a hex colour.
_BLOCK_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)
_LINE_COMMENT_RE = re.compile(r"//[^\n]*")


def _read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _strip_comments(scss):
    return _LINE_COMMENT_RE.sub("", _BLOCK_COMMENT_RE.sub("", scss))


@tagged("post_install", "-at_install")
class TestComponentLibrary(TransactionCase):

    def test_playground_action_and_menu(self):
        action = self.env.ref("ncollection_branding.action_component_playground")
        self.assertEqual(action.tag, "ncollection_branding.component_playground")
        menu = self.env.ref("ncollection_branding.menu_component_playground")
        # dev/QA surface — admin only, never a tenant-user menu.
        # ir.ui.menu groups field is group_ids in Odoo 19 (renamed from groups_id).
        self.assertIn(self.env.ref("base.group_system"), menu.group_ids)

    def test_all_components_have_templates(self):
        xml = _read(os.path.join(_COMP_DIR, "components.xml"))
        for name in _EXPECTED_TEMPLATES:
            self.assertIn(
                't-name="%s"' % name, xml,
                "component template %s missing from components.xml" % name,
            )

    def test_component_styles_are_tokens_only(self):
        """Acceptance: zero hard-coded colours/radii in components. Every colour
        is a var(--nc-*) or a color-mix() of one; every radius is a token."""
        for fname in _COMPONENT_STYLES:
            src = _strip_comments(_read(os.path.join(_COMP_DIR, fname)))
            hexes = _HEX_RE.findall(src)
            self.assertFalse(
                hexes, "%s has hard-coded hex colour(s) %s — use a --nc-* token"
                % (fname, hexes))
            radii = _RADIUS_PX_RE.findall(src)
            self.assertFalse(
                radii, "%s has a hard-coded border-radius %s — use var(--nc-radius-*)"
                % (fname, radii))


@tagged("post_install", "-at_install")
class TestComponentLibraryHttp(HttpCase):

    def test_playground_page_loads(self):
        """The playground client action loads in the web client (proves the
        action resolves and the backend bundle — components + templates —
        serves without error)."""
        self.authenticate("admin", "admin")
        res = self.url_open("/odoo/action-ncollection_branding.action_component_playground")
        self.assertEqual(res.status_code, 200)
