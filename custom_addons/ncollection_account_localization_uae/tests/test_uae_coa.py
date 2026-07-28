# -*- coding: utf-8 -*-
"""UAE Chart of Accounts (P3-T05) — full-cycle acceptance test.

Odoo's official ``l10n_ae`` ``'ae'`` chart template (applied by P3-T04) provides
the UAE chart of accounts and the VAT accounts already linked to the taxes — the
mechanism stays Odoo-owned (FPA §6). This proves the acceptance: journal entries
post to the CORRECT accounts through the whole
``sale -> invoice -> payment -> reconciliation`` cycle, on a fresh company
standing in for a fresh UAE tenant.
"""
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestUaeCoa(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env['res.company'].create({'name': 'UAE CoA Test Co'})
        cls.company._nc_apply_uae_localization()

    def _uae_env(self):
        return self.env(context=dict(
            self.env.context, allowed_company_ids=self.company.ids))

    # ---- CoA structure + VAT-account linkage -----------------------------

    def test_uae_coa_present_with_key_account_types(self):
        types = set(self.env['account.account'].search(
            [('company_ids', 'in', self.company.ids)]).mapped('account_type'))
        for needed in ('asset_receivable', 'liability_payable', 'income', 'expense'):
            self.assertIn(needed, types,
                          "the UAE CoA must include %s accounts" % needed)

    def test_vat_output_account_linked_to_5pct_sale_tax(self):
        tax = self.env['account.tax'].search([
            ('company_id', '=', self.company.id),
            ('type_tax_use', '=', 'sale'), ('amount', '=', 5.0)], limit=1)
        self.assertTrue(tax, "the 5% UAE sale VAT must exist")
        tax_rep = tax.invoice_repartition_line_ids.filtered(
            lambda r: r.repartition_type == 'tax')
        self.assertTrue(tax_rep.account_id,
                        "the 5% VAT must be linked to a VAT (output) account")

    # ---- acceptance: full cycle posts to the correct accounts ------------

    def test_full_sale_cycle_posts_to_correct_accounts(self):
        env = self._uae_env()
        product = env['product.product'].create({
            'name': 'UAE Widget', 'list_price': 100.0})
        partner = env['res.partner'].create({'name': 'UAE Customer'})

        invoice = env['account.move'].create({
            'move_type': 'out_invoice',
            'company_id': self.company.id,
            'partner_id': partner.id,
            'invoice_line_ids': [(0, 0, {
                'product_id': product.id, 'quantity': 1, 'price_unit': 100.0})],
        })
        invoice.action_post()
        self.assertEqual(invoice.state, 'posted')
        self.assertEqual(invoice.amount_total, 105.0)  # 100 + 5% VAT

        lines = invoice.line_ids
        income = lines.filtered(lambda line: line.account_id.account_type == 'income')
        self.assertAlmostEqual(sum(income.mapped('credit')), 100.0,
                               msg="revenue posts to an income account, credit 100")
        tax_lines = lines.filtered(lambda line: line.tax_line_id)
        self.assertAlmostEqual(sum(tax_lines.mapped('credit')), 5.0,
                               msg="5% VAT posts to the VAT (output) account, credit 5")
        # the posted VAT line lands on the tax's CONFIGURED output account —
        # not merely "some" account (odoo review of #45).
        tax_rep = tax_lines.tax_line_id.invoice_repartition_line_ids.filtered(
            lambda r: r.repartition_type == 'tax')
        self.assertEqual(tax_lines.account_id, tax_rep.account_id,
                         "the posted VAT line must hit the tax's configured VAT account")
        receivable = lines.filtered(
            lambda line: line.account_id.account_type == 'asset_receivable')
        self.assertAlmostEqual(sum(receivable.mapped('debit')), 105.0,
                               msg="the receivable is debited the gross 105")

        # register full payment -> auto-reconciles the receivable
        env['account.payment.register'].with_context(
            active_model='account.move', active_ids=invoice.ids
        ).create({}).action_create_payments()

        self.assertEqual(invoice.payment_state, 'paid',
                         "the invoice is fully paid after registering payment")
        self.assertAlmostEqual(invoice.amount_residual, 0.0)
        self.assertTrue(receivable.reconciled,
                        "the receivable line is reconciled against the payment")

        # the payment leg itself posts to the correct accounts: it credits the
        # receivable (clearing it) and debits a liquidity/outstanding asset
        # account — proving the *whole* cycle, not just the receivable side
        # (odoo review of #45).
        pay_lines = invoice._get_reconciled_payments().move_id.line_ids
        self.assertTrue(pay_lines, "a payment journal entry exists")
        pay_ar = pay_lines.filtered(
            lambda line: line.account_id.account_type == 'asset_receivable')
        self.assertAlmostEqual(sum(pay_ar.mapped('credit')), 105.0,
                               msg="the payment credits the receivable to clear it")
        liquidity = pay_lines - pay_ar
        self.assertAlmostEqual(sum(liquidity.mapped('debit')), 105.0,
                               msg="the payment debits a liquidity/outstanding account")
        self.assertTrue(
            all(a.account_type in ('asset_cash', 'asset_current')
                for a in liquidity.account_id),
            "the payment's money leg is a bank/cash/outstanding asset account")
