# -*- coding: utf-8 -*-
"""The Workspace Administration forms on res.company (Company, Appearance) open EDITABLE in the browser for exactly the
users the delegation accepts on save, and read-only for everyone else.

Odoo's view post-processing marks a res.company form edit="False" for any user without the ACL's write right (only
base.group_erp_manager has it); the delegation worked over RPC while its own forms were read-only. These tests pin the
browser contract to the server contract: same users, same fields, same platform level."""
import json

from lxml import etree

from odoo.exceptions import AccessError, UserError
from odoo.tests import TransactionCase, new_test_user, tagged


@tagged('post_install', '-at_install')
class TestDelegatedCompanyForms(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.parent = cls.env.company
        cls.child = cls.env['res.company'].create({'name': 'DCF Branch', 'parent_id': cls.parent.id})
        cls.other = cls.env['res.company'].create({'name': 'DCF Other Co'})
        both = {'company_id': cls.parent.id, 'company_ids': [(6, 0, (cls.parent | cls.child).ids)]}
        cls.owner = new_test_user(cls.env, login='dcf_owner',
                                  groups='base.group_user,ncollection_core.group_role_owner', **both)
        cls.admin = new_test_user(cls.env, login='dcf_admin',
                                  groups='base.group_user,ncollection_core.group_tenant_admin', **both)
        cls.clerk = new_test_user(cls.env, login='dcf_clerk', groups='base.group_user', **both)
        cls.company_delegate = new_test_user(
            cls.env, login='dcf_company', groups='base.group_user,ncollection_core.group_settings_company', **both)
        cls.config = cls.env['ncollection.workspace.config'].search([], limit=1) or \
            cls.env['ncollection.workspace.config'].create({})
        cls.config.sudo().settings_policy = False
        cls.company_form = cls.env.ref('ncollection_core.view_workspace_company_form')
        cls.appearance_form = cls.env.ref('ncollection_core.view_workspace_appearance_form')

    def _form(self, user, view):
        arch = self.env['res.company'].with_user(user).get_views([(view.id, 'form')])['views']['form']['arch']
        return etree.fromstring(arch)

    def _editable(self, node):
        return node.get('edit') not in ('False', 'false', '0')

    def _readonly(self, node, name):
        return node.xpath('//field[@name="%s"]' % name)[0].get('readonly') in ('True', 'true', '1')

    # ------------------------------------------------------------------ form editability
    def test_the_owner_and_the_workspace_administrator_get_editable_forms(self):
        for user in (self.owner, self.admin):
            for view in (self.company_form, self.appearance_form):
                with self.subTest(user=user.login, view=view.name):
                    self.assertTrue(self._editable(self._form(user, view)))
        node = self._form(self.owner, self.company_form)
        self.assertFalse(self._readonly(node, 'name'))
        self.assertFalse(self._readonly(node, 'vat'))
        self.assertTrue(self._readonly(node, 'nc_system_logo'), 'System Branding is never the tenant\'s')

    def test_a_user_outside_the_delegation_keeps_odoos_read_only_form(self):
        for view in (self.company_form, self.appearance_form):
            self.assertFalse(self._editable(self._form(self.clerk, view)))

    def test_a_section_delegate_edits_only_their_own_section(self):
        self.assertTrue(self._editable(self._form(self.company_delegate, self.company_form)))
        self.assertFalse(self._editable(self._form(self.company_delegate, self.appearance_form)))

    def test_the_platform_level_decides_the_form_too(self):
        self.config.sudo().settings_policy = json.dumps({'company': 'view', 'appearance': 'administer'})
        self.assertFalse(self._editable(self._form(self.owner, self.company_form)))
        self.assertTrue(self._editable(self._form(self.owner, self.appearance_form)))
        with self.assertRaises(AccessError):
            self.parent.with_user(self.owner).write({'city': 'Sharjah'})

    def test_other_res_company_forms_are_not_touched(self):
        node = self._form(self.owner, self.env.ref('base.view_company_form'))
        self.assertFalse(self._editable(node), 'only the listed Workspace Administration forms are delegated')

    def test_one_user_s_form_never_reaches_another(self):
        """get_view's per-user step runs after a cache shared by all users: serve the owner first, then check that
        the clerk is still refused and an administrator gets Odoo's own arch, untouched."""
        self.assertTrue(self._editable(self._form(self.owner, self.company_form)))
        self.assertFalse(self._editable(self._form(self.clerk, self.company_form)))
        erp = new_test_user(self.env, login='dcf_erp', groups='base.group_user,base.group_erp_manager',
                            company_id=self.parent.id, company_ids=[(6, 0, self.parent.ids)])
        node = self._form(erp, self.company_form)
        self.assertIsNone(node.get('edit'), 'an administrator keeps Odoo\'s own form: nothing set by the delegation')
        self.assertFalse(node.xpath('//field[@readonly="True"]'), 'no delegation read-only mark for an administrator')
        self.assertTrue(self._editable(self._form(self.owner, self.company_form)))
        self.assertFalse(self._editable(self._form(self.clerk, self.company_form)))

    def test_the_form_and_the_server_agree_on_every_field(self):
        """No drift between the view's writable set and what write() accepts: for each field on the form, the user
        may change it on screen exactly when the server accepts the write."""
        for user in (self.owner, self.company_delegate, self.clerk):
            for view in (self.company_form, self.appearance_form):
                node = self._form(user, view)
                self.assertFalse(node.xpath('//field//field'), 'a delegated form holds no sub-view')
                for field in node.iter('field'):
                    name = field.get('name')
                    shown = self._editable(node) and not self._readonly(node, name)
                    value = self.parent._fields[name].convert_to_write(self.parent[name], self.parent)
                    try:
                        with self.env.cr.savepoint():
                            self.parent.with_user(user).write({name: value})
                        accepted = True
                    except AccessError:
                        accepted = False
                    with self.subTest(user=user.login, view=view.name, field=name):
                        self.assertEqual(shown, accepted)

    # ------------------------------------------------------------------ the saves behind the form
    def test_authorized_saves_persist(self):
        self.parent.with_user(self.owner).write({'city': 'Dubai', 'nc_primary_color': '#123456'})
        self.child.with_user(self.admin).write({'city': 'Ajman'})     # a branch the user belongs to
        self.assertEqual((self.parent.city, self.parent.nc_primary_color, self.child.city),
                         ('Dubai', '#123456', 'Ajman'))

    def test_unauthorized_writes_are_refused(self):
        with self.assertRaises(AccessError):
            self.parent.with_user(self.clerk).write({'city': 'Dubai'})
        with self.assertRaises(AccessError):
            self.parent.with_user(self.company_delegate).write({'nc_primary_color': '#000000'})
        # a read-only field of the form is not writable either. On a database that already has journal items
        # Odoo's own currency guard (UserError) may refuse first; either way nothing changes.
        currency = self.parent.currency_id
        with self.assertRaises(UserError):   # AccessError is a UserError
            self.parent.with_user(self.owner).write({'currency_id': self.env.ref('base.EUR').id})
        self.assertEqual(self.parent.currency_id, currency)

    def test_another_company_is_never_reachable(self):
        with self.assertRaises(AccessError):
            self.other.with_user(self.owner).write({'city': 'Elsewhere'})
        self.assertNotEqual(self.other.city, 'Elsewhere')

    def test_the_owner_is_still_not_a_company_administrator(self):
        for group in ('base.group_system', 'base.group_erp_manager'):
            self.assertFalse(self.owner.has_group(group))
        self.assertFalse(self.env['res.company'].with_user(self.owner).has_access('write'))
