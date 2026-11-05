# flake8: noqa: E501  (fixture files and accounting assertions read best on one line)
"""Acceptance scenarios 1, 3-11, 13-17 (the import ones, 2 and 12, are in test_import.py).

Every scenario checks the journal entries (balanced), the native residuals, the reconciliation status and the
journal balance, through Odoo's own fields; nothing here reads an NCollection figure to prove an NCollection figure.
"""
from odoo import Command, fields
from odoo.exceptions import AccessError, UserError
from odoo.tests import tagged

from .common import BankCommon


@tagged('post_install', '-at_install', 'ncollection_bank_reconciliation')
class TestBankReconciliation(BankCommon):

    # 1 -----------------------------------------------------------------
    def test_01_manual_deposit_to_an_account(self):
        before = self.journal_balance(self.misr)
        st = self.line(500.0, label='Cash deposit by owner')
        self.assertFalse(st.is_reconciled)
        self.suspense_to(st, self.revenue)
        st.reconcile_bank_line()
        self.assertTrue(st.is_reconciled)
        self.assert_balanced(st.move_id)
        self.assertEqual(st.move_id.line_ids.filtered(lambda ml: ml.account_id == self.revenue).balance, -500.0)
        self.assertAlmostEqual(self.journal_balance(self.misr) - before, 500.0)

    # 3 -----------------------------------------------------------------
    def test_03_exact_invoice_payment(self):
        inv = self.invoice(1000.0)
        st = self.line(1000.0, partner=self.customer, label=inv.name)
        self.match(st, self.open_item(inv))
        st.reconcile_bank_line()
        self.assertTrue(st.is_reconciled)
        self.assert_balanced(st.move_id)
        self.assertEqual(inv.amount_residual, 0.0)
        self.assertIn(inv.payment_state, ('paid', 'in_payment'))
        self.assertTrue(self.open_item(inv).reconciled)

    # 4 -----------------------------------------------------------------
    def test_04_partial_customer_payment(self):
        inv = self.invoice(1000.0)
        st = self.line(400.0, partner=self.customer)
        self.match(st, self.open_item(inv))
        st.reconcile_bank_line()
        self.assertTrue(st.is_reconciled)
        self.assertAlmostEqual(inv.amount_residual, 600.0)
        self.assertEqual(inv.payment_state, 'partial')

    # 5 -----------------------------------------------------------------
    def test_05_one_payment_two_invoices(self):
        inv1, inv2 = self.invoice(1000.0), self.invoice(500.0)
        st = self.line(1500.0, partner=self.customer)
        self.match(st, self.open_item(inv1), self.open_item(inv2))
        st.reconcile_bank_line()
        self.assertTrue(st.is_reconciled)
        self.assert_balanced(st.move_id)
        self.assertEqual((inv1.amount_residual, inv2.amount_residual), (0.0, 0.0))

    # 6 -----------------------------------------------------------------
    def test_06_two_payments_one_invoice(self):
        inv = self.invoice(1000.0)
        st1 = self.line(600.0, partner=self.customer)
        self.match(st1, self.open_item(inv))
        st1.reconcile_bank_line()
        self.assertAlmostEqual(inv.amount_residual, 400.0)
        st2 = self.line(400.0, partner=self.customer)
        self.match(st2, self.open_item(inv))
        st2.reconcile_bank_line()
        self.assertEqual(inv.amount_residual, 0.0)
        self.assertTrue(st1.is_reconciled and st2.is_reconciled)

    # 7 -----------------------------------------------------------------
    def test_07_overpayment_leaves_customer_credit(self):
        inv = self.invoice(1000.0)
        st = self.line(1200.0, partner=self.customer)
        self.match(st, self.open_item(inv))
        # with a partner on the transaction the engine sends the remainder to that partner's receivable itself
        rest = [d for d in st.reconcile_data_info['data'] if d['kind'] == 'suspense']
        self.assertEqual(rest[0]['account_id'][0], self.customer.property_account_receivable_id.id)
        self.assertTrue(st.reconcile_data_info['can_reconcile'])
        st.reconcile_bank_line()
        self.assertTrue(st.is_reconciled)
        self.assert_balanced(st.move_id)
        self.assertEqual(inv.amount_residual, 0.0)
        credit = st.move_id.line_ids.filtered(lambda ml: ml.account_id == self.receivable and not ml.reconciled)
        self.assertEqual(credit.partner_id, self.customer)
        self.assertAlmostEqual(credit.amount_residual, -200.0, msg='the excess is an open customer credit')
        self.assertEqual(inv.nc_overpaid_amount, 0.0, 'overpaid is only stamped by a registered payment')

    # 8 -----------------------------------------------------------------
    def test_08_vendor_prepayment(self):
        st = self.line(-300.0, partner=self.vendor, label='Advance to Delta')
        rest = [d for d in st.reconcile_data_info['data'] if d['kind'] == 'suspense']
        self.assertEqual(rest[0]['account_id'][0], self.vendor.property_account_payable_id.id)
        st.reconcile_bank_line()
        advance = st.move_id.line_ids.filtered(lambda ml: ml.account_id == self.payable)
        self.assertAlmostEqual(advance.amount_residual, 300.0, msg='an open debit on the vendor: a prepayment')
        bill = self.invoice(300.0, partner=self.vendor, move_type='in_invoice')
        (self.open_item(bill) | advance).reconcile()
        self.assertEqual(bill.amount_residual, 0.0)

    # 9 -----------------------------------------------------------------
    def test_09_bank_fee_with_a_reconciliation_model(self):
        model = self.env['account.reconcile.model'].create({
            'name': 'Bank Fees', 'trigger': 'manual', 'match_label': 'contains', 'match_label_param': 'Bank Fees',
            'line_ids': [Command.create({'account_id': self.fees.id, 'amount_type': 'percentage', 'amount_string': '100'})],
        })
        before = self.journal_balance(self.misr)
        st = self.line(-25.0, label='Bank Fees March')
        from odoo.tests import Form
        with Form(st, view='account_reconcile_oca.bank_statement_line_form_reconcile_view') as f:
            f.manual_model_id = model
        st.reconcile_bank_line()
        self.assertTrue(st.is_reconciled)
        self.assertEqual(st.move_id.line_ids.filtered(lambda ml: ml.account_id == self.fees).balance, 25.0)
        self.assertAlmostEqual(self.journal_balance(self.misr) - before, -25.0)

    # 10 ----------------------------------------------------------------
    def test_10_currency_exchange_difference_booked_once(self):
        """OCA/account-reconcile#995 claims a FALSE exchange difference here. It is not false: a EUR invoice booked
        at one rate and paid at another has a real gain or loss in company currency. What must hold is that it is
        booked ONCE, the invoice closes in both currencies and every entry balances."""
        eur = self.env.ref('base.EUR')
        eur.active = True
        company_currency = self.company.currency_id
        self.assertNotEqual(eur, company_currency)
        day1, day2 = fields.Date.from_string('2026-03-01'), fields.Date.from_string('2026-03-20')
        self.env['res.currency.rate'].search([('currency_id', '=', eur.id), ('company_id', 'in', (False, self.company.id))]).unlink()
        self.env['res.currency.rate'].create([
            {'currency_id': eur.id, 'name': day1, 'rate': 0.5, 'company_id': self.company.id},   # 1 EUR = 2.00
            {'currency_id': eur.id, 'name': day2, 'rate': 0.4, 'company_id': self.company.id},   # 1 EUR = 2.50
        ])
        eur_bank = self.env['account.journal'].create({'name': 'EUR Bank', 'type': 'bank', 'code': 'BEUR',
                                                       'currency_id': eur.id})
        eur_bank.suspense_account_id = self.company.account_journal_suspense_account_id
        inv = self.invoice(100.0, currency=eur, date=day1)
        self.assertAlmostEqual(self.open_item(inv).balance, 200.0)
        st = self.line(100.0, journal=eur_bank, partner=self.customer, date=day2)
        self.assertAlmostEqual(st.move_id.line_ids.filtered(lambda ml: ml.account_id == eur_bank.default_account_id).balance, 250.0)
        self.match(st, self.open_item(inv))
        st.reconcile_bank_line()
        self.assertTrue(st.is_reconciled)
        self.assert_balanced(st.move_id)
        item = self.open_item(inv)
        self.assertTrue(item.reconciled)
        self.assertEqual((item.amount_residual, item.amount_residual_currency), (0.0, 0.0))
        gain_acc = self.company.income_currency_exchange_account_id
        loss_acc = self.company.expense_currency_exchange_account_id
        fx = self.env['account.move.line'].search([('account_id', 'in', (gain_acc | loss_acc).ids),
                                                   ('company_id', '=', self.company.id), ('parent_state', '=', 'posted')])
        self.assertAlmostEqual(-sum(fx.mapped('balance')), 50.0,
                               msg='a 50.00 exchange gain (200 booked, 250 received), booked exactly once')

    # 11 ----------------------------------------------------------------
    def test_11_internal_transfer_between_banks(self):
        self.assertTrue(self.transfer, 'the company has an internal transfer account')
        out = self.line(-1000.0, journal=self.misr, label='Transfer to Ahly')
        self.suspense_to(out, self.transfer)
        out.reconcile_bank_line()
        into = self.line(1000.0, journal=self.ahly, label='Transfer from Misr')
        self.suspense_to(into, self.transfer)
        into.reconcile_bank_line()
        legs = (out.move_id | into.move_id).line_ids.filtered(lambda ml: ml.account_id == self.transfer)
        self.assertEqual(len(legs), 2)
        if self.transfer.reconcile:
            legs.reconcile()
            self.assertTrue(all(legs.mapped('reconciled')))
        self.assertTrue(self.company.currency_id.is_zero(sum(legs.mapped('balance'))))
        touched = (out.move_id | into.move_id).line_ids.account_id
        self.assertFalse(touched.filtered(lambda a: a.internal_group in ('income', 'expense')),
                         'a transfer is neither revenue nor expense')

    # 13 ----------------------------------------------------------------
    def test_13_undo_restores_the_invoice_and_is_audited(self):
        inv = self.invoice(1000.0)
        st = self.line(1000.0, partner=self.customer)
        self.match(st, self.open_item(inv))
        st.reconcile_bank_line()
        self.assertEqual(inv.amount_residual, 0.0)
        posted_before = self.env['account.move'].search_count([('state', '=', 'posted')])
        st.unreconcile_bank_line()
        self.assertFalse(st.is_reconciled)
        self.assertAlmostEqual(inv.amount_residual, 1000.0)
        self.assertEqual(inv.payment_state, 'not_paid')
        self.assertEqual(self.env['account.move'].search_count([('state', '=', 'posted')]), posted_before,
                         'undo deletes no posted entry')
        bodies = ' '.join(st.move_id.message_ids.mapped('body'))
        self.assertIn('matched by', bodies)
        self.assertIn('undone by', bodies)
        self.assertIn(inv.name, bodies)
        with self.assertRaises(UserError):
            st.unreconcile_bank_line()
        # the pending reconciliation is rebuilt from the state AFTER the undo: no stale counterpart left to re-book
        stale = [d for d in st.reconcile_data_info['data'] if d.get('kind') == 'other']
        self.assertFalse(stale, f'no stale counterpart after undo: {stale}')
        # and it can be matched again
        self.match(st, self.open_item(inv))
        st.reconcile_bank_line()
        self.assertEqual(inv.amount_residual, 0.0)

    # 14 ----------------------------------------------------------------
    def test_14_reconciled_transaction_and_paid_items_cannot_be_used_again(self):
        inv = self.invoice(1000.0)
        st = self.line(1000.0, partner=self.customer)
        self.match(st, self.open_item(inv))
        st.reconcile_bank_line()
        with self.assertRaises(UserError):
            st.reconcile_bank_line()
        other = self.line(1000.0, partner=self.customer)
        other.reconcile_data_info = dict(other.reconcile_data_info, data=other.reconcile_data_info['data'] + [{
            'reference': f'account.move.line;{self.open_item(inv).id}', 'id': self.open_item(inv).id,
            'counterpart_line_ids': self.open_item(inv).ids, 'kind': 'other'}])
        with self.assertRaises(UserError):
            other.reconcile_bank_line()
        self.assertEqual(inv.amount_residual, 0.0, 'the invoice was not paid twice')

    # 15 ----------------------------------------------------------------
    def test_15_other_company_and_read_only_user_are_refused(self):
        other_company = self.company_data_2['company'] if hasattr(self, 'company_data_2') else self.setup_other_company()['company']
        foreign_inv = self.env['account.move'].with_company(other_company).create({
            'move_type': 'out_invoice', 'partner_id': self.customer.id, 'invoice_date': self.day,
            'invoice_line_ids': [Command.create({'name': 'X', 'quantity': 1, 'price_unit': 100.0, 'tax_ids': [Command.clear()]})],
        })
        foreign_inv.action_post()
        st = self.line(100.0, partner=self.customer)
        aml = self.open_item(foreign_inv)
        st.reconcile_data_info = dict(st.reconcile_data_info, data=st.reconcile_data_info['data'] + [{
            'reference': f'account.move.line;{aml.id}', 'id': aml.id, 'counterpart_line_ids': aml.ids, 'kind': 'other'}])
        with self.assertRaises(UserError):
            st.reconcile_bank_line()
        self.assertEqual(foreign_inv.amount_residual, 100.0)

        reader = self.env['res.users'].create({
            'name': 'Reader', 'login': 'nc_bank_reader', 'company_id': self.company.id,
            'company_ids': [Command.set(self.company.ids)],
            'group_ids': [Command.set([self.env.ref('base.group_user').id, self.env.ref('account.group_account_readonly').id])],
        })
        inv = self.invoice(50.0)
        st2 = self.line(50.0, partner=self.customer)
        self.match(st2, self.open_item(inv))
        with self.assertRaises(AccessError):
            st2.with_user(reader).reconcile_bank_line()
        st2.reconcile_bank_line()
        with self.assertRaises(AccessError):
            st2.with_user(reader).unreconcile_bank_line()
        with self.assertRaises(AccessError):
            self.misr.with_user(reader).action_nc_bank_new()
        self.assertTrue(st2.is_reconciled)

    # 16 ----------------------------------------------------------------
    def test_16_a_held_lock_is_refused_not_waited_for(self):
        """The two-connection proof (a real second database session) is in e2e/bank/concurrency.py; here the
        refusal path itself: a NOWAIT lock failure becomes a clear UserError and leaves the transaction usable."""
        from unittest.mock import patch
        from psycopg2 import errors as pg_errors
        inv = self.invoice(100.0)
        st = self.line(100.0, partner=self.customer)
        self.match(st, self.open_item(inv))
        real = type(self.env.cr).execute

        def busy(cr, query, params=None, *args, **kwargs):
            if 'FOR UPDATE NOWAIT' in str(query):
                raise pg_errors.LockNotAvailable('could not obtain lock')
            return real(cr, query, params, *args, **kwargs)
        with patch.object(type(self.env.cr), 'execute', busy), self.assertRaises(UserError):
            st.reconcile_bank_line()
        self.assertFalse(st.is_reconciled)
        st.reconcile_bank_line()
        self.assertTrue(st.is_reconciled)

    # 17 ----------------------------------------------------------------
    def test_17_cash_journal(self):
        inv = self.invoice(200.0)
        before = self.journal_balance(self.cash)
        st = self.line(200.0, journal=self.cash, partner=self.customer, label='Cash at counter')
        self.match(st, self.open_item(inv))
        st.reconcile_bank_line()
        self.assertTrue(st.is_reconciled)
        self.assertEqual(inv.amount_residual, 0.0)
        self.assertAlmostEqual(self.journal_balance(self.cash) - before, 200.0)

    # #1036 ---------------------------------------------------------------
    def test_full_paid_on_a_line_allocated_in_full_is_a_no_op(self):
        inv = self.invoice(100.0)
        st = self.line(100.0, partner=self.customer)
        self.match(st, self.open_item(inv))
        info = st.reconcile_data_info
        info['manual_reference'] = f'account.move.line;{self.open_item(inv).id}'
        st.reconcile_data_info = info
        st.button_manual_reference_full_paid()   # KeyError 'original_amount_unsigned' upstream
        st.reconcile_bank_line()
        self.assertEqual(inv.amount_residual, 0.0)

    # automated rules ---------------------------------------------------
    def test_automatic_matching_never_posts_an_ambiguous_match(self):
        """With the default automatic models ON: a transaction whose label is exactly one invoice's number is
        matched with that invoice; a transaction that fits TWO open invoices equally (same partner, same amount, no
        reference) is left for a person."""
        self.env['account.reconcile.model'].with_context(active_test=False).search(
            [('trigger', '=', 'auto_reconcile'), ('company_id', '=', self.company.id)]).active = True
        inv_a, inv_b = self.invoice(640.0), self.invoice(640.0)
        ambiguous = self.line(640.0, partner=self.customer, label='Payment')
        self.assertFalse(ambiguous.is_reconciled, 'two equal candidates: nothing is booked automatically')
        self.assertEqual((inv_a.amount_residual, inv_b.amount_residual), (640.0, 640.0))
        exact = self.line(640.0, partner=self.customer, label=inv_b.name)
        if exact.is_reconciled:
            self.assertEqual(inv_b.amount_residual, 0.0, 'the label names exactly this invoice')
            self.assertEqual(inv_a.amount_residual, 640.0, 'and only this one')
        else:
            proposed = [d.get('id') for d in exact.reconcile_data_info['data'] if d.get('kind') == 'other']
            self.assertEqual(proposed, self.open_item(inv_b).ids, 'proposed, for a person to validate')

    # card --------------------------------------------------------------
    def test_card_actions_are_scoped_to_the_journal(self):
        for method in ('action_nc_bank_transactions', 'action_nc_bank_to_reconcile', 'action_nc_bank_statements'):
            action = getattr(self.misr, method)()
            self.assertEqual(action['domain'], [('journal_id', '=', self.misr.id)], method)
        self.assertTrue(self.misr.action_nc_bank_to_reconcile()['context']['search_default_not_reconciled'])
        self.assertNotIn('search_default_not_reconciled', self.misr.action_nc_bank_transactions()['context'])
        self.assertEqual(self.misr.action_nc_bank_new()['context']['default_journal_id'], self.misr.id)
        upload = self.misr.action_nc_bank_upload()
        self.assertEqual(upload['context']['journal_id'], self.misr.id)
        self.assertTrue(upload['context']['nc_require_preview'])
        with self.assertRaises(UserError):
            self.cash.action_nc_bank_upload()
        sale = self.company_data['default_journal_sale']
        with self.assertRaises(UserError):
            sale.action_nc_bank_transactions()
        # Odoo's own Transactions button goes to the matching workspace, not to an empty statement list.
        self.assertEqual(self.misr.open_action().get('res_model'), 'account.bank.statement.line')
