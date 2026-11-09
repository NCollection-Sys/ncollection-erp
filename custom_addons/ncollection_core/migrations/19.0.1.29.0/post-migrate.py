# -*- coding: utf-8 -*-
"""Remove every Anomaly Alerts record that a plain module upgrade would leave behind.

The data files that defined them are gone from the manifest. Odoo deletes the non-``noupdate`` ones by itself, but the two
crons were ``noupdate`` and the menu, action, views and rules must not depend on that rule, so they are removed here
explicitly. Only records of THIS feature are named; the scheduler service user / group (a general non-interactive identity
used by the platform's administration service), the release gate, the audit modules and every other alert (security,
release-health) are untouched. Idempotent: a second run finds nothing.
"""
import logging

_logger = logging.getLogger(__name__)

_RECORDS = (
    ('ir.cron', ('cron_detect_anomalies', 'cron_send_alert_digest')),
    ('ir.ui.menu', ('menu_ncollection_alert',)),
    ('ir.actions.act_window', ('action_ncollection_alert',)),
    ('ir.ui.view', ('view_ncollection_alert_list', 'view_ncollection_alert_form', 'view_ncollection_alert_search')),
    ('ir.rule', ('rule_alert_sales', 'rule_alert_expense', 'rule_alert_attendance', 'rule_alert_stock')),
)


def migrate(cr, version):
    from odoo import api, SUPERUSER_ID
    env = api.Environment(cr, SUPERUSER_ID, {})
    removed = 0
    for model, names in _RECORDS:
        data = env['ir.model.data'].search([('module', '=', 'ncollection_core'), ('model', '=', model), ('name', 'in', list(names))])
        records = env[model].browse(data.mapped('res_id')).exists()
        if records:
            removed += len(records)
            records.unlink()
        data.unlink()
    # access rows of the removed model (the model itself is dropped by Odoo when no module defines it any more)
    cr.execute("""
        DELETE FROM ir_model_access WHERE model_id IN (SELECT id FROM ir_model WHERE model = 'ncollection.alert')""")
    _logger.info("Anomaly Alerts removal: %s record(s) removed (%s access row(s)).", removed, cr.rowcount)
