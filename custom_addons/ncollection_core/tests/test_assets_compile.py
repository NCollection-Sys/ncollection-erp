# -*- coding: utf-8 -*-
"""The backend stylesheet bundle must COMPILE, in both directions.

A SCSS error does not raise: Odoo stores a stub stylesheet that prints "A css error occured, using an old style to
render this page" over every backend page, and keeps serving it until the sources change. Python tests and page-error
listeners do not see it, so this compiles the real bundle (not the cached attachment) and fails on any compile error.
"""
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestBackendAssetsCompile(TransactionCase):

    def _compile(self, rtl):
        bundle = self.env['ir.qweb']._get_asset_bundle('web.assets_web', js=False, rtl=rtl)
        bundle.preprocess_css()                       # the real compile: never the cached attachment
        return bundle.css_errors

    def test_backend_bundle_compiles_ltr(self):
        self.assertEqual(self._compile(rtl=False), [], "web.assets_web (LTR) fails to compile")

    def test_backend_bundle_compiles_rtl(self):
        self.assertEqual(self._compile(rtl=True), [], "web.assets_web (RTL) fails to compile")
