# -*- coding: utf-8 -*-
"""Request 07: discount before tax (945) or after tax (950) on 1,000 - 10 % with 5 % VAT; UI totals, journal entry,
tax lines and receivable agree; posted invoices never change; only managers set the rule."""
from unittest.mock import patch

from odoo import Command
from odoo.exceptions import AccessError, ValidationError
from odoo.tests import new_test_user, tagged
from odoo.addons.account.tests.common import AccountTestInvoicingCommon


@tagged('post_install', '-at_install')
class TestCalculationRules(AccountTestInvoicingCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # the after-tax cases model a company where the rule is allowed (not UAE, no UAE e-invoicing)
        cls.startClassPatcher(patch.object(type(cls.env['res.company']), '_nc_after_tax_refused',
                                           lambda self: ''))
        cls.vat5 = cls.env['account.tax'].create({'name': 'VAT 5 calc', 'amount': 5.0, 'type_tax_use': 'sale'})
        cls.discount = cls.env['ncollection.invoice.adjustment.type'].create({
            'name': 'Global 10', 'kind': 'discount', 'value_mode': 'percent',
            'account_id': cls.company_data['default_account_revenue'].id, 'applies_to': 'customer'})

    def _invoice(self, move_type='out_invoice'):
        move = self.env['account.move'].create({
            'move_type': move_type, 'partner_id': self.partner_a.id, 'invoice_date': '2026-03-01',
            'invoice_line_ids': [Command.create({'name': 'Goods', 'quantity': 1, 'price_unit': 1000.0,
                                                 'tax_ids': [Command.set(self.vat5.ids)]})]})
        self.env['ncollection.invoice.adjustment'].create(
            {'move_id': move.id, 'type_id': self.discount.id, 'value': 10.0})
        return move

    def _assert_books(self, move, untaxed, tax, total):
        self.assertEqual(self.discount.tax_mode, 'company', 'a new discount type follows the company rule')
        self.assertAlmostEqual(move.amount_untaxed, untaxed)
        self.assertAlmostEqual(move.amount_tax, tax)
        self.assertAlmostEqual(move.amount_total, total)
        lines = move.line_ids
        self.assertAlmostEqual(sum(lines.mapped('debit')), sum(lines.mapped('credit')))
        tax_lines = lines.filtered('tax_line_id')
        self.assertAlmostEqual(abs(sum(tax_lines.mapped('balance'))), tax, msg='the tax line is the tax shown')
        term = lines.filtered(lambda ln: ln.display_type == 'payment_term')
        self.assertAlmostEqual(abs(sum(term.mapped('balance'))), total, msg='receivable = total')

    def test_before_tax_is_the_default(self):
        move = self._invoice()
        self._assert_books(move, 900.0, 45.0, 945.0)
        move.action_post()
        self._assert_books(move, 900.0, 45.0, 945.0)

    def test_after_tax_keeps_vat_on_the_undiscounted_base(self):
        self.env.company.nc_discount_tax_timing = 'after_tax'
        move = self._invoice()
        self._assert_books(move, 900.0, 50.0, 950.0)
        discount_line = move.invoice_line_ids.filtered('nc_adjustment_id')
        self.assertFalse(discount_line.tax_ids, 'the after-tax discount carries no tax')
        move.action_post()
        self._assert_books(move, 900.0, 50.0, 950.0)

    def test_credit_note_follows_the_same_rule(self):
        self.env.company.nc_discount_tax_timing = 'after_tax'
        refund = self._invoice('out_refund')
        self.assertAlmostEqual((refund.amount_tax, refund.amount_total), (50.0, 950.0))

    def test_a_posted_invoice_never_changes_when_the_rule_does(self):
        move = self._invoice()
        move.action_post()
        before = [(ln.account_id.id, ln.balance) for ln in move.line_ids]
        self.env.company.nc_discount_tax_timing = 'after_tax'
        move.invalidate_recordset()
        self.assertEqual([(ln.account_id.id, ln.balance) for ln in move.line_ids], before)
        self.assertAlmostEqual(move.amount_total, 945.0)

    def test_preview_and_who_may_save(self):
        rules = self.env['ncollection.invoice.calculation.rules'].create({})
        self.assertAlmostEqual((rules.before_tax, rules.before_total), (45.0, 945.0))
        self.assertAlmostEqual((rules.after_tax, rules.after_total), (50.0, 950.0))
        rules.discount_tax_timing = 'after_tax'
        rules.action_save()
        self.assertEqual(self.env.company.nc_discount_tax_timing, 'after_tax')
        clerk = new_test_user(self.env, login='calc_clerk', groups='account.group_account_invoice')
        with self.assertRaises(AccessError):
            self.env['ncollection.invoice.calculation.rules'].with_user(clerk).create({}).action_save()


@tagged('post_install', '-at_install')
class TestCalculationRulesUae(AccountTestInvoicingCommon):

    def test_after_tax_is_refused_for_a_uae_company(self):
        company = self.env.company
        company.account_fiscal_country_id = self.env.ref('base.ae')
        with self.assertRaises(ValidationError):
            company.nc_discount_tax_timing = 'after_tax'
        self.assertEqual(company.nc_discount_tax_timing, 'before_tax')
