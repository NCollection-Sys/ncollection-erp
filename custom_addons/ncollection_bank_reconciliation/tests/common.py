from odoo import Command, fields
from odoo.tests import Form

from odoo.addons.account.tests.common import AccountTestInvoicingCommon

VIEW = 'account_reconcile_oca.bank_statement_line_form_reconcile_view'


class BankCommon(AccountTestInvoicingCommon):
    """Deterministic fixture: one company, a "Bank Misr" bank journal, a second bank journal, a cash journal,
    a customer, a vendor, the OCA auto-matching models switched off so every match in a test is explicit."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.company_data['company']
        cls.env['account.reconcile.model'].search(
            [('trigger', '=', 'auto_reconcile'), ('company_id', '=', cls.company.id)]).active = False
        cls.misr = cls.env['account.journal'].create({'name': 'Bank Misr', 'type': 'bank', 'code': 'BMSR'})
        cls.ahly = cls.env['account.journal'].create({'name': 'Ahly Bank', 'type': 'bank', 'code': 'BAHL'})
        cls.cash = cls.env['account.journal'].create({'name': 'Main Cash', 'type': 'cash', 'code': 'CSHM'})
        for journal in (cls.misr, cls.ahly, cls.cash):
            journal.suspense_account_id = cls.company.account_journal_suspense_account_id
        cls.customer = cls.env['res.partner'].create({'name': 'Nile Customer'})
        cls.vendor = cls.env['res.partner'].create({'name': 'Delta Vendor'})
        cls.receivable = cls.company_data['default_account_receivable']
        cls.payable = cls.company_data['default_account_payable']
        cls.expense = cls.company_data['default_account_expense']
        cls.revenue = cls.company_data['default_account_revenue']
        cls.fees = cls.env['account.account'].create(
            {'name': 'Bank charges', 'code': '626100', 'account_type': 'expense'})
        cls.transfer = cls.company.transfer_account_id
        cls.day = fields.Date.from_string('2026-03-10')
        # The workspace's manual fields are driven by its JS widget; Form needs them visible (as OCA's own tests do).
        cls.env['ir.ui.view'].create({
            'name': 'NC test: workspace manual fields visible', 'model': 'account.bank.statement.line',
            'inherit_id': cls.env.ref(VIEW).id,
            'arch': '<data><field name="manual_reference" position="attributes"><attribute name="invisible">0'
                    '</attribute></field><field name="manual_delete" position="attributes"><attribute '
                    'name="invisible">0</attribute></field><field name="partner_id" position="attributes"><attribute'
                    ' name="invisible">0</attribute></field></data>',
        })

    # -------------------------------------------------------------- helpers
    def line(self, amount, journal=None, label='Transfer', partner=None, **extra):
        vals = {'journal_id': (journal or self.misr).id, 'amount': amount, 'payment_ref': label, 'date': self.day,
                'partner_id': partner.id if partner else False}
        vals.update(extra)
        return self.env['account.bank.statement.line'].create(vals)

    def invoice(self, amount, partner=None, move_type='out_invoice', currency=None, date=None):
        move = self.env['account.move'].create({
            'move_type': move_type, 'partner_id': (partner or self.customer).id,
            'invoice_date': date or self.day, 'date': date or self.day,
            'currency_id': (currency or self.company.currency_id).id,
            'invoice_line_ids': [Command.create({'name': 'Service', 'quantity': 1, 'price_unit': amount,
                                                 'tax_ids': [Command.clear()]})],
        })
        move.action_post()
        return move

    @staticmethod
    def open_item(move):
        return move.line_ids.filtered(
            lambda ml: ml.account_id.account_type in ('asset_receivable', 'liability_payable'))

    def match(self, st_line, *amls, amounts=None):
        """Add each journal item to the transaction in the OCA workspace; ``amounts`` caps them (partial)."""
        with Form(st_line, view=VIEW) as f:
            for index, aml in enumerate(amls):
                # the engine may already propose it (label = invoice number); adding it again would UNselect it
                data = st_line.reconcile_data_info.get('data', [])
                proposed = {d.get('id') for d in data if d.get('kind') == 'other'}
                if aml.id not in proposed:
                    f.add_account_move_line_id = aml
                if amounts and amounts[index] is not None:
                    f.manual_reference = f'account.move.line;{aml.id}'
                    f.manual_amount = amounts[index]
        return st_line

    def suspense_to(self, st_line, account, partner=None):
        """Send what is still unallocated (the suspense line) to ``account`` (Set Account / write-off)."""
        with Form(st_line, view=VIEW) as f:
            data = st_line.reconcile_data_info['data']
            suspense = [d for d in data if d.get('kind') == 'suspense']
            self.assertTrue(suspense, 'there is an unallocated amount to assign')
            f.manual_reference = suspense[0]['reference']
            f.manual_account_id = account
            if partner:
                f.manual_partner_id = partner
        return st_line

    def assert_balanced(self, move):
        self.assertTrue(move.currency_id.is_zero(sum(move.line_ids.mapped('balance'))),
                        f'{move.display_name} is balanced')

    def journal_balance(self, journal):
        return sum(self.env['account.move.line'].search(
            [('account_id', '=', journal.default_account_id.id), ('parent_state', '=', 'posted')]).mapped('balance'))
