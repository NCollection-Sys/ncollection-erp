# -*- coding: utf-8 -*-
"""The Document Stamp switch of ONE accounting document (nc.document.stamp.mixin): Default follows the kind's Print
Setting, On / Off applies to that document only, the PDF that leaves the print pipeline follows it, and only who may
stamp the kind may change it."""
from odoo import Command
from odoo.exceptions import AccessError
from odoo.addons.ncollection_branding.tests.stamp_print import stamps_on
from odoo.tests import tagged

from .test_document_stamp import _Common

STAMPED, BARE = [1], [0]


@tagged('post_install', '-at_install')
class TestStampPerDocument(_Common):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.invoices = cls.env.ref('account.account_invoices')

    def _doc(self, move_type='out_invoice'):
        return self.init_invoice(move_type, post=True, amounts=[100.0], invoice_date='2026-03-01')

    def test_default_follows_the_print_setting_and_on_off_apply_to_one_document_only(self):
        self._stamp('customer_invoice')
        first, second = self._doc(), self._doc()
        self.assertEqual(first.nc_stamp_mode, 'default')
        self.assertTrue(first.nc_stamp_default_on)
        self.assertEqual(stamps_on(self.invoices, first), STAMPED)
        first.nc_stamp_mode = 'off'
        self.assertEqual(stamps_on(self.invoices, first), BARE)
        self.assertEqual(stamps_on(self.invoices, second), STAMPED, "the other invoice is unaffected")
        # the kind printed without a stamp: Default follows it, On still stamps this one document
        self._type('customer_invoice').nc_show_stamp = False
        self.assertEqual(stamps_on(self.invoices, second), BARE)
        second.nc_stamp_mode = 'on'
        self.assertEqual(stamps_on(self.invoices, second), STAMPED)
        self.assertFalse(self._doc().nc_stamp_default_on)

    def test_every_accounting_kind_carries_the_switch(self):
        for move_type, code in (('out_invoice', 'customer_invoice'), ('out_refund', 'customer_credit_note'),
                                ('in_invoice', 'vendor_bill'), ('in_refund', 'vendor_credit_note')):
            with self.subTest(move_type=move_type):
                self._stamp(code)
                doc = self._doc(move_type)
                self.assertEqual(stamps_on(self.invoices, doc), STAMPED)
                doc.nc_stamp_mode = 'off'
                self.assertEqual(stamps_on(self.invoices, doc), BARE)

    def test_a_payment_receipt_carries_the_switch(self):
        invoice = self._doc()
        payment = self.env['account.payment.register'].with_context(
            active_model='account.move', active_ids=invoice.ids).create({})._create_payments()
        self._stamp('payment_receipt')
        receipt = self.env.ref('account.action_report_payment_receipt')
        self.assertEqual(stamps_on(receipt, payment), STAMPED)
        payment.nc_stamp_mode = 'off'
        self.assertEqual(stamps_on(receipt, payment), BARE)

    def test_the_choice_persists_and_never_touches_the_amounts(self):
        doc = self._doc()
        before = (doc.state, doc.name, doc.amount_untaxed, doc.amount_tax, doc.amount_total,
                  [(line.debit, line.credit, line.account_id.id) for line in doc.line_ids])
        doc.nc_stamp_mode = 'off'
        self.env.invalidate_all()
        self.assertEqual(self.env['account.move'].browse(doc.id).nc_stamp_mode, 'off')
        self.assertEqual((doc.state, doc.name, doc.amount_untaxed, doc.amount_tax, doc.amount_total,
                          [(line.debit, line.credit, line.account_id.id) for line in doc.line_ids]), before)
        self.assertEqual(doc.copy().nc_stamp_mode, 'default', "a duplicate starts from the Print Setting again")

    def test_only_who_may_stamp_the_kind_may_switch_it(self):
        company = self.env.company
        Users = self.env['res.users'].with_context(no_reset_password=True)
        common = {'company_id': company.id, 'company_ids': [Command.set(company.ids)]}
        base = self.env.ref('base.group_user')
        accountant = Users.create(dict(common, name='Plain Accountant', login='stamp_plain_accountant', group_ids=[
            Command.set([base.id, self.env.ref('account.group_account_invoice').id])]))
        adviser = Users.create(dict(common, name='Stamp Adviser', login='stamp_adviser', group_ids=[
            Command.set([base.id, self.env.ref('account.group_account_manager').id])]))
        doc = self._doc()
        self.assertFalse(doc.with_user(accountant).nc_stamp_mode_editable)
        with self.assertRaises(AccessError):
            doc.with_user(accountant).write({'nc_stamp_mode': 'off'})
        with self.assertRaises(AccessError):
            self.env['account.move'].with_user(accountant).create({
                'move_type': 'out_invoice', 'partner_id': self.partner_a.id, 'nc_stamp_mode': 'off'})
        self.assertEqual(doc.nc_stamp_mode, 'default')
        self.assertTrue(doc.with_user(adviser).nc_stamp_mode_editable)
        doc.with_user(adviser).write({'nc_stamp_mode': 'off'})
        self.assertEqual(doc.nc_stamp_mode, 'off')
        # an unrelated write by the accountant is not refused
        doc.with_user(accountant).write({'narration': 'a note'})

    def test_a_client_context_cannot_unstamp_a_document(self):
        self._stamp('customer_invoice')
        doc = self._doc()
        self.assertEqual(stamps_on(self.invoices.with_context(nc_no_stamp=True).sudo(False), doc), STAMPED)
