# -*- coding: utf-8 -*-
"""F1-T02: the fiscal-year baseline applied at provisioning (post_init_hook →
res.company._nc_apply_accounting_baseline)."""
from odoo.addons.ncollection_account_core.hooks import post_init_hook
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestAccountingBaseline(TransactionCase):

    def test_baseline_sets_the_calendar_fiscal_year(self):
        company = self.env['res.company'].create({'name': 'NC FY Co'})
        # simulate a company that diverged from the calendar year
        company.write({'fiscalyear_last_day': 30, 'fiscalyear_last_month': '6'})
        company._nc_apply_accounting_baseline()
        self.assertEqual(company.fiscalyear_last_day, 31)
        self.assertEqual(company.fiscalyear_last_month, '12')

    def test_baseline_is_idempotent(self):
        company = self.env['res.company'].create({'name': 'NC FY Co 2'})
        company._nc_apply_accounting_baseline()
        company._nc_apply_accounting_baseline()  # second run is a clean no-op
        self.assertEqual(company.fiscalyear_last_day, 31)
        self.assertEqual(company.fiscalyear_last_month, '12')

    def test_post_init_hook_normalizes_companies(self):
        # Prove the hook's OWN logic fires (search all companies → apply the
        # baseline). Divert off-baseline FIRST, else Odoo's native 31/12 default
        # would make this pass whether or not the hook ran (a vacuous check).
        company = self.env['res.company'].create({
            'name': 'NC Hook Co',
            'fiscalyear_last_day': 30, 'fiscalyear_last_month': '6'})
        post_init_hook(self.env)
        self.assertEqual(company.fiscalyear_last_day, 31)
        self.assertEqual(company.fiscalyear_last_month, '12')
