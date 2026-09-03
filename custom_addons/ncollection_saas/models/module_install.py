# -*- coding: utf-8 -*-
"""Install a plan's newly licensed modules into an EXISTING tenant DB (#459).

THE GAP THIS CLOSES. Provisioning installs `CORE_TENANT_MODULES` + the plan's
modules when a tenant database is created. After that, a plan change only ever
pushed *licensing* — config sync writes `allowed_module_names` into the tenant's
`ncollection.workspace.config`, which drives Ring 1 (menus) and Ring 2 (ORM).
Nothing installed the module, so a tenant could be shown an application that did
not exist in their database. The launcher made that visible; the defect was
older than the launcher.

SHAPE, AND WHY THIS ONE. The install runs the same way provisioning and fleet
migration run tenant-side work: an ISOLATED `odoo` SUBPROCESS driven from the
platform (`SaasSubprocessMixin`), never an ORM cursor on the tenant database
(Rule 3 / two-layer), and never from a web request. The module set comes from
the tenant's plan — a caller cannot name modules — so the worst a compromised
caller achieves is installing what the tenant is already licensed for.

`odoo -i` IS IDEMPOTENT for already-installed modules, which is what makes this
safe to re-run: the engine does not need to read the tenant's module table to
decide what is missing, so there is no cross-database read and no stale
"missing" list to get wrong.

NOT UNINSTALLING, DELIBERATELY. Revoking a licence hides the app (Ring 1) and
blocks its models (Ring 2); it does NOT uninstall. Uninstalling drops the
module's tables — customer data — and Odoo offers no supported way to put it
back. Losing data because a subscription was downgraded for a month is a
different and much worse failure than an app that stops being reachable. If the
product ever wants physical removal it needs its own ticket, with a backup step
and an explicit confirmation; it is not a side effect of a plan edit.
"""
import logging

from odoo import api, fields, models
from odoo.exceptions import UserError

from .provisioning_job import CORE_TENANT_MODULES, PROVISION_CHANNEL

_logger = logging.getLogger(__name__)

MODULE_INSTALL_STATES = [
    ('none', 'Nothing to do'),
    ('queued', 'Queued'),
    ('running', 'Installing'),
    ('done', 'Installed'),
    ('failed', 'Failed'),
]


class TenantModuleInstall(models.Model):
    # The tenant gains the platform's subprocess primitives here — the same
    # mixin provisioning_job and fleet_migration_line use, so the isolated
    # `odoo` invocation, the connection args and the DB-name guard have ONE
    # definition (#243's lesson).
    #
    # With a LIST _inherit, _name must be set explicitly — no exception.
    _name = 'ncollection.tenant'
    _inherit = ['ncollection.tenant', 'ncollection.saas.subprocess.mixin']

    module_install_state = fields.Selection(
        MODULE_INSTALL_STATES, default='none', readonly=True, copy=False,
        string='Module Installation',
        help="Result of the last attempt to install this tenant's licensed "
             "modules into its database. 'Failed' means the modules are "
             "licensed but NOT usable yet — the workspace is not ready.")
    module_install_last_ok = fields.Datetime(
        readonly=True, copy=False, string='Modules Installed On')
    module_install_last_error = fields.Char(readonly=True, copy=False)
    module_install_log = fields.Text(readonly=True, copy=False)

    # ---- the admin entry point -------------------------------------------
    def action_install_licensed_modules(self):
        """Queue the install of this tenant's plan modules into its database.

        Refuses a tenant with no ready database rather than queueing work that
        cannot succeed — the same stance `action_config_sync_now` takes.
        """
        for tenant in self:
            if tenant.database_status != 'ready' or not tenant.database_name:
                raise UserError(self.env._(
                    "Tenant '%s' has no ready database to install into.",
                    tenant.company_name))
            modules = tenant._nc_licensed_module_list()
            if not modules:
                raise UserError(self.env._(
                    "Plan '%s' licenses no additional modules — the core "
                    "modules are already installed.",
                    tenant.plan_id.name or '—'))
            tenant.write({
                'module_install_state': 'queued',
                'module_install_last_error': False,
            })
            tenant.with_delay(
                channel=PROVISION_CHANNEL,
                description=self.env._(
                    "Install licensed modules for '%s'", tenant.database_name),
                identity_key='nc-module-install-%s' % tenant.id,
            ).run_module_install()
        return True

    def action_install_licensed_modules_sync(self):
        """Run it inline (manual button / tests). Same engine, no queue —
        mirroring provisioning's `action_run_sync`, and the path that works on
        a stack without the queue_job runner."""
        for tenant in self:
            tenant.run_module_install()
        return True

    # ---- the engine -------------------------------------------------------
    def run_module_install(self):
        """Install the plan's modules into the tenant DB. Public so queue_job
        can call it in the runner worker.

        Failure is RECORDED, not raised: the caller's transaction must commit
        the failed state, or the tenant would keep claiming it is fine. That is
        the same reasoning `provisioning_job.run_provisioning` documents.
        """
        self.ensure_one()
        db = self.database_name
        modules = self._nc_licensed_module_list()
        if not modules:
            self.write({'module_install_state': 'none'})
            return True

        # The name guard from the shared mixin: this string becomes a
        # subprocess argument, so it is validated even though it came from our
        # own record rather than from a request.
        self._assert_safe_db_name(db)

        self.write({'module_install_state': 'running'})
        try:
            cmd = ['odoo'] + self._odoo_conn_args(db) + [
                '-i', ','.join(modules),
                '--stop-after-init', '--no-http', '--max-cron-threads=0',
            ]
            out = self._run_odoo_subprocess(
                cmd, self.env._("module install"))
        except Exception as exc:  # noqa: BLE001 - must record, not propagate
            self.write({
                'module_install_state': 'failed',
                'module_install_last_error': str(exc)[:255],
                'module_install_log': (str(exc) or '')[-4000:],
            })
            _logger.warning("Module install failed for tenant %s (%s): %s",
                            self.company_name, db, exc)
            return False

        self.write({
            'module_install_state': 'done',
            'module_install_last_ok': fields.Datetime.now(),
            'module_install_last_error': False,
            'module_install_log': (out or '')[-4000:],
        })
        _logger.info("Installed %s into tenant database %s",
                     ', '.join(modules), db)
        # The tenant's workspace config must agree with what is now installed;
        # licensing and installation drifting apart is the whole defect.
        self._config_sync_enqueue()
        return True

    # ---- what may be installed -------------------------------------------
    def _nc_licensed_module_list(self):
        """The plan's modules, minus the ones every tenant already has.

        THE SECURITY BOUNDARY: read from the tenant's own plan, never from a
        caller. `get_allowed_module_list()` is the plan's own parser, so this
        cannot disagree with what provisioning installs or what config sync
        pushes.
        """
        self.ensure_one()
        plan = self.plan_id
        if not plan:
            return []
        return [m for m in plan.get_allowed_module_list()
                if m not in CORE_TENANT_MODULES]

    def _nc_enqueue_module_install(self):
        """Queue the install for the ready tenants in `self`, one job EACH.

        Per-tenant jobs on purpose (#461): a single job covering a whole plan
        would make one tenant's failure decide the outcome for all of them, and
        the state fields are per tenant. Separate jobs keep the failures
        independent and individually retryable.

        Mirrors `_config_sync_enqueue` exactly — same channel, same
        identity_key shape, same "ready tenants only" rule — so the two halves
        of a plan change behave the same way and there is only one queueing
        idiom in this module to understand.
        """
        for tenant in self:
            if tenant.database_status != 'ready' or not tenant.database_name:
                continue
            if not tenant._nc_licensed_module_list():
                continue
            tenant.module_install_state = 'queued'
            tenant.with_delay(
                channel=PROVISION_CHANNEL,
                description=self.env._(
                    "Install licensed modules -> '%s'", tenant.database_name),
                # De-duplicates: while one install is pending for this tenant,
                # a second plan save does not stack another.
                identity_key='nc-module-install-%s' % tenant.id,
            ).run_module_install()

    @api.model
    def _nc_tenants_needing_module_install(self, plan):
        """Ready tenants on `plan` that license something beyond the core set.

        Used by the plan-side prompt so an admin is told which workspaces a
        module change still has to reach.
        """
        return self.search([
            ('plan_id', '=', plan.id),
            ('database_status', '=', 'ready'),
        ]).filtered(lambda t: t._nc_licensed_module_list())
