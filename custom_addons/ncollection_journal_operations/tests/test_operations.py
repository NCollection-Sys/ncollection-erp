from odoo import fields
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import Form, tagged

from .common import JournalOpsCommon


@tagged('post_install', '-at_install')
class TestBalances(JournalOpsCommon):

    def test_new_journals_get_the_card_without_setup(self):
        journal = self.env['account.journal'].create({'name': 'Brand New Cash', 'type': 'cash', 'code': 'BNC9'})
        data = journal._get_journal_dashboard_data_batched()[journal.id]
        self.assertTrue(data['nc_ops'])
        self.assertEqual(data['nc_pending_count'], 0)
        self.assertIn('0.00', data['nc_balance'])
        sale = self.company_data['default_journal_sale']
        self.assertNotIn('nc_ops', sale._get_journal_dashboard_data_batched()[sale.id])

    def test_balance_is_ledger_without_reconciliation(self):
        self._bank_line(self.bank, 500.0, 'opening deposit')
        self.assertAlmostEqual(self._figures(self.bank)['balance'], 500.0)
        receipt = self._op(self.bank, 'receipt', 'customer', 100.0, self.customer)
        self.assertEqual(receipt.state, 'in_process')
        figures = self._figures(self.bank)
        self.assertAlmostEqual(figures['balance'], 500.0, msg="pending money is not liquidity yet")
        self.assertAlmostEqual(figures['pending_in'], 100.0)
        self.assertEqual(figures['pending_count'], 1)
        draft = self._op(self.bank, 'payment', 'supplier', 40.0, self.supplier, post=False)
        self.assertEqual(draft.state, 'draft')
        self.assertAlmostEqual(self._figures(self.bank)['pending_out'], 0.0, msg="drafts never count")

    def test_pending_signs_both_directions(self):
        self._op(self.bank, 'receipt', 'customer', 100.0, self.customer)
        self._op(self.bank, 'payment', 'supplier', 30.0, self.supplier)
        figures = self._figures(self.bank)
        self.assertAlmostEqual(figures['pending_in'], 100.0)
        self.assertAlmostEqual(figures['pending_out'], -30.0)
        data = self.bank._get_journal_dashboard_data_batched()[self.bank.id]
        self.assertIn('70.00', data['nc_pending'])
        self.assertEqual(self._figures(self.bank2)['pending_count'], 0, "scoped to the journal")

    def test_direct_method_is_paid_and_counted_once(self):
        method = self.bank2.inbound_payment_method_line_ids[:1]
        method.payment_account_id = self.bank2.default_account_id
        receipt = self._op(self.bank2, 'receipt', 'customer', 80.0, self.customer)
        self.assertEqual(receipt.state, 'paid')
        self.assertFalse(receipt.nc_can_mark_paid)
        figures = self._figures(self.bank2)
        self.assertAlmostEqual(figures['balance'], 80.0)
        self.assertEqual(figures['pending_count'], 0)


@tagged('post_install', '-at_install')
class TestPaymentReceipt(JournalOpsCommon):

    def _lines(self, pay):
        return {line.account_id: (line.debit, line.credit) for line in pay.move_id.line_ids}

    def test_six_combinations_post_the_right_entries(self):
        receivable = self.customer.property_account_receivable_id
        payable = self.supplier.property_account_payable_id
        out_rec = self.bank.inbound_payment_method_line_ids[:1].payment_account_id
        out_pay = self.bank.outbound_payment_method_line_ids[:1].payment_account_id
        cases = [
            ('receipt', 'customer', self.customer, None, {out_rec: (50, 0), receivable: (0, 50)}),
            ('receipt', 'supplier', self.supplier, None, {out_rec: (50, 0), payable: (0, 50)}),
            ('receipt', 'account', None, self.other_income, {out_rec: (50, 0), self.other_income: (0, 50)}),
            ('payment', 'supplier', self.supplier, None, {payable: (50, 0), out_pay: (0, 50)}),
            ('payment', 'customer', self.customer, None, {receivable: (50, 0), out_pay: (0, 50)}),
            ('payment', 'account', None, self.fees, {self.fees: (50, 0), out_pay: (0, 50)}),
        ]
        for operation, kind, partner, account, expected in cases:
            with self.subTest(operation=operation, kind=kind):
                pay = self._op(self.bank, operation, kind, 50.0, partner, account)
                self.assertEqual(pay.journal_id, self.bank)
                self.assertEqual(pay.payment_type, 'inbound' if operation == 'receipt' else 'outbound')
                self.assertEqual(pay.state, 'in_process')
                self.assertEqual(self._lines(pay), expected)
                self.assertTrue(pay.move_id.currency_id.is_zero(sum(pay.move_id.line_ids.mapped('balance'))))
                if kind == 'account':
                    self.assertFalse(pay.partner_id, "no placeholder partner")

    def test_card_form_shows_only_the_payee_partner_field(self):
        from lxml import etree
        view = self.env.ref('ncollection_journal_operations.view_payment_form_operation')
        arch = etree.fromstring(self.env['account.payment'].get_views([(view.id, 'form')])['views']['form']['arch'])
        partners = arch.xpath("//field[@name='partner_id']")
        shown = [node for node in partners if node.get('invisible') not in ('1', 'True')]
        self.assertEqual(len(shown), 1, [etree.tostring(node)[:200] for node in shown])
        self.assertEqual(shown[0].get('string'), 'Payee')
        self.assertIn('nc_counterpart_type', shown[0].get('invisible'))
        for kind, partner in (('customer', self.customer), ('supplier', self.supplier)):
            action = self.bank.action_nc_ops_receipt()
            form = Form(self.env['account.payment'].with_context(**action['context']), view=view)
            form.nc_counterpart_type = kind
            form.partner_id = partner
            form.amount = 3.0
            pay = form.save()
            self.assertEqual((pay.partner_id, pay.partner_type), (partner, kind))

    def test_bank_fees_through_the_card_form(self):
        action = self.bank.action_nc_ops_payment()
        form = Form(self.env['account.payment'].with_context(**action['context']),
                    view='ncollection_journal_operations.view_payment_form_operation')
        form.nc_counterpart_type = 'account'
        form.nc_counterpart_account_id = self.fees
        form.amount = 12.5
        form.memo = 'March bank charges'
        pay = form.save()
        pay.action_post()
        self.assertEqual(pay.journal_id, self.bank)
        self.assertEqual(pay.destination_account_id, self.fees)
        fee_line = pay.move_id.line_ids.filtered(lambda line: line.account_id == self.fees)
        self.assertAlmostEqual(fee_line.debit, 12.5)
        self.assertEqual(pay.memo, 'March bank charges')

    def test_account_type_refuses_self_offsetting_and_partner_accounts(self):
        own_outstanding = self.bank.outbound_payment_method_line_ids[:1].payment_account_id
        for account in (self.customer.property_account_receivable_id, self.bank2.default_account_id,
                        own_outstanding, self.company.transfer_account_id):
            with self.subTest(account=account.display_name), self.assertRaises(ValidationError):
                self._op(self.bank, 'payment', 'account', 10.0, account=account, post=False)

    def test_journal_and_direction_are_locked_server_side(self):
        pay = self.env['account.payment'].create({
            'nc_operation': 'payment', 'nc_source_journal_id': self.bank.id, 'journal_id': self.cash.id,
            'payment_type': 'inbound', 'nc_counterpart_type': 'supplier', 'partner_id': self.supplier.id,
            'amount': 10.0})
        self.assertEqual(pay.journal_id, self.bank, "the card journal wins over a crafted journal_id")
        self.assertEqual(pay.payment_type, 'outbound', "a Payment is outgoing whatever the client sent")
        with self.assertRaises(ValidationError):
            pay.journal_id = self.cash
        with self.assertRaises(ValidationError):
            pay.nc_source_journal_id = self.cash
        with self.assertRaises(ValidationError):
            pay.payment_type = 'inbound'
        with self.assertRaises(ValidationError):
            pay.nc_operation = 'receipt'

    def test_rpc_cannot_bypass_the_card_rules(self):
        method = self.bank.outbound_payment_method_line_ids[:1]
        with self.assertRaises(ValidationError):
            self.env['account.payment'].create({
                'journal_id': self.bank.id, 'payment_type': 'outbound', 'nc_counterpart_type': 'account',
                'nc_counterpart_account_id': self.bank2.default_account_id.id, 'amount': 5.0,
                'payment_method_line_id': method.id})
        plain = self.env['account.payment'].create({
            'journal_id': self.bank.id, 'payment_type': 'inbound', 'partner_type': 'customer',
            'partner_id': self.customer.id, 'amount': 5.0})
        for vals in ({'nc_operation': 'receipt'}, {'nc_source_journal_id': self.bank.id}):
            with self.subTest(vals=vals), self.assertRaises(ValidationError):
                plain.write(vals)
        card = self.env['account.account'].create({'code': '201991', 'name': 'Card (ops test)',
                                                   'account_type': 'liability_credit_card'})
        with self.assertRaises(ValidationError):
            self._op(self.bank, 'payment', 'account', 5.0, account=card, post=False)

    def test_payee_type_must_match(self):
        pay = self._op(self.bank, 'receipt', 'account', 10.0, partner=self.customer, account=self.other_income,
                       post=False)
        self.assertFalse(pay.partner_id, "an Account operation never carries a partner")
        with self.assertRaises(ValidationError):
            pay.partner_id = self.customer
        supplier_pay = self._op(self.bank, 'payment', 'supplier', 10.0, self.supplier, post=False)
        with self.assertRaises(ValidationError):
            supplier_pay.partner_type = 'customer'


@tagged('post_install', '-at_install')
class TestMarkAsPaid(JournalOpsCommon):

    def test_receipt_moves_outstanding_to_bank_and_is_paid(self):
        receipt = self._op(self.bank, 'receipt', 'customer', 100.0, self.customer)
        outstanding = receipt.move_id.line_ids.filtered(lambda line: line.debit).account_id
        self.assertTrue(receipt.nc_can_mark_paid)
        st_line = receipt._nc_mark_as_paid()
        self.assertEqual(receipt.state, 'paid')
        self.assertTrue(receipt.is_matched)
        self.assertFalse(receipt.nc_can_mark_paid)
        lines = {line.account_id: (line.debit, line.credit) for line in st_line.move_id.line_ids}
        self.assertEqual(lines, {self.bank.default_account_id: (100.0, 0.0), outstanding: (0.0, 100.0)})
        self.assertTrue(receipt.move_id.line_ids.filtered(lambda line: line.account_id == outstanding).reconciled)
        figures = self._figures(self.bank)
        self.assertAlmostEqual(figures['balance'], 100.0)
        self.assertEqual(figures['pending_count'], 0)
        self.assertEqual(st_line.nc_settlement_payment_id, receipt)

    def test_payment_direction_and_cash_journal(self):
        payment = self._op(self.cash, 'payment', 'supplier', 60.0, self.supplier)
        outstanding = payment.move_id.line_ids.filtered(lambda line: line.credit).account_id
        st_line = payment._nc_mark_as_paid()
        lines = {line.account_id: (line.debit, line.credit) for line in st_line.move_id.line_ids}
        self.assertEqual(lines, {self.cash.default_account_id: (0.0, 60.0), outstanding: (60.0, 0.0)})
        self.assertAlmostEqual(self._figures(self.cash)['balance'], -60.0)
        self.assertEqual(payment.state, 'paid')

    def test_uses_the_posted_account_not_todays_method(self):
        receipt = self._op(self.bank, 'receipt', 'customer', 25.0, self.customer)
        posted_account = receipt.move_id.line_ids.filtered(lambda line: line.debit).account_id
        other = self.env['account.account'].create({'code': '101977', 'name': 'Changed later',
                                                    'account_type': 'asset_current', 'reconcile': True})
        self.bank.inbound_payment_method_line_ids[:1].payment_account_id = other
        receipt.invalidate_recordset()
        st_line = receipt._nc_mark_as_paid()
        self.assertIn(posted_account, st_line.move_id.line_ids.account_id)
        self.assertNotIn(other, st_line.move_id.line_ids.account_id)
        self.assertEqual(receipt.state, 'paid')

    def test_twice_is_refused(self):
        receipt = self._op(self.bank, 'receipt', 'customer', 10.0, self.customer)
        receipt._nc_mark_as_paid()
        with self.assertRaises(UserError):
            receipt._nc_mark_as_paid()
        self.assertEqual(len(receipt.nc_settlement_line_ids), 1)

    def test_ineligible(self):
        draft = self._op(self.bank, 'receipt', 'customer', 10.0, self.customer, post=False)
        self.assertFalse(draft.nc_can_mark_paid)
        with self.assertRaises(UserError):
            draft._nc_mark_as_paid()
        cancelled = self._op(self.bank, 'receipt', 'customer', 10.0, self.customer)
        cancelled.action_draft()
        cancelled.action_cancel()
        with self.assertRaises(UserError):
            cancelled._nc_mark_as_paid()

    def test_waiting_bank_transaction_must_be_matched_instead(self):
        receipt = self._op(self.bank, 'receipt', 'customer', 75.0, self.customer)
        self._bank_line(self.bank, 75.0, 'transfer from customer')
        with self.assertRaises(UserError):
            receipt._nc_mark_as_paid()
        self.assertEqual(receipt.state, 'in_process')
        self.assertFalse(receipt.nc_settlement_line_ids)

    def test_partial_then_remainder(self):
        receipt = self._op(self.bank, 'receipt', 'customer', 100.0, self.customer)
        outstanding = receipt.move_id.line_ids.filtered(lambda line: line.debit)
        st_line = self.env['account.bank.statement.line'].create({
            'journal_id': self.bank.id, 'date': fields.Date.today(), 'payment_ref': 'part', 'amount': 40.0,
            'counterpart_account_id': outstanding.account_id.id})
        (st_line.move_id.line_ids.filtered(lambda line: line.account_id == outstanding.account_id) | outstanding)\
            .reconcile()
        receipt.invalidate_recordset()
        self.assertAlmostEqual(receipt.nc_pending_amount, 60.0)
        self.assertAlmostEqual(self._figures(self.bank)['pending_in'], 60.0)
        settlement = receipt._nc_mark_as_paid()
        self.assertAlmostEqual(settlement.amount, 60.0)
        self.assertEqual(receipt.state, 'paid')
        self.assertAlmostEqual(self._figures(self.bank)['balance'], 100.0)

    def test_invoice_receipt_paid_in_community_still_pending_for_the_bank(self):
        invoice = self._invoice(self.customer, 200.0)
        wizard = self.env['account.payment.register'].with_context(
            active_model='account.move', active_ids=invoice.ids).create({'journal_id': self.bank.id})
        payment = wizard._create_payments()
        self.assertIn(invoice.payment_state, ('paid', 'in_payment'))
        self.assertTrue(payment.nc_can_mark_paid, "the money is still on Outstanding Receipts")
        self.assertAlmostEqual(self._figures(self.bank)['pending_in'], 200.0)
        payment._nc_mark_as_paid()
        self.assertTrue(payment.is_matched)
        self.assertEqual(invoice.payment_state, 'paid', "invoice reconciliation untouched")
        self.assertAlmostEqual(self._figures(self.bank)['balance'], 200.0)

    def test_foreign_currency_payment(self):
        eur = self.setup_other_currency('EUR', rates=[('2016-01-01', 2.0)])
        receipt = self._op(self.bank, 'receipt', 'customer', 50.0, self.customer, currency_id=eur.id)
        settlement = receipt._nc_mark_as_paid()
        self.assertEqual(settlement.foreign_currency_id, eur)
        self.assertAlmostEqual(settlement.amount_currency, 50.0)
        self.assertEqual(receipt.state, 'paid')
        self.assertTrue(settlement.move_id.currency_id.is_zero(sum(settlement.move_id.line_ids.mapped('balance'))))

    def test_wizard_flow(self):
        receipt = self._op(self.bank, 'receipt', 'customer', 33.0, self.customer)
        action = receipt.action_nc_mark_as_paid()
        wizard = self.env[action['res_model']].with_context(**action['context']).create({})
        self.assertAlmostEqual(wizard.amount, 33.0)
        self.assertEqual(wizard.to_account_id, self.bank.default_account_id)
        wizard.action_confirm()
        self.assertEqual(receipt.state, 'paid')


@tagged('post_install', '-at_install')
class TestBankFileAfterMarkAsPaid(JournalOpsCommon):

    def _file_vals(self, amount, day, label='BANK CREDIT', bank_id='bank-abc-1', partner=None):
        line = {'payment_ref': label, 'amount': amount, 'date': day, 'unique_import_id': bank_id}
        if partner:
            line['partner_id'] = partner.id
        return [{'transactions': [line]}]

    def _id(self, vals):
        return vals[0]['transactions'][0]['unique_import_id']

    def test_import_recognises_the_settlement_and_a_reimport(self):
        receipt = self._op(self.bank, 'receipt', 'customer', 90.0, self.customer, memo='INV 5521 Nile')
        settlement = receipt._nc_mark_as_paid()
        wizard = self.env['account.statement.import']
        today = fields.Date.today()
        preview = wizard.with_context(nc_skipped_rows=[])._complete_stmts_vals(
            self._file_vals(90.0, today, 'TRF NILE inv 5521 nile'), self.bank, False)
        self.assertEqual(self._id(preview), settlement.unique_import_id)
        self.assertFalse(settlement.nc_bank_confirmed, "the preview writes nothing")
        wizard._complete_stmts_vals(self._file_vals(90.0, today, 'TRF NILE inv 5521 nile'), self.bank, False)
        self.assertTrue(settlement.nc_bank_confirmed)
        self.assertTrue(settlement.nc_bank_import_id)
        again = wizard._complete_stmts_vals(self._file_vals(90.0, today, 'TRF NILE inv 5521 nile'), self.bank, False)
        self.assertEqual(self._id(again), settlement.unique_import_id, "the same file again stays the same line")
        other = wizard._complete_stmts_vals(
            self._file_vals(90.0, today, 'INV 5521 Nile', bank_id='bank-xyz-9'), self.bank, False)
        self.assertNotEqual(self._id(other), settlement.unique_import_id, "a confirmed settlement is used once")

    def test_partner_is_an_identity_link(self):
        receipt = self._op(self.bank, 'receipt', 'customer', 70.0, self.customer)
        settlement = receipt._nc_mark_as_paid()
        vals = self.env['account.statement.import']._complete_stmts_vals(
            self._file_vals(70.0, fields.Date.today(), partner=self.customer), self.bank, False)
        self.assertEqual(self._id(vals), settlement.unique_import_id)

    def test_amount_and_date_alone_never_swallow_a_line(self):
        receipt = self._op(self.bank, 'receipt', 'customer', 90.0, self.customer, memo='INV 5521 Nile')
        settlement = receipt._nc_mark_as_paid()
        other_customer = self.env['res.partner'].create({'name': 'Other payer', 'customer_rank': 1})
        cases = [(90.0, fields.Date.today(), 'BANK CREDIT', None),
                 (90.0, fields.Date.today(), 'BANK CREDIT', other_customer),
                 (91.0, fields.Date.today(), 'INV 5521 Nile', None),
                 (90.0, fields.Date.add(fields.Date.today(), days=20), 'INV 5521 Nile', None)]
        for amount, day, label, partner in cases:
            vals = self.env['account.statement.import']._complete_stmts_vals(
                self._file_vals(amount, day, label, partner=partner), self.bank, False)
            self.assertNotEqual(self._id(vals), settlement.unique_import_id, (amount, label))
        self.assertFalse(settlement.nc_bank_confirmed)


@tagged('post_install', '-at_install')
class TestInternalTransfer(JournalOpsCommon):

    def _transfer(self, source, destination, amount, **extra):
        return self.env['ncollection.internal.transfer'].create({
            'source_journal_id': source.id, 'destination_journal_id': destination.id, 'amount': amount,
            'memo': 'move cash', **extra})

    def test_all_four_routes(self):
        for source, destination in ((self.cash, self.bank), (self.bank, self.cash), (self.bank, self.bank2)):
            with self.subTest(route=f'{source.type}->{destination.type}'):
                transfer = self._transfer(source, destination, 70.0)
                transfer.action_post()
                out, inn = transfer.source_payment_id, transfer.destination_payment_id
                self.assertEqual((out.journal_id, out.payment_type), (source, 'outbound'))
                self.assertEqual((inn.journal_id, inn.payment_type), (destination, 'inbound'))
                self.assertEqual(out.paired_internal_transfer_payment_id, inn)
                transfer_lines = (out.move_id | inn.move_id).line_ids.filtered(
                    lambda line: line.account_id == self.company.transfer_account_id)
                self.assertEqual(len(transfer_lines), 2)
                self.assertTrue(all(transfer_lines.mapped('reconciled')))
                self.assertEqual(transfer.progress, 'pending', "not claimed as arrived")
                out._nc_mark_as_paid()
                inn._nc_mark_as_paid()
                transfer.invalidate_recordset(['progress'])
                self.assertEqual(transfer.progress, 'done')
        cash2 = self.env['account.journal'].create({'name': 'Second Box', 'type': 'cash', 'code': 'OPC2'})
        transfer = self._transfer(self.cash, cash2, 5.0)
        transfer.action_post()
        self.assertEqual(transfer.destination_payment_id.journal_id, cash2)

    def test_balances_move_once(self):
        transfer = self._transfer(self.bank, self.bank2, 40.0)
        transfer.action_post()
        transfer.source_payment_id._nc_mark_as_paid()
        transfer.destination_payment_id._nc_mark_as_paid()
        self.assertAlmostEqual(self._figures(self.bank)['balance'], -40.0)
        self.assertAlmostEqual(self._figures(self.bank2)['balance'], 40.0)

    def test_refusals(self):
        with self.assertRaises(ValidationError):
            self._transfer(self.bank, self.bank, 10.0)
        sale = self.company_data['default_journal_sale']
        with self.assertRaises(ValidationError):
            self._transfer(self.bank, sale, 10.0)
        with self.assertRaises(ValidationError):
            self._transfer(self.bank, self.cash, -5.0)
        transfer = self._transfer(self.bank, self.cash, 10.0)
        transfer.action_post()
        with self.assertRaises(UserError):
            transfer.action_post()
        self.assertEqual(self.env['account.payment'].search_count([('nc_transfer_id', '=', transfer.id)]), 2)
        with self.assertRaises(ValidationError):
            transfer.source_journal_id = self.bank2

    def test_posting_fields_are_not_writable_and_sides_are_equal(self):
        transfer = self._transfer(self.bank, self.cash, 20.0)
        with self.assertRaises(UserError):
            transfer.write({'state': 'posted'})
        with self.assertRaises(ValidationError):
            transfer.destination_amount = 30.0
        transfer.action_post()
        with self.assertRaises(UserError):
            transfer.write({'state': 'draft', 'source_payment_id': False})
        with self.assertRaises(UserError):
            transfer.action_post()
        self.assertEqual(self.env['account.payment'].search_count([('nc_transfer_id', '=', transfer.id)]), 2)

    def test_other_company_destination_refused(self):
        other = self.company_data_2['default_journal_bank']
        with self.assertRaises(UserError):
            self._transfer(self.bank, other, 10.0)

    def test_multi_currency(self):
        eur = self.setup_other_currency('EUR', rates=[('2016-01-01', 2.0)])
        eur_bank = self.env['account.journal'].create({'name': 'EUR bank', 'type': 'bank', 'code': 'OPEU',
                                                       'currency_id': eur.id})
        transfer = self._transfer(self.bank, eur_bank, 100.0)
        self.assertTrue(transfer.multi_currency)
        self.assertAlmostEqual(transfer.destination_amount, 200.0)
        transfer.action_post()
        self.assertEqual(transfer.destination_payment_id.currency_id, eur)
        self.assertAlmostEqual(transfer.destination_payment_id.amount, 200.0)


@tagged('post_install', '-at_install')
class TestTransactions(JournalOpsCommon):

    def _rows(self, journal):
        self.env.flush_all()
        return self.env['ncollection.journal.transaction'].search(
            [('journal_id', '=', journal.id)], order='date, id')

    def _ledger(self, journal, cutoff=None):
        domain = [('account_id', '=', journal.default_account_id.id), ('parent_state', '=', 'posted')]
        if cutoff:
            domain.append(('date', '<=', cutoff))
        return sum(self.env['account.move.line'].search(domain).mapped('balance'))

    def _assert_running_equals_ledger(self, journal):
        rows = self._rows(journal)
        for cutoff in sorted(set(rows.mapped('date'))):
            upto = rows.filtered(lambda r: r.date <= cutoff).sorted(lambda r: (r.date, r.id))
            self.assertAlmostEqual(upto[-1].running_balance, self._ledger(journal, cutoff),
                                   msg=f"running balance at {cutoff}")
        self.assertAlmostEqual(sum(rows.mapped('liquidity_balance')), self._ledger(journal))
        return rows

    def _match(self, payment, amount, day, ref):
        """A bank line of ``amount`` (may differ from the payment) matched with the payment's outstanding line."""
        outstanding = payment._nc_open_liquidity_lines()
        st_line = self.env['account.bank.statement.line'].create({
            'journal_id': payment.journal_id.id, 'date': day, 'payment_ref': ref, 'amount': amount,
            'counterpart_account_id': outstanding.account_id.id})
        (st_line.move_id.line_ids.filtered(lambda line: line.account_id == outstanding.account_id)
         | outstanding).reconcile()
        return st_line

    def test_one_row_per_operation_and_running_balance(self):
        self._bank_line(self.bank, 300.0, 'deposit')
        receipt = self._op(self.bank, 'receipt', 'customer', 100.0, self.customer)
        receipt._nc_mark_as_paid()
        pending = self._op(self.bank, 'payment', 'supplier', 30.0, self.supplier)
        fee = self._op(self.bank, 'payment', 'account', 5.0, account=self.fees)
        fee._nc_mark_as_paid()
        rows = self._assert_running_equals_ledger(self.bank)
        self.assertEqual(len(rows.filtered(lambda r: r.payment_id == receipt and r.operation == 'receipt')), 1)
        confirmation = rows.filtered(lambda r: r.statement_line_id.nc_settlement_payment_id == receipt)
        self.assertEqual(confirmation.operation, 'settlement')
        self.assertEqual(confirmation.payment_id, receipt)
        self.assertFalse(confirmation.amount_in or confirmation.amount_out, "never a second incoming amount")
        self.assertAlmostEqual(sum(rows.mapped('amount_in')), 400.0)
        self.assertEqual(len(rows.filtered(lambda r: r.operation == 'bank')), 1)
        row = rows.filtered(lambda r: r.payment_id == pending and r.operation == 'payment')
        self.assertEqual(row.status, 'in_process')
        self.assertAlmostEqual(row.pending_balance, -30.0)
        self.assertAlmostEqual(row.liquidity_balance, 0.0)
        self.assertEqual(rows.filtered(lambda r: r.payment_id == fee and r.operation != 'settlement')
                         .operation, 'account')
        self.assertAlmostEqual(self._figures(self.bank)['balance'], 395.0)

    def test_partial_and_different_amounts_follow_the_posted_lines_at_every_cutoff(self):
        today = fields.Date.today()
        day = lambda n: fields.Date.add(today, days=n)  # noqa: E731
        self._bank_line(self.bank, 1000.0, 'opening', date=day(-20))
        receipt = self._op(self.bank, 'receipt', 'customer', 100.0, self.customer, date=day(-15))
        self._match(receipt, 95.0, day(-10), 'bank says 95')            # partial, different amount, later date
        self.assertAlmostEqual(receipt.nc_pending_amount, 5.0)
        payment = self._op(self.bank, 'payment', 'supplier', 40.0, self.supplier, date=day(-9))
        self._match(payment, -40.0, day(-3), 'cheque cleared')          # settled a week later
        self.env['account.move'].create({                               # a manual entry on the bank account
            'move_type': 'entry', 'date': day(-5), 'journal_id': self.company_data['default_journal_misc'].id,
            'line_ids': [(0, 0, {'account_id': self.bank.default_account_id.id, 'debit': 7.0, 'name': 'adj'}),
                         (0, 0, {'account_id': self.other_income.id, 'credit': 7.0, 'name': 'adj'})]}).action_post()
        receipt._nc_mark_as_paid(day(-1))                               # the remaining 5, on its own date
        rows = self._assert_running_equals_ledger(self.bank)
        self.assertAlmostEqual(self._ledger(self.bank), 1000 + 95 - 40 + 7 + 5)
        self.assertAlmostEqual(rows.filtered(lambda r: r.operation == 'receipt').liquidity_balance, 0.0,
                               "nothing is counted at the nominal amount on the payment's date")
        settlements = rows.filtered(lambda r: r.operation == 'settlement')
        self.assertEqual(sorted(settlements.mapped('liquidity_balance')), [-40.0, 5.0, 95.0])
        self.assertAlmostEqual(self._figures(self.bank)['balance'], self._ledger(self.bank))
        before_settlement = rows.filtered(lambda r: r.date <= day(-11)).sorted(lambda r: (r.date, r.id))[-1]
        self.assertAlmostEqual(before_settlement.running_balance, 1000.0, msg="the receipt was not yet in the bank")

    def test_direct_method_counts_on_the_payment_row(self):
        method = self.bank2.inbound_payment_method_line_ids[:1]
        method.payment_account_id = self.bank2.default_account_id
        receipt = self._op(self.bank2, 'receipt', 'customer', 80.0, self.customer)
        rows = self._assert_running_equals_ledger(self.bank2)
        self.assertAlmostEqual(rows.filtered(lambda r: r.payment_id == receipt).liquidity_balance, 80.0)

    def test_other_journal_not_shown(self):
        self._op(self.bank2, 'receipt', 'customer', 10.0, self.customer)
        self.assertFalse(self._rows(self.bank))

    def test_actions_are_scoped(self):
        self._op(self.bank, 'receipt', 'customer', 10.0, self.customer)
        self._op(self.bank2, 'receipt', 'customer', 20.0, self.customer)
        action = self.bank.action_nc_ops_pending()
        payments = self.env['account.payment'].search(action['domain'])
        self.assertEqual(payments.journal_id, self.bank)
        self.assertIn(('journal_id', '=', self.bank.id), self.bank.action_nc_ops_transactions()['domain'])
        reconcile = self.bank.action_nc_ops_reconcile()
        self.assertIn(('journal_id', '=', self.bank.id), reconcile['domain'])


@tagged('post_install', '-at_install')
class TestSecurity(JournalOpsCommon):

    def _user(self, groups):
        return self.env['res.users'].create({
            'name': 'ops user', 'login': f'ops-{groups[0]}', 'company_id': self.company.id,
            'company_ids': [(6, 0, self.company.ids)],
            'group_ids': [(6, 0, [self.env.ref(g).id for g in groups])]})

    def test_readonly_accountant_cannot_operate(self):
        receipt = self._op(self.bank, 'receipt', 'customer', 10.0, self.customer)
        user = self._user(['base.group_user', 'account.group_account_readonly'])
        with self.assertRaises(AccessError):
            receipt.with_user(user)._nc_mark_as_paid()
        with self.assertRaises(AccessError):
            self.bank.with_user(user).action_nc_ops_payment()
        with self.assertRaises(AccessError):
            self.env['ncollection.internal.transfer'].with_user(user).create({
                'source_journal_id': self.bank.id, 'destination_journal_id': self.cash.id, 'amount': 1.0})
        self.assertEqual(receipt.state, 'in_process')

    def test_other_company_records_are_invisible(self):
        company_2 = self.company_data_2['company']
        other_bank = self.company_data_2['default_journal_bank']
        self.env['account.bank.statement.line'].with_company(company_2).create({
            'journal_id': other_bank.id, 'date': fields.Date.today(), 'payment_ref': 'c2', 'amount': 10.0})
        self._bank_line(self.bank, 5.0, 'c1')
        user = self._user(['base.group_user', 'account.group_account_user'])
        with self.assertRaises(AccessError):
            other_bank.with_user(user).action_nc_ops_transactions()
        with self.assertRaises(AccessError):
            other_bank.with_user(user).action_nc_ops_payment()
        self.env.flush_all()
        rows = self.env['ncollection.journal.transaction'].with_user(user).search([])
        self.assertTrue(rows)
        self.assertEqual(rows.company_id, self.company)
        figures = self.bank.with_user(user)._nc_ops_figures()
        self.assertAlmostEqual(figures[self.bank.id]['balance'], 5.0)
