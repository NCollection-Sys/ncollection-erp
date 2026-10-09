# -*- coding: utf-8 -*-
"""The report hub + viewer service: catalog, menu filing, run payload, drill ids.

What these pin, in order of how badly each would hurt if it regressed:

* the viewer shows the ENGINE rows: a payload is the same rows the wizard
  computes (and the PDF/XLSX read), never a second calculation;
* every link the viewer can draw has a real id behind it (and none is invented);
* a user never gets a card for a report they cannot open;
* arranging the menus is idempotent and files each report under its category.
"""
from datetime import date

from odoo.addons.account.tests.common import AccountTestInvoicingCommon
from odoo.exceptions import AccessError, UserError
from odoo.tests import tagged


@tagged('post_install', '-at_install')
class TestReportHub(AccountTestInvoicingCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Service = cls.env['ncollection.report.service']
        cls.receivable = cls.company_data['default_account_receivable']
        cls.revenue = cls.company_data['default_account_revenue']
        cls.journal = cls.company_data['default_journal_misc']
        cls.partner = cls.partner_a
        cls.move = cls.env['account.move'].create({
            'move_type': 'entry', 'journal_id': cls.journal.id, 'date': date(2026, 6, 15),
            'line_ids': [
                (0, 0, {'account_id': cls.receivable.id, 'debit': 1000.0,
                        'credit': 0.0, 'partner_id': cls.partner.id}),
                (0, 0, {'account_id': cls.revenue.id, 'debit': 0.0, 'credit': 1000.0}),
            ]})
        cls.move.action_post()
        cls.filters = {'date_from': '2026-01-01', 'date_to': '2026-12-31',
                       'target_move': 'posted'}

    # ------------------------------------------------------------ the run
    def test_trial_balance_payload_is_the_engines_rows(self):
        data = self.Service.nc_report_run(
            'ncollection.account.report.trial.balance', self.filters)
        wizard = self.env['ncollection.account.report.trial.balance'].browse(
            data['wizard']['id'])
        self.assertEqual(len(data['rows']), len(wizard._nc_compute_lines()))
        by_account = {r['account_id']: r for r in data['rows'] if r.get('account_id')}
        self.assertAlmostEqual(by_account[self.receivable.id]['debit'], 1000.0)
        self.assertAlmostEqual(by_account[self.revenue.id]['credit'], 1000.0)
        # an independent aggregate of the same posted lines agrees
        self.env.cr.execute("""
            SELECT COALESCE(SUM(debit), 0) FROM account_move_line
             WHERE parent_state = 'posted' AND company_id = %s AND date BETWEEN %s AND %s
               AND account_id = %s""", (self.env.company.id, date(2026, 1, 1),
                                        date(2026, 12, 31), self.receivable.id))
        self.assertAlmostEqual(self.env.cr.fetchone()[0],
                               by_account[self.receivable.id]['debit'])

    def test_general_ledger_rows_carry_exact_source_ids(self):
        data = self.Service.nc_report_run(
            'ncollection.account.report.general.ledger', self.filters)
        lines = [r for r in data['rows'] if not r.get('is_initial')]
        self.assertTrue(lines)
        aml = {line.id: line for line in self.move.line_ids}
        for row in lines:
            self.assertEqual(row['move_id'], self.move.id)
            self.assertIn(row['move_line_id'], aml)
            self.assertEqual(aml[row['move_line_id']].account_id.id, row['account_id'])
            # a plain entry has no payment / order behind it: no link is invented
            self.assertFalse(row['payment_id'])
            self.assertFalse(row['sale_order_id'])
            self.assertFalse(row['purchase_order_id'])

    def test_filters_narrow_the_run(self):
        data = self.Service.nc_report_run(
            'ncollection.account.report.general.ledger',
            dict(self.filters, date_from='2026-07-01'))
        self.assertFalse([r for r in data['rows'] if not r.get('is_initial')])

    def test_unknown_or_non_report_models_are_refused(self):
        for model in ('res.users', 'ncollection.account.report', 'does.not.exist'):
            with self.assertRaises(UserError):
                self.Service.nc_report_run(model, {})

    def test_report_specific_filters_are_exposed(self):
        data = self.Service.nc_report_run(
            'ncollection.account.report.aged.partner', self.filters)
        names = {f['name'] for f in data['filters']}
        self.assertIn('partner_scope', names)
        self.assertIn('period_length', names)
        payable = self.Service.nc_report_run(
            'ncollection.account.report.aged.partner',
            dict(self.filters, partner_scope='payable'))
        self.assertEqual(payable['title'], 'Aged Payable')

    def test_a_bad_filter_value_is_reported_not_raised(self):
        data = self.Service.nc_report_run(
            'ncollection.account.report.aged.partner',
            dict(self.filters, period_length=-5))
        self.assertIn('error', data)

    def test_aged_detail_adds_up_to_its_summary_row(self):
        invoice = self.env['account.move'].create({
            'move_type': 'out_invoice', 'partner_id': self.partner.id,
            'invoice_date': date(2026, 5, 1), 'invoice_date_due': date(2026, 5, 31),
            'invoice_line_ids': [(0, 0, {'name': 'x', 'price_unit': 250.0, 'quantity': 1})]})
        invoice.action_post()
        data = self.Service.nc_report_run(
            'ncollection.account.report.aged.partner', self.filters)
        self.assertTrue(data['detail'])
        row = next(r for r in data['rows'] if r['partner_id'] == self.partner.id)
        detail = self.Service.nc_report_detail(
            'ncollection.account.report.aged.partner', data['wizard']['id'],
            self.partner.id)
        self.assertAlmostEqual(sum(d['amount'] for d in detail), row['total'])
        self.assertIn(invoice.id, [d['move_id'] for d in detail])

    def test_export_targets_the_run_on_screen(self):
        data = self.Service.nc_report_run(
            'ncollection.account.report.trial.balance', self.filters)
        action = self.Service.nc_report_export(
            'ncollection.account.report.trial.balance', data['wizard']['id'], 'pdf')
        self.assertEqual(action['type'], 'ir.actions.report')
        with self.assertRaises(UserError):
            self.Service.nc_report_export(
                'ncollection.account.report.trial.balance', 0, 'pdf')

    # --------------------------------------------------------------- catalog
    def test_catalog_lists_the_financial_reports_for_an_accountant(self):
        catalog = self.Service.nc_report_catalog()
        by_key = {c['key']: c for c in catalog['categories']}
        names = [r['name'] for r in by_key['financial']['reports']]
        self.assertIn('Trial Balance', names)
        self.assertIn('General Ledger', names)
        self.assertIn('Partner Ledger', [r['name'] for r in by_key['receivables']['reports']])
        # no report appears in two categories
        seen = [r['xmlid'] for c in catalog['categories'] for r in c['reports']]
        self.assertEqual(len(seen), len(set(seen)))

    def test_a_user_without_accounting_rights_gets_no_financial_cards(self):
        outsider = self.env['res.users'].with_context(no_reset_password=True).create({
            'name': 'No Accounting', 'login': 'rep_outsider_x',
            'group_ids': [(6, 0, [self.env.ref('base.group_user').id])]})
        catalog = self.Service.with_user(outsider).nc_report_catalog()
        reports = {r['xmlid'] for c in catalog['categories'] for r in c['reports']}
        self.assertNotIn('ncollection_account_reports.menu_report_trial_balance', reports)
        with self.assertRaises(AccessError):
            self.Service.with_user(outsider).nc_report_run(
                'ncollection.account.report.trial.balance', self.filters)

    # ----------------------------------------------------------- menu filing
    def test_every_catalogued_report_is_filed_under_its_category(self):
        self.Service._nc_arrange_menus()
        for key, entries in self.Service._nc_catalog():
            category = self.env.ref('ncollection_account_reports.menu_nc_reports_%s' % key)
            sequences = []
            for xmlid, _name in entries:
                menu = self.env.ref(xmlid, raise_if_not_found=False)
                if not menu:
                    continue
                self.assertEqual(menu.parent_id, category, xmlid)
                sequences.append(menu.sequence)
            self.assertEqual(len(sequences), len(set(sequences)),
                             'duplicate sequence in %s' % key)

    def test_arranging_is_idempotent(self):
        self.Service._nc_arrange_menus()
        self.assertEqual(self.Service._nc_arrange_menus(), 0)

    def test_there_is_no_duplicate_report_name_in_the_tree(self):
        self.Service._nc_arrange_menus()
        names = []
        for key, _entries in self.Service._nc_catalog():
            category = self.env.ref('ncollection_account_reports.menu_nc_reports_%s' % key)
            names += category.child_id.filtered('active').mapped('name')
        self.assertEqual(len(names), len(set(names)), names)

    def test_filing_a_report_never_widens_who_can_see_it(self):
        """A menu is visible only if its parent is, so moving a report out of
        its app's 'Reporting' folder used to expose it to everyone allowed to
        see the new parent (a Sales user got Sales Analysis, which natively
        needs Sales / Administrator). The origin folder's groups travel with it."""
        from odoo.addons.ncollection_account_reports.models.report_service import (
            _ORIGIN_FOLDER_BY_MODULE,
        )
        self.Service._nc_arrange_menus()
        for _key, entries in self.Service._nc_catalog():
            for xmlid, _name in entries:
                menu = self.env.ref(xmlid, raise_if_not_found=False)
                folder_xmlid = _ORIGIN_FOLDER_BY_MODULE.get(xmlid.split('.')[0])
                folder = folder_xmlid and self.env.ref(folder_xmlid, raise_if_not_found=False)
                if not menu or not folder or not folder.group_ids:
                    continue
                self.assertTrue(
                    menu.group_ids,
                    '%s was filed out of a restricted folder and lost its restriction' % xmlid)

    def test_grouped_purchase_menus_carry_the_base_menus_groups(self):
        base = self.env.ref('purchase.purchase_report', raise_if_not_found=False)
        if not base or not base.group_ids:
            return
        self.Service._nc_ensure_grouped_menus()
        for name in ('menu_purchase_by_vendor', 'menu_purchase_by_product'):
            menu = self.env.ref('ncollection_account_reports.' + name)
            self.assertEqual(menu.group_ids, base.group_ids, name)

    # ------------------------------------------------ who is shown a native report
    def test_every_curated_menu_is_a_catalogued_report(self):
        """A curated entry for a menu the hub does not list would be dead config."""
        from odoo.addons.ncollection_account_reports.models.report_service import (
            _REPORT_MENU_GROUPS,
        )
        catalogued = {xmlid for _k, entries in self.Service._nc_catalog() for xmlid, _n in entries}
        self.assertFalse(set(_REPORT_MENU_GROUPS) - catalogued)

    def _fixture_native_menu(self, admin_group):
        """A native-style analysis menu gated on an 'Administrator' group, filed under
        a catalogued xml id so the arranger treats it like Sales Analysis."""
        menu = self.env['ir.ui.menu'].create({
            'name': 'Fixture analysis', 'group_ids': [(6, 0, admin_group.ids)],
            'action': 'ir.actions.act_window,%s' % self.env.ref('base.action_res_users').id})
        self.env['ir.model.data'].create({
            'module': 'ncollection_account_reports', 'name': 'fixture_native_menu',
            'model': 'ir.ui.menu', 'res_id': menu.id, 'noupdate': True})
        return menu

    def test_a_native_menu_is_aligned_to_the_least_group_that_can_read_it(self):
        """Sales / Purchase / Inventory Analysis were gated on an Administrator group no
        role carries, so those hub categories were empty for EVERYONE, Owner and CEO
        included. The curated map moves the gate to the app's ordinary user group."""
        from unittest.mock import patch
        from odoo.addons.ncollection_account_reports.models import report_service as svc
        admin_group = self.env['res.groups'].create({'name': 'Fixture / Administrator'})
        user_group = self.env['res.groups'].create({'name': 'Fixture / User'})
        self.env['ir.model.data'].create({
            'module': 'ncollection_account_reports', 'name': 'fixture_user_group',
            'model': 'res.groups', 'res_id': user_group.id, 'noupdate': True})
        menu = self._fixture_native_menu(admin_group)
        xmlid = 'ncollection_account_reports.fixture_native_menu'
        catalog = [('financial', [(xmlid, None)])]
        with patch.object(type(self.Service), '_nc_catalog', lambda _self: catalog), \
                patch.dict(svc._REPORT_MENU_GROUPS,
                           {xmlid: ('ncollection_account_reports.fixture_user_group',)}):
            self.Service._nc_arrange_menus()
        self.assertEqual(menu.group_ids, user_group,
                         "the Administrator gate must be replaced, not added to")

    def test_a_curated_group_from_a_missing_module_never_widens_the_menu(self):
        """If the app is not installed its group does not resolve; the menu keeps
        whatever its own module gave it rather than becoming visible to all."""
        from unittest.mock import patch
        from odoo.addons.ncollection_account_reports.models import report_service as svc
        admin_group = self.env['res.groups'].create({'name': 'Fixture2 / Administrator'})
        menu = self._fixture_native_menu(admin_group)
        xmlid = 'ncollection_account_reports.fixture_native_menu'
        catalog = [('financial', [(xmlid, None)])]
        with patch.object(type(self.Service), '_nc_catalog', lambda _self: catalog), \
                patch.dict(svc._REPORT_MENU_GROUPS, {xmlid: ('no_such_module.group_user',)}):
            self.assertFalse(self.Service._nc_curated_groups(xmlid))
            self.Service._nc_arrange_menus()
        self.assertEqual(menu.group_ids, admin_group)

    def test_curated_groups_do_not_touch_menus_that_are_not_curated(self):
        self.assertFalse(self.Service._nc_curated_groups('account.menu_action_account_invoice_report_all'))

    # ------------------------------------------- native screens exposed through the hub
    def _native_menu(self, name):
        return self.env.ref('ncollection_account_reports.' + name, raise_if_not_found=False)

    def test_native_screens_get_a_hub_menu_over_the_same_action(self):
        """Payments, vendor bills, vendor on-time delivery and scrap already exist as native
        screens but were not reachable from the hub. The hub gets its OWN menu pointing at the
        SAME action: nothing is moved out of its workflow folder and nothing is copied."""
        from odoo.addons.ncollection_account_reports.models.report_service import _NATIVE_REPORT_MENUS
        self.Service._nc_ensure_native_menus()
        for name, (action_xmlid, _title, category, group_xmlids) in _NATIVE_REPORT_MENUS.items():
            with self.subTest(menu=name):
                action = self.env.ref(action_xmlid, raise_if_not_found=False)
                groups_present = all(self.env.ref(g, raise_if_not_found=False) for g in group_xmlids)
                menu = self._native_menu(name)
                if not action or not groups_present:
                    self.assertFalse(menu, "a menu was created although its module is not installed")
                    continue
                self.assertTrue(menu, "%s should exist: its module is installed" % name)
                self.assertEqual(menu.action, action, "must reuse the native action, not a copy")
                self.assertEqual(menu.parent_id,
                                 self.env.ref('ncollection_account_reports.menu_nc_reports_%s' % category))
                self.assertEqual(menu.group_ids, self.env.ref(group_xmlids[0]),
                                 "only the group that already holds the ORM right may see it")

    def test_creating_the_native_menus_is_idempotent(self):
        Menu = self.env['ir.ui.menu'].with_context(active_test=False)
        self.Service._nc_ensure_native_menus()
        before = Menu.search_count([])
        self.Service._nc_ensure_native_menus()
        self.assertEqual(Menu.search_count([]), before)

    def test_the_native_payment_menu_does_not_leave_its_workflow_folder(self):
        native = self.env.ref('account.menu_action_account_payments_receivable', raise_if_not_found=False)
        parent_before = native.parent_id
        self.Service._nc_arrange_menus()
        self.assertEqual(native.parent_id, parent_before, "the Accounting > Customers > Payments menu must stay")

    def test_the_payment_cards_reach_an_accountant_and_nobody_else(self):
        self.Service._nc_arrange_menus()
        accountant = self.env['res.users'].create({
            'name': 'Hub Accountant', 'login': 'nc_hub_native_acct',
            'group_ids': [(6, 0, [self.env.ref('base.group_user').id,
                                  self.env.ref('account.group_account_readonly').id])]})
        plain = self.env['res.users'].create({
            'name': 'Hub Plain', 'login': 'nc_hub_native_plain',
            'group_ids': [(6, 0, [self.env.ref('base.group_user').id])]})

        def names(user):
            cat = self.Service.with_user(user).nc_report_catalog()
            return {r['name'] for c in cat['categories'] for r in c['reports']}
        self.assertLessEqual({'Customer Payments', 'Vendor Payments', 'Vendor Bill Analysis'}, names(accountant))
        self.assertFalse({'Customer Payments', 'Vendor Payments', 'Vendor Bill Analysis'} & names(plain))
