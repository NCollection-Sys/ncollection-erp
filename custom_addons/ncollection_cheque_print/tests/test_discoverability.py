# -*- coding: utf-8 -*-
"""Can a person actually FIND cheque printing? Present in the registry is not the same as reachable.

By design (see CHEQUE_PRINTING.md, "Where to find it") the module has NO Home tile: its menus sit inside the Accounting
app and the other entry point is the Print Cheque button on a vendor payment. These tests pin that every entry point is
there, reachable with the right rights, and absent without them, so a later change to a menu parent, a group or the
payment view cannot silently hide the feature again."""
from lxml import etree

from odoo.addons.account.tests.common import AccountTestInvoicingCommon
from odoo.tests import tagged

MODULE = 'ncollection_cheque_print'
ROOT = 'menu_nc_cheque_root'
LEAVES = {'menu_nc_cheque_template': 'action_nc_cheque_template', 'menu_nc_cheque_job': 'action_nc_cheque_job',
          'menu_nc_cheque_profile': 'action_nc_cheque_profile'}


@tagged('post_install', '-at_install')
class TestChequeDiscoverability(AccountTestInvoicingCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Menu = cls.env['ir.ui.menu']
        users = cls.env['res.users'].with_context(no_reset_password=True)

        def make(login, group):
            groups = [cls.env.ref('base.group_user').id] + ([cls.env.ref(group).id] if group else [])
            return users.create({'name': login, 'login': login, 'company_id': cls.env.company.id,
                                 'company_ids': [(6, 0, cls.env.company.ids)], 'group_ids': [(6, 0, groups)]})
        cls.accountant = make('disc_acct', 'account.group_account_user')
        cls.readonly = make('disc_ro', 'account.group_account_readonly')
        cls.plain = make('disc_plain', None)

    def _menu(self, name):
        return self.env.ref('%s.%s' % (MODULE, name))

    # ------------------------------------------------------------------ the menus
    def test_the_entry_is_one_menu_inside_accounting_with_three_destinations(self):
        root = self._menu(ROOT)
        self.assertEqual(root.parent_id, self.env.ref('account.menu_finance'))
        self.assertFalse(root.action, "a folder, not an action")
        for leaf_name, action_name in LEAVES.items():
            leaf = self._menu(leaf_name)
            self.assertEqual(leaf.parent_id, root)
            self.assertEqual(leaf.action, self.env.ref('%s.%s' % (MODULE, action_name)))
        # exactly these four menus: nothing hidden, nothing redundant, no second root next to Accounting
        owned = self.env['ir.model.data'].search([('module', '=', MODULE), ('model', '=', 'ir.ui.menu')])
        self.assertEqual(len(owned), 1 + len(LEAVES))
        self.assertFalse(self.Menu.browse(owned.mapped('res_id')).filtered(lambda m: not m.parent_id))

    def test_no_home_tile_and_no_root_application_menu(self):
        """Home tiles are root menus. This module must not add one: it belongs inside Accounting."""
        for user in (self.accountant, self.readonly):
            apps = self.Menu.with_user(user).nc_tenant_apps()
            self.assertFalse([a for a in apps if (a['xmlid'] or '').startswith(MODULE + '.')],
                             "cheque printing grew a Home tile of its own")

    def test_an_accountant_reaches_every_destination(self):
        visible = self.Menu.with_user(self.accountant)._visible_menu_ids(False)
        for name in [ROOT, *LEAVES]:
            self.assertIn(self._menu(name).id, visible, name)
        for action_name in LEAVES.values():
            action = self.env.ref('%s.%s' % (MODULE, action_name)).with_user(self.accountant)
            self.assertTrue(self.env[action.res_model].with_user(self.accountant).has_access('read'))

    def test_a_read_only_user_sees_the_history_but_cannot_change_anything(self):
        visible = self.Menu.with_user(self.readonly)._visible_menu_ids(False)
        for name in [ROOT, *LEAVES]:
            self.assertIn(self._menu(name).id, visible, name)
        template = self.env['ncollection.cheque.template'].with_user(self.readonly)
        self.assertTrue(template.has_access('read'))
        for operation in ('write', 'create', 'unlink'):
            self.assertFalse(template.has_access(operation), operation)

    def test_a_user_without_accounting_sees_nothing(self):
        visible = self.Menu.with_user(self.plain)._visible_menu_ids(False)
        for name in [ROOT, *LEAVES]:
            self.assertNotIn(self._menu(name).id, visible, name)
        self.assertFalse(self.env['ncollection.cheque.template'].with_user(self.plain).has_access('read'))

    # ------------------------------------------------------------------ the other entry point
    def _payment_buttons(self, user):
        view = self.env['account.payment'].with_user(user).get_view(
            self.env.ref('account.view_account_payment_form').id, 'form')
        tree = etree.fromstring(view['arch'])
        return {b.get('name'): b for b in tree.xpath('//header//button')}

    def test_print_cheque_is_a_button_on_the_vendor_payment_for_accountants_only(self):
        button = self._payment_buttons(self.accountant).get('action_nc_print_cheque')
        self.assertIsNotNone(button, "the Print Cheque button is missing from the payment form")
        self.assertIn("outbound", button.get('invisible') or '', "it must only show on a payment you make")
        self.assertNotIn('action_nc_print_cheque', self._payment_buttons(self.readonly))
        payment = self.env['account.payment'].create({
            'payment_type': 'outbound', 'partner_type': 'supplier', 'amount': 10.0,
            'partner_id': self.env['res.partner'].create({'name': 'Synthetic Payee'}).id,
            'journal_id': self.company_data['default_journal_bank'].id})
        action = payment.with_user(self.accountant).action_nc_print_cheque()
        self.assertEqual(action['res_model'], 'ncollection.cheque.print.wizard')
        self.assertEqual(action['target'], 'new')

    # ------------------------------------------------------------------ the module record itself
    def test_the_module_has_a_real_icon_and_is_flagged_as_an_application(self):
        module = self.env['ir.module.module'].sudo().search([('name', '=', MODULE)])
        self.assertEqual(module.state, 'installed')
        self.assertTrue(module.application)
        self.assertEqual(module.icon, '/%s/static/description/icon.png' % MODULE)
        self.assertTrue(module.icon_image, "no icon: the Apps list would show the letter fallback")

    def test_the_menus_have_an_arabic_name_in_the_catalogue(self):
        """The names an Arabic user reads. Checked against the shipped catalogue, which is what is loaded when the
        language is installed, so it does not depend on this test database having Arabic loaded."""
        import os
        path = os.path.join(os.path.dirname(__file__), '..', 'i18n', 'ar.po')
        with open(path, encoding='utf-8') as handle:
            catalogue = handle.read()
        for name in ('Cheque Printing', 'Print Jobs', 'Cheque Templates', 'Printer Profiles', 'Print Cheque'):
            self.assertIn('msgid "%s"\nmsgstr "' % name, catalogue, name)
            self.assertNotIn('msgid "%s"\nmsgstr ""' % name, catalogue, name)
