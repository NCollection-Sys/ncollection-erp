# -*- coding: utf-8 -*-
"""Tenant backup manager (P2-T05, ARCHITECTURE_DATA_PLATFORM §5.1).

The tenant-granular layer ON TOP of PITR (P2-T04): a nightly per-tenant
`pg_dump` + filestore tar, encrypted, with `ncollection.backup` records, a
retention pyramid (7 daily / 4 weekly / 12 monthly), failure alerting, and a
monthly restore drill. PITR covers cluster disaster recovery (RPO ~1 min); this
covers cheap per-tenant restore + long-term archival.

The heavy work runs OFF the HTTP workers: the crons enqueue jobs on the
provisioning queue channel, executed by the dedicated runner (P2-T01), which
shells out to scripts/backup/*.sh (pg_dump/tar/openssl) — never a cross-DB ORM
cursor (Rule 3).
"""

import logging
import os
import subprocess

from odoo import api, fields, models

_logger = logging.getLogger(__name__)

_BACKUP_SCRIPT = os.path.join(
    os.path.dirname(__file__), '..', 'scripts', 'backup', 'tenant_backup.sh')
_RESTORE_SCRIPT = os.path.join(
    os.path.dirname(__file__), '..', 'scripts', 'backup', 'tenant_restore.sh')

# Reuse the provisioning runner's channel so heavy backups never touch the HTTP
# workers (ARCHITECTURE_DATA_PLATFORM §10).
_BACKUP_CHANNEL = 'root.provisioning'
_SUBPROCESS_TIMEOUT = 60 * 60  # 1 h — a large tenant dump can be slow

# Retention pyramid (§5.1): keep the newest N of each type per tenant.
_RETENTION = {'daily': 7, 'weekly': 4, 'monthly': 12}


class NcollectionBackup(models.Model):
    _name = 'ncollection.backup'
    _description = 'Tenant Backup'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'create_date desc, id desc'

    name = fields.Char(compute='_compute_name', store=True)
    tenant_id = fields.Many2one(
        'ncollection.tenant', required=True,
        ondelete='cascade', tracking=True)
    database_name = fields.Char(related='tenant_id.database_name', store=True)
    backup_type = fields.Selection(
        selection=[('daily', 'Daily'), ('weekly', 'Weekly'), ('monthly', 'Monthly')],
        default='daily', required=True, tracking=True)
    status = fields.Selection(
        selection=[('pending', 'Pending'), ('running', 'Running'),
                   ('done', 'Done'), ('failed', 'Failed')],
        default='pending', required=True, tracking=True)
    file_path = fields.Char(string='Backup File', readonly=True)
    file_size = fields.Integer(string='Size (bytes)', readonly=True)
    started_at = fields.Datetime(readonly=True)
    completed_at = fields.Datetime(readonly=True)
    error_log = fields.Text(readonly=True)

    @api.depends('database_name', 'backup_type', 'create_date')
    def _compute_name(self):
        for rec in self:
            stamp = fields.Datetime.to_string(rec.create_date or fields.Datetime.now())
            rec.name = '%s / %s / %s' % (
                rec.database_name or '?', rec.backup_type, stamp)

    # ---- engine ----------------------------------------------------------

    def action_run_sync(self):
        """Run inline (manual button / tests) — same engine, no queue."""
        for rec in self:
            rec.run_backup()
        return True

    def _enqueue(self):
        """Queue the backup on the provisioning channel (off HTTP workers)."""
        for rec in self:
            rec.with_delay(
                channel=_BACKUP_CHANNEL,
                description="Backup tenant '%s'" % rec.database_name,
                identity_key='nc-backup-%s' % rec.id,
            ).run_backup()

    def run_backup(self):
        """Produce the encrypted per-tenant bundle; record result or failure.

        Public so OCA queue_job can call it in the runner worker. Failure is
        recorded + alerted, not raised past the record — the nightly cron and
        the next run heal a transient miss."""
        self.ensure_one()
        if self.status in ('running', 'done'):
            return
        db = self.database_name
        if not db:
            self.write({'status': 'failed', 'error_log': 'Tenant has no database.'})
            return
        self.write({'status': 'running', 'started_at': fields.Datetime.now()})
        try:
            out = self._run_subprocess(['bash', _BACKUP_SCRIPT, db, self.backup_type])
            path = self._parse_result(out, 'RESULT_PATH')
            size = self._parse_result(out, 'RESULT_BYTES')
            self.write({
                'status': 'done',
                'file_path': path,
                'file_size': int(size) if size and size.isdigit() else 0,
                'completed_at': fields.Datetime.now(),
            })
        except Exception as exc:  # pylint: disable=broad-except
            _logger.warning("Backup failed for '%s': %s", db, exc)
            self.write({
                'status': 'failed',
                'error_log': str(exc)[-4000:],
                'completed_at': fields.Datetime.now(),
            })
            self._alert_failure()

    def restore_to(self, target_db):
        """Restore this backup into a SCRATCH database (never a live tenant).
        Public so the wizard + queue_job can call it. Returns the target."""
        self.ensure_one()
        if not self.file_path:
            raise RuntimeError("Backup has no file to restore.")
        self._run_subprocess(['bash', _RESTORE_SCRIPT, self.file_path, target_db])
        return target_db

    def _run_subprocess(self, cmd):
        result = subprocess.run(
            cmd, env=self._script_env(), capture_output=True, text=True,
            timeout=_SUBPROCESS_TIMEOUT, check=False)
        if result.returncode != 0:
            raise RuntimeError((result.stderr or result.stdout or '')[-2000:])
        return result.stdout or ''

    @api.model
    def _script_env(self):
        """Env for the backup/restore scripts: DB connection + the cipher pass
        (from the secrets store / .env — never hard-coded)."""
        env = os.environ.copy()
        env.setdefault('HOST', 'db')
        env.setdefault('USER', 'odoo')
        return env

    @staticmethod
    def _parse_result(out, key):
        for line in (out or '').splitlines():
            if line.startswith(key + '='):
                return line[len(key) + 1:].strip()
        return ''

    # ---- crons -----------------------------------------------------------

    @api.model
    def _cron_nightly_backups(self):
        """Nightly: one backup per ready tenant, typed for the retention pyramid
        (monthly on the 1st, weekly on Sunday, else daily)."""
        btype = self._backup_type_for_today()
        tenants = self.env['ncollection.tenant'].search([
            ('database_status', '=', 'ready'), ('database_name', '!=', False)])
        for tenant in tenants:
            self.create({'tenant_id': tenant.id, 'backup_type': btype})._enqueue()
        return True

    @staticmethod
    def _backup_type_for_today():
        today = fields.Date.today()
        if today.day == 1:
            return 'monthly'
        if today.weekday() == 6:  # Sunday
            return 'weekly'
        return 'daily'

    @api.model
    def _cron_prune(self):
        """Enforce 7 daily / 4 weekly / 12 monthly per tenant; delete the file
        and the record for anything beyond the window."""
        # Distinct tenants that HAVE backups (read_group avoids scanning every
        # record with a bare search([])).
        groups = self.read_group(
            [('status', '=', 'done')], ['tenant_id'], ['tenant_id'])
        tenant_ids = [g['tenant_id'][0] for g in groups if g['tenant_id']]
        for tenant_id in tenant_ids:
            for btype, keep in _RETENTION.items():
                recs = self.search([
                    ('tenant_id', '=', tenant_id), ('backup_type', '=', btype),
                    ('status', '=', 'done')], order='create_date desc')
                for rec in recs[keep:]:
                    rec._delete_file()
                    rec.unlink()
        return True

    def _delete_file(self):
        self.ensure_one()
        if self.file_path and os.path.isfile(self.file_path):
            try:
                os.remove(self.file_path)
            except OSError as exc:
                _logger.warning("Could not delete backup file %s: %s", self.file_path, exc)

    @api.model
    def _cron_restore_drill(self):
        """§5.3 monthly drill: restore the newest good backup to a scratch DB and
        confirm it is non-empty. Proves the backups are restorable, not just
        present. Logged on the backup record."""
        rec = self.search([('status', '=', 'done'), ('file_path', '!=', False)],
                          limit=1, order='create_date desc')
        if not rec:
            return False
        target = 'drill_%s' % (rec.database_name or 'tenant')
        # Safety (P3-T12 / ISO-2): the unattended drill dropdb+createdb's `target`.
        # Never let it clobber a LIVE tenant DB — mirror the interactive wizard's
        # guard (BackupRestoreWizard.action_restore). If a tenant somehow owns
        # this name, skip + alert rather than destroy a real tenant's database.
        if self.env['ncollection.tenant'].sudo().search_count(
                [('database_name', '=', target)]):
            rec.message_post(body=self.env._(
                "Restore drill SKIPPED: scratch name '%s' collides with a live "
                "tenant database — refusing to overwrite it.", target))
            rec._alert_failure()
            return False
        try:
            rec.restore_to(target)
            rec.message_post(body=self.env._(
                "Restore drill OK: %(file)s restored to scratch DB %(db)s.",
                file=rec.file_path, db=target))
        except Exception as exc:  # pylint: disable=broad-except
            rec.message_post(body=self.env._("Restore drill FAILED: %s", exc))
            rec._alert_failure()
        return True

    # ---- alerting --------------------------------------------------------

    def _alert_failure(self):
        self.ensure_one()
        body = self.env._(
            "Tenant backup FAILED for %(db)s (%(type)s).",
            db=self.database_name, type=self.backup_type)
        self.message_post(body=body)
        self.activity_schedule(
            'mail.mail_activity_data_todo',
            summary=self.env._("Investigate backup failure: %s", self.database_name),
            note=body)
