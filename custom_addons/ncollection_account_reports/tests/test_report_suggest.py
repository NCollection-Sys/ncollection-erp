# -*- coding: utf-8 -*-
"""Filter suggestions of the report viewer (owner review 2026-11-18): Journals / Accounts / Partners list choices
with an EMPTY term, typing narrows them, the report's company bounds them, the user's own rights decide (no sudo),
and a forged id sent back as a filter is dropped, never echoed with its name."""
from odoo.exceptions import AccessError, UserError
from odoo.tests import TransactionCase, new_test_user, tagged

MODEL = 'ncollection.account.report.trial.balance'


@tagged('post_install', '-at_install')
class TestReportSuggest(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.other_company = cls.env['res.company'].create({'name': 'Suggest Other Co'})
        common = dict(company_id=cls.company.id, company_ids=[(6, 0, cls.company.ids)])
        cls.reader = new_test_user(cls.env, login='nc_sugg_reader',
                                   groups='base.group_user,account.group_account_readonly', **common)
        cls.plain = new_test_user(cls.env, login='nc_sugg_plain', groups='base.group_user', **common)
        cls.journal = cls.env['account.journal'].create({'name': 'Suggest Bank', 'code': 'SGB', 'type': 'bank'})
        cls.foreign_journal = cls.env['account.journal'].with_company(cls.other_company).create(
            {'name': 'Foreign Suggest Bank', 'code': 'FSG', 'type': 'bank', 'company_id': cls.other_company.id})
        cls.Service = cls.env['ncollection.report.service']

    def _suggest(self, user, field, term='', **kw):
        return self.Service.with_user(user).nc_report_suggest(MODEL, field, term, **kw)

    def test_empty_term_lists_choices(self):
        for field in ('journal_ids', 'account_ids', 'partner_ids'):
            with self.subTest(field=field):
                res = self._suggest(self.reader, field, '')
                self.assertTrue(res['records'], "suggestions before typing")
                self.assertLessEqual(len(res['records']), 20)

    def test_typing_narrows_and_journal_shows_its_code(self):
        res = self._suggest(self.reader, 'journal_ids', 'Suggest')
        names = [r['name'] for r in res['records']]
        self.assertIn('Suggest Bank (SGB)', names)
        self.assertNotIn('Foreign Suggest Bank (FSG)', names, "another company's journal is never offered")
        accounts = self._suggest(self.reader, 'account_ids', '', limit=5)
        code = self.env['account.account'].browse(accounts['records'][0]['id']).code
        found = self._suggest(self.reader, 'account_ids', code)
        self.assertIn(accounts['records'][0]['id'], [r['id'] for r in found['records']], "search by code")

    def test_paging_and_exclusion(self):
        first = self._suggest(self.reader, 'account_ids', '', limit=3)
        self.assertTrue(first['more'])
        second = self._suggest(self.reader, 'account_ids', '', limit=3, offset=3)
        self.assertFalse({r['id'] for r in first['records']} & {r['id'] for r in second['records']})
        chosen = first['records'][0]['id']
        again = self._suggest(self.reader, 'account_ids', '', limit=3, exclude_ids=[chosen])
        self.assertNotIn(chosen, [r['id'] for r in again['records']], "a chosen record is not offered twice")

    def test_rights_are_the_users(self):
        with self.assertRaises(AccessError):
            self._suggest(self.plain, 'account_ids', '')
        with self.assertRaises(UserError):
            self._suggest(self.reader, 'company_id', '')          # not a many2many filter
        with self.assertRaises(UserError):
            self._suggest(self.reader, 'group_ids', '')           # not a filter of the report

    def test_a_forged_filter_id_is_dropped(self):
        run = self.Service.with_user(self.reader).nc_report_run(
            MODEL, {'journal_ids': [self.journal.id, self.foreign_journal.id]})
        wizard = self.env[MODEL].browse(run['wizard']['id'])
        self.assertEqual(wizard.journal_ids, self.journal, "the other company's journal never reaches the report")
