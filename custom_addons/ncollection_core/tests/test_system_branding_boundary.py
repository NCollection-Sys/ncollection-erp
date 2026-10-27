# -*- coding: utf-8 -*-
"""Who may change the identity of the SYSTEM, and who may not.

The tenant Owner role implies ``base.group_system`` (they administer their workspace). That must not let them rename
the platform: System Branding is changed by a dedicated group, ``System Branding Manager``, held by the platform's own
staff and by a Platform Administrator while inside a tenant, never by the Owner role. ``base.group_system`` keeps its
global meaning; nothing here widens or narrows it.

A tenant database has no Platform Admin group (the subscription layer is not installed there), whereas the all-in-one
database these tests run on has it. ``_as_tenant_database`` removes that one platform link inside the test transaction
(rolled back afterwards) so the tenant topology is what is under test.
"""
import hashlib

from odoo import Command
from odoo.exceptions import AccessError, ValidationError
from odoo.tests import TransactionCase, tagged

NONCE = 'B0undary-token-for-tests-' + 'z' * 24
MANAGER = 'ncollection_branding.group_system_branding_manager'


def _grant(ref='u31', name='Pat Platform'):
    return {'token_hash': hashlib.sha256(NONCE.encode()).hexdigest(), 'platform_ref': ref, 'platform_name': name,
            'return_url': 'https://platform.invalid/odoo', 'ttl': 60}


@tagged('post_install', '-at_install')
class TestSystemBrandingBoundary(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Brand = cls.env['ncollection.product.branding']
        cls.record = cls.Brand._nc_record()
        cls.company = cls.env.company
        cls.manager_group = cls.env.ref(MANAGER)
        cls._as_tenant_database()
        cls.owner = cls.env['res.users'].with_context(no_reset_password=True).create({
            'name': 'Workspace Owner', 'login': 'boundary_owner',
            'group_ids': [Command.set([cls.env.ref('ncollection_core.group_role_owner').id])],
            'company_id': cls.company.id, 'company_ids': [Command.set(cls.company.ids)]})
        cls.settings_admin = cls.env['res.users'].with_context(no_reset_password=True).create({
            'name': 'Settings Administrator', 'login': 'boundary_settings_admin',
            'group_ids': [Command.set([cls.env.ref('base.group_system').id])],
            'company_id': cls.company.id, 'company_ids': [Command.set(cls.company.ids)]})

    @classmethod
    def _as_tenant_database(cls):
        """A tenant database has no Platform Admin group: take the platform layer's link to it out (rolled back)."""
        platform = cls.env.ref('ncollection_subscription.group_platform_admin', raise_if_not_found=False)
        if platform:
            cls.env.ref('base.group_system').write({'implied_ids': [Command.unlink(platform.id)]})
        cls.env.registry.clear_cache()

    # ----------------------------------------------------------------- the Owner
    def test_a_tenant_owner_does_not_hold_the_system_branding_right(self):
        self.assertTrue(self.owner.has_group('base.group_system'), 'the Owner role still implies Settings: unchanged')
        self.assertFalse(self.owner.has_group(MANAGER))
        self.assertNotIn(self.manager_group, self.env.ref('ncollection_core.group_role_owner').all_implied_ids)

    def test_the_owner_and_a_settings_administrator_are_refused_at_the_orm_not_only_in_the_menu(self):
        for user in (self.owner, self.settings_admin):
            with self.subTest(user=user.login):
                for call in (
                    lambda: self.record.with_user(user).write({'name': 'Hijacked'}),
                    lambda: self.record.with_user(user).write({'primary_color': '#000000'}),
                    lambda: self.record.with_user(user).write({'copyright_text': 'pwned'}),
                    lambda: self.Brand.with_user(user).create({'name': 'Hijacked'}),
                    lambda: self.Brand.with_user(user).action_open(),
                    lambda: self.record.with_user(user).read(['name']),     # not even the form's data
                ):
                    with self.assertRaises(AccessError):
                        call()
        self.assertNotEqual(self.record.name, 'Hijacked')

    def test_the_system_branding_menus_are_hidden_from_the_owner(self):
        visible = self.env['ir.ui.menu'].with_user(self.owner)._visible_menu_ids()
        for xml_id in ('ncollection_branding.menu_product_branding',
                       'ncollection_core.menu_workspace_product_branding'):
            with self.subTest(menu=xml_id):
                self.assertNotIn(self.env.ref(xml_id).id, visible)
                self.assertEqual(self.env.ref(xml_id).group_ids, self.manager_group)

    def test_every_page_still_reads_the_system_identity_for_the_owner(self):
        brand = self.Brand.with_user(self.owner).nc_brand()
        self.assertTrue(brand['name'] and brand['primary_color'] and brand['copyright'])

    # ------------------------------------------- the Owner cannot GIVE themselves the right (pre-commit review H1)
    # The Owner implies base.group_system and so base.group_erp_manager, which Odoo lets write users and groups. Each
    # route below worked before the grant guard in ncollection_branding/models/system_branding_grant.py.
    def _assert_owner_never_holds_it(self):
        # The truth, read from the relation tables (what the guard itself compares), then has_group with fresh
        # caches: a savepoint rollback inside a test does not reset registry caches the way a failed request does.
        holders = self.env['res.users']._nc_system_branding_holder_ids(self.manager_group)
        self.assertNotIn(self.owner.id, holders, 'the Owner holds the right in the database')
        self.env.registry.clear_cache('groups')
        self.env.registry.clear_cache()
        self.env.invalidate_all()
        self.assertFalse(self.owner.has_group(MANAGER))
        self.assertNotIn(self.owner, self.manager_group.with_context(active_test=False).all_user_ids)

    def test_the_owner_cannot_assign_the_group_to_themselves_or_to_anybody(self):
        for actor in (self.owner, self.settings_admin):
            with self.subTest(actor=actor.login):
                with self.assertRaises(AccessError):
                    actor.with_user(actor).write({'group_ids': [Command.link(self.manager_group.id)]})
                with self.assertRaises(AccessError):
                    self.owner.with_user(actor).write({'group_ids': [Command.link(self.manager_group.id)]})
                with self.assertRaises(AccessError):
                    self.env['res.users'].with_user(actor).with_context(no_reset_password=True).create({
                        'name': 'Branding Smuggler', 'login': 'boundary_smuggler_%s' % actor.id,
                        'group_ids': [Command.set([self.env.ref('base.group_user').id, self.manager_group.id])]})
                with self.assertRaises(AccessError):
                    self.record.with_user(actor).write({'name': 'Hijacked'})
        self._assert_owner_never_holds_it()

    def test_the_owner_cannot_reach_the_group_through_a_custom_role(self):
        Role = self.env['ncollection.role'].with_user(self.owner)
        with self.assertRaises(ValidationError):
            Role.create({'name': 'Branding Role', 'custom_group_ids': [Command.set(self.manager_group.ids)]})
        # A role the Owner made legitimately, whose group only LATER comes to imply the right (the superuser made it
        # so): assigning it is still refused, by the user guard, not only by the role constraint.
        carrier = self.env['res.groups'].create({'name': 'Boundary Carrier'})
        role = Role.create({'name': 'Carrier Role', 'custom_group_ids': [Command.set(carrier.ids)]})
        carrier.write({'implied_ids': [Command.link(self.manager_group.id)]})
        with self.assertRaises(AccessError):
            self.owner.with_user(self.owner).nc_set_roles((self.owner.nc_role_ids | role).ids)
        self._assert_owner_never_holds_it()

    def test_the_owner_cannot_reach_the_group_by_rewiring_groups(self):
        owner_group = self.env.ref('ncollection_core.group_role_owner')
        Groups = self.env['res.groups'].with_user(self.owner)
        for label, call in (
            ('user_ids', lambda: self.manager_group.with_user(self.owner).write({
                'user_ids': [Command.link(self.owner.id)]})),
            ('implied_ids', lambda: owner_group.with_user(self.owner).write({
                'implied_ids': [Command.link(self.manager_group.id)]})),
            ('implied_by_ids', lambda: self.manager_group.with_user(self.owner).write({
                'implied_by_ids': [Command.link(owner_group.id)]})),
            ('system implies it', lambda: self.env.ref('base.group_system').with_user(self.owner).write({
                'implied_ids': [Command.link(self.manager_group.id)]})),
            ('new group', lambda: Groups.create({
                'name': 'Boundary Bridge', 'implied_ids': [Command.link(self.manager_group.id)],
                'user_ids': [Command.link(self.owner.id)]})),
        ):
            with self.subTest(route=label):
                with self.assertRaises(AccessError):
                    call()
        self._assert_owner_never_holds_it()

    def test_the_guard_leaves_ordinary_user_and_group_administration_alone(self):
        """Only GIVING the right is refused: the Owner still creates users, groups and implications that do not reach
        it, and the guard never blocks taking the right away."""
        member = self.env['res.users'].with_user(self.owner).with_context(no_reset_password=True).create({
            'name': 'Ordinary Member', 'login': 'boundary_member',
            'group_ids': [Command.set([self.env.ref('base.group_user').id])]})
        team = self.env['res.groups'].with_user(self.owner).create({
            'name': 'Boundary Team', 'implied_ids': [Command.link(self.env.ref('base.group_user').id)],
            'user_ids': [Command.link(member.id)]})
        member.with_user(self.owner).write({'group_ids': [Command.link(team.id)]})
        self.assertTrue(member.has_group('base.group_user'))
        _Entry, _entry, proxy = self._enter('u77')
        proxy.with_user(self.owner).write({'group_ids': [Command.unlink(self.manager_group.id)]})
        self.assertFalse(proxy.has_group(MANAGER), 'removing the right is never restricted')

    def test_the_superuser_still_gives_the_right(self):
        """Install, upgrade, migrations, the provisioning seed and the shell run as the superuser."""
        operator = self.env['res.users'].with_context(no_reset_password=True).create({
            'name': 'Seeded Operator', 'login': 'boundary_seeded_operator',
            'group_ids': [Command.set([self.env.ref('base.group_user').id, self.manager_group.id])]})
        self.assertTrue(operator.has_group(MANAGER))
        self.record.with_user(operator).write({'browser_title': 'Seeded Console'})
        self.assertEqual(self.Brand.nc_brand()['title'], 'Seeded Console')

    # ------------------------------------------- tenant branding and company identity are the Owner's
    def test_the_owner_keeps_tenant_branding_and_company_identity_and_neither_touches_the_system(self):
        before = self.Brand.nc_brand()
        company = self.company.with_user(self.owner)
        company.write({'nc_primary_color': '#123456', 'nc_secondary_color': '#654321', 'nc_sidebar_color': '#0A0B0C'})
        company.write({'name': 'Boundary Trading FZ-LLC'})
        company.flush_recordset()
        self.assertEqual(self.company.nc_primary_color, '#123456', 'tenant branding: the Owner changes it')
        self.assertEqual(self.company.name, 'Boundary Trading FZ-LLC', 'company identity: the Owner changes it')
        self.env.registry.clear_cache()
        after = self.Brand.nc_brand()
        for key in ('name', 'short_name', 'title', 'copyright', 'primary_color', 'secondary_color'):
            self.assertEqual(after[key], before[key],
                             'System Branding %s is not affected by the tenant or the company' % key)

    # ----------------------------------------------------------------- the platform administrator inside a tenant
    def _enter(self, ref='u31'):
        Entry = self.env['ncollection.platform.entry']
        entry = Entry.browse(Entry.sudo().create(Entry._grant_vals(_grant(ref))).ids)
        return Entry, entry, Entry._proxy_user(entry)

    def test_the_platform_administrator_inside_a_tenant_holds_the_right_and_can_use_it(self):
        _Entry, _entry, proxy = self._enter()
        self.assertTrue(proxy.nc_platform_proxy)
        self.assertTrue(proxy.has_group(MANAGER))
        self.record.with_user(proxy).write({'browser_title': 'Platform Console', 'primary_color': '#0B6E4F'})
        self.assertEqual(self.Brand.nc_brand()['title'], 'Platform Console')
        self.assertTrue(self.Brand.with_user(proxy).action_open())

    def test_a_returning_platform_administrator_gets_the_right_back_if_it_was_ever_removed(self):
        Entry, entry, proxy = self._enter()
        proxy.write({'group_ids': [Command.unlink(self.manager_group.id)]})
        self.assertFalse(proxy.has_group(MANAGER))
        again = Entry._proxy_user(entry)
        self.assertEqual(again, proxy, 'the same proxy user, refreshed')
        self.assertTrue(again.has_group(MANAGER))

    def test_the_right_the_proxy_holds_is_not_a_right_of_any_ordinary_user(self):
        _Entry, _entry, proxy = self._enter()
        holders = self.manager_group.with_context(active_test=False).user_ids
        self.assertEqual(holders, proxy, 'in a tenant database the platform proxy is the only holder')
        self.assertNotIn(self.owner, holders)
        self.assertNotIn(self.settings_admin, holders)
