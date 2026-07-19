# -*- coding: utf-8 -*-
"""ncollection.auth.log — the tenant-local authentication audit trail (P1-T19).

Design rules (ARCHITECTURE_SECURITY.md §6):
- Logging ONLY. This model never participates in authentication decisions —
  enforcement is Odoo core's login cooldown + OCA auth_session_timeout.
- Failure events are written through a SEPARATE cursor (`_capture_isolated`)
  because the failed-login exception rolls the request transaction back —
  a same-cursor row would vanish with it.
- Rows are readable by Settings/system users only and writable by no one
  (hooks write via sudo); mirrored in ir.model.access.csv (Rule 4).
"""

from odoo import SUPERUSER_ID, api, fields, models
from odoo.http import request

EVENT_TYPES = [
    ('login_success', 'Login success'),
    ('login_failed', 'Login failed'),
    ('logout', 'Logout'),
    ('reset_request', 'Password reset requested'),
    ('reset_complete', 'Password reset completed'),
]


class NcollectionAuthLog(models.Model):
    _name = 'ncollection.auth.log'
    _description = 'Authentication audit log'
    _order = 'create_date desc, id desc'

    event_type = fields.Selection(EVENT_TYPES, required=True, index=True)
    login = fields.Char(index=True)
    user_id = fields.Many2one('res.users', ondelete='set null', index=True)
    ip_address = fields.Char()
    user_agent = fields.Char()
    db_name = fields.Char()

    @api.model
    def _prepare_capture_vals(self, event_type, login=None, user=None):
        """Collect event + HTTP context. Safe with no HTTP request (shell/cron)."""
        ip_address = user_agent = None
        if request:
            httprequest = request.httprequest
            # With proxy_mode=True Odoo already resolved X-Forwarded-For, so
            # remote_addr is the real client IP behind the Nginx edge.
            ip_address = httprequest.remote_addr
            user_agent = (httprequest.user_agent.string or '')[:512]
        return {
            'event_type': event_type,
            'login': login or (user and user.login) or None,
            'user_id': user.id if user else None,
            'ip_address': ip_address,
            'user_agent': user_agent,
            'db_name': self.env.cr.dbname,
        }

    @api.model
    def _capture(self, event_type, login=None, user=None):
        """Log an event inside the current transaction (success paths)."""
        self.sudo().create(self._prepare_capture_vals(event_type, login=login, user=user))

    @api.model
    def _capture_isolated(self, event_type, login=None):
        """Log an event in its OWN cursor so it survives the caller's rollback.

        Used for login failures: the AccessDenied that follows rolls back the
        request transaction, which would erase a same-cursor log row.
        """
        vals = self._prepare_capture_vals(event_type, login=login)
        with self.env.registry.cursor() as cr:
            env = api.Environment(cr, SUPERUSER_ID, {})
            env['ncollection.auth.log'].create(vals)
            # Explicit: a real Cursor also commits on clean exit, but the
            # TestCursor used under the test framework rolls its savepoint
            # back on exit unless committed — without this, failure rows
            # vanish in tests while working in production.
            cr.commit()

    @api.model
    def _capture_cleanup_for_tests(self, login):
        """Remove isolated-cursor rows created by tests. Isolated writes commit
        outside the test transaction, so the rollback cannot clean them —
        this deletes them the same way they were written. Test helper only."""
        with self.env.registry.cursor() as cr:
            env = api.Environment(cr, SUPERUSER_ID, {})
            env['ncollection.auth.log'].search([('login', '=', login)]).unlink()
