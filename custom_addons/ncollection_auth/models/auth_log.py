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

import logging
from datetime import timedelta

from odoo import SUPERUSER_ID, api, fields, models
from odoo.http import request

_logger = logging.getLogger(__name__)

# Row-level retention (#219). ARCHITECTURE_SECURITY documented deletion only at
# tenant OFFBOARDING (DB drop); this table needed a policy for a LIVE tenant,
# because ip_address + user_agent are PII written on every auth event and #178
# scaled that from one demo tenant to every provisioned one.
RETENTION_PARAM = 'ncollection_auth.log_retention_days'
DEFAULT_RETENTION_DAYS = 180

# Deleted oldest-first in chunks rather than one statement: on a tenant that has
# been logging for a long time the first run could otherwise hold a very large
# transaction. MAX_BATCHES bounds a single run so the cron cannot monopolise a
# worker — whatever is left is collected by the next daily run.
GC_CHUNK_SIZE = 5000
GC_MAX_BATCHES = 20

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

    # The retention purge (#219) filters on create_date, which Odoo does NOT
    # index by default. Without this the nightly cron sequentially scans a table
    # that grows with every auth event on every tenant, forever. Declared via
    # models.Index because Odoo 19 ignores the legacy _sql_constraints list and
    # this is the same table-object API models.Constraint uses.
    _create_date_index = models.Index('(create_date)')

    # ------------------------------------------------------------------
    # Retention (#219)
    # ------------------------------------------------------------------

    @api.model
    def _retention_days(self):
        """Configured retention window in days. 0 or less means DISABLED.

        Reading through ir.config_parameter (rather than a module constant)
        is what makes the window tunable per tenant without a code change —
        the same pattern the module's other hardening knobs already use.
        A non-numeric value falls back to the default and says so loudly:
        silently treating garbage as "0" would disable the purge, and a
        retention policy that quietly stops running is worse than none.
        """
        raw = self.env['ir.config_parameter'].sudo().get_param(
            RETENTION_PARAM, DEFAULT_RETENTION_DAYS)
        try:
            return int(raw)
        except (TypeError, ValueError):
            _logger.warning(
                "%s is %r, which is not a number — falling back to %s days.",
                RETENTION_PARAM, raw, DEFAULT_RETENTION_DAYS)
            return DEFAULT_RETENTION_DAYS

    @api.model
    def _gc_auth_log(self):
        """Delete auth-log rows past the retention window. Returns the count.

        Called by the ``ir.cron`` in ``data/auth_cron.xml``. Deliberately a
        named scheduled action rather than an ``@api.autovacuum`` hook: under
        UAE PDPL this purge is evidence, and a distinct Scheduled Action has
        its own last-run/next-run timestamp that an auditor can point at and an
        operator can disable on its own. An autovacuum hook is bundled
        anonymously into Odoo's shared "Base: Auto-vacuum internal data" job
        alongside unrelated cleanup.

        Deletes, rather than anonymises: the acceptance criteria say rows past
        the window are *removed*. Anonymising (nulling ip_address/user_agent,
        keeping event_type + timestamp) would preserve security analytics and
        remains the obvious future variant if the policy changes.
        """
        days = self._retention_days()
        if days <= 0:
            _logger.info(
                "Auth-log retention is disabled (%s = %s).", RETENTION_PARAM, days)
            return 0

        deadline = fields.Datetime.now() - timedelta(days=days)
        total = 0
        for _batch in range(GC_MAX_BATCHES):
            rows = self.sudo().search(
                [('create_date', '<', deadline)],
                limit=GC_CHUNK_SIZE, order='create_date asc')
            if not rows:
                break
            count = len(rows)
            rows.unlink()
            total += count
            if count < GC_CHUNK_SIZE:
                break

        # Logged unconditionally, including the zero case: the audit story for
        # a retention policy is "it ran and here is what it did", and a silent
        # cron cannot be distinguished from one that never fired.
        _logger.info(
            "Auth-log retention: %s row(s) older than %s day(s) deleted "
            "(cutoff %s).", total, days, deadline)
        return total

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
            # Explicit commit on OUR OWN cursor — the sanctioned exception to
            # the never-commit rule (OCA contribution guide: committing is
            # legitimate on a cursor you created yourself). Required so the
            # row survives the caller's rollback.
            cr.commit()  # pylint: disable=invalid-commit
