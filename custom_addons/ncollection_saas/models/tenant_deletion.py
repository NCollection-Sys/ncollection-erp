# -*- coding: utf-8 -*-
"""Delete Tenant: permanently remove a disposable tenant - its database, filestore and every platform record of it.

Authority. A Platform Administrator on THIS tenant's ``platform_admin_ids`` allow-list (the list that already governs
Enter Company and Tenant Administration), checked on every call, server side. The browser sends a tenant id and the
identifier it typed, nothing else: the database name, the filestore path and the routing are resolved here from the
trusted tenant record, and the typed identifier must equal that record's database name exactly.

Refusals, before anything is touched: the platform's own database and every reserved / fixture name
(``_GENERATED_NAME_BLOCKLIST``), a name that is not a strict tenant identifier, a database the tenant record does not
own, a tenant that is provisioning, held by the release gate for an upgrade, or a target of an in-flight Fleet
Migration, and a tenant named in the ``ncollection_saas.tenant_delete_protected`` system parameter.

The run. ``ncollection.tenant.deletion`` is the platform-level audit record and survives the tenant. Its state
moves requested -> deleting -> deleted | failed; a step that raises is caught (the platform-record step inside a
savepoint, so a partial removal rolls back) and recorded as a failure with the step it stopped at, never as a success.
The order is: database (DROP ... WITH (FORCE), which also ends every session of it, so no entry grant or session can
be used again), filestore, then the platform records (the tenant row; its domains, backups (rows and files, #295),
provisioning jobs, fleet lines, module overrides and allow-list go with it by cascade; its subscriptions are removed
first; invoices keep their rows, unlinked). Every step is idempotent, so Retry on a failed record resumes where it
stopped.
"""
import logging
import os
import shutil

from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError
from odoo.tools import config

from .tenant import _GENERATED_NAME_BLOCKLIST

_logger = logging.getLogger(__name__)

_PLATFORM_GROUP = 'ncollection_subscription.group_platform_admin'
PROTECTED_PARAM = 'ncollection_saas.tenant_delete_protected'
_ACTIVE_FLEET_LINE = ('pending', 'snapshotting', 'upgrading', 'probing')


class TenantDeletion(models.Model):
    _name = 'ncollection.tenant.deletion'
    _description = 'Tenant deletion (audit)'
    _order = 'id desc'
    _rec_name = 'database_name'

    tenant_id = fields.Many2one('ncollection.tenant', string='Tenant', ondelete='set null', readonly=True)
    company_name = fields.Char(readonly=True)
    tenant_uuid = fields.Char(string='Tenant UUID', readonly=True)
    database_name = fields.Char(string='Database', readonly=True, required=True)
    requested_by = fields.Many2one('res.users', string='Requested by', readonly=True, ondelete='restrict')
    state = fields.Selection([
        ('requested', 'Delete requested'),
        ('deleting', 'Deleting'),
        ('deleted', 'Deleted'),
        ('failed', 'Delete failed'),
    ], string='Status', default='requested', required=True, readonly=True)
    database_dropped = fields.Boolean(readonly=True)
    filestore_removed = fields.Boolean(readonly=True)
    records_removed = fields.Boolean(string='Platform records removed', readonly=True)
    finished_at = fields.Datetime(readonly=True)
    message = fields.Text(string='Result', readonly=True)

    # ------------------------------------------------------------------ authority
    @api.model
    def _nc_assert_may_delete(self, tenant):
        user = self.env.user
        if not user.has_group(_PLATFORM_GROUP):
            raise AccessError(self.env._("Only a Platform Administrator can delete a tenant."))
        if user not in tenant.sudo().platform_admin_ids:
            raise AccessError(self.env._("You are not authorised to delete %s.", tenant.sudo().company_name))

    @api.model
    def _nc_protected_names(self):
        raw = self.env['ir.config_parameter'].sudo().get_param(PROTECTED_PARAM) or ''
        return {name.strip() for name in raw.split(',') if name.strip()}

    @api.model
    def _nc_resolve(self, tenant, typed):
        """The trusted database name of ``tenant``, or a refusal. Nothing here trusts the caller beyond the id."""
        tenant = tenant.sudo()
        db = (tenant.database_name or '').strip()
        if not db:
            raise UserError(self.env._("%s has no database to delete.", tenant.company_name))
        if (typed or '').strip() != db:
            raise UserError(self.env._(
                "The identifier you typed does not match. Type %s exactly to delete this tenant.", db))
        if db == self.env.cr.dbname or db in _GENERATED_NAME_BLOCKLIST:
            raise AccessError(self.env._("%s is the platform's or a reserved database and can never be deleted.", db))
        self.env['ncollection.saas.subprocess.mixin']._assert_safe_db_name(db)
        if db in self._nc_protected_names():
            raise UserError(self.env._("%s is protected from deletion on this platform.", db))
        if self.env['ncollection.tenant'].sudo().search_count([('database_name', '=', db)]) != 1:
            raise UserError(self.env._("%s is not owned by exactly one tenant record; nothing was deleted.", db))
        if tenant.database_status in ('provisioning', 'validating'):
            raise UserError(self.env._("%s is being provisioned. Wait for it to finish.", tenant.company_name))
        if self.env['ncollection.provisioning.job'].sudo().search_count(
                [('tenant_id', '=', tenant.id), ('status', 'in', ('queued', 'running'))]):
            raise UserError(self.env._("%s has a provisioning job in progress.", tenant.company_name))
        if tenant.nc_release_state == 'upgrading':
            raise UserError(self.env._("%s is being upgraded. Wait for the upgrade to finish.", tenant.company_name))
        if self.env['ncollection.fleet.migration.line'].sudo().search_count([
                ('tenant_id', '=', tenant.id), ('state', 'in', _ACTIVE_FLEET_LINE),
                ('migration_id.state', 'in', ('draft', 'canary', 'rolling'))]):
            raise UserError(self.env._("%s is a target of a Fleet Migration in progress.", tenant.company_name))
        return db

    @api.model
    def _nc_filestore_path(self, db):
        """The tenant's filestore, resolved from the server's own data directory and asserted to sit inside it."""
        root = os.path.realpath(os.path.join(config['data_dir'], 'filestore'))
        target = os.path.realpath(config.filestore(db))
        if os.path.dirname(target) != root or os.path.basename(target) != db:
            raise UserError(self.env._("The filestore of %s is not inside the server's storage root.", db))
        return target

    # ------------------------------------------------------------------ the run
    @api.model
    def _nc_request(self, tenant, typed):
        """Validate, record the request, then run it. Returns the audit record."""
        self._nc_assert_may_delete(tenant)
        db = self._nc_resolve(tenant, typed)
        tenant = tenant.sudo()
        record = self.sudo().create({
            'tenant_id': tenant.id, 'company_name': tenant.company_name, 'database_name': db,
            'tenant_uuid': getattr(tenant, 'tenant_uuid', False) or False, 'requested_by': self.env.uid,
        })
        _logger.warning("Tenant deletion requested: %s (tenant %s) by user %s", db, tenant.id, self.env.uid)
        record._nc_run()
        return record

    def action_retry(self):
        self.ensure_one()
        if self.state != 'failed':
            raise UserError(self.env._("Only a failed deletion can be retried."))
        tenant = self.tenant_id
        if tenant:
            self._nc_assert_may_delete(tenant)
            self._nc_resolve(tenant, self.database_name)
        elif not self.env.user.has_group(_PLATFORM_GROUP):
            raise AccessError(self.env._("Only a Platform Administrator can delete a tenant."))
        self._nc_run()
        return self._nc_result_action()

    def _nc_run(self):
        self.ensure_one()
        db = self.database_name
        self.sudo().write({'state': 'deleting', 'message': False})
        step = 'database'
        try:
            subproc = self.env['ncollection.saas.subprocess.mixin']
            subproc._assert_safe_db_name(db)
            if db == self.env.cr.dbname or db in _GENERATED_NAME_BLOCKLIST:
                raise AccessError(self.env._("Refusing to delete a platform or reserved database."))
            subproc._drop_database(db)                      # IF EXISTS: a retry after a drop is a no-op
            self.sudo().write({'database_dropped': True})
            step = 'filestore'
            path = self._nc_filestore_path(db)
            if os.path.isdir(path):
                shutil.rmtree(path)
            self.sudo().write({'filestore_removed': True})
            step = 'records'
            tenant = self.tenant_id.sudo()
            if tenant:
                with self.env.cr.savepoint():
                    self.env['ncollection.subscription'].sudo().with_context(active_test=False).search(
                        [('tenant_id', '=', tenant.id)]).unlink()
                    tenant.unlink()
            self.sudo().write({'records_removed': True, 'state': 'deleted', 'finished_at': fields.Datetime.now(),
                               'message': self.env._("Deleted: database, filestore and platform records.")})
            _logger.warning("Tenant deletion done: %s", db)
        except Exception as exc:                            # noqa: BLE001 - recorded, never reported as success
            reason = self.env._("Failed at the %(step)s step: %(error)s", step=step, error=str(exc)[-500:])
            _logger.error("Tenant deletion of %s failed at %s", db, step, exc_info=True)
            self.sudo().write({'state': 'failed', 'finished_at': fields.Datetime.now(), 'message': reason})

    def _nc_result_action(self):
        return {'type': 'ir.actions.act_window', 'res_model': self._name, 'res_id': self.id,
                'view_mode': 'form', 'target': 'current', 'name': self.env._("Tenant deletion")}


class TenantDeleteWizard(models.TransientModel):
    _name = 'ncollection.tenant.delete.wizard'
    _description = 'Delete Tenant'

    tenant_id = fields.Many2one('ncollection.tenant', required=True, readonly=True, ondelete='cascade')
    company_name = fields.Char(related='tenant_id.company_name')
    database_name = fields.Char(related='tenant_id.database_name', string='Identifier')
    tenant_url = fields.Char(related='tenant_id.tenant_url')
    confirm_identifier = fields.Char(string='Type the identifier to confirm')

    @api.model
    def default_get(self, fields_list):
        values = super().default_get(fields_list)
        if self.env.context.get('active_model') == 'ncollection.tenant' and self.env.context.get('active_id'):
            values.setdefault('tenant_id', self.env.context['active_id'])
        return values

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        for wizard in records:
            self.env['ncollection.tenant.deletion']._nc_assert_may_delete(wizard.tenant_id)
        return records

    def action_delete(self):
        self.ensure_one()
        record = self.env['ncollection.tenant.deletion']._nc_request(self.tenant_id, self.confirm_identifier)
        return record._nc_result_action()
