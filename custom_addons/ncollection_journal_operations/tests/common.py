from odoo import Command, fields

from odoo.addons.account.tests.common import AccountTestInvoicingCommon


class JournalOpsCommon(AccountTestInvoicingCommon):
    """Journals created HERE (never the fixture's names): two banks and a cash box, each with its own outstanding
    accounts on its methods, so every figure is read from configuration, not from a known name or code."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.company_data['company']
        cls.company_data_2 = cls.setup_other_company()
        cls.env.user.company_id = cls.company
        cls.currency = cls.company.currency_id
        Account = cls.env['account.account']
        cls.env['account.reconcile.model'].search(
            [('trigger', '=', 'auto_reconcile'), ('company_id', '=', cls.company.id)]).active = False

        def outstanding(code, name):
            return Account.create({'code': code, 'name': name, 'account_type': 'asset_current', 'reconcile': True})

        cls.bank = cls._nc_journal('Ops Bank One', 'bank', 'OPB1',
                                   outstanding('101931', 'Ops Out Receipts 1'), outstanding('101932', 'Ops Out Pay 1'))
        cls.bank2 = cls._nc_journal('Ops Bank Two', 'bank', 'OPB2',
                                    outstanding('101933', 'Ops Out Receipts 2'), outstanding('101934', 'Ops Out Pay 2'))
        cls.cash = cls._nc_journal('Ops Cash', 'cash', 'OPC1',
                                   outstanding('101935', 'Ops Out Receipts C'), outstanding('101936', 'Ops Out Pay C'))
        cls.customer = cls.env['res.partner'].create({'name': 'Ops Customer', 'customer_rank': 1})
        cls.supplier = cls.env['res.partner'].create({'name': 'Ops Supplier', 'supplier_rank': 1})
        cls.fees = Account.create({'code': '620901', 'name': 'Bank Fees (ops test)', 'account_type': 'expense'})
        cls.other_income = Account.create({'code': '790901', 'name': 'Other Income (ops test)',
                                           'account_type': 'income_other'})
        if not cls.company.transfer_account_id:
            cls.company.transfer_account_id = Account.create({
                'code': '101999', 'name': 'Internal Transfer (ops test)', 'account_type': 'asset_current',
                'reconcile': True})

    @classmethod
    def _nc_journal(cls, name, kind, code, out_receipts, out_payments):
        journal = cls.env['account.journal'].create({'name': name, 'type': kind, 'code': code})
        journal.suspense_account_id = cls.company.account_journal_suspense_account_id
        journal.inbound_payment_method_line_ids[:1].payment_account_id = out_receipts
        journal.outbound_payment_method_line_ids[:1].payment_account_id = out_payments
        return journal

    def _op(self, journal, operation, kind, amount, partner=None, account=None, post=True, **extra):
        vals = {
            'nc_operation': operation, 'nc_source_journal_id': journal.id, 'nc_counterpart_type': kind,
            'amount': amount, 'date': fields.Date.today(),
            'partner_id': partner.id if partner else False,
            'nc_counterpart_account_id': account.id if account else False,
            **extra,
        }
        pay = self.env['account.payment'].create(vals)
        if post:
            pay.action_post()
        return pay

    def _figures(self, journal):
        return journal._nc_ops_figures()[journal.id]

    def _bank_line(self, journal, amount, ref='bank line', date=None, partner=None):
        return self.env['account.bank.statement.line'].create({
            'journal_id': journal.id, 'date': date or fields.Date.today(), 'payment_ref': ref, 'amount': amount,
            'partner_id': partner.id if partner else False})

    def _invoice(self, partner, amount, move_type='out_invoice'):
        move = self.env['account.move'].create({
            'move_type': move_type, 'partner_id': partner.id, 'invoice_date': fields.Date.today(),
            'invoice_line_ids': [Command.create({'name': 'x', 'quantity': 1, 'price_unit': amount, 'tax_ids': []})]})
        move.action_post()
        return move
