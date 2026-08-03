# -*- coding: utf-8 -*-
"""Restore-backup wizard (P2-T05).

Restores a `ncollection.backup` into a SCRATCH/staging database — never the live
tenant. The restore runs off the HTTP workers (provisioning queue channel), the
same isolation as backups + provisioning.
"""

from odoo import api, fields, models
from odoo.exceptions import UserError

_BACKUP_CHANNEL = 'root.provisioning'


class BackupRestoreWizard(models.TransientModel):
    _name = 'ncollection.backup.restore.wizard'
    _description = 'Restore Tenant Backup to a Scratch Database'

    backup_id = fields.Many2one(
        'ncollection.backup', required=True,
        domain=[('status', '=', 'done'), ('file_path', '!=', False)])
    target_db = fields.Char(
        string='Restore Into (scratch DB)', required=True,
        help="A NON-live database name. The restore refuses to overwrite an "
             "existing tenant's database.")

    @api.onchange('backup_id')
    def _onchange_backup_id(self):
        if self.backup_id:
            self.target_db = 'restore_%s' % (self.backup_id.database_name or 'tenant')

    def action_restore(self):
        self.ensure_one()
        # Safety: never let a restore clobber a live tenant DB.
        # active_test=False: an ARCHIVED tenant keeps its live database, and
        # sudo() does not bypass Odoo's implicit `active = True` filter (#275).
        if self.env['ncollection.tenant'].sudo().with_context(
                active_test=False).search_count(
                    [('database_name', '=', self.target_db)]):
            raise UserError(self.env._(
                "'%s' is a live tenant database. Choose a scratch/staging "
                "name — THIS WIZARD never overwrites a live tenant. (An "
                "in-place rollback of a tenant's OWN snapshot is a different "
                "path: action_restore_in_place, #244.)", self.target_db))
        self.backup_id.with_delay(
            channel=_BACKUP_CHANNEL,
            description="Restore '%s' -> '%s'" % (
                self.backup_id.database_name, self.target_db),
        ).restore_to(self.target_db)
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'type': 'success',
                'message': self.env._(
                    "Restore of %(src)s into %(db)s queued.",
                    src=self.backup_id.database_name, db=self.target_db),
                'next': {'type': 'ir.actions.act_window_close'},
            },
        }
