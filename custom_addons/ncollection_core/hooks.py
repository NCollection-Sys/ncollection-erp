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
}


# THE SCHEDULER'S READ ACCESS (#347), as explicit per-model ACL rows rather
# than group implications.
#
# The first attempt granted group_cron_service the app-user groups
# (sales_team.group_sale_salesman_all_leads, stock.group_stock_user,
# hr.group_hr_user) under a comment saying "READ grants only". A reviewer
# checked that against Odoo's shipped CSVs and it was FALSE: those groups carry
# write/create on sale.order and crm.lead, and UNLINK on stock lots, packages
# and hr.employee. A non-interactive scheduler held the delete rights of three
# entire apps behind a comment asserting the opposite.
#
# These are the five models the detectors actually read — nothing else — and
# each row is read-only (1,0,0,0). Same discipline as group_config_sync, which
# is scoped to one model's writes and says so truthfully.
#
# Created in code, not in ir.model.access.csv, because a CSV row cannot be
# conditional: it references model_<module>_<model>, and a tenant without `hr`
# or `stock` would fail to install on a missing xmlid. This mirrors why
# ROLE_IMPLICATIONS exists.
SCHEDULER_READ_MODELS = (
    'sale.order',
    'account.move',
    'hr.attendance',
    'stock.quant',
    'stock.warehouse.orderpoint',
)


def _sync_scheduler_read_access(env):
    """Grant group_cron_service read-only ACLs on the detector models present.

    Idempotent, and skips silently for models this database does not have.
    Returns {'granted': [...], 'skipped': [...]} so tests can assert on it.
    """
    group = env.ref('ncollection_core.group_cron_service',
                    raise_if_not_found=False)
    if not group:
        return {'granted': [], 'skipped': list(SCHEDULER_READ_MODELS)}

    Access = env['ir.model.access'].sudo()
    granted, skipped = [], []
    for model_name in SCHEDULER_READ_MODELS:
        model = env['ir.model'].sudo().search(
            [('model', '=', model_name)], limit=1)
        if not model:
            skipped.append(model_name)      # module not installed here
            continue
        existing = Access.search([
            ('model_id', '=', model.id), ('group_id', '=', group.id)], limit=1)
        values = {
            'name': 'ncollection.scheduler.read.%s' % model_name,
            'model_id': model.id,
            'group_id': group.id,
            'perm_read': True,
            'perm_write': False,
            'perm_create': False,
            'perm_unlink': False,
        }
        if existing:
            existing.write(values)
        else:
            Access.create(values)
        granted.append(model_name)
    _logger.info("#347 scheduler read access: granted=%s skipped=%s",
                 granted, skipped)
    return {'granted': granted, 'skipped': skipped}


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
    _sync_scheduler_read_access(env)
