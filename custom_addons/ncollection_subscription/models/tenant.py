import uuid

from odoo import SUPERUSER_ID, fields, models
from odoo.exceptions import ValidationError


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
    # Only the provisioning ENGINE (which sets the `nc_provisioning` context) or a
    # superuser may move a tenant into 'provisioning'/'ready'. A
    # group_platform_admin has write access to ncollection.tenant and could
    # otherwise flip database_status='ready' by hand — the status config-sync keys
    # its cross-DB push on. Harmless today (the unique(database_name) constraint
    # means a record can only point at its OWN db), but a cleaner boundary than
    # "any writer with the group". Superuser/SUPERUSER_ID (migrations, tests,
    # shell, sudo'd flows) stay allowed.
    _NC_ENGINE_ONLY_DB_STATUSES = ('provisioning', 'ready')

    # Guards the WRITE (transition) path only — the vector the assessment names
    # ("a group_platform_admin can still write database_status='ready'"). Creates
    # aren't guarded: only base.group_system / superuser can create a tenant, and
    # every real create uses the 'not_provisioned' default (checkout) or runs as
    # superuser (tests/seed) — there is no non-super create-to-'ready' flow.
    def write(self, vals):
        status = vals.get('database_status')
        if (status in self._NC_ENGINE_ONLY_DB_STATUSES
                and self.env.uid != SUPERUSER_ID and not self.env.su
                and not self.env.context.get('nc_provisioning')):
            raise ValidationError(self.env._(
                "Only the provisioning engine may set a tenant's database status "
                "to '%(status)s'.", status=status))
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
