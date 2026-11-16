from odoo.exceptions import AccessError, ValidationError
from odoo.tests import TransactionCase, new_test_user, tagged


@tagged('post_install', '-at_install')
class TestLabelOverride(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Override = cls.env['ncollection.label.override']
        cls.engine = cls.env['ncollection.label.engine']
        cls.admin = new_test_user(cls.env, login='nc_label_admin',
                                  groups='base.group_user,ncollection_core.group_tenant_admin')
        cls.plain = new_test_user(cls.env, login='nc_label_plain', groups='base.group_user')
        cls.field = cls.env['ir.model.fields']._get('res.partner', 'ref')

    def _label(self, model='res.partner', name='ref'):
        self.env.registry.clear_cache()
        return self.env[model].with_context(lang='en_US').fields_get([name])[name]['string']

    def test_field_label_override_shows_and_resets(self):
        original = self._label()
        override = self.Override.with_user(self.admin).create({
            'target_type': 'field', 'field_id': self.field.id, 'lang': 'en_US', 'value': 'Customer Code'})
        self.assertEqual(self._label(), 'Customer Code')
        self.assertEqual(override.original, original)
        override.with_user(self.admin).action_reset()
        self.assertEqual(self._label(), original)
        events = self.env['ncollection.label.audit'].search([('element', '=like', 'Field Label:%')]).mapped('event')
        self.assertIn('create', events)
        self.assertIn('reset', events)

    def test_view_text_override_reaches_the_rendered_view(self):
        view = self.env.ref('base.view_partner_form')
        term = sorted(t for t in self.Override._nc_view_terms(view, 'en_US') if t.isalpha() and len(t) > 4)[0]
        self.Override.with_user(self.admin).create({
            'target_type': 'view', 'view_id': view.id, 'source_term': term, 'lang': 'en_US', 'value': 'Zzlabeltext'})
        views = self.env['res.partner'].with_context(lang='en_US').get_views([(view.id, 'form')])
        arch = views['views']['form']['arch']
        self.assertIn('Zzlabeltext', arch)

    def test_menu_rule_and_precedence(self):
        menu = self.env['ir.ui.menu'].create({'name': 'Zzqword Reports'})
        rule = self.env['ncollection.label.rule'].create({'name': 't', 'lang': 'en_US', 'pairs': 'Zzqword => Qqxterm'})
        self.engine._nc_apply_all(force=True)
        self.assertEqual(menu.name, 'Qqxterm Reports', "platform rule")
        self.assertGreaterEqual(rule.last_count, 1)
        self.Override.with_user(self.admin).create({
            'target_type': 'menu', 'menu_id': menu.id, 'lang': 'en_US', 'value': 'My Own Reports'})
        self.engine._nc_apply_all(force=True)
        menu.invalidate_recordset()
        self.assertEqual(menu.name, 'My Own Reports', "a tenant override beats the platform rule")

    def test_case_is_preserved(self):
        rule = self.env['ncollection.label.rule'].create({'name': 't', 'lang': 'en_US',
                                                          'pairs': 'Vendors => Suppliers\nVendor => Supplier'})
        replace = rule._replacer()
        self.assertEqual(replace('Vendor Bills for vendors; VENDOR'), 'Supplier Bills for suppliers; SUPPLIER')
        self.assertEqual(replace('Vendorship'), 'Vendorship', "whole words only")

    def test_reapplied_after_an_upgrade_reloads_the_original(self):
        self.Override.with_user(self.admin).create({
            'target_type': 'field', 'field_id': self.field.id, 'lang': 'en_US', 'value': 'Customer Code'})
        self.field.update_field_translations('field_description', {'en_US': 'Reference'})  # what an upgrade does
        self.env['ir.config_parameter'].set_param('ncollection_core.label_signature', 'stale')
        self.engine._nc_apply_all()
        self.assertEqual(self._label(), 'Customer Code')

    def test_refusals(self):
        with self.assertRaises(AccessError):
            self.Override.with_user(self.plain).create({
                'target_type': 'field', 'field_id': self.field.id, 'lang': 'en_US', 'value': 'x'})
        with self.assertRaises(ValidationError):
            self.Override.with_user(self.admin).create({
                'target_type': 'field', 'field_id': self.field.id, 'lang': 'en_US', 'value': '<b>x</b>'})
        groups_field = self.env['ir.model.fields']._get('res.groups', 'name')
        with self.assertRaises(ValidationError):
            self.Override.with_user(self.admin).create({
                'target_type': 'field', 'field_id': groups_field.id, 'lang': 'en_US', 'value': 'Team'})
        view = self.env.ref('base.view_partner_form')
        with self.assertRaises(ValidationError):
            self.Override.with_user(self.admin).create({
                'target_type': 'view', 'view_id': view.id, 'source_term': 'Not on this screen', 'lang': 'en_US',
                'value': 'x'})
        log = self.env['ncollection.label.audit'].sudo().create({'event': 'rule', 'element': 'x'})
        with self.assertRaises(AccessError):
            log.unlink()
        with self.assertRaises(AccessError):
            log.write({'element': 'y'})
