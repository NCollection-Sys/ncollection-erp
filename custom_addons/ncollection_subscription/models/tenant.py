import uuid

from odoo import SUPERUSER_ID, api, fields, models
from odoo.exceptions import ValidationError

from .localization import localization_package

# Platform base domain (#476). The KEY is the one ncollection_saas.domain has
# always used — the definition moved, the configuration did not, so no
# deployment needs re-configuring.
BASE_DOMAIN_PARAM = 'ncollection_saas.base_domain'
DEFAULT_BASE_DOMAIN = 'ncollectionerp.com'

# NOTE ON VALIDATION. The subdomain format rule, the reserved/offensive word
# lists and the availability probe already exist, battle-tested, in
# ncollection_saas.checkout (they back the public signup flow). They are NOT
# duplicated here: this module cannot import the SaaS layer (the dependency runs
# the other way), so the FIELD is declared here and the SaaS layer constrains it
# — exactly the arrangement `database_name` already uses.


class Tenant(models.Model):
    _name = 'ncollection.tenant'
    _description = 'NCollection Tenant Company'
    _order = 'company_name asc'
    # The record's human name. Without this Odoo falls back to `_rec_name =
    # 'name'`, finds no such field, and `display_name` becomes the literal
    # string "ncollection.tenant,5" — which is what the breadcrumb, every
    # many2one to a tenant, and every chatter subject line have been showing.
    # The model has always had a perfectly good name; nothing pointed at it.
    _rec_name = 'company_name'
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
        # No string=: pylint-odoo strips the _id suffix, so 'Country' is
        # exactly what Odoo labels this anyway (W8113).
        'res.country',
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

    # ------------------------------------------------------------------
    # Subdomain / URL (#476)
    # ------------------------------------------------------------------
    # THREE SEPARATE CONCEPTS, previously two-and-a-half:
    #
    #   company_name    "Atlas Trading LLC"      what a human calls the tenant
    #   database_name   "atlas"                  the Postgres identity
    #   subdomain       "atlas"                  the host label it answers on
    #
    # They were conflated because `ncollection.domain._fqdn_for_tenant()` built
    # the FQDN straight from `database_name`, and a free-text `domain` Char sat
    # on this model that NOTHING read — an operator could type anything into it
    # and it changed nothing at all. That field is gone; this one is the real
    # thing, and `_fqdn_for_tenant()` now reads it.
    subdomain = fields.Char(
        # No string=: Odoo titles this 'Subdomain' from the field name (W8113).
        tracking=True, index=True,
        help="Host label the tenant answers on, e.g. 'atlas' in "
             "atlas.ncollectionerp.com. Defaults to the database name.")
    base_domain = fields.Char(
        compute='_compute_tenant_url',
        help="Platform base domain, configured once in SaaS Settings.")
    tenant_url = fields.Char(
        string='Tenant URL', compute='_compute_tenant_url',
        help="Where this tenant is reachable: <subdomain>.<base domain>.")

    @api.depends('subdomain', 'database_name')
    def _compute_tenant_url(self):
        base = self._nc_base_domain()
        for tenant in self:
            tenant.base_domain = base
            label = tenant._nc_subdomain_label()
            tenant.tenant_url = 'https://%s.%s' % (label, base) if label else False

    def _nc_subdomain_label(self):
        """The host label, falling back to the database name.

        The fallback is what makes this safe to add to a platform that already
        has tenants: every existing one keeps answering on exactly the host it
        answered on before, with no migration and no re-provisioning.
        """
        self.ensure_one()
        return ((self.subdomain or self.database_name) or '').strip().lower()

    @api.model
    def _nc_base_domain(self):
        """The platform base domain — ONE definition, read by both layers.

        `ncollection_saas.domain` owned this and the parameter key; the tenant
        model could not reach it, because the SaaS layer depends on this module
        and not the other way round. Rather than a second copy of the key (the
        arrangement that produces two answers to one question), the definition
        moved here and `ncollection.domain._base_domain()` delegates to it. The
        PARAMETER KEY is unchanged, so no deployment has to be re-configured.
        """
        return (self.env['ir.config_parameter'].sudo().get_param(
            BASE_DOMAIN_PARAM, DEFAULT_BASE_DOMAIN) or '').strip().lower()

    @api.onchange('subdomain')
    def _onchange_subdomain_normalise(self):
        """Lower-case and trim as the operator types, so the value they see is
        the value that will be stored and routed to."""
        for tenant in self:
            if tenant.subdomain:
                tenant.subdomain = tenant.subdomain.strip().lower()

    contact_name = fields.Char(string='Contact Name')
    email = fields.Char(
        help="Contact address for notices and password recovery. This is NOT "
             "the login — the administrator signs in with the username below.")
    phone = fields.Char(string='Phone')

    # ------------------------------------------------------------------
    # Administrator credentials (#476)
    # ------------------------------------------------------------------
    # Provisioning used the EMAIL as the tenant admin's login, because that is
    # what the seed was handed. Odoo's `res.users.login` is a username field
    # that merely accepts an address, so nothing forced that choice — and it
    # meant the customer's contact address and their credential could never
    # diverge: change one and you changed the other.
    admin_login = fields.Char(
        string='Administrator Username', tracking=True, copy=False,
        help="Username the tenant administrator signs in with. Defaults to the "
             "email address when left empty, which is the behaviour every "
             "tenant provisioned before this field had.")
    # WRITE-ONLY, and deliberately not `password=True` alone: it is wiped the
    # moment provisioning succeeds (_nc_clear_admin_password), so the platform
    # database does not keep a usable tenant credential a day longer than the
    # queue takes to drain. Restricted to the SaaS admin group meanwhile.
    admin_password = fields.Char(
        string='Administrator Password', copy=False,
        groups='ncollection_subscription.group_platform_admin',
        help="Set once, used at provisioning, then erased from this record. "
             "Leave empty to have the tenant born hardened: an unguessable "
             "password the owner replaces through the emailed reset link.")
    # Stored, not transient: a non-stored Char cannot be read back by the
    # @api.constrains that compares it, so the mismatch check would never fire
    # — the test that caught this asserted the refusal, not the field's type.
    # It is erased together with the password it confirms.
    admin_password_confirm = fields.Char(
        string='Confirm Password', copy=False,
        groups='ncollection_subscription.group_platform_admin',
        help="Retype the password. Erased with it once provisioning applies it.")
    admin_credentials_state = fields.Selection(
        selection=[
            ('pending', 'Set, awaiting provisioning'),
            ('applied', 'Applied to the tenant database'),
            ('reset_link', 'Owner sets it via the reset link'),
        ],
        string='Administrator Credentials', compute='_compute_admin_credentials_state',
        help="Whether a password is waiting to be applied, has been applied and "
             "erased from here, or was never set.")

    @api.depends('admin_password', 'database_status')
    def _compute_admin_credentials_state(self):
        for tenant in self:
            if tenant.admin_password:
                tenant.admin_credentials_state = 'pending'
            elif tenant.database_status == 'ready':
                tenant.admin_credentials_state = 'applied'
            else:
                tenant.admin_credentials_state = 'reset_link'

    def _nc_admin_login(self):
        """The username the tenant administrator signs in with.

        Falls back to the email so that a tenant created before `admin_login`
        existed — or by any flow that does not set it, such as the public
        checkout — gets exactly the login it would have got before.
        """
        self.ensure_one()
        return ((self.admin_login or self.email) or '').strip()

    @api.constrains('admin_password', 'admin_password_confirm')
    def _check_admin_password_confirmation(self):
        """Catch a mistyped password HERE, where it can still be corrected.

        Without this the mismatch is discovered by the customer, at their first
        login attempt, against a database that has already been built.
        """
        for tenant in self:
            if not tenant.admin_password:
                continue
            # A blank confirmation means the value came from somewhere other
            # than the form (an import, a server action); the form always sends
            # both, so only a genuine mismatch is rejected.
            if tenant.admin_password_confirm and \
                    tenant.admin_password != tenant.admin_password_confirm:
                raise ValidationError(self.env._(
                    "The administrator passwords do not match."))
            if len(tenant.admin_password) < 8:
                raise ValidationError(self.env._(
                    "The administrator password must be at least 8 characters."))

    def _nc_clear_admin_password(self):
        """Erase the password once the tenant database has it.

        The platform holds a usable tenant credential only for the window
        between an operator typing it and the provisioning job draining — and
        the credential lives in the tenant's own database afterwards, which is
        the only place it needs to be.
        """
        holding = self.filtered(
            lambda t: t.admin_password or t.admin_password_confirm)
        if holding:
            holding.sudo().write({
                'admin_password': False, 'admin_password_confirm': False})
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
    # #476: per-tenant deltas against the plan. See tenant_module_override.py.
    module_override_ids = fields.One2many(
        'ncollection.tenant.module.override', 'tenant_id',
        string='Module Overrides')
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

    @api.depends('plan_id', 'plan_id.allowed_module_names', 'country_id',
                 'module_override_ids.module_name', 'module_override_ids.mode',
                 'module_override_ids.active')
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
        """EVERY module this tenant is entitled to.

            plan  UNION  localization package  MINUS  disabled  UNION  added

        THE SINGLE AUTHORITY, deliberately. Four things independently answered
        this question before #469 — provisioning's ``_module_list``, the
        module-install job's ``_nc_licensed_module_list``, config sync's
        ``_config_sync_vals`` and the display field above — and adding a second
        SOURCE to four separate readers is exactly how a module ends up
        installed but unlicensed, or licensed but never installed (#461). They
        all read this, so #476's per-tenant overrides land in one place too.

        ORDER MATTERS. Disabling is applied before adding, so an override that
        both disables and adds the same module is impossible to express (the
        unique constraint) rather than resolved by luck.

        Core modules are NOT included: they are installed unconditionally and
        each consumer subtracts or adds them per its own contract.
        """
        self.ensure_one()
        plan = self.plan_id
        modules = plan.get_allowed_module_list() if plan else []
        for name in self._nc_localization_modules():
            if name not in modules:
                modules.append(name)
        disabled, added = self._nc_module_overrides()
        modules = [name for name in modules if name not in disabled]
        for name in added:
            if name not in modules:
                modules.append(name)
        return modules

    def _nc_module_overrides(self):
        """(disabled, added) technical names for this tenant — #476.

        A LOCALIZATION MODULE CANNOT BE DISABLED. It is not a feature the
        customer bought; it is what makes their books legal, and a chart of
        accounts cannot be un-loaded by revoking the licence — the tables stay,
        the menus vanish, and the tenant is left with accounting they cannot
        see. Same reasoning that keeps those modules out of the plan picker.
        """
        self.ensure_one()
        protected = set(self._nc_localization_modules())
        disabled, added = set(), []
        for override in self.module_override_ids:
            name = (override.module_name or '').strip()
            if not name:
                continue
            if override.mode == 'disable':
                if name not in protected:
                    disabled.add(name)
            elif name not in added:
                added.append(name)
        return disabled, added

    def _nc_push_entitlement_change(self):
        """Re-sync licensing and install anything newly granted.

        Routes through the EXISTING lifecycle rather than a second one: the
        same two hooks a plan edit uses, so an override is delivered to the
        tenant by exactly the path a plan change is — queued, deduplicated and
        retryable. Both hooks live in ncollection_saas, so this is a no-op on a
        platform where only ncollection_subscription is installed; that layer
        overrides this method to do the real work.
        """
        return True

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
