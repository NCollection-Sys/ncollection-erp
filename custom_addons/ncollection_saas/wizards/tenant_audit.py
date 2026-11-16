"""A tenant's general Audit Log as seen from the platform: a read-only snapshot of event METADATA, fetched over the
tenant administration channel (models/tenant_admin.py ``_nc_admin_call``) with the same two checks as Users
(Platform Administrator AND on this tenant's Platform Access list). Never field values, never a direct database read.
Each read is noted on the tenant's chatter (who looked, which filter)."""
from odoo import fields, models
from odoo.exceptions import UserError

_PAGE = 50


class TenantAudit(models.TransientModel):
    _name = 'ncollection.tenant.audit'
    _description = 'Tenant audit log (snapshot)'

    tenant_id = fields.Many2one('ncollection.tenant', required=True, readonly=True, ondelete='cascade')
    model = fields.Char(string='Model (technical name)', help="Optional, e.g. sale.order")
    date_from = fields.Date(string='From')
    date_to = fields.Date(string='To')
    offset = fields.Integer(readonly=True)
    total = fields.Integer(string='Events', readonly=True)
    subscribed_rules = fields.Integer(string='Recorded models', readonly=True)
    state = fields.Selection([('available', 'Available'), ('unavailable', 'Not available on this workspace')],
                             readonly=True, default='available')
    message = fields.Char(readonly=True)
    line_ids = fields.One2many('ncollection.tenant.audit.line', 'audit_id', readonly=True)

    def _nc_load(self, offset=0):
        self.ensure_one()
        payload = {'offset': max(offset, 0), 'limit': _PAGE, 'model': (self.model or '').strip(),
                   'date_from': self.date_from and self.date_from.isoformat() or '',
                   'date_to': self.date_to and self.date_to.isoformat() or ''}
        try:
            reply = self.tenant_id._nc_admin_call('list_audit_logs', payload)
        except UserError as exc:
            if 'out of date' not in str(exc) and 'does not offer' not in str(exc):
                raise
            # 404 / unknown method: the workspace has no general Audit Log (not in its plan, or not installed).
            self.write({'state': 'unavailable', 'line_ids': [(5, 0, 0)], 'total': 0, 'offset': 0,
                        'message': self.env._("This workspace has no general Audit Log (not in its plan or not "
                                              "installed). Nothing was recorded there, so nothing is shown.")})
            return
        self.write({
            'state': 'available', 'message': False, 'offset': payload['offset'],
            'total': reply.get('total') or 0, 'subscribed_rules': reply.get('subscribed_rules') or 0,
            'line_ids': [(5, 0, 0)] + [(0, 0, {
                'remote_id': row['id'], 'date': row['date'], 'user': row['user'], 'model': row['model'],
                'model_name': row['model_name'], 'res_id': row['res_id'], 'record': row['record'],
                'method': row['method'], 'changes': row['changes']}) for row in reply.get('rows', [])],
        })
        self.tenant_id._nc_admin_note(self.env._(
            "Audit Log read from the platform (%(rows)s of %(total)s events, filter: %(filter)s).",
            rows=len(reply.get('rows', [])), total=self.total,
            filter=', '.join(f"{k}={v}" for k, v in payload.items() if v and k not in ('limit',)) or '-'))

    def _nc_reopen(self):
        return {'type': 'ir.actions.act_window', 'res_model': self._name, 'res_id': self.id, 'view_mode': 'form',
                'target': 'current', 'name': self.env._('Audit Log of %s', self.tenant_id.company_name)}

    def action_search(self):
        self._nc_load(0)
        return self._nc_reopen()

    def action_next(self):
        if self.offset + _PAGE < self.total:
            self._nc_load(self.offset + _PAGE)
        return self._nc_reopen()

    def action_previous(self):
        self._nc_load(self.offset - _PAGE)
        return self._nc_reopen()


class TenantAuditLine(models.TransientModel):
    _name = 'ncollection.tenant.audit.line'
    _description = 'Tenant audit event (snapshot row)'
    _order = 'date desc, remote_id desc'

    audit_id = fields.Many2one('ncollection.tenant.audit', required=True, ondelete='cascade')
    remote_id = fields.Integer(string='Event', readonly=True)
    date = fields.Char(string='When', readonly=True)
    user = fields.Char(readonly=True)
    model = fields.Char(readonly=True)
    model_name = fields.Char(string='Document Type', readonly=True)
    res_id = fields.Integer(string='Record ID', readonly=True)
    record = fields.Char(readonly=True)
    method = fields.Char(string='Operation', readonly=True)
    changes = fields.Integer(string='Fields Changed', readonly=True)
