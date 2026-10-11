# -*- coding: utf-8 -*-
"""The OCA report wizards must not be open to every internal user.

``account_financial_report`` grants its wizards to ``base.group_user``. A role audit
found a salesperson and a department manager could create the General Ledger and
Trial Balance wizards over RPC and read the company's whole ledger, although the
menus were hidden from them -- a menu is not a control. These tests pin the
server-side rule, proved by effect (a create is attempted) and not by reading the
configuration back.
"""
from datetime import date

from odoo.addons.account.tests.common import AccountTestInvoicingCommon
from odoo.addons.ncollection_account_reports.models.report_service import _OCA_REPORT_WIZARDS
from odoo.exceptions import AccessError
from odoo.tests import tagged


@tagged('post_install', '-at_install')
class TestOcaWizardAccess(AccountTestInvoicingCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Service = cls.env['ncollection.report.service']
        Service._nc_harden_oca_wizards()
        cls.present = [m for m in _OCA_REPORT_WIZARDS if m in cls.env]
        group_user = cls.env.ref('base.group_user')
        cls.internal = cls.env['res.users'].create({
            'name': 'Plain Internal', 'login': 'nc_oca_plain', 'group_ids': [(6, 0, group_user.ids)]})
        cls.accountant = cls.env['res.users'].create({
            'name': 'Read-only Accountant', 'login': 'nc_oca_acct',
            'group_ids': [(6, 0, [group_user.id, cls.env.ref('account.group_account_readonly').id])]})
        cls.dates = {'date_from': date(2026, 1, 1), 'date_to': date(2026, 12, 31), 'date_at': date(2026, 12, 31)}

    def _minimal(self, model):
        """The date fields this wizard declares, so a create is valid for who may create it."""
        return {k: v for k, v in self.dates.items() if k in self.env[model]._fields}

    def test_the_oca_module_is_installed_so_this_proves_something(self):
        self.assertTrue(self.present, "account_financial_report must be installed for these "
                                      "tests to mean anything (CI installs it)")

    def test_an_internal_user_cannot_create_any_oca_report_wizard(self):
        for model in self.present:
            with self.subTest(model=model), self.assertRaises(AccessError):
                self.env[model].with_user(self.internal).create(self._minimal(model))

    def test_an_accountant_still_can(self):
        for model in self.present:
            if model.startswith('account.age.report.configuration'):
                continue        # configuration rows need a parent/name; their denial is covered above
            with self.subTest(model=model):
                self.assertTrue(self.env[model].with_user(self.accountant).create(self._minimal(model)))

    def test_hardening_is_idempotent_and_survives_an_upgrade_reset(self):
        Service = self.env['ncollection.report.service']
        self.assertEqual(Service._nc_harden_oca_wizards(), 0, "second run must be a no-op")
        # an upgrade of the OCA module re-writes its rule to base.group_user
        rows = self.env['ir.model.access'].sudo().search(
            [('model_id.model', 'in', list(self.present))])
        rows.write({'group_id': self.env.ref('base.group_user').id})
        self.assertEqual(Service._nc_harden_oca_wizards(), len(rows))
        with self.assertRaises(AccessError):
            self.env[self.present[0]].with_user(self.internal).create(self._minimal(self.present[0]))
