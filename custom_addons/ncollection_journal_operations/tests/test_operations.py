from odoo import fields
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import Form, new_test_user, tagged
from odoo.tools.safe_eval import safe_eval

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
            [('journal_id', '=', journal.id)], order='value_date, id')

    def _ledger(self, journal, cutoff=None):
        domain = [('account_id', '=', journal.default_account_id.id), ('parent_state', '=', 'posted')]
        if cutoff:
            domain.append(('date', '<=', cutoff))
        return sum(self.env['account.move.line'].search(domain).mapped('balance'))

    def _assert_running_equals_ledger(self, journal, skip=()):
        rows = self._rows(journal)
        for cutoff in sorted(set(rows.mapped('value_date')) - set(skip)):
            upto = rows.filtered(lambda r: r.value_date <= cutoff).sorted(lambda r: (r.value_date, r.id))
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
        self.assertFalse(rows.filtered(lambda r: r.operation == 'settlement'), "a Bank Confirmation is never a row")
        row = rows.filtered(lambda r: r.payment_id == receipt)
        self.assertEqual(len(row), 1)
        self.assertEqual(row.statement_line_id.nc_settlement_payment_id, receipt)
        self.assertAlmostEqual(row.liquidity_balance, 100.0, msg="the Paid receipt moves the balance on its own row")
        self.assertAlmostEqual(sum(rows.mapped('amount_in')), 400.0)
        self.assertEqual(len(rows.filtered(lambda r: r.operation == 'bank')), 1)
        row = rows.filtered(lambda r: r.payment_id == pending and r.operation == 'payment')
        self.assertEqual(row.status, 'in_process')
        self.assertAlmostEqual(row.pending_balance, -30.0)
        self.assertAlmostEqual(row.liquidity_balance, 0.0)
        self.assertEqual(rows.filtered(lambda r: r.payment_id == fee).operation, 'account')
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
        # EVERY cut-off equals the ledger, also between the receipt's two confirmations (day -10 and day -1)
        rows = self._assert_running_equals_ledger(self.bank)
        self.assertAlmostEqual(self._ledger(self.bank), 1000 + 95 - 40 + 7 + 5)
        receipt_row = rows.filtered(lambda r: r.operation == 'receipt')
        self.assertAlmostEqual(receipt_row.liquidity_balance, 95.0, msg="what the bank booked on its first day")
        self.assertEqual(receipt_row.value_date, day(-10), "the first confirmation counts on its own day")
        later = rows.filtered(lambda r: r.operation == 'settlement')
        self.assertEqual(len(later), 1, "the second confirmation day is a row of its own")
        self.assertEqual((later.payment_id, later.value_date), (receipt, day(-1)))
        self.assertAlmostEqual(later.liquidity_balance, 5.0)
        self.assertAlmostEqual(sum(rows.filtered(lambda r: r.payment_id == receipt).mapped('liquidity_balance')),
                               100.0, msg="the receipt counts once in total: no duplicated movement")
        payment_row = rows.filtered(lambda r: r.operation == 'payment')
        self.assertEqual((payment_row.date, payment_row.bank_date), (day(-9), day(-3)))
        self.assertAlmostEqual(payment_row.liquidity_balance, -40.0)
        self.assertAlmostEqual(self._figures(self.bank)['balance'], self._ledger(self.bank))
        before = rows.filtered(lambda r: r.value_date <= day(-11)).sorted(lambda r: (r.value_date, r.id))[-1]
        self.assertAlmostEqual(before.running_balance, 1000.0, msg="the receipt was not yet in the bank")

    def test_confirmations_across_a_month_end_count_in_their_own_months(self):
        """A receipt confirmed partly on the last day of a month and partly in the next: daily and monthly movements,
        the running balance at every cut-off, the newest-first display and the card all agree with the ledger."""
        first_of_month = fields.Date.today().replace(day=1)
        month_end = fields.Date.subtract(first_of_month, days=1)            # last day of the previous month
        before = fields.Date.subtract(first_of_month, days=10)
        self._bank_line(self.bank, 500.0, 'opening', date=fields.Date.subtract(first_of_month, days=20))
        receipt = self._op(self.bank, 'receipt', 'customer', 300.0, self.customer, date=before)
        self._match(receipt, 120.0, month_end, 'first part, month end')
        self._match(receipt, 80.0, fields.Date.add(first_of_month, days=1), 'second part, next month')
        receipt._nc_mark_as_paid(fields.Date.add(first_of_month, days=2))  # the remaining 100, a third day
        rows = self._assert_running_equals_ledger(self.bank)
        own = rows.filtered(lambda r: r.payment_id == receipt)
        self.assertEqual(sorted(own.mapped('value_date')),
                         [month_end, fields.Date.add(first_of_month, days=1), fields.Date.add(first_of_month, days=2)])
        self.assertAlmostEqual(sum(own.mapped('liquidity_balance')), 300.0, msg="counted once in total")
        # monthly movement = ledger movement of that month
        for start, stop in ((fields.Date.subtract(first_of_month, months=1), month_end),
                            (first_of_month, fields.Date.add(first_of_month, days=27))):
            month_rows = rows.filtered(lambda r: start <= r.value_date <= stop)
            ledger = self._ledger(self.bank, stop) - self._ledger(self.bank, fields.Date.subtract(start, days=1))
            self.assertAlmostEqual(sum(month_rows.mapped('liquidity_balance')), ledger, msg=f"month from {start}")
        # the list shows newest first; its running balances are still the chronological ones
        shown = self.env['ncollection.journal.transaction'].search([('journal_id', '=', self.bank.id)])
        self.assertEqual(shown.ids, rows.sorted(lambda r: (r.value_date, r.id), reverse=True).ids)
        self.assertAlmostEqual(shown[0].running_balance, self._ledger(self.bank))
        self.assertAlmostEqual(self._figures(self.bank)['balance'], self._ledger(self.bank))
        # drill-down: a later confirmation opens its bank line; nothing posted was rewritten
        later = own.filtered(lambda r: r.operation == 'settlement')
        self.assertTrue(later and all(r.statement_line_id for r in later))
        self.assertTrue(all(m.state == 'posted' for m in own.move_id))

    def test_several_confirmations_on_one_day_are_one_movement_of_that_day(self):
        """Two bank lines on the SAME day for one payment, and a third later: the same day is summed once (no
        duplicate), the later day is its own row in the bank line's currency, every cut-off equals the ledger."""
        today = fields.Date.today()
        day = lambda n: fields.Date.add(today, days=n)  # noqa: E731
        receipt = self._op(self.bank, 'receipt', 'customer', 90.0, self.customer, date=day(-12))
        self._match(receipt, 30.0, day(-8), 'part one')
        self._match(receipt, 20.0, day(-8), 'part two, same day')
        self._match(receipt, 40.0, day(-2), 'rest')
        rows = self._assert_running_equals_ledger(self.bank)
        own = rows.filtered(lambda r: r.payment_id == receipt)
        self.assertEqual(len(own), 2, "one row per confirmation DAY, not per bank line")
        first = own.filtered(lambda r: r.operation == 'receipt')
        later = own.filtered(lambda r: r.operation == 'settlement')
        self.assertEqual((first.value_date, later.value_date), (day(-8), day(-2)))
        self.assertAlmostEqual(first.liquidity_balance, 50.0)
        self.assertAlmostEqual(later.liquidity_balance, 40.0)
        self.assertEqual(later.currency_id, later.statement_line_id.move_id.currency_id,
                         "a confirmation row is labelled in its bank line's currency")
        self.assertEqual(len(set(rows.ids)), len(rows), "row ids are unique")

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


@tagged('post_install', '-at_install')
class TestPayeeAndRows(JournalOpsCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.both = cls.env['res.partner'].create({'name': 'Ops Both Roles', 'customer_rank': 1, 'supplier_rank': 1})
        cls.contact = cls.env['res.partner'].create({'name': 'Ops Plain Contact'})
        cls.supplier_person = cls.env['res.partner'].create({'name': 'Ops Supplier Clerk',
                                                             'parent_id': cls.supplier.id})

    def test_eligibility_follows_odoo_ranks_and_commercial_partner(self):
        Pay = self.env['account.payment']
        self.assertTrue(Pay._nc_payee_eligible(self.both, 'customer'))
        self.assertTrue(Pay._nc_payee_eligible(self.both, 'supplier'))
        self.assertTrue(Pay._nc_payee_eligible(self.supplier_person, 'supplier'), "contact of a supplier company")
        self.assertFalse(Pay._nc_payee_eligible(self.supplier, 'customer'))
        self.assertFalse(Pay._nc_payee_eligible(self.contact, 'customer'))
        found = self.env['res.partner'].search(Pay._nc_payee_domain('supplier'))
        self.assertIn(self.both, found)
        self.assertNotIn(self.customer, found)

    def test_server_refuses_ineligible_payee(self):
        with self.assertRaises(ValidationError):
            self._op(self.bank, 'payment', 'supplier', 10.0, self.customer, post=False)
        pay = self._op(self.bank, 'payment', 'supplier', 10.0, self.supplier, post=False)
        with self.assertRaises(ValidationError):
            pay.partner_id = self.contact

    def test_type_switch_keeps_a_dual_role_partner_and_clears_others(self):
        form = Form(self.env['account.payment'].with_context(
            default_nc_operation='payment', default_nc_source_journal_id=self.bank.id,
            default_journal_id=self.bank.id, default_payment_type='outbound'),
            view='ncollection_journal_operations.view_payment_form_operation')
        form.nc_counterpart_type = 'supplier'
        form.partner_id = self.both
        form.nc_counterpart_type = 'customer'
        self.assertEqual(form.partner_id, self.both, "a partner of both roles stays selected")
        form.partner_id = self.customer
        form.nc_counterpart_type = 'supplier'
        self.assertFalse(form.partner_id, "a customer-only partner is cleared")
        form.partner_id = self.both
        form.amount = 7.0
        pay = form.save()
        self.assertEqual((pay.partner_id, pay.partner_type), (self.both, 'supplier'))

    def test_row_opens_its_authoritative_record(self):
        receipt = self._op(self.bank, 'receipt', 'customer', 40.0, self.customer)
        receipt._nc_mark_as_paid()
        self._bank_line(self.bank, 9.0, 'deposit')
        transfer = self.env['ncollection.internal.transfer'].create({
            'source_journal_id': self.bank.id, 'destination_journal_id': self.cash.id, 'amount': 5.0})
        transfer.action_post()
        self.env.flush_all()
        rows = self.env['ncollection.journal.transaction'].search([('journal_id', '=', self.bank.id)])
        opened = {r.operation: r.action_open_source() for r in rows}
        self.assertEqual((opened['receipt']['res_model'], opened['receipt']['res_id']),
                         ('account.payment', receipt.id))
        self.assertNotIn('settlement', opened, 'a Bank Confirmation is folded into its receipt')
        self.assertEqual(opened['bank']['res_model'], 'account.bank.statement.line')
        self.assertEqual((opened['transfer']['res_model'], opened['transfer']['res_id']),
                         ('ncollection.internal.transfer', transfer.id))

    def test_list_row_is_the_action_and_icons_are_gone(self):
        arch = self.env.ref('ncollection_journal_operations.view_journal_transaction_list').arch
        self.assertIn('action="action_open_source"', arch)
        self.assertNotIn('<button', arch)


@tagged('post_install', '-at_install')
class TestTransferPaymentMethod(JournalOpsCommon):

    def _transfer(self, src, dst, amount=10.0, **extra):
        return self.env['ncollection.internal.transfer'].create({
            'source_journal_id': src.id, 'destination_journal_id': dst.id, 'amount': amount, **extra})

    def test_defaults_to_the_first_outgoing_method_of_the_source(self):
        first = self.bank.outbound_payment_method_line_ids.sorted(lambda line: (line.sequence, line.id))[:1]
        transfer = self._transfer(self.bank, self.cash)
        self.assertEqual(transfer.payment_method_line_id, first)
        transfer.action_post()
        self.assertEqual(transfer.source_payment_id.payment_method_line_id, first)

    def test_another_journals_method_is_refused(self):
        foreign = self.bank2.outbound_payment_method_line_ids[:1]
        with self.assertRaises(ValidationError):
            self._transfer(self.bank, self.cash, payment_method_line_id=foreign.id)
        inbound = self.bank.inbound_payment_method_line_ids[:1]
        with self.assertRaises(ValidationError):
            self._transfer(self.bank, self.cash, payment_method_line_id=inbound.id)

    def test_outstanding_method_is_in_progress_until_both_banks_confirm(self):
        transfer = self._transfer(self.bank, self.cash)
        transfer.action_post()
        self.assertEqual(transfer.nc_status, 'in_progress')
        transfer.source_payment_id._nc_mark_as_paid()
        transfer.invalidate_recordset(['nc_status', 'progress'])
        self.assertEqual(transfer.nc_status, 'in_progress', "the receiving side has not arrived yet")
        transfer.destination_payment_id._nc_mark_as_paid()
        transfer.invalidate_recordset(['nc_status', 'progress'])
        self.assertEqual(transfer.nc_status, 'paid')

    def test_direct_methods_on_both_sides_are_paid_at_once_and_balanced(self):
        for journal, lines in ((self.bank, 'outbound_payment_method_line_ids'),
                               (self.cash, 'inbound_payment_method_line_ids')):
            journal[lines][:1].payment_account_id = journal.default_account_id
        before = {j: self._figures(j)['balance'] for j in (self.bank, self.cash)}
        transfer = self._transfer(self.bank, self.cash, amount=25.0)
        transfer.action_post()
        self.assertEqual(transfer.nc_status, 'paid')
        moves = transfer.source_payment_id.move_id | transfer.destination_payment_id.move_id
        for move in moves:
            self.assertAlmostEqual(sum(move.line_ids.mapped('balance')), 0.0)
        tl = moves.line_ids.filtered(lambda line: line.account_id == self.company.transfer_account_id)
        self.assertTrue(all(tl.mapped('reconciled')), "the transfer account is cleared, never bypassed")
        self.assertAlmostEqual(self._figures(self.bank)['balance'], before[self.bank] - 25.0)
        self.assertAlmostEqual(self._figures(self.cash)['balance'], before[self.cash] + 25.0)

    def test_all_four_routes_keep_the_chosen_method(self):
        current = {'account_type': 'asset_current', 'reconcile': True}
        cash2 = self._nc_journal('Ops Cash Two', 'cash', 'OPC2', self.fees.copy({'code': '101937', **current}),
                                 self.fees.copy({'code': '101938', **current}))
        for src, dst in ((self.bank, self.bank2), (self.bank, self.cash), (self.cash, self.bank), (self.cash, cash2)):
            transfer = self._transfer(src, dst)
            transfer.action_post()
            self.assertEqual(transfer.source_payment_id.payment_method_line_id, transfer.payment_method_line_id)
            self.assertEqual(transfer.nc_status, 'in_progress')


@tagged('post_install', '-at_install')
class TestTransferIncomingMethod(JournalOpsCommon):
    """The receiving leg's own Incoming Payment Method (owner review 2026-11): real, validated, used by the posted
    payment; and transfer legs never listed as customer receipts / supplier payments."""

    def _transfer(self, src, dst, amount=10.0, **extra):
        return self.env['ncollection.internal.transfer'].create({
            'source_journal_id': src.id, 'destination_journal_id': dst.id, 'amount': amount, **extra})

    def _second_inbound(self, journal, name, account):
        method = journal.inbound_payment_method_line_ids[:1].payment_method_id
        return self.env['account.payment.method.line'].create({
            'name': name, 'journal_id': journal.id, 'payment_method_id': method.id, 'payment_account_id': account.id,
            'sequence': 99})

    def test_defaults_to_the_first_incoming_method_and_follows_the_to_journal(self):
        first_cash = self.cash.inbound_payment_method_line_ids.sorted(lambda line: (line.sequence, line.id))[:1]
        transfer = self._transfer(self.bank, self.cash)
        self.assertEqual(transfer.destination_payment_method_line_id, first_cash)
        transfer.destination_journal_id = self.bank2
        first_bank2 = self.bank2.inbound_payment_method_line_ids.sorted(lambda line: (line.sequence, line.id))[:1]
        self.assertEqual(transfer.destination_payment_method_line_id, first_bank2, "replaced when To Journal changes")

    def test_the_chosen_incoming_method_is_the_one_the_receiving_payment_uses(self):
        direct = self._second_inbound(self.cash, 'Counter Deposit', self.cash.default_account_id)
        transfer = self._transfer(self.bank, self.cash, amount=40.0, destination_payment_method_line_id=direct.id)
        transfer.action_post()
        self.assertEqual(transfer.destination_payment_id.payment_method_line_id, direct)
        self.assertEqual(transfer.source_payment_id.payment_method_line_id, transfer.payment_method_line_id)
        self.assertEqual(transfer.destination_payment_id.state, 'paid', "direct method: booked on the cash account")
        self.assertNotEqual(transfer.source_payment_id.state, 'paid', "the source still waits for its bank")
        self.assertEqual(transfer.nc_status, 'in_progress', "both legs count: one is not settled")
        dest_line = transfer.destination_payment_id.move_id.line_ids.filtered(lambda line: line.debit)
        self.assertEqual(dest_line.account_id, self.cash.default_account_id)

    def test_wrong_incoming_methods_are_refused(self):
        with self.assertRaises(ValidationError):           # another journal's method
            self._transfer(self.bank, self.cash,
                           destination_payment_method_line_id=self.bank2.inbound_payment_method_line_ids[:1].id)
        with self.assertRaises(ValidationError):           # an outgoing method of the right journal
            self._transfer(self.bank, self.cash,
                           destination_payment_method_line_id=self.cash.outbound_payment_method_line_ids[:1].id)

    def test_journal_without_incoming_method_cannot_be_posted(self):
        transfer = self._transfer(self.bank, self.cash)
        self.cash.inbound_payment_method_line_ids.unlink()
        transfer.invalidate_recordset()
        transfer.destination_payment_method_line_id = False
        with self.assertRaises(UserError):
            transfer.action_post()

    def test_transfer_legs_are_not_customer_receipts_or_supplier_payments(self):
        transfer = self._transfer(self.cash, self.bank, amount=15.0)
        transfer.action_post()
        legs = transfer.source_payment_id | transfer.destination_payment_id
        no_partner_receipt = self._op(self.bank, 'receipt', 'account', 9.0, account=self.other_income)
        receipt = self._op(self.bank, 'receipt', 'customer', 11.0, self.customer)
        payment = self._op(self.bank, 'payment', 'supplier', 12.0, self.supplier)
        for xmlid, own in (('account.action_account_payments', receipt | no_partner_receipt),
                           ('account.action_account_payments_payable', payment)):
            action = self.env.ref(xmlid)
            listed = self.env['account.payment'].search(safe_eval(action.domain or '[]'))
            self.assertFalse(listed & legs, xmlid)
            self.assertEqual(listed & own, own, "real business payments (with or without partner) stay listed")
        rows = self.env['ncollection.journal.transaction'].search([('payment_id', 'in', legs.ids)])
        self.assertEqual(set(rows.mapped('journal_id').ids), {self.cash.id, self.bank.id},
                         "the transfer stays on both journals' Transactions")
        for row in rows:
            self.assertEqual(row._nc_source_record(), transfer)


@tagged('post_install', '-at_install')
class TestCardGraph(JournalOpsCommon):

    def test_graph_is_the_posted_ledger_and_ends_on_the_current_balance(self):
        receipt = self._op(self.bank, 'receipt', 'customer', 70.0, self.customer)
        receipt._nc_mark_as_paid()
        self._op(self.bank, 'payment', 'account', 20.0, account=self.fees)._nc_mark_as_paid()
        graph = self.bank._get_bank_cash_graph_data()[self.bank.id][0]
        self.assertFalse(graph['is_sample_data'])
        self.assertAlmostEqual(graph['values'][-1]['y'], self._figures(self.bank)['balance'])
        self.assertAlmostEqual(graph['values'][-1]['y'], 50.0)

    def test_no_posted_line_is_an_honest_empty_state_not_sample_data(self):
        self._op(self.cash, 'receipt', 'customer', 15.0, self.customer)  # pending only: nothing on the cash account
        self.assertEqual(self.cash._nc_ledger_history()[self.cash.id], [])
        data = {self.cash.id: {}}
        self.cash._fill_bank_cash_dashboard_data(data)
        self.assertTrue(data[self.cash.id]['nc_graph_empty'])


@tagged('post_install', '-at_install')
class TestPostedBalanceAndCard(JournalOpsCommon):

    def test_posted_balance_rows_add_up_to_the_card_and_filters_never_change_a_balance(self):
        self._bank_line(self.bank, 250.0, 'deposit')
        receipt = self._op(self.bank, 'receipt', 'customer', 100.0, self.customer)
        receipt._nc_mark_as_paid()
        self._op(self.bank, 'payment', 'supplier', 30.0, self.supplier)          # in process: no balance effect
        self.env.flush_all()
        action = self.bank.action_nc_ops_posted_balance()
        Tx = self.env['ncollection.journal.transaction']
        rows = Tx.search(action['domain'], order='value_date, id')
        self.assertEqual(len(rows), 2, 'only rows that moved the account')
        self.assertAlmostEqual(sum(rows.mapped('balance_amount')), self._figures(self.bank)['balance'])
        self.assertAlmostEqual(rows[-1].running_balance, self._figures(self.bank)['balance'])
        everything = {r.id: r.running_balance for r in Tx.search([('journal_id', '=', self.bank.id)])}
        for row in Tx.search(action['domain'] + [('operation', '=', 'receipt')]):
            self.assertAlmostEqual(row.running_balance, everything[row.id], msg='a filter never re-bases a row')

    def test_the_five_card_shortcuts_are_gone_and_still_reachable_from_the_menu(self):
        arch = self.env['account.journal'].get_view(
            self.env.ref('account.account_journal_dashboard_kanban_view').id, 'kanban')['arch']
        self.assertNotIn('o_nc_bank_nav', arch)
        for method in ('action_nc_ops_transactions', 'action_nc_ops_reconcile', 'action_nc_bank_batches',
                       'action_nc_bank_duplicates', 'action_nc_bank_new', 'action_nc_ops_posted_balance'):
            self.assertIn(f'name="{method}"', arch, method)


@tagged('post_install', '-at_install')
class TestSuspenseFallback(JournalOpsCommon):
    """Request 11: no payee -> the company's Unidentified Payments (Suspense) account, never the bank Suspense,
    Outstanding, transfer, receivable or payable account; a chosen account or payee is never overridden."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.suspense = cls.env['account.account'].create({
            'code': '109901', 'name': 'Unidentified Payments (ops test)', 'account_type': 'asset_current',
            'reconcile': True})

    def _counterpart(self, pay):
        return pay.move_id.line_ids.filtered(lambda line: line.account_id == pay.destination_account_id)

    def test_unconfigured_is_a_clear_error_never_an_arbitrary_account(self):
        self.company.nc_payment_suspense_account_id = False
        receipt = self._op(self.bank, 'receipt', 'customer', 40.0, post=False)
        self.assertTrue(receipt.nc_suspense_fallback)
        with self.assertRaises(UserError):
            receipt.action_post()
        with self.assertRaises(ValidationError):
            self._op(self.bank, 'payment', 'account', 10.0, post=False)

    def test_every_case_without_a_payee_posts_balanced_to_suspense(self):
        self.company.nc_payment_suspense_account_id = self.suspense
        cases = [('receipt', 'customer', 100.0), ('payment', 'supplier', 60.0), ('payment', 'account', 25.0),
                 ('receipt', 'account', 15.0)]
        for operation, kind, amount in cases:
            with self.subTest(operation=operation, kind=kind):
                pay = self._op(self.bank, operation, kind, amount)
                self.assertTrue(pay.nc_suspense_fallback)
                self.assertEqual(pay.destination_account_id, self.suspense)
                self.assertEqual(pay.state, 'in_process')
                lines = pay.move_id.line_ids
                self.assertAlmostEqual(sum(lines.mapped('debit')), sum(lines.mapped('credit')))
                sign = -1 if operation == 'receipt' else 1
                self.assertAlmostEqual(self._counterpart(pay).balance, sign * amount)
                self.assertNotIn(self.suspense, lines.account_id.filtered(
                    lambda a: a == self.bank.suspense_account_id))

    def test_a_chosen_payee_or_account_is_never_overridden(self):
        self.company.nc_payment_suspense_account_id = self.suspense
        receipt = self._op(self.bank, 'receipt', 'customer', 10.0, self.customer)
        self.assertFalse(receipt.nc_suspense_fallback)
        self.assertEqual(receipt.destination_account_id, self.customer.property_account_receivable_id)
        fee = self._op(self.bank, 'payment', 'account', 5.0, account=self.fees)
        self.assertEqual(fee.destination_account_id, self.fees)

    def test_suspense_is_cleared_later_by_a_reclassification(self):
        self.company.nc_payment_suspense_account_id = self.suspense
        receipt = self._op(self.bank, 'receipt', 'customer', 70.0)
        invoice = self._invoice(self.customer, 70.0)
        reclass = self.env['account.move'].create({
            'move_type': 'entry', 'journal_id': self.company_data['default_journal_misc'].id,
            'line_ids': [(0, 0, {'account_id': self.suspense.id, 'debit': 70.0, 'name': 'payee identified'}),
                         (0, 0, {'account_id': self.customer.property_account_receivable_id.id, 'credit': 70.0,
                                 'partner_id': self.customer.id, 'name': 'payee identified'})]})
        reclass.action_post()
        (self._counterpart(receipt) | reclass.line_ids.filtered(lambda ln: ln.account_id == self.suspense)).reconcile()
        (invoice.line_ids | reclass.line_ids).filtered(
            lambda ln: ln.account_id == self.customer.property_account_receivable_id).reconcile()
        self.assertEqual(invoice.payment_state, 'paid')
        self.assertTrue(self._counterpart(receipt).reconciled, 'suspense cleared')

    def test_the_suspense_account_cannot_be_a_bank_or_clearing_account(self):
        for account in (self.bank.default_account_id, self.company.transfer_account_id,
                        self.company.account_journal_suspense_account_id,
                        self.customer.property_account_receivable_id):
            with self.subTest(account=account.display_name), self.assertRaises(ValidationError):
                with self.env.cr.savepoint():
                    self.company.nc_payment_suspense_account_id = account


@tagged('post_install', '-at_install')
class TestTransferFeesAndExchange(JournalOpsCommon):
    """Requests 13 / 14. Company currency is the fixture's (USD). Sent = Bank Fee + value received + exchange loss
    (or - gain), every entry balanced, the fee never doubles as an exchange difference and never posts twice."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company.nc_bank_fee_account_id = cls.fees
        cls.eur = cls.setup_other_currency('EUR', rates=[('2016-01-01', 2.0)])      # 1 USD = 2 EUR
        cls.gbp = cls.setup_other_currency('GBP', rates=[('2016-01-01', 0.5)])      # 1 USD = 0.5 GBP
        cls.eur_bank = cls._nc_foreign('EUR bank', 'OPEU', cls.eur)
        cls.gbp_bank = cls._nc_foreign('GBP bank', 'OPGB', cls.gbp)

    @classmethod
    def _nc_foreign(cls, name, code, currency):
        journal = cls.env['account.journal'].create({'name': name, 'type': 'bank', 'code': code,
                                                     'currency_id': currency.id})
        return journal

    def _transfer(self, source, destination, amount, **extra):
        return self.env['ncollection.internal.transfer'].create({
            'source_journal_id': source.id, 'destination_journal_id': destination.id, 'amount': amount, **extra})

    def _assert_booked(self, transfer, fee_company, exchange_company):
        moves = (transfer.source_payment_id.move_id | transfer.destination_payment_id.move_id
                 | transfer.fee_move_id | transfer.exchange_move_id)
        for move in moves:
            self.assertAlmostEqual(sum(move.line_ids.mapped('debit')), sum(move.line_ids.mapped('credit')), 2)
        transfer_lines = moves.line_ids.filtered(lambda ln: ln.account_id == self.company.transfer_account_id)
        self.assertAlmostEqual(sum(transfer_lines.mapped('balance')), 0.0, 2, 'clearing account nets to zero')
        self.assertTrue(all(transfer_lines.mapped('reconciled')))
        fee_lines = moves.line_ids.filtered(lambda ln: ln.account_id == self.fees)
        self.assertAlmostEqual(sum(fee_lines.mapped('balance')), fee_company, 2)
        self.assertAlmostEqual(transfer.exchange_amount, exchange_company, 2)
        sent = -transfer.source_payment_id.move_id.line_ids.filtered(
            lambda ln: ln.account_id != self.company.transfer_account_id).balance
        received = transfer.destination_payment_id.move_id.line_ids.filtered(
            lambda ln: ln.account_id != self.company.transfer_account_id).balance
        self.assertAlmostEqual(sent, fee_company + received - exchange_company, 2, 'sent = fee + received - gain')

    def test_same_currency_fee_goes_to_bank_fees_and_nothing_to_exchange(self):
        transfer = self._transfer(self.bank, self.cash, 100.0, fee_amount=5.0)
        self.assertAlmostEqual(transfer.destination_amount, 95.0)
        self.assertTrue(transfer.show_exchange)
        transfer.action_post()
        self.assertFalse(transfer.exchange_move_id)
        self._assert_booked(transfer, 5.0, 0.0)

    def test_zero_fee_same_currency_is_unchanged(self):
        transfer = self._transfer(self.bank, self.cash, 50.0)
        self.assertFalse(transfer.show_exchange)
        transfer.action_post()
        self.assertFalse(transfer.fee_move_id | transfer.exchange_move_id)
        self._assert_booked(transfer, 0.0, 0.0)

    def test_company_to_foreign_with_fee_and_a_loss(self):
        transfer = self._transfer(self.bank, self.eur_bank, 100.0, fee_amount=2.0)
        transfer.destination_amount = 190.0
        self.assertAlmostEqual(transfer.accounting_rate, 2.0)
        self.assertAlmostEqual(transfer.expected_amount, 196.0)
        self.assertAlmostEqual(transfer.difference_amount, -6.0)
        self.assertAlmostEqual(transfer.exchange_estimate, -3.0)
        self.assertAlmostEqual(transfer.effective_rate, 190.0 / 98.0)   # received / (sent - fee)
        transfer.action_post()
        self._assert_booked(transfer, 2.0, -3.0)

    def test_foreign_to_company_with_a_gain(self):
        transfer = self._transfer(self.eur_bank, self.bank, 200.0)
        transfer.destination_amount = 105.0
        self.assertAlmostEqual(transfer.expected_amount, 100.0)
        self.assertAlmostEqual(transfer.exchange_estimate, 5.0)
        transfer.action_post()
        self._assert_booked(transfer, 0.0, 5.0)

    def test_third_currency_both_sides_foreign(self):
        transfer = self._transfer(self.eur_bank, self.gbp_bank, 200.0, fee_amount=4.0)   # 4 EUR = 2 USD
        transfer.destination_amount = 52.0                                                 # = 104 USD
        self.assertAlmostEqual(transfer.accounting_rate, 0.25)
        self.assertAlmostEqual(transfer.expected_amount, 49.0)
        transfer.action_post()
        self._assert_booked(transfer, 2.0, 6.0)          # 104 received - (100 sent - 2 fee) = +6 gain

    def test_receipt_figures_match_the_booked_entries(self):
        """The printed receipt reads the POSTED entries and reconciles: sent = fee + received - gain."""
        cases = [(self.bank, self.cash, 100.0, 5.0, None, 5.0, 0.0),
                 (self.bank, self.eur_bank, 100.0, 2.0, 190.0, 2.0, -3.0),
                 (self.eur_bank, self.gbp_bank, 200.0, 4.0, 52.0, 2.0, 6.0)]
        for source, dest, amount, fee, received, fee_c, exchange in cases:
            with self.subTest(source=source.name, dest=dest.name):
                transfer = self._transfer(source, dest, amount, fee_amount=fee)
                if received is not None:
                    transfer.destination_amount = received
                with self.assertRaises(UserError):
                    transfer._nc_receipt_values()          # no receipt before posting
                transfer.action_post()
                values = transfer._nc_receipt_values()
                self.assertTrue(values['balanced'], values)
                self.assertAlmostEqual(values['fee_company'], fee_c, 2)
                self.assertAlmostEqual(values['exchange'], exchange, 2)
                self.assertAlmostEqual(values['sent_company'],
                                       values['fee_company'] + values['received_company'] - values['exchange'], 2)
                self.assertAlmostEqual(values['effective_rate'], transfer.destination_amount / (amount - fee))
                html, kind = self.env['ir.actions.report']._render_qweb_html(
                    'ncollection_journal_operations.report_internal_transfer_receipt', transfer.ids)
                self.assertEqual(kind, 'html')
                self.assertIn(transfer.name, html.decode())
                action = transfer.action_nc_print_receipt()
                # Odoo first opens Configure Document Layout when the company has none chosen yet
                receipt = 'ncollection_journal_operations.report_internal_transfer_receipt'
                self.assertTrue(action.get('report_name') == receipt
                                or action.get('res_model') == 'base.document.layout', action)
                self.assertTrue(values['reconciled'])

    def test_receipt_refused_when_an_entry_is_no_longer_posted_and_readonly_can_print(self):
        transfer = self._transfer(self.bank, self.eur_bank, 100.0, fee_amount=2.0)
        transfer.destination_amount = 190.0
        transfer.action_post()
        reader = new_test_user(self.env, login='nc_receipt_reader',
                               groups='base.group_user,account.group_account_readonly')
        values = transfer.with_user(reader)._nc_receipt_values()          # read-only accountant: same figures
        self.assertTrue(values['balanced'])
        html, _kind = self.env['ir.actions.report'].with_user(reader)._render_qweb_html(
            'ncollection_journal_operations.report_internal_transfer_receipt', transfer.ids)
        self.assertIn(transfer.name, html.decode())
        transfer.fee_move_id.button_draft()
        with self.assertRaises(UserError):
            transfer._nc_receipt_values()

    def test_missing_rate_is_refused(self):
        jpy = self.setup_other_currency('JPY', rates=[('2099-01-01', 150.0)])
        jpy_bank = self._nc_foreign('JPY bank', 'OPJP', jpy)
        transfer = self._transfer(self.bank, jpy_bank, 10.0)
        self.assertTrue(transfer.rate_missing)
        with self.assertRaises(UserError):
            transfer.action_post()
        self.assertEqual(transfer.state, 'draft')

    def test_fee_without_a_fee_account_is_refused_and_bad_fees_are_rejected(self):
        self.company.nc_bank_fee_account_id = False
        with self.assertRaises(UserError):
            self._transfer(self.bank, self.cash, 100.0, fee_amount=1.0).action_post()
        self.company.nc_bank_fee_account_id = self.fees
        for fee in (-1.0, 100.0):
            with self.subTest(fee=fee), self.assertRaises(ValidationError):
                self._transfer(self.bank, self.cash, 100.0, fee_amount=fee)

    def test_posted_transfer_is_never_rewritten_or_posted_twice(self):
        transfer = self._transfer(self.bank, self.eur_bank, 100.0, fee_amount=1.0)
        transfer.action_post()
        with self.assertRaises(UserError):
            transfer.fee_amount = 3.0
        with self.assertRaises(UserError):
            transfer.action_post()
        self.assertEqual(self.env['account.move'].search_count([('line_ids.account_id', '=', self.fees.id),
                                                                ('id', '=', transfer.fee_move_id.id)]), 1)


@tagged('post_install', '-at_install')
class TestTransactionPalette(JournalOpsCommon):

    def test_bank_transactions_use_the_semantic_money_classes(self):
        arch = self.env['ncollection.journal.transaction'].get_view(view_type='list')['arch']
        self.assertIn('o_nc_fin_in', arch)
        self.assertIn('o_nc_fin_out', arch)
        self.assertNotIn('decoration-success="direction', arch, 'direction is no longer a whole-row colour')
