import html
import logging
import time
from datetime import timedelta

import psycopg2

from odoo import api, fields, models
from odoo.tools import config

_logger = logging.getLogger(__name__)

# System databases that are never a tenant workspace.
_SYSTEM_DBS = ('postgres', 'template0', 'template1')
_MB = 1024.0 * 1024.0

# Short-lived per-process cache for the storage read: opening the dashboard
# repeatedly must not burn a maintenance connection on every render (storage
# changes slowly). Only successful reads are cached; a failure retries next time.
_DB_SIZE_TTL = 60.0  # seconds
_db_size_cache = {'ts': 0.0, 'value': ([], 0, None)}


class SubscriptionDashboard(models.TransientModel):
    _name = 'ncollection.subscription.dashboard'
    _description = 'NCollection SaaS Dashboard'

    # ---- tenant / subscription KPIs (P1-T07) -----------------------------
    total_tenants = fields.Integer(compute='_compute_kpis')
    active_tenants = fields.Integer(compute='_compute_kpis')
    trial_tenants = fields.Integer(compute='_compute_kpis')
    active_subscriptions = fields.Integer(compute='_compute_kpis')
    monthly_revenue = fields.Monetary(compute='_compute_kpis')  # MRR (monthly-normalized)
    expiring_soon = fields.Integer(compute='_compute_kpis')
    currency_id = fields.Many2one('res.currency', default=lambda self: self.env.company.currency_id)

    # ---- revenue / churn / conversion (P2-T15) ---------------------------
    churned_subscriptions = fields.Integer(compute='_compute_growth')
    churn_rate = fields.Float(
        compute='_compute_growth',
        help='Point-in-time proxy: churned / (active + churned). Not a 30-day rate '
             '(no status-transition history yet).')
    trial_conversion_rate = fields.Float(
        compute='_compute_growth',
        help='Point-in-time proxy: active / (active + trial).')

    # ---- operational monitors (P2-T15) — the acceptance-critical surface --
    failed_job_ids = fields.Many2many(
        'ncollection.provisioning.job', compute='_compute_monitors',
        string='Failed Provisioning')
    at_risk_subscription_ids = fields.Many2many(
        'ncollection.subscription', compute='_compute_monitors',
        string='At-risk Subscriptions')

    # ---- DB / storage health (P2-T15) — cluster-level infra read ---------
    db_count = fields.Integer(compute='_compute_storage')
    db_storage_total_mb = fields.Float(compute='_compute_storage', string='Total Storage (MB)')
    db_storage_html = fields.Html(compute='_compute_storage', sanitize=False)

    @api.depends_context('uid')
    def _compute_kpis(self):
        tenant_model = self.env['ncollection.tenant']
        subscription_model = self.env['ncollection.subscription']

        total_tenants = tenant_model.search_count([])
        active_tenants = tenant_model.search_count([('status', '=', 'active')])
        trial_tenants = tenant_model.search_count([('status', '=', 'trial')])

        active_subscriptions_recs = subscription_model.search([('status', '=', 'active')])
        active_subscriptions = len(active_subscriptions_recs)

        # MRR: sum the stored subscription.mrr over active subs — single source of
        # truth with the revenue graph/pivot (no duplicated normalization formula).
        monthly_revenue = sum(active_subscriptions_recs.mapped('mrr'))

        soon = fields.Date.context_today(self) + timedelta(days=30)
        expiring_soon = subscription_model.search_count([
            ('status', '=', 'active'),
            ('end_date', '!=', False),
            ('end_date', '<=', soon),
        ])

        for record in self:
            record.total_tenants = total_tenants
            record.active_tenants = active_tenants
            record.trial_tenants = trial_tenants
            record.active_subscriptions = active_subscriptions
            record.monthly_revenue = monthly_revenue
            record.expiring_soon = expiring_soon

    @api.depends_context('uid')
    def _compute_growth(self):
        Sub = self.env['ncollection.subscription']
        active = Sub.search_count([('status', '=', 'active')])
        trial = Sub.search_count([('status', '=', 'trial')])
        churned = Sub.search_count([('status', 'in', ('cancelled', 'expired'))])
        # Pragmatic point-in-time proxies (P2-T15): with no status-transition
        # history yet, these are current-state ratios, not true 30-day rates.
        churn_rate = (100.0 * churned / (active + churned)) if (active + churned) else 0.0
        conversion = (100.0 * active / (active + trial)) if (active + trial) else 0.0
        for rec in self:
            rec.churned_subscriptions = churned
            rec.churn_rate = churn_rate
            rec.trial_conversion_rate = conversion

    @api.depends_context('uid')
    def _compute_monitors(self):
        Job = self.env['ncollection.provisioning.job']
        Sub = self.env['ncollection.subscription']
        failed = Job.search([('status', '=', 'failed')], order='write_date desc', limit=10)
        soon = fields.Date.context_today(self) + timedelta(days=30)
        at_risk = Sub.search([
            '|', ('status', '=', 'suspended'),
            '&', '&', ('status', 'in', ('active', 'expired')),
            ('end_date', '!=', False), ('end_date', '<=', soon),
        ], order='end_date asc', limit=10)
        for rec in self:
            rec.failed_job_ids = failed
            rec.at_risk_subscription_ids = at_risk

    @api.depends_context('uid')
    def _compute_storage(self):
        rows, total, err = self._read_db_sizes()
        shown = rows[:15]
        # Only look up the databases we actually display (bounded), to tag them
        # tenant vs platform — never load the whole tenant table.
        tenant_names = set(self.env['ncollection.tenant'].search(
            [('database_name', 'in', [name for name, _ in shown])]).mapped('database_name'))
        for rec in self:
            rec.db_count = len(rows)
            rec.db_storage_total_mb = total / _MB
            if err:
                rec.db_storage_html = (
                    '<p class="text-muted">Storage statistics are currently unavailable.</p>')
                continue
            lines = []
            for name, size in shown:
                label = html.escape(name or '')
                tag = '' if name in tenant_names else ' <span class="text-muted">(platform)</span>'
                lines.append(
                    '<tr><td>%s%s</td><td class="text-end">%.1f MB</td></tr>'
                    % (label, tag, size / _MB))
            rec.db_storage_html = (
                '<table class="table table-sm o_ncollection_db_table">'
                '<thead><tr><th>Database</th><th class="text-end">Size</th></tr></thead>'
                '<tbody>%s</tbody></table>' % ''.join(lines))

    def _read_db_sizes(self):
        """Per-database sizes from the cluster over the maintenance connection.

        This is an INFRA read (`pg_database_size`), never a tenant-data ORM query,
        so two-layer isolation (Rule 3) is preserved — same sanctioned channel the
        provisioning engine uses for existence checks. Fixed SQL, no user input.
        Best-effort: a transport error must never break the dashboard. Cached for
        _DB_SIZE_TTL so repeated dashboard opens don't churn maintenance connections.
        """
        now = time.monotonic()
        if _db_size_cache['ts'] and (now - _db_size_cache['ts']) < _DB_SIZE_TTL:
            return _db_size_cache['value']
        params = {
            'host': config.get('db_host') or 'localhost',
            'port': config.get('db_port') or 5432,
            'user': config.get('db_user'),
            'password': config.get('db_password'),
            'dbname': 'postgres',
            'connect_timeout': 5,
            # Bound query execution too — a stuck pg_database_size must not hang the
            # page (connect_timeout only bounds the handshake).
            'options': '-c statement_timeout=3000',
        }
        params = {k: v for k, v in params.items() if v not in (None, False)}
        try:
            conn = psycopg2.connect(**params)
        except Exception as exc:  # noqa: BLE001 - never break the dashboard on transport
            _logger.warning('Dashboard: could not read DB sizes: %s', exc)
            return [], 0, str(exc)
        try:
            conn.set_session(readonly=True)
            cur = conn.cursor()
            cur.execute(
                "SELECT datname, pg_database_size(datname) FROM pg_database "
                "WHERE datistemplate = false AND datname NOT IN %s ORDER BY 2 DESC",
                (_SYSTEM_DBS,))
            rows = cur.fetchall()
        except Exception as exc:  # noqa: BLE001
            _logger.warning('Dashboard: DB size query failed: %s', exc)
            return [], 0, str(exc)
        finally:
            conn.close()
        total = sum(size for _, size in rows)
        _db_size_cache.update(ts=now, value=(rows, total, None))  # cache success only
        return _db_size_cache['value']
