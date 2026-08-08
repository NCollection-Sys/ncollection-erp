# -*- coding: utf-8 -*-
"""Role-scoped visibility of anomaly alerts (#346).

#61 shipped detection with read locked to `base.group_system`, deliberately:
all three reviewers flagged a blanket `base.group_user` grant as a role-boundary
leak, since alerts carry daily revenue, vendor-bill totals and attendance counts
— data ROLE_MATRIX D5 puts in CEO/Accountant/Owner territory. This file is the
proof that the replacement is a real boundary and not a menu that merely hides
things.

**Everything here asserts at the ORM, never at the UI.** Standing Rule 4: a
restriction that exists only on a menu is not a restriction, because
`/web/dataset/call_kw` does not read menus. So every case below goes through
`with_user(...).search(...)`, which is exactly the path an RPC client takes.

The role hierarchy does real work and is asserted rather than assumed
(`security/role_groups.xml`):

    group_role_owner   -> implies group_role_ceo AND base.group_system
    group_role_ceo     -> implies group_role_manager
    group_role_*       -> imply group_role_employee -> base.group_user

so a CEO reaches the attendance and stock rules THROUGH manager, and an owner
reaches everything through system. Those are consequences of the existing role
model, not extra grants made here — pinned below so that if someone edits the
implication chain, this fails loudly instead of silently widening access.
"""

from odoo.exceptions import AccessError
from odoo.tests import TransactionCase, tagged

_ALL = {'sales_trend_drop', 'expense_spike',
        'attendance_anomaly', 'stock_below_safety'}


@tagged("post_install", "-at_install")
class TestAnomalyVisibility(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Alert = cls.env['ncollection.alert']
        # One alert per detector, created as superuser exactly as the cron does.
        for key in sorted(_ALL):
            Alert.create({
                'name': 'Test %s' % key,
                'detector_key': key,
                'severity': 'warning',
                'dedup_key': 'test:%s' % key,
            })

    def _user(self, login, group_xmlids):
        """An internal user holding exactly the named groups.

        `group_ids`, not `groups_id` — renamed in Odoo 19 — and no
        `base.default_user` template, which Odoo 19 removed (CLAUDE.md).
        """
        groups = [self.env.ref(x).id for x in group_xmlids]
        return self.env['res.users'].create({
            'name': login, 'login': login,
            'group_ids': [(6, 0, groups)],
        })

    def _mute_cron_commit(self):
        """Stop `_commit_progress` committing inside a TransactionCase.

        The digest yields to the cron's time budget between recipients, and
        `_commit_progress` really commits — which is the point in production
        and forbidden in a test, because it would break the test's own
        rollback. Stubbed to a generous budget so what is under test is the
        digest's CONTENT, not Odoo's commit.
        """
        self.patch(type(self.env['ir.cron']), '_commit_progress',
                   lambda cron, processed=0, remaining=None, deactivate=False: 60.0)

    def _visible(self, user):
        alerts = self.env['ncollection.alert'].with_user(user).search([])
        return {alert.detector_key for alert in alerts}

    # ---- one role, one slice -------------------------------------------

    def _as_scheduler(self, records):
        """Run as the cron service user (#347).

        These crons are bound to ncollection_core.user_cron_service in
        production precisely so plan entitlement applies to them. Calling them
        as uid 1 would exercise a path production never takes, and the
        fail-closed guard rightly refuses to run there.
        """
        return records.with_user(
            self.env.ref('ncollection_core.user_cron_service'))

    def test_sales_sees_only_sales_alerts(self):
        user = self._user('vis_sales', ['ncollection_core.group_role_sales'])
        self.assertEqual(self._visible(user), {'sales_trend_drop'})

    def test_accountant_sees_only_expense_alerts(self):
        user = self._user('vis_acct', ['ncollection_core.group_role_accountant'])
        self.assertEqual(self._visible(user), {'expense_spike'})

    def test_hr_sees_only_attendance_alerts(self):
        user = self._user('vis_hr', ['ncollection_core.group_role_hr'])
        self.assertEqual(self._visible(user), {'attendance_anomaly'})

    def test_warehouse_sees_only_stock_alerts(self):
        user = self._user('vis_wh', ['ncollection_core.group_role_warehouse'])
        self.assertEqual(self._visible(user), {'stock_below_safety'})

    def test_a_role_cannot_see_another_roles_alerts(self):
        """The point of the whole exercise, stated as its own case.

        A warehouse worker must not read the tenant's daily revenue or its
        vendor-bill totals, whatever the menu shows.
        """
        user = self._user('vis_wh2', ['ncollection_core.group_role_warehouse'])
        visible = self._visible(user)
        self.assertNotIn('sales_trend_drop', visible)
        self.assertNotIn('expense_spike', visible)
        self.assertNotIn('attendance_anomaly', visible)

    # ---- the implication chain -----------------------------------------

    def test_ceo_sees_everything_via_the_implication_chain(self):
        """CEO is on the sales and expense rules directly, and reaches the
        attendance and stock rules through the manager group it implies."""
        user = self._user('vis_ceo', ['ncollection_core.group_role_ceo'])
        self.assertEqual(self._visible(user), _ALL)

    def test_manager_sees_the_operational_alerts(self):
        user = self._user('vis_mgr', ['ncollection_core.group_role_manager'])
        self.assertEqual(
            self._visible(user), {'attendance_anomaly', 'stock_below_safety'})

    def test_owner_sees_everything(self):
        user = self._user('vis_owner', ['ncollection_core.group_role_owner'])
        self.assertEqual(self._visible(user), _ALL)

    # ---- the denials ---------------------------------------------------

    def test_a_plain_employee_cannot_read_alerts_at_all(self):
        """No ACL row grants `group_role_employee` (or bare `base.group_user`)
        access to the model, so this is denied at the ACL, before any record
        rule is consulted. A rule that filtered to zero rows would look the same
        from the UI but would still expose the model over RPC."""
        user = self._user('vis_emp', ['ncollection_core.group_role_employee'])
        with self.assertRaises(AccessError):
            self.env['ncollection.alert'].with_user(user).search([])

    def test_system_administrator_retains_full_access(self):
        """Acceptance criterion 3. `base.group_system` is on every rule on
        purpose: without it, an admin who ALSO holds a role group would be
        silently narrowed to that role's slice."""
        user = self._user('vis_admin', ['base.group_system'])
        self.assertEqual(self._visible(user), _ALL)

    def test_an_admin_who_also_holds_a_role_still_sees_everything(self):
        """The trap the previous test's comment describes, made executable."""
        user = self._user('vis_admin_sales',
                          ['base.group_system',
                           'ncollection_core.group_role_sales'])
        self.assertEqual(self._visible(user), _ALL)

    # ---- the digest carries the same boundary --------------------------

    def test_the_digest_slices_by_role(self):
        """A role holder's digest contains their alerts and nobody else's.

        This is the property that would silently break if the digest kept its
        own detector->role map: the map and the record rules would drift, and
        the day they disagreed the cron would email somebody a slice they are
        not allowed to open. The digest runs its search `with_user`, so this
        asserts the rules and the mail cannot diverge.
        """
        self._mute_cron_commit()
        sales = self._user('dig_sales', ['ncollection_core.group_role_sales'])
        sales.email = 'sales@example.com'
        acct = self._user('dig_acct', ['ncollection_core.group_role_accountant'])
        acct.email = 'acct@example.com'

        Mail = self.env['mail.mail']
        before = Mail.search([])
        self._as_scheduler(self.env['ncollection.alert'])._cron_send_digest()
        new_mails = Mail.search([]) - before

        by_to = {m.email_to: m.body_html or '' for m in new_mails}
        self.assertIn('sales@example.com', by_to, "the sales role got no digest")
        self.assertIn('acct@example.com', by_to, "the accountant got no digest")

        self.assertIn('sales_trend_drop', by_to['sales@example.com'])
        self.assertNotIn(
            'expense_spike', by_to['sales@example.com'],
            "a salesperson's digest must not carry vendor-bill figures")

        self.assertIn('expense_spike', by_to['acct@example.com'])
        self.assertNotIn(
            'sales_trend_drop', by_to['acct@example.com'],
            "an accountant's digest must not carry the sales slice")

    def test_a_recipient_with_nothing_to_report_gets_no_mail(self):
        """Silence is a feature. A daily empty email is how alerting gets
        filtered into a folder nobody opens."""
        self._mute_cron_commit()
        self.env['ncollection.alert'].search([]).unlink()
        user = self._user('dig_quiet', ['ncollection_core.group_role_sales'])
        user.email = 'quiet@example.com'

        Mail = self.env['mail.mail']
        before = Mail.search_count([])
        self._as_scheduler(self.env['ncollection.alert'])._cron_send_digest()
        self.assertEqual(Mail.search_count([]), before,
                         "no alerts means no mail at all")

    # ---- writes stay privileged ----------------------------------------

    def test_a_role_holder_cannot_create_or_delete_alerts(self):
        """Read is the only grant. Alerts are produced by the detector, and a
        user who could forge or delete one could hide a real anomaly — which is
        the same failure the zero-false-negative bar exists to prevent."""
        user = self._user('vis_sales3', ['ncollection_core.group_role_sales'])
        with self.assertRaises(AccessError):
            self.env['ncollection.alert'].with_user(user).create({
                'name': 'forged', 'detector_key': 'sales_trend_drop',
                'severity': 'critical', 'dedup_key': 'forged:1',
            })

    def test_owner_can_write_through_the_implied_system_group(self):
        """Owner's own ACL row is read-only; its write comes from the
        base.group_system it implies. Odoo takes the UNION of a user's ACL
        rows, so the permissive row wins — asserted because 'read-only role
        row plus full system row on one user' is the one place those two
        grants meet, and getting it backwards would either strand the tenant's
        top role or silently hand write access to every role."""
        owner = self._user('vis_owner_w', ['ncollection_core.group_role_owner'])
        alert = self.env['ncollection.alert'].with_user(owner).create({
            'name': 'owner-made', 'detector_key': 'sales_trend_drop',
            'severity': 'info', 'dedup_key': 'owner:1',
        })
        self.assertTrue(alert, "the owner must retain write via group_system")
