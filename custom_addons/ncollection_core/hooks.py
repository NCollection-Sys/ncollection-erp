# -*- coding: utf-8 -*-
"""Runtime linking of cross-module role implications (P1-T08).

Why not plain XML ``implied_ids``: provisioning installs ONLY the plan's
modules (ARCHITECTURE_SECURITY §4, Ring 3), so groups like
``sales_team.group_sale_salesman`` may not exist on a given tenant DB and a
hard XML reference would break module installation. This helper resolves
target groups by XML-ID at runtime, links what exists, and silently skips
what doesn't.

Contract: re-run ``_sync_role_implications`` after any module
install/upgrade on the tenant (wired through provisioning P2-T01 and config
sync P2-T03). Idempotent — safe to call any number of times.

The authoritative human-readable matrix lives in docs/ROLE_MATRIX.md; the
mapping below and that document MUST change together (enforced by the
transitive-closure test in tests/test_roles.py).
"""

import logging

_logger = logging.getLogger(__name__)

# role xml-id -> cross-module group xml-ids to imply (only linked when the
# target group's module is installed on this database).
ROLE_IMPLICATIONS = {
    'ncollection_core.group_role_sales': [
        'sales_team.group_sale_salesman',
    ],
    'ncollection_core.group_role_warehouse': [
        'stock.group_stock_user',
    ],
    'ncollection_core.group_role_hr': [
        'hr.group_hr_user',
    ],
    'ncollection_core.group_role_accountant': [
        'account.group_account_user',
    ],
    'ncollection_core.group_role_manager': [
        # Department-level: all-documents visibility in operational modules.
        'sales_team.group_sale_salesman_all_leads',
        'stock.group_stock_user',
        'hr.group_hr_user',
    ],
    'ncollection_core.group_role_ceo': [
        # Financials strictly READ-ONLY at executive level.
        'account.group_account_readonly',
    ],
    'ncollection_core.group_role_owner': [
        # Owner overrides CEO's read-only with full accounting access.
        'account.group_account_user',
    ],
    # THE SCHEDULER (#347). Binding crons to a non-superuser identity is what
    # makes Ring-2 licence enforcement apply to them — but a plain
    # base.group_user can read none of the models the detectors query, so the
    # fix would close the bypass by silently killing the feature: every
    # detection run returning nothing, on every plan, with correct and broken
    # looking identical. Caught in review.
    #
    # These grants are the READ side only, and the plan still gates them: Ring 2
    # denies a blocked namespace to this user exactly as it does to a human.
    # Licensing stays inherited, never re-implemented.
    #
    # Reusing this table rather than a second mechanism matters, because it is
    # already conditional on the target module being installed — a tenant
    # without `hr` must not fail to install over a missing group xmlid.
    'ncollection_core.group_cron_service': [
        # ALL documents, not `group_sale_salesman`: that carries an
        # own-documents-only record rule, and a scheduler owns nothing, so it
        # would see an empty tenant and report no anomalies.
        'sales_team.group_sale_salesman_all_leads',
        'stock.group_stock_user',
        'hr.group_hr_user',
        # READ-ONLY. A detector reads accounting; it never writes it.
        'account.group_account_readonly',
    ],
}


def _sync_role_implications(env):
    """Link cross-module implied_ids for whatever target groups exist.

    Returns a dict {role_xmlid: {'linked': [...], 'skipped': [...]}} so
    callers (and tests) can assert exactly what happened.
    """
    result = {}
    for role_xmlid, target_xmlids in ROLE_IMPLICATIONS.items():
        role = env.ref(role_xmlid, raise_if_not_found=False)
        if not role:  # role data not loaded yet — nothing to do
            _logger.warning("Role %s not found; skipping its implications", role_xmlid)
            continue
        linked, skipped = [], []
        for target_xmlid in target_xmlids:
            target = env.ref(target_xmlid, raise_if_not_found=False)
            if not target:
                skipped.append(target_xmlid)
                continue
            if target not in role.implied_ids:
                role.write({'implied_ids': [(4, target.id)]})
            linked.append(target_xmlid)
        result[role_xmlid] = {'linked': linked, 'skipped': skipped}
        if skipped:
            _logger.info(
                "Role %s: linked %s, skipped (module not installed) %s",
                role_xmlid, linked, skipped,
            )
    return result


def post_init_hook(env):
    _sync_role_implications(env)
