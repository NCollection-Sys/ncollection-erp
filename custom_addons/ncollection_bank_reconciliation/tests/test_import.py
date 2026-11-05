# flake8: noqa: E501  (fixture files and accounting assertions read best on one line)
"""Acceptance scenarios 2 (imported withdrawal) and 12 (duplicate import), plus the import gates."""
import base64

from odoo.exceptions import UserError
from odoo.tests import tagged

from .common import BankCommon

QIF = b"""!Type:Bank
D03/10/26
T-250.00
PBank Misr ATM withdrawal
^
D03/11/26
T1,000.00
PNile Customer transfer
^
D03/11/26
T1,000.00
PNile Customer transfer
^
"""

CSV = b'''Date,Label,Reference,Currency,Amount
03/12/2026,Supplier Delta,PO-77,USD,-120.50
03/12/2026,Deposit,DEP-1,USD,300.00
03/12/2026,Euro receipt,EU-1,EUR,99.00
'''


CAMT = b"""<?xml version="1.0" encoding="UTF-8"?>
<Document xmlns="urn:iso:std:iso:20022:tech:xsd:camt.053.001.02">
 <BkToCstmrStmt>
  <GrpHdr><MsgId>NC-1</MsgId><CreDtTm>2026-03-15T10:00:00</CreDtTm></GrpHdr>
  <Stmt>
   <Id>NC-ST-1</Id><CreDtTm>2026-03-15T10:00:00</CreDtTm>
   <Acct><Id><IBAN>%(iban)s</IBAN></Id><Ccy>%(ccy)s</Ccy></Acct>
   <Bal><Tp><CdOrPrtry><Cd>OPBD</Cd></CdOrPrtry></Tp><Amt Ccy="%(ccy)s">1000.00</Amt><CdtDbtInd>CRDT</CdtDbtInd><Dt><Dt>2026-03-13</Dt></Dt></Bal>
   <Bal><Tp><CdOrPrtry><Cd>CLBD</Cd></CdOrPrtry></Tp><Amt Ccy="%(ccy)s">1215.00</Amt><CdtDbtInd>CRDT</CdtDbtInd><Dt><Dt>2026-03-14</Dt></Dt></Bal>
   <Ntry><Amt Ccy="%(ccy)s">250.00</Amt><CdtDbtInd>CRDT</CdtDbtInd><Sts>BOOK</Sts><BookgDt><Dt>2026-03-14</Dt></BookgDt>
    <ValDt><Dt>2026-03-14</Dt></ValDt><AcctSvcrRef>NCREF-001</AcctSvcrRef>
    <NtryDtls><TxDtls><Refs><AcctSvcrRef>NCREF-001</AcctSvcrRef></Refs><RmtInf><Ustrd>Customer receipt</Ustrd></RmtInf></TxDtls></NtryDtls></Ntry>
   <Ntry><Amt Ccy="%(ccy)s">35.00</Amt><CdtDbtInd>DBIT</CdtDbtInd><Sts>BOOK</Sts><BookgDt><Dt>2026-03-14</Dt></BookgDt>
    <ValDt><Dt>2026-03-14</Dt></ValDt><AcctSvcrRef>NCREF-002</AcctSvcrRef>
    <NtryDtls><TxDtls><Refs><AcctSvcrRef>NCREF-002</AcctSvcrRef></Refs><RmtInf><Ustrd>Bank Fees</Ustrd></RmtInf></TxDtls></NtryDtls></Ntry>
  </Stmt>
 </BkToCstmrStmt>
</Document>
"""


@tagged('post_install', '-at_install', 'ncollection_bank_reconciliation')
class TestStatementImport(BankCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env.ref('base.USD').active = True
        cls.env.ref('base.EUR').active = True
        cls.misr.currency_id = cls.env.ref('base.USD') if cls.company.currency_id.name != 'USD' else False
        cls.mapping = cls.env['account.statement.import.sheet.mapping'].create({
            'name': 'NC test CSV', 'float_thousands_sep': 'none', 'float_decimal_sep': 'dot', 'delimiter': 'comma',
            'quotechar': '"', 'timestamp_format': '%m/%d/%Y', 'timestamp_column': 'Date', 'amount_type': 'simple_value',
            'amount_column': 'Amount', 'currency_column': 'Currency', 'description_column': 'Label',
            'reference_column': 'Reference', 'header_lines_skip_count': 0,
        })

    def wizard(self, content, name, journal=None, **vals):
        ctx = (journal or self.misr).action_nc_bank_upload()['context']
        return self.env['account.statement.import'].with_context(**ctx).create(
            dict({'statement_file': base64.b64encode(content), 'statement_filename': name}, **vals))

    def lines(self, journal=None):
        return self.env['account.bank.statement.line'].search([('journal_id', '=', (journal or self.misr).id)])

    # 2 ------------------------------------------------------------------
    def test_02_imported_withdrawal_after_preview(self):
        wiz = self.wizard(QIF, 'march.qif')
        with self.assertRaises(UserError):
            wiz.import_file_button()                     # preview is required from the journal card
        self.assertFalse(self.lines())
        wiz.action_nc_preview()
        self.assertIn('3 transactions', wiz.nc_preview_html)
        self.assertIn('Bank Misr ATM withdrawal', wiz.nc_preview_html)
        self.assertEqual(wiz.nc_preview_count, 3)
        wiz.import_file_button()
        lines = self.lines()
        self.assertEqual(len(lines), 3, 'two identical lines in one file are both kept')
        atm = lines.filtered(lambda ml: ml.payment_ref == 'Bank Misr ATM withdrawal')
        self.assertEqual(atm.amount, -250.0)
        self.assertFalse(atm.is_reconciled)
        self.assertTrue(all(lines.mapped('unique_import_id')))
        self.assertTrue(all(uid.startswith(f'nc-fp-{self.misr.id}-') for uid in lines.mapped('unique_import_id')))

    # 12 -----------------------------------------------------------------
    def test_12_duplicate_import_is_skipped_and_retry_is_safe(self):
        first = self.wizard(QIF, 'march.qif')
        first.action_nc_preview()
        first.import_file_button()
        self.assertEqual(len(self.lines()), 3)
        again = self.wizard(QIF, 'march-again.qif')
        again.action_nc_preview()
        self.assertEqual(again.nc_preview_count, 0)
        self.assertIn('Already imported', again.nc_preview_html)
        with self.assertRaises(UserError):
            again.import_file_button()
        self.assertEqual(len(self.lines()), 3, 'nothing imported twice')
        # an overlapping file: one known line plus one new one
        overlap = self.wizard(QIF.replace(b'^\nD03/11/26\nT1,000.00\nPNile Customer transfer\n^\n', b'^\n', 1)
                              + b'D03/13/26\nT-40.00\nPCard fee\n^\n', 'overlap.qif')
        overlap.action_nc_preview()
        overlap.import_file_button()
        self.assertEqual(len(self.lines()), 4)

    def test_changed_file_after_preview_is_refused(self):
        wiz = self.wizard(QIF, 'a.qif')
        wiz.action_nc_preview()
        wiz.statement_file = base64.b64encode(QIF + b'D03/14/26\nT-1.00\nPX\n^\n')
        with self.assertRaises(UserError):
            wiz.import_file_button()
        self.assertFalse(self.lines())

    def test_csv_preview_names_rows_in_another_currency(self):
        if (self.misr.currency_id or self.company.currency_id).name != 'USD':
            self.skipTest('fixture expects a USD journal')
        wiz = self.wizard(CSV, 'march.csv', sheet_mapping_id=self.mapping.id)
        wiz.action_nc_preview()
        self.assertEqual(wiz.nc_preview_count, 2)
        self.assertIn('1 rows are in another currency', wiz.nc_preview_html)
        self.assertIn('Euro receipt', wiz.nc_preview_html)
        wiz.import_file_button()
        lines = self.lines()
        self.assertEqual(sorted(lines.mapped('amount')), [-120.5, 300.0])
        self.assertEqual(set(lines.mapped('ref')), {'PO-77', 'DEP-1'})

    def test_file_for_another_account_or_journal_is_refused(self):
        self.misr.bank_account_id = self.env['res.partner.bank'].create(
            {'acc_number': '41234', 'partner_id': self.company.partner_id.id})
        currency = self.misr.currency_id or self.company.currency_id
        wiz = self.env['account.statement.import']
        self.assertEqual(wiz._match_journal('41234', currency), self.misr)
        # OCA matches with ilike, so a file for account "1234" would land in the "41234" journal. Exact here.
        with self.assertRaises(UserError):
            wiz._match_journal('1234', currency)
        # a file of Bank Misr uploaded from the Ahly card
        with self.assertRaises(UserError):
            wiz.with_context(journal_id=self.ahly.id, nc_strict_journal=True)._match_journal('41234', currency)

    def test_camt053_exact_journal_and_bank_reference_dedup(self):
        iban = 'EG380019000500000000263180002'
        currency = (self.misr.currency_id or self.company.currency_id).name
        self.misr.bank_account_id = self.env['res.partner.bank'].create(
            {'acc_number': iban, 'partner_id': self.company.partner_id.id})
        camt = CAMT % {b'iban': iban.encode(), b'ccy': currency.encode()}
        refused = self.wizard(camt, 'misr.xml', journal=self.ahly)
        with self.assertRaises(UserError):
            refused.action_nc_preview()                   # a Bank Misr file uploaded on the Ahly card
        wiz = self.wizard(camt, 'misr.xml')
        wiz.action_nc_preview()
        self.assertEqual(wiz.nc_preview_count, 2)
        wiz.import_file_button()
        lines = self.lines()
        self.assertEqual(sorted(lines.mapped('amount')), [-35.0, 250.0])
        # OCA's CAMT parser keeps the bank reference only as `ref` and sets NO unique_import_id, so upstream a
        # re-imported CAMT file is booked twice. The fingerprint (which includes that reference) is the key here.
        self.assertEqual(set(lines.mapped('ref')), {'NCREF-001', 'NCREF-002'})
        self.assertTrue(all(uid.startswith(f'nc-fp-{self.misr.id}-') for uid in lines.mapped('unique_import_id')))
        again = self.wizard(camt, 'misr-again.xml')
        again.action_nc_preview()
        self.assertEqual(again.nc_preview_count, 0)
        self.assertEqual(len(self.lines()), 2)

    def test_journal_of_another_company_is_refused(self):
        other = self.setup_other_company()['company']
        foreign = self.env['account.journal'].with_company(other).create(
            {'name': 'Other bank', 'type': 'bank', 'code': 'OTHB', 'company_id': other.id})
        self.env.user.company_ids |= other
        wiz = self.env['account.statement.import'].with_context(journal_id=foreign.id)
        with self.assertRaises(UserError):
            wiz._match_journal(None, foreign.currency_id or other.currency_id)
