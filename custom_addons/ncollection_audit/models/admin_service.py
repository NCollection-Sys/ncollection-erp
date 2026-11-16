"""The platform's READ-ONLY window on this workspace's general Audit Log (OCA ``auditlog``).

Reached exactly like Tenant Administration: the platform calls ``ncollection.admin.service.list_audit_logs`` over
JSON-2 with this tenant's derived bearer (ncollection_core/models/admin_service.py: service account only, fixed request keys,
the platform administrator named in ``actor``). The platform never reads this database directly.

What leaves the workspace: event METADATA only (when, who, which model and record, which operation, how many fields
changed). Never the old / new VALUES: they can hold personal or financial data, and the field-level detail stays
inside the workspace for its own entitled users. Bounded: at most 100 rows per call, a date window, offset paging.
If this module is not installed the method does not exist, and the platform says the Audit Log is not available on
that workspace (it never invents an empty history).
"""
import logging
from datetime import datetime

from odoo import api, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)
_MAX_ROWS = 100
_KEYS = {'actor', 'offset', 'limit', 'model', 'date_from', 'date_to'}


def _date(value, name):
    if value in (None, '', False):
        return None
    try:
        return datetime.strptime(str(value), '%Y-%m-%d')
    except ValueError:
        raise UserError(f"Malformed request ({name}).") from None


class AdminService(models.AbstractModel):
    _inherit = 'ncollection.admin.service'

    @api.model
    def _nc_service_keys(self):
        keys = super()._nc_service_keys()
        keys['list_audit_logs'] = _KEYS
        return keys

    @api.model
    def list_audit_logs(self, vals):
        env = self._service_only('list_audit_logs', vals)
        offset, limit = vals.get('offset', 0), vals.get('limit', 50)
        if (isinstance(offset, bool) or not isinstance(offset, int) or offset < 0
                or isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= _MAX_ROWS):
            raise UserError(self.env._("Malformed request."))
        model = vals.get('model') or ''
        if model and (not isinstance(model, str) or len(model) > 128 or not model.replace('.', '').replace('_', '')
                      .isalnum()):
            raise UserError(self.env._("Malformed request."))
        date_from, date_to = _date(vals.get('date_from'), 'date_from'), _date(vals.get('date_to'), 'date_to')
        domain = []
        if model:
            domain.append(('model_model', '=', model))
        if date_from:
            domain.append(('create_date', '>=', date_from))
        if date_to:
            domain.append(('create_date', '<', date_to.replace(hour=23, minute=59, second=59)))
        Log = env['auditlog.log']
        total = Log.search_count(domain)
        logs = Log.search(domain, offset=offset, limit=limit, order='create_date desc, id desc')
        rules = env['auditlog.rule'].search_count([('state', '=', 'subscribed')])
        _logger.info("Audit Log read by the platform: %s, %d rows (offset %d, model %r)",
                     env.context.get('nc_platform_actor'), len(logs), offset, model)
        return {
            'available': True, 'subscribed_rules': rules, 'total': total,
            'rows': [{
                'id': log.id, 'date': log.create_date.isoformat(sep=' ', timespec='seconds'),
                'user': log.user_id.name or '', 'model': log.model_model or '', 'model_name': log.model_name or '',
                'res_id': log.res_id or 0, 'record': (log.name or '')[:120], 'method': log.method or '',
                'changes': len(log.line_ids),
            } for log in logs],
        }
