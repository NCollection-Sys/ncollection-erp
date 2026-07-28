# -*- coding: utf-8 -*-
"""UAE AED & multi-currency (P3-T06) — a USD invoice converts to AED at the peg.

AED is already the company currency (loaded by the 'ae' chart, P3-T04) and Odoo
core OWNS the conversion engine. This ticket adds the localization data on top:
the GCC currencies are activated, multi-currency is enabled, and the fixed
USD/AED peg (3.6725) is seeded so a foreign-currency invoice converts
deterministically — no external rate feed. Automated rate freshness for floating
currencies is a deferred follow-up (see README).
"""
from unittest.mock import patch

from odoo.tests import TransactionCase, tagged
from odoo.tools import mute_logger

_AED_PER_USD = 3.6725
_RES_COMPANY_LOGGER = (
    'odoo.addons.ncollection_account_localization_uae.models.res_company')


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

    def test_usd_invoice_nonround_amount_rounds_to_fils(self):
        # a non-round amount exercises AED fils rounding on the conversion, not
        # just the clean 100 -> 367.25 case.
        env = self.env(context=dict(
            self.env.context, allowed_company_ids=self.company.ids))
        product = env['product.product'].create({
            'name': 'Odd Widget', 'list_price': 33.33})
        partner = env['res.partner'].create({
            'name': 'Odd Customer', 'company_type': 'company'})
        invoice = env['account.move'].create({
            'move_type': 'out_invoice', 'company_id': self.company.id,
            'partner_id': partner.id, 'currency_id': self._cur('USD').id,
            'invoice_line_ids': [(0, 0, {
                'product_id': product.id, 'quantity': 1, 'price_unit': 33.33,
                'tax_ids': [(6, 0, [])]})],
        })
        invoice.action_post()
        self.assertAlmostEqual(
            invoice.amount_total_signed, round(33.33 * _AED_PER_USD, 2), places=2,
            msg="33.33 USD must convert and round to the fils in AED")

    def test_currency_setup_is_idempotent(self):
        # both the install hook and the migration re-run this; a second call
        # must not duplicate rate rows or the group grant.
        Rate = self.env['res.currency.rate']
        dom = [('company_id', '=', self.company.root_id.id)]
        before = Rate.search_count(dom)
        self.company._nc_setup_uae_currencies()
        self.assertEqual(Rate.search_count(dom), before,
                         "re-running currency setup must not duplicate rates")

    @mute_logger(_RES_COMPANY_LOGGER)
    def test_currency_setup_is_fail_soft_and_keeps_cursor_usable(self):
        # a rate-seed failure must neither raise nor poison the transaction
        # (regression: fail-soft without a savepoint aborts the whole cursor).
        # mute_logger: the forced failures below are logged with exc_info, which
        # would otherwise trip CI's traceback gate.
        company = self.env['res.company'].create({'name': 'FailSoft Co'})
        rate_cls = type(self.env['res.currency.rate'])

        def boom(self2, vals):
            raise ValueError("forced rate-seed failure")

        with patch.object(rate_cls, 'create', boom):
            company._nc_setup_uae_currencies()  # must NOT raise
        # the cursor is still usable — a normal ORM write succeeds afterwards
        probe = self.env['res.partner'].create({'name': 'probe after failure'})
        self.assertTrue(probe.id,
                        "a currency-setup failure must not poison the cursor")

    def test_rates_scoped_to_root_for_branch_company(self):
        # Odoo rejects res.currency.rate on a branch company (_check_company_id)
        # and only reads the root's rate at conversion time, so rates must be
        # seeded on the ROOT. On the buggy company.id scoping this raised a
        # (swallowed) ValidationError and seeded nothing.
        root = self.env['res.company'].create({'name': 'Root Co'})
        branch = self.env['res.company'].create({
            'name': 'Branch Co', 'parent_id': root.id})
        branch._nc_setup_uae_currencies()
        Rate = self.env['res.currency.rate']
        usd = self._cur('USD')
        self.assertTrue(Rate.search_count([
            ('currency_id', '=', usd.id), ('company_id', '=', root.id)]),
            "the peg rate must be seeded on the root company")
        self.assertFalse(Rate.search_count([
            ('currency_id', '=', usd.id), ('company_id', '=', branch.id)]),
            "no rate may be scoped to a branch company (Odoo rejects it)")
