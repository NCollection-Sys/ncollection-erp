# flake8: noqa: E501  (accounting assertions read best on one line)
"""Sales Order Matching: a sales order is not a receivable.

Every scenario proves the same three things from Odoo's own records: the journal entries balance, nothing is revenue
until a real invoice is posted, and the invoice/payment residuals are what the money says.
"""
from odoo import Command
from odoo.exceptions import AccessError, UserError
from odoo.tests import tagged

from odoo.addons.ncollection_bank_reconciliation.tests.common import BankCommon


@tagged('post_install', '-at_install', 'ncollection_bank_reconciliation')
class TestSaleMatch(BankCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.product = cls.env['product.product'].create({
            'name': 'Consulting day', 'type': 'service', 'list_price': 1000.0, 'invoice_policy': 'order',
            'taxes_id': [Command.clear()],
        })
        group = cls.env.ref
        base = [group('base.group_user').id, group('account.group_account_user').id]
        cls.accountant = cls._user('nc_accountant', base)
        cls.sales_accountant = cls._user('nc_sales_accountant', base + [group('sales_team.group_sale_salesman').id])
        cls.reader = cls._user('nc_reader', [group('base.group_user').id, group('account.group_account_readonly').id])
        cls.other_company = cls.setup_other_company()['company']

    def _pricelist(self, currency):
        return self.env['product.pricelist'].sudo().search(
            [('currency_id', '=', currency.id), ('company_id', 'in', (False, self.company.id))], limit=1
        ) or self.env['product.pricelist'].sudo().create({'name': f'NC {currency.name}', 'currency_id': currency.id})

    @classmethod
    def _user(cls, login, groups):
        return cls.env['res.users'].with_context(no_reset_password=True).create({
            'name': login, 'login': login, 'email': f'{login}@example.com', 'company_id': cls.company.id,
            'company_ids': [Command.set(cls.company.ids)], 'group_ids': [Command.set(groups)]})

    # -------------------------------------------------------------- helpers
    def order(self, amount=1000.0, partner=None, confirm=True, currency=None, company=None):
        # fixture only: the salesman who takes the order; the code under test never uses sudo
        order = self.env['sale.order'].sudo().with_company(company or self.company).create({
            'partner_id': (partner or self.customer).id,
            'company_id': (company or self.company).id,
            **({"pricelist_id": self._pricelist(currency).id} if currency else {}),
            'order_line': [Command.create({'product_id': self.product.id, 'product_uom_qty': 1,
                                           'price_unit': amount, 'tax_ids': [Command.clear()]})],
        })
        if confirm:
            order.action_confirm()
        return order

    def wizard(self, st_line, *orders, user=None, **vals):
        wizard = self.env['ncollection.bank.sale.match'].with_user(user or self.env.user).with_context(
            default_statement_line_id=st_line.id).create(vals)
        for line in (wizard.line_ids if orders else wizard.line_ids.browse()):
            line.selected = line.order_id in orders
        return wizard

    def revenue_total(self):
        lines = self.env['account.move.line'].search(
            [('account_id', '=', self.revenue.id), ('company_id', '=', self.company.id), ('parent_state', '=', 'posted')])
        return -sum(lines.mapped('balance'))

    # ---------------------------------------------------------------- tests
    def test_01_order_invoice_match_reconcile(self):
        so = self.order(1000.0)
        st = self.line(1000.0, partner=self.customer, label=f'Payment {so.name}')
        revenue_before = self.revenue_total()
        wizard = self.wizard(st)
        # the order named in the bank reference is already ticked, the rest is the user's choice
        self.assertEqual(wizard.line_ids.filtered('selected').order_id, so)
        self.assertEqual(self.revenue_total(), revenue_before, "a sales order books no revenue")
        wizard.post = True
        wizard.action_create_invoice()
        invoice = so.invoice_ids
        self.assertEqual((invoice.state, invoice.move_type), ('posted', 'out_invoice'))
        self.assertEqual(self.revenue_total() - revenue_before, 1000.0, "revenue comes from the posted invoice only")
        # the invoice's receivable item is now proposed on the transaction; validation is the normal one
        data = st.reconcile_data_info['data']
        self.assertIn(self.open_item(invoice).id, [d.get('id') for d in data if d.get('kind') == 'other'])
        self.assertFalse(st.is_reconciled)
        st.reconcile_bank_line()
        self.assertTrue(st.is_reconciled)
        self.assert_balanced(st.move_id)
        self.assertEqual(invoice.amount_residual, 0.0)
        self.assertIn(invoice.payment_state, ('paid', 'in_payment'))
        # the bank entry itself carries no revenue line
        self.assertFalse(st.move_id.line_ids.filtered(lambda ml: ml.account_id == self.revenue))
        self.assertEqual(self.revenue_total() - revenue_before, 1000.0)

    def test_02_draft_invoice_is_created_without_posting(self):
        so = self.order(500.0)
        st = self.line(500.0, partner=self.customer)
        self.wizard(st, so).action_create_invoice()
        self.assertEqual(so.invoice_ids.state, 'draft')
        self.assertFalse([d for d in st.reconcile_data_info['data'] if d.get('kind') == 'other'])
        self.assertEqual(self.revenue_total(), 0.0, "a draft invoice books nothing")

    def test_03_multiple_orders_one_payment(self):
        so1, so2 = self.order(1000.0), self.order(500.0)
        st = self.line(1500.0, partner=self.customer, label=f'{so1.name} {so2.name}')
        wizard = self.wizard(st)
        self.assertEqual(wizard.line_ids.filtered('selected').order_id, so1 | so2)
        wizard.post = True
        wizard.action_create_invoice()
        invoices = so1.invoice_ids | so2.invoice_ids
        # Odoo's own default is consolidated billing: one invoice for both orders of the customer
        self.assertEqual(len(invoices), 1)
        self.assertAlmostEqual(invoices.amount_total, 1500.0)
        st.reconcile_bank_line()
        self.assert_balanced(st.move_id)
        self.assertEqual(sum(invoices.mapped('amount_residual')), 0.0)

    def test_04_ambiguous_reference_selects_nothing(self):
        so1, so2 = self.order(1000.0), self.order(1000.0)
        st = self.line(1000.0, partner=self.customer, label='Payment, thank you')
        wizard = self.wizard(st)
        self.assertEqual(wizard.line_ids.order_id, so1 | so2)
        self.assertFalse(wizard.line_ids.filtered('selected'), "no guess when the bank reference names no order")
        with self.assertRaises(UserError):
            wizard.action_create_invoice()

    def test_05_already_invoiced_order_is_refused_but_its_invoice_can_be_matched(self):
        so = self.order(800.0)
        invoice = so._create_invoices(final=True)
        invoice.action_post()
        st = self.line(800.0, partner=self.customer)
        wizard = self.wizard(st, so)
        self.assertEqual(wizard.line_ids.invoice_status, 'invoiced')
        with self.assertRaisesRegex(UserError, 'nothing left to invoice'):
            wizard.action_create_invoice()
        self.assertEqual(len(so.invoice_ids), 1, "no second invoice")
        wizard.action_add_posted_invoices()
        st.reconcile_bank_line()
        self.assertEqual(invoice.amount_residual, 0.0)

    def test_06_duplicate_creation_is_refused_while_a_draft_exists(self):
        so = self.order(900.0)
        st = self.line(900.0, partner=self.customer)
        self.wizard(st, so).action_create_invoice()
        with self.assertRaisesRegex(UserError, 'draft invoice'):
            self.wizard(st, so).action_create_invoice()
        self.assertEqual(len(so.invoice_ids), 1)

    def test_07_down_payment_percentage_then_partial_invoicing(self):
        so = self.order(1000.0)
        st = self.line(300.0, partner=self.customer, label=so.name)
        wizard = self.wizard(st, so, user=self.sales_accountant, method='percentage', percentage=30.0, post=True)
        wizard.action_create_invoice()
        down = so.invoice_ids
        self.assertEqual(len(down), 1)
        self.assertAlmostEqual(down.amount_total, 300.0)
        self.assertEqual(down.state, 'posted')
        st.reconcile_bank_line()
        self.assertEqual(down.amount_residual, 0.0)
        self.assert_balanced(st.move_id)
        # the order is still to be invoiced for the rest, and that is a normal invoice that deducts the down payment
        so.invalidate_recordset()
        self.assertEqual(so.invoice_status, 'to invoice')
        final = so._create_invoices(final=True)
        self.assertAlmostEqual(final.amount_total, 700.0)

    def test_08_down_payment_fixed_amount(self):
        so = self.order(1000.0)
        st = self.line(250.0, partner=self.customer, label=so.name)
        self.wizard(st, so, user=self.sales_accountant, method='fixed', fixed_amount=250.0, post=True).action_create_invoice()
        self.assertAlmostEqual(so.invoice_ids.amount_total, 250.0)
        st.reconcile_bank_line()
        self.assertEqual(so.invoice_ids.amount_residual, 0.0)

    def test_09_down_payment_needs_the_sales_right(self):
        so = self.order(1000.0)
        st = self.line(300.0, partner=self.customer)
        wizard = self.wizard(st, so, user=self.accountant, method='percentage', percentage=30.0)
        with self.assertRaisesRegex(UserError, 'Sales user right'):
            wizard.action_create_invoice()
        self.assertFalse(so.invoice_ids)

    def test_10_partially_invoiced_order_is_matched_for_what_was_invoiced(self):
        so = self.order(1000.0)
        down_st = self.line(400.0, partner=self.customer)
        self.wizard(down_st, so, user=self.sales_accountant, method='fixed', fixed_amount=400.0, post=True).action_create_invoice()
        self.assertEqual(so.invoice_status, 'to invoice')
        so.invalidate_recordset()
        wizard = self.wizard(down_st, so)
        self.assertAlmostEqual(wizard.line_ids.amount_invoiced, 400.0)
        self.assertAlmostEqual(wizard.line_ids.amount_total, 1000.0)

    def test_11_overpayment_leaves_a_customer_credit(self):
        so = self.order(1000.0)
        st = self.line(1200.0, partner=self.customer, label=so.name)
        self.wizard(st, so, post=True).action_create_invoice()
        st.reconcile_bank_line()
        self.assert_balanced(st.move_id)
        self.assertEqual(so.invoice_ids.amount_residual, 0.0)
        credit = st.move_id.line_ids.filtered(lambda ml: ml.account_id == self.receivable and not ml.reconciled)
        self.assertAlmostEqual(sum(credit.mapped('amount_residual')), -200.0)

    def test_12_partial_payment_leaves_the_invoice_open(self):
        so = self.order(1000.0)
        st = self.line(400.0, partner=self.customer, label=so.name)
        self.wizard(st, so, post=True).action_create_invoice()
        st.reconcile_bank_line()
        self.assertAlmostEqual(so.invoice_ids.amount_residual, 600.0)
        self.assertEqual(so.invoice_ids.payment_state, 'partial')

    def test_13_cancelled_draft_other_customer_and_other_company_are_not_offered(self):
        cancelled = self.order(100.0)
        cancelled._action_cancel()
        draft = self.order(100.0, confirm=False)
        other_customer = self.order(100.0, partner=self.vendor)
        company2 = self.other_company
        other_company = self.order(100.0, company=company2)
        ok = self.order(100.0)
        st = self.line(100.0, partner=self.customer)
        wizard = self.wizard(st)
        self.assertEqual(wizard.line_ids.order_id, ok)
        for foreign in (cancelled, draft, other_customer, other_company):
            self.assertNotIn(foreign, wizard.line_ids.order_id)
        # a forged line for another customer's order is refused when invoicing
        forged = self.env['ncollection.bank.sale.match.line'].create({'wizard_id': wizard.id, 'order_id': other_customer.id, 'selected': True})
        with self.assertRaisesRegex(UserError, 'another customer or company'):
            wizard.action_create_invoice()
        self.assertFalse(other_customer.invoice_ids)
        forged.unlink()

    def test_14_foreign_currency_order_is_flagged_and_invoiced_in_its_currency(self):
        eur = self.env.ref('base.EUR')
        eur.active = True
        so = self.order(1000.0, currency=eur)
        st = self.line(1000.0, partner=self.customer)
        wizard = self.wizard(st, so)
        self.assertTrue(wizard.line_ids.other_currency)
        wizard.action_create_invoice()
        self.assertEqual(so.invoice_ids.currency_id, eur)

    def test_15_read_only_user_cannot_use_it(self):
        so = self.order(100.0)
        st = self.line(100.0, partner=self.customer)
        with self.assertRaises(AccessError):
            self.env['ncollection.bank.sale.match'].with_user(self.reader).with_context(
                default_statement_line_id=st.id).create({})
        self.assertFalse(so.invoice_ids)

    def test_16_reconciled_transaction_is_refused(self):
        so = self.order(100.0)
        other = self.invoice(100.0)
        st = self.line(100.0, partner=self.customer)
        self.match(st, self.open_item(other))
        st.reconcile_bank_line()
        with self.assertRaisesRegex(UserError, 'already reconciled'):
            self.wizard(st, so).action_create_invoice()
        self.assertFalse(so.invoice_ids)

    def test_17_undo_and_retry(self):
        so = self.order(1000.0)
        st = self.line(1000.0, partner=self.customer, label=so.name)
        self.wizard(st, so, post=True).action_create_invoice()
        invoice = so.invoice_ids
        st.reconcile_bank_line()
        self.assertEqual(invoice.amount_residual, 0.0)
        st.unreconcile_bank_line()
        self.assertAlmostEqual(invoice.amount_residual, 1000.0)
        self.assertEqual(len(so.invoice_ids), 1, "undo does not touch the invoice")
        # retry: add the (again unpaid) posted invoice and reconcile once more
        self.wizard(st, so).action_add_posted_invoices()
        st.reconcile_bank_line()
        self.assertEqual(invoice.amount_residual, 0.0)
        self.assert_balanced(st.move_id)
