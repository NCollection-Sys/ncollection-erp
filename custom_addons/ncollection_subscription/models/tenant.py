import uuid

from odoo import SUPERUSER_ID, api, fields, models
from odoo.exceptions import ValidationError

from .localization import localization_package


class Tenant(models.Model):
    _name = 'ncollection.tenant'
    _description = 'NCollection Tenant Company'
    _order = 'company_name asc'
    _inherit = ['mail.thread', 'mail.activity.mixin']

    # Guarded lifecycle: current status -> statuses allowed to move to.
    _ALLOWED_TRANSITIONS = {
        'trial': {'active', 'expired'},
        'active': {'suspended', 'expired'},
        'suspended': {'active', 'expired'},
        'expired': set(),  # terminal (reactivation is a business decision, not a model default)
    }

    company_name = fields.Char(required=True, tracking=True)
    tenant_uuid = fields.Char(
        string='Tenant UUID',
        copy=False,
        readonly=True,
        default=lambda self: str(uuid.uuid4()),
    )
    database_name = fields.Char(string='Database Name', tracking=True)
    database_status = fields.Selection(
        selection=[
            ('not_provisioned', 'Not Provisioned'),
            ('provisioning', 'Provisioning'),
            ('ready', 'Ready'),
            ('error', 'Error'),
        ],
        default='not_provisioned',
        required=True,
        tracking=True,
        string='Database Status',
    )
    trial_end_date = fields.Date(string='Trial End Date')
    portal_url = fields.Char(string='Portal URL')
    onboarding_stage = fields.Selection(
        selection=[
            ('signup', 'Signup'),
            ('setup', 'Setup'),
            ('training', 'Training'),
            ('go_live', 'Go Live'),
            ('completed', 'Completed'),
        ],
        default='signup',
        required=True,
        tracking=True,
        string='Onboarding Stage',
    )
    # #469: the tenant's country DRIVES provisioning, it does not describe it.
    # A country with a localization package (see models/localization.py) makes
    # provisioning install that package in the SAME `-i` as `account`, so the
    # real chart of accounts is loaded while the database is still empty — the
    # only moment loading one is safe. Left empty, the tenant provisions with
    # no localization, exactly as before.
    country_id = fields.Many2one(
        'res.country', string='Country',
        help='Drives localization at provisioning: chart of accounts, '
             'currency and tax setup. Changing it after the database exists '
             'does NOT re-localize it — use "Apply localization" for that.')
    localization_status = fields.Char(
        string='Localization', compute='_compute_localization_status',
        help='Which localization package this tenant provisions with.')

    @api.depends('country_id')
    def _compute_localization_status(self):
        for tenant in self:
            package = tenant._nc_localization_package()
            if package:
                tenant.localization_status = '%s (%s)' % (
                    package['name'], package['chart_template'])
            elif tenant.country_id:
                tenant.localization_status = self.env._(
                    'No localization package for %s', tenant.country_id.code)
            else:
                tenant.localization_status = self.env._('No country set')

    domain = fields.Char(string='Domain / Subdomain')
    contact_name = fields.Char(string='Contact Name')
    email = fields.Char(string='Email')
    phone = fields.Char(string='Phone')
    plan_id = fields.Many2one('ncollection.subscription.plan', string='Subscription Plan', tracking=True)
    subscription_id = fields.Many2one('ncollection.subscription', string='Current Subscription')
    status = fields.Selection(
        selection=[
            ('trial', 'Trial'),
            ('active', 'Active'),
            ('suspended', 'Suspended'),
            ('expired', 'Expired'),
        ],
        default='trial',
        required=True,
        tracking=True,
    )
    # #455: what this tenant's plan actually licenses, shown read-only on the
    # tenant form. Computed, never stored: the plan is the single source of
    # truth (provisioning installs CORE_TENANT_MODULES + this, config sync
    # pushes this into the tenant's workspace config), so a stored copy here
    # would be a second version of the answer that drifts the moment a plan is
    # edited. Empty means "core modules only" — which is a valid plan, not an
    # error, and the reason provisioning must tolerate a blank list (#451).
    effective_module_names = fields.Char(
        string='Licensed Modules',
        compute='_compute_effective_module_names',
        help="Modules this tenant's plan licenses, on top of the core modules "
             "every tenant always gets. Set them on the plan — saving there "
             "queues a config-sync push to every tenant using it.",
    )

    subscription_ids = fields.One2many('ncollection.subscription', 'tenant_id', string='Subscriptions')
    provisioning_job_ids = fields.One2many('ncollection.provisioning.job', 'tenant_id', string='Provisioning Jobs')
    active = fields.Boolean(default=True)

    # Odoo 19 dropped `_sql_constraints` (silently ignored) — use models.Constraint.
    _tenant_uuid_unique = models.Constraint(
        'unique(tenant_uuid)',
        'The tenant UUID must be unique.',
    )
    # ISO-1 (#225): database_name IS the tenant's Postgres DB identity. Two records
    # sharing it let one tenant's config-sync push (bearer derived purely from the
    # name) land on ANOTHER tenant's DB — a cross-tenant takeover. Enforce
    # uniqueness at the DB level. Unnamed tenants are exempt (empty -> NULL, and
    # Postgres UNIQUE allows multiple NULLs).
    _database_name_unique = models.Constraint(
        'unique(database_name)',
        'That database name is already assigned to another tenant.',
    )

    # ------------------------------------------------------------------
    # database_status transition guard (ISO-1 defense-in-depth, #228)
    # ------------------------------------------------------------------
    # Only a superuser / SUPERUSER_ID may move a tenant into 'provisioning'/'ready'
    # — the provisioning engine authorises its own transitions with sudo() (see
    # provisioning_job), which sets env.su. A group_platform_admin has write access
    # to ncollection.tenant and could otherwise flip database_status='ready' by
    # hand — the status config-sync keys its cross-DB push on. Harmless today (the
    # unique(database_name) constraint means a record can only point at its OWN
    # db), but a cleaner boundary.
    #
    # Authorisation is gated ONLY on env.su / env.uid == SUPERUSER_ID, NEVER on a
    # context key: env.context is fully client-controlled on every RPC path
    # (/web/dataset/call_kw, execute_kw merge the caller's context verbatim), so a
    # context flag would be trivially forgeable by the very actor this guards
    # (#228 review). env.su / SUPERUSER_ID are interpreter-level and not
    # RPC-spoofable. Both write() AND create() are guarded — base.group_system
    # (a real non-super admin) has create rights on this model too.
    _NC_ENGINE_ONLY_DB_STATUSES = ('provisioning', 'ready')

    def _nc_check_engine_only_status(self, status):
        if (status in self._NC_ENGINE_ONLY_DB_STATUSES
                and self.env.uid != SUPERUSER_ID and not self.env.su):
            raise ValidationError(self.env._(
                "Only the provisioning engine may set a tenant's database status "
                "to '%(status)s'.", status=status))

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            self._nc_check_engine_only_status(vals.get('database_status'))
        return super().create(vals_list)

    def write(self, vals):
        self._nc_check_engine_only_status(vals.get('database_status'))
        return super().write(vals)

    # ------------------------------------------------------------------
    # Guarded lifecycle transitions
    # ------------------------------------------------------------------
    def _transition(self, new_status):
        for tenant in self:
            allowed = self._ALLOWED_TRANSITIONS.get(tenant.status, set())
            if new_status not in allowed:
                raise ValidationError(
                    self.env._(
                        'Invalid tenant transition: %(current)s -> %(new)s '
                        '(tenant "%(name)s").',
                        current=tenant.status, new=new_status, name=tenant.company_name,
                    )
                )
        self.write({'status': new_status})

    @api.depends('plan_id', 'plan_id.allowed_module_names', 'country_id')
    def _compute_effective_module_names(self):
        """Display mirror of ``_nc_effective_module_list()`` (#455, #469)."""
        for tenant in self:
            tenant.effective_module_names = ', '.join(
                tenant._nc_effective_module_list())

    # ------------------------------------------------------------------
    # Country localization (#469)
    # ------------------------------------------------------------------
    def _nc_localization_package(self):
        """This tenant's localization package, or None.

        None is a normal state: a tenant in a country we ship no package for
        provisions with no localization, exactly as every tenant did before
        #469. Nothing downstream branches on the country itself — only on
        whether a package came back — which is what keeps adding Saudi Arabia
        or Egypt a table entry rather than an engine change.
        """
        self.ensure_one()
        return localization_package(self.country_id.code)

    def _nc_localization_modules(self):
        """The modules this tenant's country requires, or []."""
        package = self._nc_localization_package()
        return list(package['modules']) if package else []

    def _nc_effective_module_list(self):
        """EVERY module this tenant is entitled to: plan UNION localization.

        THE SINGLE AUTHORITY, deliberately. Four things independently answered
        this question before #469 — provisioning's ``_module_list``, the
        module-install job's ``_nc_licensed_module_list``, config sync's
        ``_config_sync_vals`` and the display field above — and adding a second
        SOURCE (the country) to four separate readers is exactly how a module
        ends up installed but unlicensed, or licensed but never installed
        (#461). They all read this now.

        Core modules are NOT included: they are installed unconditionally and
        each consumer subtracts or adds them per its own contract.
        """
        self.ensure_one()
        plan = self.plan_id
        modules = plan.get_allowed_module_list() if plan else []
        for name in self._nc_localization_modules():
            if name not in modules:
                modules.append(name)
        return modules

    def action_activate(self):
        """trial/suspended -> active."""
        self._transition('active')

    def action_suspend(self):
        """active -> suspended."""
        self._transition('suspended')

    def action_expire(self):
        """trial/active/suspended -> expired (terminal)."""
        self._transition('expired')

    # ------------------------------------------------------------------
    # Chatter
    # ------------------------------------------------------------------
    def _track_subtype(self, init_values):
        self.ensure_one()
        if 'status' in init_values:
            return self.env.ref('ncollection_subscription.mt_tenant_status')
        return super()._track_subtype(init_values)
