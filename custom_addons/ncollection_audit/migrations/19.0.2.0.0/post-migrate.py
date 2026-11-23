"""19.0.2.0.0 (Audit Log 2.0): classify the EXISTING events, never invent anything.

* ``nc_action`` from the recorded method (create / write / unlink) - a fact already in each row;
* ``nc_doc_type`` = the recorded model (the finer invoice / bill split is not guessed for old rows);
* ``nc_company_id`` from the document's company ONLY where the document still exists; an event whose document is
  gone keeps no company (it is not assigned to a company it may never have belonged to);
* ``capture_record`` on the rules this module seeded, so deletions from now on keep their pre-deletion values.

Timestamps, users, values and the content digests are not touched (the digest does not cover these columns, so every
old row still verifies). Set-based SQL, safe to run again: each UPDATE only fills what is still empty.
"""
import logging

from odoo import SUPERUSER_ID, api
from odoo.tools import SQL

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return
    # ONLY the events that existed before 2.0 (the ones nothing has classified yet). A re-run, or events written by
    # 2.0 itself, are never touched: their company is the one recorded at the time, not today's.
    cr.execute("DROP TABLE IF EXISTS nc_audit_legacy")
    cr.execute("CREATE TEMP TABLE nc_audit_legacy AS SELECT id FROM auditlog_log WHERE nc_action IS NULL")
    cr.execute("""
        UPDATE auditlog_log
           SET nc_action = CASE method WHEN 'create' THEN 'create' WHEN 'write' THEN 'update'
                                       WHEN 'unlink' THEN 'delete' ELSE 'other' END
         WHERE nc_action IS NULL AND id IN (SELECT id FROM nc_audit_legacy)""")
    actions = cr.rowcount
    cr.execute("""UPDATE auditlog_log SET nc_doc_type = model_model
                   WHERE nc_doc_type IS NULL AND model_model IS NOT NULL
                     AND id IN (SELECT id FROM nc_audit_legacy)""")
    cr.execute("""UPDATE auditlog_log SET nc_source = 'oca'
                   WHERE nc_source IS NULL AND id IN (SELECT id FROM nc_audit_legacy)""")
    cr.execute("""
        SELECT DISTINCT l.model_model, m.model
          FROM auditlog_log l
          JOIN ir_model m ON m.model = l.model_model
          JOIN ir_model_fields f ON f.model_id = m.id AND f.name = 'company_id' AND f.ttype = 'many2one'
                                AND f.relation = 'res.company' AND f.store
         WHERE l.nc_company_id IS NULL AND l.id IN (SELECT id FROM nc_audit_legacy)""")
    env = api.Environment(cr, SUPERUSER_ID, {})
    companies = 0
    for model_name, _model in cr.fetchall():
        if model_name not in env or not env[model_name]._auto:
            continue
        table = env[model_name]._table
        cr.execute(SQL("""
            UPDATE auditlog_log l SET nc_company_id = t.company_id
              FROM %s t
             WHERE l.model_model = %s AND l.res_id = t.id AND l.nc_company_id IS NULL AND t.company_id IS NOT NULL
               AND l.id IN (SELECT id FROM nc_audit_legacy)
        """, SQL.identifier(table), model_name))
        companies += cr.rowcount
    cr.execute("""
        UPDATE auditlog_log l SET nc_company_id = l.res_id
         WHERE l.model_model = 'res.company' AND l.nc_company_id IS NULL AND l.id IN (SELECT id FROM nc_audit_legacy)
           AND EXISTS (SELECT 1 FROM res_company c WHERE c.id = l.res_id)""")
    companies += cr.rowcount
    cr.execute("UPDATE auditlog_rule SET capture_record = TRUE WHERE name LIKE 'NCollection audit:%%' "
               "AND capture_record IS NOT TRUE")
    _logger.info("ncollection_audit 2.0: %s events classified, %s given their document's company", actions, companies)
    langs = [code for code, _name in env['res.lang'].get_installed() if code != 'en_US']
    if langs:
        env['ir.module.module'].search([('name', '=', 'ncollection_audit')])._update_translations(
            langs, overwrite=False)
