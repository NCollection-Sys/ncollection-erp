# -*- coding: utf-8 -*-
"""Tenant role definitions: existence, chains, escalation audit (P1-T08).

The escalation test enforces docs/ROLE_MATRIX.md: a role's transitive
implied-group closure must stay inside the matrix allowlist. Widening a
chain without updating the matrix (and this allowlist) fails CI.
"""

from odoo.tests import TransactionCase, tagged

from odoo.addons.ncollection_core.hooks import (
    ROLE_IMPLICATIONS,
    _sync_role_implications,
)

ROLE_XMLIDS = [
    'ncollection_core.group_role_employee',
    'ncollection_core.group_role_sales',
    'ncollection_core.group_role_warehouse',
    'ncollection_core.group_role_hr',
    'ncollection_core.group_role_accountant',
    'ncollection_core.group_role_manager',
    'ncollection_core.group_role_ceo',
    'ncollection_core.group_role_owner',
]

# ROLE_MATRIX.md §2, flattened: every group xml-id a role may transitively
# imply, beyond the NCollection role groups themselves and whatever the
# allowed targets themselves imply (Odoo core chains below base groups are
# trusted; we audit OUR outbound edges, not Odoo's internal ones).
MATRIX_ALLOWED_DIRECT = {
    'ncollection_core.group_role_employee': {'base.group_user'},
    # The scheduler service account (#347). NOT a human role — it is the
    # identity tenant crons run as, so Ring-2 licence enforcement applies to
    # scheduled work (a superuser cron bypasses the access machinery entirely).
    # READ grants only, and `_all_leads` rather than `group_sale_salesman`
    # because the latter carries an own-documents-only rule and a scheduler
    # owns nothing. Accounting is deliberately READ-ONLY.
    'ncollection_core.group_cron_service': {
        'sales_team.group_sale_salesman_all_leads',
        'stock.group_stock_user',
        'hr.group_hr_user',
        'account.group_account_readonly',
    },
    'ncollection_core.group_role_sales': {
        'ncollection_core.group_role_employee',
        'sales_team.group_sale_salesman',
    },
    'ncollection_core.group_role_warehouse': {
        'ncollection_core.group_role_employee',
        'stock.group_stock_user',
    },
    'ncollection_core.group_role_hr': {
        'ncollection_core.group_role_employee',
        'hr.group_hr_user',
    },
    'ncollection_core.group_role_accountant': {
        'ncollection_core.group_role_employee',
        'account.group_account_user',
    },
    'ncollection_core.group_role_manager': {
        'ncollection_core.group_role_employee',
        'sales_team.group_sale_salesman_all_leads',
        'stock.group_stock_user',
        'hr.group_hr_user',
    },
    'ncollection_core.group_role_ceo': {
        'ncollection_core.group_role_manager',
        'account.group_account_readonly',
    },
    'ncollection_core.group_role_owner': {
        'ncollection_core.group_role_ceo',
        'base.group_system',
        'account.group_account_user',
    },
}


@tagged("post_install", "-at_install")
class TestTenantRoles(TransactionCase):

    def _xmlid_of(self, group):
        data = self.env['ir.model.data'].search([
            ('model', '=', 'res.groups'), ('res_id', '=', group.id),
        ], limit=1)
        return f"{data.module}.{data.name}" if data else f"?.{group.id}"

    # ---------------- existence & base chains ----------------

    def test_all_roles_importable(self):
        """All 8 groups exist after install — on ANY tenant module set."""
        for xmlid in ROLE_XMLIDS:
            self.assertTrue(
                self.env.ref(xmlid, raise_if_not_found=False),
                f"{xmlid} must exist after module install",
            )

    def test_employee_implies_internal_user(self):
        employee = self.env.ref('ncollection_core.group_role_employee')
        self.assertIn(self.env.ref('base.group_user'), employee.implied_ids)

    def test_owner_implies_system(self):
        owner = self.env.ref('ncollection_core.group_role_owner')
        closure = owner.all_implied_ids
        self.assertIn(self.env.ref('base.group_system'), closure)

    def test_only_owner_reaches_system(self):
        system = self.env.ref('base.group_system')
        for xmlid in ROLE_XMLIDS:
            if xmlid.endswith('group_role_owner'):
                continue
            role = self.env.ref(xmlid)
            closure = role.all_implied_ids
            self.assertNotIn(
                system, closure,
                f"{xmlid} must NOT reach base.group_system (ROLE_MATRIX §5)",
            )

    def test_role_hierarchy_chain(self):
        """Owner ⊃ CEO ⊃ Manager ⊃ Employee."""
        owner = self.env.ref('ncollection_core.group_role_owner')
        closure = owner.all_implied_ids
        for xmlid in ('group_role_ceo', 'group_role_manager', 'group_role_employee'):
            self.assertIn(self.env.ref(f'ncollection_core.{xmlid}'), closure)

    # ---------------- escalation audit ----------------

    def test_no_unexpected_escalation(self):
        """Each role's DIRECT implied set ⊆ ROLE_MATRIX allowlist.

        Direct edges are what this addon controls; Odoo-internal chains
        below allowed targets are Odoo's responsibility, not audited here.
        """
        for role_xmlid, allowed in MATRIX_ALLOWED_DIRECT.items():
            role = self.env.ref(role_xmlid)
            for implied in role.implied_ids:
                implied_xmlid = self._xmlid_of(implied)
                self.assertIn(
                    implied_xmlid, allowed,
                    f"{role_xmlid} implies {implied_xmlid} which is NOT in "
                    f"docs/ROLE_MATRIX.md — update the matrix in the same PR "
                    f"or remove the implication",
                )

    def test_hook_mapping_matches_matrix(self):
        """hooks.ROLE_IMPLICATIONS must stay inside the matrix allowlist."""
        for role_xmlid, targets in ROLE_IMPLICATIONS.items():
            allowed = MATRIX_ALLOWED_DIRECT[role_xmlid]
            for target in targets:
                self.assertIn(
                    target, allowed,
                    f"hooks.ROLE_IMPLICATIONS[{role_xmlid}] contains {target} "
                    f"not allowed by ROLE_MATRIX.md",
                )

    # ---------------- runtime sync behaviour ----------------

    def test_sync_skips_absent_modules_gracefully(self):
        """Sync must never raise, whatever module set is installed."""
        result = _sync_role_implications(self.env)
        self.assertTrue(result, "sync must report per-role results")
        for role_xmlid, outcome in result.items():
            for target in outcome['linked']:
                self.assertTrue(self.env.ref(target, raise_if_not_found=False))
            for target in outcome['skipped']:
                self.assertFalse(self.env.ref(target, raise_if_not_found=False))

    def test_sync_idempotent(self):
        """Running sync twice yields identical implied sets."""
        _sync_role_implications(self.env)
        snapshot = {
            xmlid: set(self.env.ref(xmlid).implied_ids.ids) for xmlid in ROLE_XMLIDS
        }
        _sync_role_implications(self.env)
        after = {
            xmlid: set(self.env.ref(xmlid).implied_ids.ids) for xmlid in ROLE_XMLIDS
        }
        self.assertEqual(snapshot, after)

    def test_sync_links_installed_targets(self):
        """Every target whose group exists on this DB must end up linked."""
        result = _sync_role_implications(self.env)
        for role_xmlid, outcome in result.items():
            role = self.env.ref(role_xmlid)
            for target_xmlid in outcome['linked']:
                self.assertIn(
                    self.env.ref(target_xmlid), role.implied_ids,
                    f"{role_xmlid} must imply {target_xmlid} after sync",
                )
