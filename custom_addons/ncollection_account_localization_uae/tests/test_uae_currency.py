# -*- coding: utf-8 -*-
"""UAE AED & multi-currency (P3-T06) — a USD invoice converts to AED at the peg.

AED is already the company currency (loaded by the 'ae' chart, P3-T04) and Odoo
core OWNS the conversion engine. This ticket adds the localization data on top:
the GCC currencies are activated, multi-currency is enabled, and the fixed
USD/AED peg (3.6725) is seeded so a foreign-currency invoice converts
deterministically — no external rate feed. Automated rate freshness for floating
currencies is a deferred follow-up (see README).
"""
from odoo.tests import TransactionCase, tagged

_AED_PER_USD = 3.6725


@tagged('post_install', '-at_install')
class TestUaeCurrency(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env['res.company'].create({'name': 'UAE Currency Co'})
        cls.company._nc_apply_uae_localization()
        cls.Currency = cls.env['res.currency'].with_context(active_test=False)

    def _cur(self, code):
        return self.Currency.search([('name', '=', code)], limit=1)

    def test_company_currency_is_aed(self):
        self.assertEqual(self.company.currency_id.name, 'AED',
                         "the 'ae' chart makes AED the company currency")

    def test_gcc_currencies_preloaded_active(self):
        for code in ('USD', 'EUR', 'SAR', 'KWD', 'BHD', 'QAR', 'OMR'):
            self.assertTrue(self._cur(code).active,
                            "%s must be activated for UAE tenants" % code)

    def test_multi_currency_enabled(self):
        group_mc = self.env.ref('base.group_multi_currency')
        group_user = self.env.ref('base.group_user')
        self.assertIn(group_mc, group_user.implied_ids,
                      "multi-currency must be enabled (group implied on the "
                      "internal-user group)")

    def test_aed_pegged_usd_rate_seeded(self):
        rate = self.env['res.currency.rate'].search([
            ('currency_id', '=', self._cur('USD').id),
            ('company_id', '=', self.company.id)], limit=1)
        self.assertTrue(rate, "a USD/AED peg rate must be seeded for the company")
        self.assertAlmostEqual(rate.rate, 1.0 / _AED_PER_USD, places=6,
                               msg="USD rate must be the fixed 1/3.6725 peg")

    def test_aed_rounding_is_fils(self):
        self.assertEqual(self.company.currency_id.rounding, 0.01,
                         "AED must round to the fils (0.01)")

    def test_usd_invoice_posts_with_correct_aed_conversion(self):
        # THE acceptance: "a USD invoice posts with correct AED conversion at the
        # day's rate." Taxes cleared so the assertion isolates the conversion.
        env = self.env(context=dict(
            self.env.context, allowed_company_ids=self.company.ids))
        product = env['product.product'].create({
            'name': 'USD Widget', 'list_price': 100.0})
        partner = env['res.partner'].create({
            'name': 'USD Customer', 'company_type': 'company'})
        invoice = env['account.move'].create({
            'move_type': 'out_invoice', 'company_id': self.company.id,
            'partner_id': partner.id, 'currency_id': self._cur('USD').id,
            'invoice_line_ids': [(0, 0, {
                'product_id': product.id, 'quantity': 1, 'price_unit': 100.0,
                'tax_ids': [(6, 0, [])]})],
        })
        invoice.action_post()
        self.assertEqual(invoice.currency_id.name, 'USD')
        self.assertAlmostEqual(invoice.amount_total, 100.0, places=2)
        # amount_total_signed is expressed in the company currency (AED)
        self.assertAlmostEqual(
            invoice.amount_total_signed, 100.0 * _AED_PER_USD, places=2,
            msg="100 USD must post as 367.25 AED at the fixed peg")
