# -*- coding: utf-8 -*-
"""Settings of optional applications on the unified tenant Settings page (wizards/app_settings.py): offered only while
their module is installed, applied where Odoo stores them with only their own side effect (never Odoo's whole
set_values), the section level and the administrator rule enforced on save, and no privilege gained by the Owner."""
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import TransactionCase, new_test_user, tagged

from ..wizards.app_settings import APP_SETTINGS, COMPANY_FIELDS, MAXIMUM, TENANT_WIDE, _TYPES


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
        # tenant-wide settings need a user of EVERY company (the Owner of a multi-company tenant is)
        cls.owner.write({'company_ids': [(6, 0, cls.env['res.company'].search([]).ids)]})

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

    def test_the_owner_switches_a_feature_group(self):
        name = 'group_product_pricelist'
        if name not in self.native:
            return                          # product is not installed on this database: nothing to offer
        group = self.env.ref('product.group_product_pricelist')
        # only empty pricelists in the company currency: switching off is then allowed (see the refusal test)
        self.env['product.pricelist'].search([]).filtered(
            lambda pl: pl.item_ids or pl.currency_id != (pl.company_id or self.env.company).currency_id
        ).action_archive()
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

    def test_a_save_writes_no_other_parameter_and_archives_nothing(self):
        """Odoo's whole set_values is never run: no configuration parameter is rewritten, no record archived."""
        if 'group_product_pricelist' not in self.native:
            return
        params = self.env['ir.config_parameter'].sudo()
        params.set_param('ncollection.test.untouched', '5')
        before = {p['key']: p['value'] for p in params.search_read([], ['key', 'value'])}
        active = self.env['product.pricelist'].search([])
        start = self._settings(self.owner).group_product_pricelist
        if start:
            self.env['product.pricelist'].create({'name': 'App settings priced', 'item_ids': [(0, 0, {
                'compute_price': 'percentage', 'percent_price': 10})]})
            active = self.env['product.pricelist'].search([])
        if start:                           # switching off is refused while a priced list exists
            with self.assertRaises(UserError):
                self._settings(self.owner, group_product_pricelist=False).action_nc_save()
        else:
            self._settings(self.owner, group_product_pricelist=True).action_nc_save()
        after = {p['key']: p['value'] for p in params.search_read([], ['key', 'value'])}
        self.assertEqual(before, after)
        self.assertLessEqual(active, self.env['product.pricelist'].search([]))

    def test_pricelists_stay_on_while_a_pricelist_changes_prices(self):
        if 'group_product_pricelist' not in self.native:
            return
        self._settings(self.owner, group_product_pricelist=True).action_nc_save()
        priced = self.env['product.pricelist'].create({'name': 'App settings rules', 'item_ids': [(0, 0, {
            'compute_price': 'percentage', 'percent_price': 10})]})
        with self.assertRaises(UserError):
            self._settings(self.owner, group_product_pricelist=False).action_nc_save()
        self.assertTrue(priced.active)
        self.assertTrue(self.env.ref('product.group_product_pricelist') in self.env.ref('base.group_user').implied_ids)

    def test_numbers_have_an_upper_bound(self):
        for name, maximum in MAXIMUM.items():
            if name not in self.native:
                continue
            with self.assertRaises(ValidationError):
                self._settings(self.owner, **{name: maximum + 1}).action_nc_save()

    def test_tenant_wide_settings_say_so(self):
        for name in APP_SETTINGS:
            label = self.Page._fields[name].string
            self.assertEqual(label.endswith('(all companies)'), name in TENANT_WIDE, name)
            if name in self.native and self.native[name].related:
                self.assertNotIn(name, TENANT_WIDE, name)      # a company field is never labelled tenant-wide

    def test_a_delegate_cannot_write_an_accounting_control_on_the_company_directly(self):
        from ..wizards.app_settings import ADMIN_ONLY_COMPANY_FIELDS
        for field in ADMIN_ONLY_COMPANY_FIELDS:
            if field not in self.env['res.company']._fields:
                continue
            with self.assertRaises(AccessError):
                self.env.company.with_user(self.delegate).write({field: not self.env.company[field]})

    # ---------------------------------------------------------------- the side effect of each setting
    def _toggle(self, name, value):
        self._settings(self.owner, **{name: value}).action_nc_save()

    def test_a_single_company_user_cannot_change_a_tenant_wide_setting(self):
        name = next((n for n in TENANT_WIDE if n in self.native and _TYPES[n] == 'boolean'
                     and APP_SETTINGS[n][1] == 'business' and not APP_SETTINGS[n][4]), None)
        if not name:
            return
        self.env['res.company'].create({'name': 'App settings second company'})
        current = self._settings(self.delegate)[name]
        with self.assertRaises(AccessError):
            self._settings(self.delegate, **{name: not current}).action_nc_save()

    def test_switching_the_security_lead_time_off_sets_it_to_zero(self):
        if 'use_security_lead' not in self.native:
            return
        self._toggle('use_security_lead', True)
        self.env.company.security_lead = 3
        self._toggle('use_security_lead', False)
        self.assertEqual(self.env.company.security_lead, 0)

    def test_leads_follow_on_every_team_that_uses_opportunities(self):
        if 'group_use_lead' not in self.native:
            return
        team = self.env['crm.team'].create({'name': 'App settings team', 'use_opportunities': True})
        start = self._settings(self.owner).group_use_lead
        self._toggle('group_use_lead', not start)
        self.assertEqual(team.use_leads, not start)
        self._toggle('group_use_lead', start)
        self.assertEqual(team.use_leads, start)

    def test_quotation_templates_off_clears_the_default_template(self):
        if 'group_sale_order_template' not in self.native:
            return
        self._toggle('group_sale_order_template', True)
        template = self.env['sale.order.template'].create({'name': 'App settings template'})
        self.env.company.sale_order_template_id = template
        self._toggle('group_sale_order_template', False)
        self.assertFalse(self.env.company.sale_order_template_id)
        self.assertTrue(template.exists())

    def test_storage_locations_need_an_inventory_manager(self):
        if 'group_stock_multi_locations' not in self.native:
            return
        current = self._settings(self.owner).group_stock_multi_locations
        if self.owner.has_group('stock.group_stock_manager'):
            return
        with self.assertRaises(AccessError):
            self._toggle('group_stock_multi_locations', not current)

    def test_work_orders_stay_on_while_operations_exist(self):
        if 'group_mrp_routings' not in self.native:
            return
        self._toggle('group_mrp_routings', True)
        product = self.env['product.product'].create({'name': 'App settings product'})
        center = self.env['mrp.workcenter'].create({'name': 'App settings center'})
        bom = self.env['mrp.bom'].create({'product_tmpl_id': product.product_tmpl_id.id, 'operation_ids': [
            (0, 0, {'name': 'Cut', 'workcenter_id': center.id})]})
        with self.assertRaises(UserError):
            self._toggle('group_mrp_routings', False)
        self.assertTrue(bom.operation_ids.active)

    def test_unlocking_by_default_unlocks_open_manufacturing_orders(self):
        if 'group_unlocked_by_default' not in self.native:
            return
        product = self.env['product.product'].create({'name': 'App settings made', 'is_storable': True})
        self._toggle('group_unlocked_by_default', False)
        order = self.env['mrp.production'].create({'product_id': product.id, 'product_qty': 1})
        order.is_locked = True
        self._toggle('group_unlocked_by_default', True)
        self.assertFalse(order.is_locked)

    def test_project_stages_show_the_stage_change_message_type(self):
        if 'group_project_stages' not in self.native:
            return
        subtype = self.env.ref('project.mt_project_stage_change')
        start = self._settings(self.owner).group_project_stages
        self._toggle('group_project_stages', not start)
        self.assertEqual(subtype.hidden, start)
        self._toggle('group_project_stages', start)
        self.assertEqual(subtype.hidden, not start)

    def test_mailing_campaigns_drive_the_ab_testing_scheduler(self):
        if 'group_mass_mailing_campaign' not in self.native:
            return
        cron = self.env.ref('mass_mailing.ir_cron_mass_mailing_ab_testing')
        start = self._settings(self.owner).group_mass_mailing_campaign
        self._toggle('group_mass_mailing_campaign', not start)
        self.assertEqual(cron.active, not start)
        self._toggle('group_mass_mailing_campaign', start)

    def test_a_direct_company_write_is_bounded_too(self):
        for name, maximum in MAXIMUM.items():
            field = COMPANY_FIELDS.get(name)
            if not field or field not in self.env['res.company']._fields:
                continue
            with self.assertRaises(ValidationError):
                self.env.company.with_user(self.owner).write({field: maximum + 1})
