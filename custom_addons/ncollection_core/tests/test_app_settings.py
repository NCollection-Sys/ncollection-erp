# -*- coding: utf-8 -*-
"""Settings of optional applications on the unified tenant Settings page (wizards/app_settings.py): offered only while
their module is installed, applied where Odoo applies them (its own set_values for settings with side effects), the
section level and the administrator rule enforced on save, and no privilege gained by the Owner."""
from odoo.exceptions import AccessError
from odoo.tests import TransactionCase, new_test_user, tagged

from ..wizards.app_settings import APP_SETTINGS, _TYPES


@tagged('post_install', '-at_install')
class TestAppSettings(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        company = cls.env.company
        common = {'company_id': company.id, 'company_ids': [(6, 0, company.ids)]}
        cls.owner = new_test_user(cls.env, login='as_owner',
                                  groups='base.group_user,ncollection_core.group_role_owner',
                                  **common)
        cls.delegate = new_test_user(cls.env, login='as_delegate',
                                     groups='base.group_user,ncollection_core.group_settings_business', **common)
        cls.employee = new_test_user(cls.env, login='as_employee', groups='base.group_user', **common)
        cls.native = cls.env['res.config.settings']._fields
        cls.Page = cls.env['ncollection.business.settings']

    def _settings(self, user, **vals):
        return self.Page.with_user(user).create(vals)

    def test_the_table_matches_the_native_settings(self):
        """A row whose native field changed type would be silently dropped: fail instead, so the table is fixed."""
        self.assertEqual(set(APP_SETTINGS), set(_TYPES))
        for name in APP_SETTINGS:
            if name in self.native:
                self.assertEqual(self.native[name].type, _TYPES[name], name)

    def test_only_installed_applications_are_offered(self):
        spec = self.Page._nc_settings_spec()
        for name, row in APP_SETTINGS.items():
            self.assertEqual(name in spec, name in self.native, name)
        apps = self.Page.with_user(self.owner).create({}).nc_apps
        for module in {row[0] for row in APP_SETTINGS.values()}:
            installed = any(row[0] == module and name in self.native for name, row in APP_SETTINGS.items())
            self.assertEqual(' %s ' % module in apps, installed, module)

    def test_a_setting_of_a_missing_application_is_never_written(self):
        absent = [name for name in APP_SETTINGS if name not in self.native and _TYPES[name] == 'boolean']
        for name in absent:
            # the wizard has the field, but nothing reads or writes it while the module is not installed
            self._settings(self.owner, **{name: True}).action_nc_save()
            self.assertNotIn(name, self.Page._nc_settings_spec())

    def test_the_owner_changes_a_setting_through_odoos_own_set_values(self):
        name = 'group_product_pricelist'
        if name not in self.native:
            return                          # product is not installed on this database: nothing to offer
        group = self.env.ref('product.group_product_pricelist')
        users = self.env.ref('base.group_user')
        start = group in users.implied_ids
        self._settings(self.owner, **{name: not start}).action_nc_save()
        self.assertEqual(group in users.implied_ids, not start)
        self.assertEqual(self._settings(self.owner)[name], not start)     # read back on a fresh page
        self._settings(self.owner, **{name: start}).action_nc_save()
        self.assertEqual(group in users.implied_ids, start)
        self.assertFalse(self.owner.has_group('base.group_system'))
        self.assertFalse(self.owner.has_group('base.group_erp_manager'))

    def test_a_native_company_setting_persists_on_the_current_company_only(self):
        name = next((n for n, row in APP_SETTINGS.items() if n in self.native and row[3] == 'native'
                     and not row[4] and _TYPES[n] == 'boolean' and self.native[n].related
                     and str(self.native[n].related).startswith('company_id.')), None)
        if not name:
            return
        other = self.env['res.company'].create({'name': 'App settings other'})
        field = str(self.native[name].related).split('.')[1]
        self.env.company[field] = False
        other[field] = False
        self._settings(self.owner, **{name: True}).action_nc_save()
        self.assertTrue(self.env.company[field])
        self.assertFalse(other[field])

    def test_accounting_controls_need_a_workspace_administrator(self):
        for name, row in APP_SETTINGS.items():
            if not row[4] or name not in self.native or _TYPES[name] != 'boolean':
                continue
            current = self._settings(self.owner)[name]
            with self.assertRaises(AccessError):
                self._settings(self.delegate, **{name: not current}).action_nc_save()

    def test_a_plain_user_cannot_open_or_save_the_page(self):
        with self.assertRaises(AccessError):
            self._settings(self.employee, group_mrp_byproducts=True).action_nc_save()

    def test_a_page_save_leaves_other_parameters_exactly_as_they_were(self):
        if 'group_product_pricelist' not in self.native:
            return
        params = self.env['ir.config_parameter'].sudo()
        params.set_param('ncollection.test.untouched', '5')
        before = {p['key']: p['value'] for p in params.search_read([], ['key', 'value'])}
        start = self._settings(self.owner).group_product_pricelist
        self._settings(self.owner, group_product_pricelist=not start).action_nc_save()
        after = {p['key']: p['value'] for p in params.search_read([], ['key', 'value'])}
        self.assertEqual(before, after)

    def test_a_delegate_cannot_write_an_accounting_control_on_the_company_directly(self):
        from ..wizards.app_settings import ADMIN_ONLY_COMPANY_FIELDS
        for field in ADMIN_ONLY_COMPANY_FIELDS:
            if field not in self.env['res.company']._fields:
                continue
            with self.assertRaises(AccessError):
                self.env.company.with_user(self.delegate).write({field: not self.env.company[field]})
