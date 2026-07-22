# -*- coding: utf-8 -*-
"""Design-system token conformance (UI-T01 / #128).

Guards the canonical --nc-<category>-<name> vocabulary against regressions:

  1. The token layer (tokens.scss) ships in both asset bundles.
  2. The runtime per-tenant injection emits CANONICAL colour tokens, never the
     legacy flat names — otherwise a component reading --nc-color-primary would
     silently miss a tenant override.
  3. Component sheets carry no reference to the legacy flat token names
     (aliases live only in tokens.scss, kept one release for safety).

These are static-source assertions (cheap, no HTTP) — module install already
proves the sheets compile; this proves they speak one vocabulary.
"""

import os

from odoo.tests import TransactionCase, tagged

_MODULE_DIR = os.path.dirname(os.path.dirname(__file__))

# Legacy flat names that must no longer be *consumed* by components. Kept as
# aliases in tokens.scss only, so that file is excluded from the scan.
_LEGACY_TOKENS = (
    "--nc-primary", "--nc-secondary", "--nc-bg", "--nc-silver",
    "--nc-surface", "--nc-border", "--nc-radius", "--nc-shadow",
    "--nc-success", "--nc-danger",
)

# Component sheets/scripts that must reference canonical tokens only.
_COMPONENT_SOURCES = (
    "static/src/scss/theme_colors.scss",
    "static/src/scss/login.scss",
)


def _read(rel_path):
    with open(os.path.join(_MODULE_DIR, *rel_path.split("/")), encoding="utf-8") as fh:
        return fh.read()


@tagged("post_install", "-at_install")
class TestDesignTokens(TransactionCase):

    def test_tokens_scss_in_both_bundles(self):
        module = self.env["ir.module.module"].search(
            [("name", "=", "ncollection_branding")])
        self.assertTrue(module)
        self.assertTrue(
            os.path.exists(os.path.join(_MODULE_DIR, "static", "src", "scss", "tokens.scss")),
            "tokens.scss (the design-system token layer) must exist",
        )

    def test_injection_emits_canonical_tokens(self):
        arch = _read("views/branding_theme_templates.xml")
        self.assertIn("--nc-color-primary", arch)
        self.assertIn("--nc-color-secondary", arch)
        # legacy flat colour names must NOT be emitted by the injection
        self.assertNotIn("--nc-primary:", arch)
        self.assertNotIn("--nc-secondary:", arch)

    def test_components_use_canonical_tokens_only(self):
        for rel in _COMPONENT_SOURCES:
            src = _read(rel)
            for legacy in _LEGACY_TOKENS:
                # match the legacy token only where it is *used*: var(--nc-x,
                # or var(--nc-x)  — never a longer canonical name (…-strong).
                self.assertNotIn(
                    "var(%s," % legacy, src,
                    "%s consumes legacy token %s — use the canonical "
                    "--nc-<category>-<name> form (UI-T01/#128)" % (rel, legacy),
                )
