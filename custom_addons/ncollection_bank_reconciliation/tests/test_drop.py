# flake8: noqa: E501
"""Drag and drop hands a file to the SAME import wizard; it never imports by itself and never skips a check."""
import base64

from odoo import Command
from odoo.exceptions import AccessError, UserError
from odoo.tests import tagged

from odoo.addons.ncollection_bank_reconciliation.wizard.account_statement_import import NC_UPLOAD_MAX_BYTES

from .common import BankCommon
from .test_import import QIF


def b64(raw):
    return base64.b64encode(raw).decode()


@tagged('post_install', '-at_install', 'ncollection_bank_reconciliation')
class TestDrop(BankCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env.ref('base.USD').active = True
        cls.accountant = cls.env['res.users'].with_context(no_reset_password=True).create({
            'name': 'nc_drop_accountant', 'login': 'nc_drop_accountant', 'email': 'd@example.com',
            'company_id': cls.company.id, 'company_ids': [Command.set(cls.company.ids)],
            'group_ids': [Command.set([cls.env.ref('base.group_user').id, cls.env.ref('account.group_account_user').id])]})
        cls.reader = cls.env['res.users'].with_context(no_reset_password=True).create({
            'name': 'nc_drop_reader', 'login': 'nc_drop_reader', 'email': 'r@example.com',
            'company_id': cls.company.id, 'company_ids': [Command.set(cls.company.ids)],
            'group_ids': [Command.set([cls.env.ref('base.group_user').id, cls.env.ref('account.group_account_readonly').id])]})

    def drop(self, name='march.qif', raw=QIF, journal=None, user=None):
        journal = (journal or self.misr).with_user(user or self.env.user)
        return journal.action_nc_bank_drop(name, b64(raw))

    def lines(self):
        return self.env['account.bank.statement.line'].search_count([('journal_id', '=', self.misr.id)])

    def test_01_drop_opens_the_wizard_with_the_file_and_imports_nothing(self):
        before = self.lines()
        action = self.drop()
        self.assertEqual(action['res_model'], 'account.statement.import')
        wizard = self.env['account.statement.import'].browse(action['res_id'])
        self.assertEqual(wizard.statement_filename, 'march.qif')
        self.assertFalse(wizard.nc_preview_checksum, "a drop is not a preview")
        self.assertEqual(self.lines(), before, "a drop creates no transaction")
        self.assertTrue(action['context'].get('nc_require_preview'))
        self.assertEqual(action['context'].get('journal_id'), self.misr.id)

    def test_02_import_without_preview_is_refused(self):
        wizard = self.env['account.statement.import'].browse(self.drop()['res_id'])
        before = self.lines()
        with self.assertRaisesRegex(UserError, 'Preview this file'):
            wizard.with_context(**self.drop()['context']).import_file_button()
        self.assertEqual(self.lines(), before)

    def test_03_drop_preview_import_is_the_normal_pipeline(self):
        action = self.drop()
        wizard = self.env['account.statement.import'].with_context(**action['context']).browse(action['res_id'])
        wizard.action_nc_preview()
        self.assertTrue(wizard.nc_preview_checksum)
        self.assertEqual(wizard.nc_preview_count, 3, "the two identical QIF rows both stay, as in the Upload path")
        wizard.import_file_button()
        self.assertEqual(self.lines(), 3)
        # dropping the same file again: the duplicate detection of the pipeline applies
        again = self.env['account.statement.import'].with_context(**self.drop()['context']).browse(self.drop()['res_id'])
        again.action_nc_preview()
        self.assertEqual(again.nc_preview_count, 0)
        with self.assertRaisesRegex(UserError, 'already imported'):
            again.import_file_button()
        self.assertEqual(self.lines(), 3)

    def test_04_wrong_type_size_empty_and_garbage_are_refused_by_the_server(self):
        before = self.lines()
        with self.assertRaisesRegex(UserError, 'not a bank statement file'):
            self.drop('statement.exe')
        with self.assertRaisesRegex(UserError, 'not a bank statement file'):
            self.drop('notes.pdf')
        with self.assertRaisesRegex(UserError, 'larger than'):
            self.drop('big.csv', raw=b'x' * (NC_UPLOAD_MAX_BYTES + 1))
        with self.assertRaisesRegex(UserError, 'empty'):
            self.drop('empty.csv', raw=b'')
        with self.assertRaisesRegex(UserError, 'could not be read'):
            self.misr.action_nc_bank_drop('a.csv', '***not base64***')
        self.assertEqual(self.lines(), before)

    def test_05_a_cash_journal_and_a_reader_are_refused(self):
        with self.assertRaises(UserError):
            self.drop(journal=self.cash)
        with self.assertRaises(AccessError):
            self.drop(user=self.reader)
        self.assertTrue(self.drop(user=self.accountant)['res_id'])

    def test_06_the_preview_rechecks_a_file_that_was_swapped_in_the_wizard(self):
        wizard = self.env['account.statement.import'].with_context(**self.drop()['context']).browse(self.drop()['res_id'])
        wizard.statement_filename = 'payload.exe'
        with self.assertRaisesRegex(UserError, 'not a bank statement file'):
            wizard.action_nc_preview()
