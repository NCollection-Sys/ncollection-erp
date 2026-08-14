# -*- coding: utf-8 -*-
"""F4-T03 review round: the properties three reviewers proved were NOT held.

Each class here exists because a claim this module made in prose turned out to
be false, or true only by luck. Every one of these fails against the code as it
was written before the review.
"""
from datetime import date
from unittest.mock import patch

from odoo.exceptions import AccessError, UserError
from odoo.tests import tagged

from odoo.addons.ncollection_account_assets.models import asset as nc_asset
from odoo.addons.ncollection_account_assets.wizard import asset_transfer

from .common import AssetCommon


@tagged('post_install', '-at_install')
class TestTransferExemptionIsNotForgeable(AssetCommon):
    """THE CRITICAL, found independently by two reviewers.

    The exemption from OCA's `_check_profile_change` was originally keyed on
    `self.env.context['nc_asset_transfer_ids']`, on the reasoning that naming
    exact record ids beat a bare boolean. Narrower — but not safer, because
    `odoo/service/model.py:call_kw` applies the CALLER's context with no
    allowlist:

        context = kwargs.pop('context', None) or {}
        recs = recs.with_context(context)

    So one RPC call disabled a data-integrity constraint, with no wizard, no
    reclassification entry, and no manager rights — OCA grants `account.asset`
    write to `group_account_user` and `group_account_invoice`, both well below
    the transfer wizard's manager-only gate.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.asset = cls._asset()
        cls._post_board_through(cls.asset, date(2026, 12, 31))

    def test_a_forged_context_key_cannot_disable_the_guard(self):
        """The exact shape of the RPC that used to work.

        `with_context()` is what `call_kw` does with a caller-supplied context,
        so this IS the remote call, minus the transport.
        """
        for forged in ({'nc_asset_transfer_ids': (self.asset.id,)},
                       {'nc_asset_transfer_ids': [self.asset.id]},
                       {'nc_asset_transfer_ids': True},
                       {'nc_asset_transfer': True}):
            with self.assertRaises(
                    UserError,
                    msg="context %r disabled the profile guard" % forged):
                self.asset.with_context(**forged).write(
                    {'profile_id': self.profile_veh.id})

    def test_an_ordinary_accounting_user_cannot_forge_it_either(self):
        """The privilege half. OCA's own ACL gives this role full write on
        `account.asset`, so the bypass was reachable below the wizard's gate."""
        clerk = self.env['res.users'].create({
            'name': 'Invoicing Clerk', 'login': 'nc_asset_clerk',
            'group_ids': [(6, 0, [
                self.env.ref('account.group_account_user').id,
                self.env.ref('base.group_user').id])],
        })
        self.assertTrue(
            self.env['account.asset'].with_user(clerk).check_access_rights(
                'write', raise_exception=False),
            "this role can no longer write account.asset, so this test no "
            "longer exercises the privilege it was written for")
        with self.assertRaises(UserError):
            self.asset.with_user(clerk).with_context(
                nc_asset_transfer_ids=(self.asset.id,)).write(
                    {'profile_id': self.profile_veh.id})

    def test_the_scope_is_released_even_when_the_write_raises(self):
        """A leaked scope would re-open the hole for the NEXT caller on this
        thread — the failure mode a thread-local trades for the forgeable one,
        and the reason the scope restores in `finally`."""
        with self.assertRaises(ValueError):
            with nc_asset.nc_transfer_scope((self.asset.id,)):
                raise ValueError("boom")
        self.assertEqual(nc_asset._nc_transferring_ids(), frozenset(),
                         "the transfer scope leaked past its block")
        with self.assertRaises(UserError):
            self.asset.write({'profile_id': self.profile_veh.id})

    def test_the_scope_nests_without_disarming_the_outer_one(self):
        other = self._asset(name='Nested')
        with nc_asset.nc_transfer_scope((self.asset.id,)):
            with nc_asset.nc_transfer_scope((other.id,)):
                self.assertEqual(nc_asset._nc_transferring_ids(),
                                 frozenset({self.asset.id, other.id}))
            self.assertEqual(nc_asset._nc_transferring_ids(),
                             frozenset({self.asset.id}))
        self.assertEqual(nc_asset._nc_transferring_ids(), frozenset())


@tagged('post_install', '-at_install')
class TestTransferIsAtomic(AssetCommon):
    """The reclassification entry is posted BEFORE the asset is written.

    Safe in the normal path only because Odoo wraps a request in one
    transaction — an assumption this module stated in a comment and never
    tested. If the write raises, the posted move must not survive, or the GL
    describes a transfer that did not happen.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.asset = cls._asset()
        cls._post_board_through(cls.asset, date(2026, 12, 31))

    def test_a_failed_write_leaves_no_orphan_reclassification_entry(self):
        before = self.env['account.move'].search_count([])
        wizard = self.Transfer.create({
            'asset_id': self.asset.id, 'date': date(2027, 1, 1),
            'profile_id': self.profile_veh.id,
        })
        # Fail AFTER the move is posted and BEFORE the profile lands — the one
        # window where the two halves can disagree.
        with self.assertRaises(UserError), self.env.cr.savepoint():
            with patch.object(type(self.asset), 'write',
                              side_effect=UserError("write refused")):
                wizard.action_transfer()
        self.assertEqual(
            self.env['account.move'].search_count([]), before,
            "a reclassification entry survived a failed transfer, so the GL "
            "records a move the asset never made")
        self.assertEqual(self.asset.profile_id, self.profile_it)


@tagged('post_install', '-at_install')
class TestTransferCompanyBoundary(AssetCommon):
    """A transfer moves an asset between ACCOUNTS, never between COMPANIES.

    Odoo's `check_company=True` already blocks most of this on the
    reclassification path — but an analytic-only transfer posts no move and
    touches none of it. Borrowed protection the module neither states nor tests
    is what this repo's regression ledger is made of.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.asset = cls._asset()
        cls.other_company = cls.env['res.company'].create({'name': 'NC Other'})

    def test_an_asset_outside_the_users_companies_is_refused(self):
        """Narrow the user's allowed companies rather than move the asset:
        moving it would trip Odoo's own company-consistency error on the
        profile first, and prove that instead of this guard.

        The MESSAGE is asserted, not just the exception type. `UserError` alone
        does NOT discriminate — Odoo's own `check_company` raises the same class
        for the same scenario, so the first version of this test passed with the
        guard deleted. Found by the RED proof, not by review.
        """
        wizard = self.Transfer.create({
            'asset_id': self.asset.id, 'date': date(2027, 1, 1),
            'profile_id': self.profile_veh.id,
        })
        scoped = wizard.with_context(
            allowed_company_ids=[self.other_company.id])
        self.assertNotIn(self.asset.company_id, scoped.env.companies,
                         "the fixture did not actually narrow the companies")
        with self.assertRaises(UserError) as caught:
            scoped.action_transfer()
        self.assertIn("not among your allowed companies", str(caught.exception))

    def test_an_ANALYTIC_ONLY_transfer_is_stopped_by_OCA_s_record_rule(self):
        """The layer that actually enforces this — measured, not assumed.

        The review flagged analytic-only transfers as the gap Odoo's
        `check_company` cannot cover: no new profile means no move and no
        `profile_id` write, so nothing type-checks the company. That much is
        true. The premise that FOLLOWED from it was not: this test was written
        expecting `_nc_assert_company` to be the thing that refuses, and it is
        not — `account_asset_management`'s own global `ir.rule`
        (`company_id in company_ids`) denies the READ first, so the wizard
        cannot so much as look at the asset.

        Kept, and renamed to say so. "Borrowed protection the module neither
        states nor tests" was the actual finding, and this is the test that
        stops it being borrowed silently: if a future OCA pin drops or weakens
        that rule, this fails rather than the boundary quietly disappearing.
        `_nc_assert_company` remains as defence in depth and for a legible
        message, not as the only lock.
        """
        plan = self.env['account.analytic.plan'].create({'name': 'Dept X'})
        centre = self.env['account.analytic.account'].create(
            {'name': 'Theirs', 'plan_id': plan.id})
        wizard = self.Transfer.create({
            'asset_id': self.asset.id, 'date': date(2027, 1, 1),
            'analytic_distribution': {str(centre.id): 100.0},
        })
        scoped = wizard.with_context(
            allowed_company_ids=[self.other_company.id])
        with self.assertRaises(AccessError):
            scoped.action_transfer()

    def test_a_journal_from_another_company_is_refused(self):
        foreign = self.env['account.journal'].sudo().create({
            'name': 'Their Misc', 'code': 'TMSC', 'type': 'general',
            'company_id': self.other_company.id,
        })
        wizard = self.Transfer.create({
            'asset_id': self.asset.id, 'date': date(2027, 1, 1),
            'profile_id': self.profile_veh.id,
        })
        wizard.journal_id = foreign
        with self.assertRaises(UserError) as caught:
            wizard.action_transfer()
        self.assertIn("but the asset belongs to", str(caught.exception))


@tagged('post_install', '-at_install')
class TestSharedGlPredicate(AssetCommon):
    """`_nc_is_in_gl` has ONE definition. These assert the two callers still
    reach it, so a future edit cannot re-fork them silently."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.asset = cls._asset()
        cls._post_board_through(cls.asset, date(2027, 12, 31))

    def test_the_register_and_the_transfer_agree_under_matching_parameters(self):
        report = self.Register.create({
            'date_from': date(2020, 1, 1), 'date_to': date(2030, 12, 31),
            'target_move': 'posted'})
        wizard = self.Transfer.create(
            {'asset_id': self.asset.id, 'date': date(2030, 12, 31)})
        register_view = [line for line in self.asset.depreciation_line_ids
                         if report._nc_line_counts(line)]
        self.assertTrue(register_view, "nothing counted; this proves nothing")
        self.assertAlmostEqual(
            sum(line.amount for line in register_view),
            wizard._nc_accumulated(), 2,
            "the register and the transfer disagree about what is in the GL")

    def test_target_move_all_is_a_deliberate_difference_not_a_drift(self):
        """The transfer passes 'posted' and never 'all' — a reclassification
        may only move value the GL has recognised. Asserted so the asymmetry
        reads as a decision."""
        line = self.asset.depreciation_line_ids.filtered(
            lambda dep: dep.move_id)[:1]
        self.assertTrue(line)
        line.move_id.button_draft()
        self.assertFalse(line._nc_is_in_gl('posted'))
        self.assertTrue(line._nc_is_in_gl('all'))


@tagged('post_install', '-at_install')
class TestProfileDerivedDiscoveryIsTransitive(AssetCommon):
    """The discovery walks a transitive closure, not one level.

    `method_end` is compute+store+writable and declares
    `@api.depends("method_number")` — it never names `profile_id`, yet
    `method_number` is profile-derived. A one-level scan missed it, and it was
    harmless only because `_compute_method_end` no-ops when `method_number` is
    set. That is luck in the fixtures, not a property of the code.
    """

    def test_method_end_is_discovered_through_method_number(self):
        wizard = self.Transfer.create(
            {'asset_id': self._asset().id, 'date': date(2027, 1, 1)})
        derived = wizard._nc_profile_derived_fields()
        self.assertIn('method_number', derived, "the root is missing")
        self.assertIn('method_end', derived,
                      "the discovery is one-level: method_end depends on "
                      "method_number, which is profile-derived")

    def test_a_callable_depends_is_skipped_rather_than_crashed_on(self):
        """`@api.depends` also accepts a single callable for dynamic
        dependency lists. No `account.asset` field uses that form today — which
        is exactly why the branch is tested directly rather than through the
        model: `tuple(<function>)` raises TypeError, and that would break EVERY
        transfer, not just the one new field."""
        def compute_with_tuple_depends():
            pass

        def compute_with_callable_depends():
            pass

        compute_with_tuple_depends._depends = ('profile_id', 'method_number.x')
        compute_with_callable_depends._depends = lambda env: ('profile_id',)

        self.assertEqual(
            asset_transfer._nc_depends_roots(compute_with_tuple_depends),
            {'profile_id', 'method_number'})
        self.assertEqual(
            asset_transfer._nc_depends_roots(compute_with_callable_depends),
            set(), "a callable @api.depends was not skipped, so the discovery "
                   "raises TypeError and every transfer fails")
        self.assertEqual(asset_transfer._nc_depends_roots(object()), set())


@tagged('post_install', '-at_install')
class TestOcaCronStaysInactive(AssetCommon):
    """`account_asset_management` ships `ir_cron_assets_generator`, and its work
    is UNBOUNDED: `asset_compute()` does `search([('state', '=', 'open')])` with
    no batch limit and no time-box, then posts an entry for every result.

    `config/odoo.prod.conf` runs `max_cron_threads = 1` with
    `limit_time_real_cron = 3600` — ONE shared cron thread serving every tenant
    database on the node, sequentially. An unbounded job there blocks every
    other tenant's crons (license enforcement, config-sync reconcile) for up to
    an hour. That is the exact class of defect #310 fixed for the ECB fetch, and
    `ARCHITECTURE_DATA_PLATFORM.md`'s cron-hygiene invariant forbids it:
    anything over 30s belongs on the queue runner.

    It ships `active=False`, which is why this is a live guard rather than a
    live incident. This test is what keeps that true across an OCA pin bump —
    a `noupdate="1"` record will not be re-activated by an upgrade, but a
    future pin could change the shipped default and nothing else would notice.

    Making it SAFE to enable — a batched, tenant-scoped job on the queue runner
    — is deliberately out of #122's scope and filed separately, so that
    "ships inactive" cannot quietly read as "handled".
    """

    def test_the_generator_cron_is_shipped_inactive(self):
        cron = self.env.ref(
            'account_asset_management.ir_cron_assets_generator',
            raise_if_not_found=False)
        self.assertTrue(cron, "the OCA asset cron is gone — if the pin dropped "
                              "it, delete this test; if it was renamed, this "
                              "guard is now checking nothing")
        self.assertFalse(
            cron.active,
            "the OCA asset-generation cron is ACTIVE. It computes every open "
            "asset with no batch limit on a node whose max_cron_threads is 1, "
            "which starves every other tenant's crons (#310). It must stay "
            "inactive until a batched, queue-runner version exists.")
