# -*- coding: utf-8 -*-
"""UAE VAT localization (P3-T04) — acceptance test.

Proves the retarget's acceptance: *a sale in a fresh tenant computes 5% VAT with
correct tax accounts*. We REUSE Odoo's official 'ae' chart template (l10n_ae) —
this verifies our APPLICATION of it (mechanisms stay Odoo-owned), on a fresh
company standing in for a fresh tenant.
"""
from unittest.mock import patch

from odoo.tests import TransactionCase, tagged
from odoo.tools import mute_logger

_RES_COMPANY_LOG = 'odoo.addons.ncollection_account_localization_uae.models.res_company'


@tagged('post_install', '-at_install')
class TestUaeVat(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # A fresh company with no chart of accounts yet — the "fresh tenant".
        cls.company = cls.env['res.company'].create({'name': 'UAE VAT Test Co'})
        cls.company._nc_apply_uae_localization()

    def _uae_env(self):
        """Env scoped to the UAE company, so product/company tax defaults resolve
        to that company (not the test's main company)."""
        return self.env(context=dict(
            self.env.context, allowed_company_ids=self.company.ids))

    def test_ae_chart_template_loaded(self):
        self.assertEqual(self.company.chart_template, 'ae',
                         "the UAE 'ae' chart template must be loaded")

    def test_vat_taxes_and_fiscal_positions_exist(self):
        sale_taxes = self.env['account.tax'].search([
            ('company_id', '=', self.company.id), ('type_tax_use', '=', 'sale')])
        rates = sale_taxes.mapped('amount')
        self.assertIn(5.0, rates, "5% standard VAT must exist")
        self.assertIn(0.0, rates, "0% zero-rated / exempt VAT must exist")
        self.assertTrue(
            self.env['account.fiscal.position'].search_count(
                [('company_id', '=', self.company.id)]),
            "UAE fiscal positions (domestic/GCC/international) must exist")

    def test_default_sale_tax_is_5pct(self):
        self.assertTrue(self.company.account_sale_tax_id,
                        "the company default sale tax must be wired")
        self.assertEqual(self.company.account_sale_tax_id.amount, 5.0)

    def test_sale_computes_5pct_vat_with_correct_account(self):
        env = self._uae_env()
        product = env['product.product'].create({
            'name': 'UAE Widget', 'list_price': 100.0})
        invoice = env['account.move'].create({
            'move_type': 'out_invoice',
            'company_id': self.company.id,
            'partner_id': env['res.partner'].create({'name': 'UAE Customer'}).id,
            'invoice_line_ids': [(0, 0, {
                'product_id': product.id, 'quantity': 1, 'price_unit': 100.0})],
        })
        line = invoice.invoice_line_ids
        self.assertIn(5.0, line.tax_ids.mapped('amount'),
                      "the line must carry the 5% UAE VAT by default")
        self.assertEqual(invoice.amount_tax, 5.0, "5% of 100 = 5 VAT")
        self.assertEqual(invoice.amount_total, 105.0)
        # Post it and assert the real posted tax line lands on a tax account
        # (acceptance: "correct tax accounts" is about the posted entry).
        invoice.action_post()
        tax_line = invoice.line_ids.filtered('tax_line_id')
        self.assertTrue(tax_line, "the posted move has a dedicated VAT tax line")
        self.assertEqual(tax_line.tax_line_id.amount, 5.0)
        self.assertEqual(tax_line.balance, -5.0, "5 VAT credited on a sale")
        self.assertTrue(tax_line.account_id,
                        "the posted 5% VAT must land on a real tax account")

    def test_default_company_localized_at_install(self):
        """Regression guard for the install-time race (odoo + code review of
        #44). The DEFAULT company — the one a fresh tenant actually uses — must
        end up on the 'ae' chart with 5% VAT after install, NOT Odoo's
        generic_coa fallback (whose DEFERRED auto-install fires AFTER every
        post_init hook and would otherwise clobber ours). A fresh-company test
        alone dodges this race (it only fires at first `account` install), so we
        assert against the real default company here."""
        main = self.env.ref('base.main_company')
        self.assertEqual(
            main.chart_template, 'ae',
            "the tenant's default company must get the UAE chart, not generic_coa")
        self.assertEqual(main.account_sale_tax_id.amount, 5.0)

    @mute_logger(_RES_COMPANY_LOG)
    def test_localization_failure_rolls_back_and_is_retryable(self):
        """Fail-soft with a savepoint (security review of #44): a mid-load
        failure must not raise AND must not leave the company stuck on a
        half-applied chart — the savepoint rolls the partial write back so it
        stays retryable, and the cursor is not poisoned."""
        company = self.env['res.company'].create({'name': 'Fail VAT Co'})
        ChartTemplate = type(self.env['account.chart.template'])

        def _set_then_boom(template_code, company, install_demo=False, **kw):
            company.chart_template = template_code  # partial write, then fail
            raise ValueError('boom mid-load')

        with patch.object(ChartTemplate, 'try_loading', side_effect=_set_then_boom):
            company._nc_apply_uae_localization()  # must NOT raise
        company.invalidate_recordset(['chart_template'])
        self.assertFalse(company.chart_template,
                         "the savepoint rolls back the partial write — retryable")
        # cursor is healthy (not left in a failed transaction):
        self.assertTrue(self.env['res.company'].search_count([]))
