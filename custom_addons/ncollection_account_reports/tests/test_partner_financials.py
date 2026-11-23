# -*- coding: utf-8 -*-
"""Requests 02 / 03 / 04: the Customers / Suppliers balance, its status, the Summary and the statement it opens agree;
a scope never lets a partner of the other side narrow a run; no partner is "Paid" without any transaction."""
from odoo import Command, fields
from odoo.addons.account.tests.common import AccountTestInvoicingCommon
from odoo.tests import tagged


@tagged('post_install', '-at_install')
class TestPartnerFinancials(AccountTestInvoicingCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.customer = cls.env['res.partner'].create({'name': 'PF Customer', 'customer_rank': 1})
        cls.supplier = cls.env['res.partner'].create({'name': 'PF Supplier', 'supplier_rank': 1})
        cls.both = cls.env['res.partner'].create({'name': 'PF Both', 'customer_rank': 1, 'supplier_rank': 1})
        cls.fresh = cls.env['res.partner'].create({'name': 'PF Fresh', 'customer_rank': 1})

    def _doc(self, partner, amount, move_type='out_invoice'):
        move = self.env['account.move'].create({
            'move_type': move_type, 'partner_id': partner.id, 'invoice_date': fields.Date.today(),
            'invoice_line_ids': [Command.create({'name': 'x', 'quantity': 1, 'price_unit': amount,
                                                 'tax_ids': [Command.clear()]})]})
        move.action_post()
        return move

    def _pay(self, move, amount):
        self.env['account.payment.register'].with_context(active_model='account.move', active_ids=move.ids).create(
            {'amount': amount})._create_payments()

    def _statement_closing(self, partner, scope):
        action = partner.with_context(nc_partner_scope=scope).action_nc_partner_summary()
        filters = action['context']['nc_report_filters']
        data = self.env['ncollection.report.service'].nc_report_run(action['context']['nc_report_model'], filters)
        rows = [r for r in data['rows'] if r.get('partner_id') == partner.id]
        return rows[-1]['running_balance'] if rows else 0.0

    def test_status_never_says_paid_without_a_transaction(self):
        self.assertEqual(self.fresh.nc_customer_status, 'none')
        self.assertEqual(self.fresh.nc_receivable_balance, 0.0)

    def test_outstanding_partial_settled_credit(self):
        inv = self._doc(self.customer, 100.0)
        self.customer.invalidate_recordset()
        self.assertEqual((self.customer.nc_customer_status, self.customer.nc_receivable_balance), ('outstanding', 100.0))
        self._pay(inv, 40.0)
        self.customer.invalidate_recordset()
        self.assertEqual((self.customer.nc_customer_status, self.customer.nc_receivable_balance), ('partial', 60.0))
        self._pay(inv, 60.0)
        self.customer.invalidate_recordset()
        self.assertEqual(self.customer.nc_customer_status, 'settled')
        self.env['account.payment'].create({'payment_type': 'inbound', 'partner_type': 'customer',
                                            'partner_id': self.customer.id, 'amount': 25.0}).action_post()
        self.customer.invalidate_recordset()
        self.assertEqual((self.customer.nc_customer_status, self.customer.nc_receivable_balance), ('credit', -25.0))

    def test_supplier_side_and_dual_role(self):
        self._doc(self.supplier, 70.0, 'in_invoice')
        self._doc(self.both, 30.0)
        self._doc(self.both, 12.0, 'in_invoice')
        (self.supplier | self.both).invalidate_recordset()
        self.assertEqual((self.supplier.nc_supplier_status, self.supplier.nc_payable_balance), ('outstanding', 70.0))
        self.assertEqual(self.supplier.nc_customer_status, 'none')
        self.assertEqual((self.both.nc_receivable_balance, self.both.nc_payable_balance), (30.0, 12.0))

    def test_the_list_balance_equals_the_summary_statement(self):
        inv = self._doc(self.customer, 200.0)
        self._pay(inv, 50.0)
        self._doc(self.both, 9.0, 'in_invoice')
        (self.customer | self.both).invalidate_recordset()
        self.assertAlmostEqual(self._statement_closing(self.customer, 'receivable'),
                               self.customer.nc_receivable_balance)
        # payable is a credit balance in the ledger: the list shows it positive (owed to the supplier)
        self.assertAlmostEqual(-self._statement_closing(self.both, 'payable'), self.both.nc_payable_balance)

    def test_a_partner_of_the_other_scope_never_narrows_the_run(self):
        Service = self.env['ncollection.report.service']
        today = str(fields.Date.today())
        base = {'date_from': today, 'date_to': today, 'partner_scope': 'payable'}
        Model = self.env['ncollection.account.report.statement']
        vals = Service._nc_clean_filters(Model, {**base, 'partner_ids': [self.customer.id, self.supplier.id]})
        self.assertEqual(vals['partner_ids'], [(6, 0, [self.supplier.id])], 'the customer-only partner is dropped')
        suggest = Service.nc_report_suggest('ncollection.account.report.statement', 'partner_ids', 'PF',
                                            scope='receivable')
        names = {r['name'] for r in suggest['records']}
        self.assertIn('PF Customer', names)
        self.assertIn('PF Both', names)
        self.assertNotIn('PF Supplier', names)
