from odoo import api, fields, models
from odoo.exceptions import ValidationError

from .localization import PLAN_EXCLUDED_MODULES


class SubscriptionPlan(models.Model):
    _name = 'ncollection.subscription.plan'
    _description = 'NCollection Subscription Plan'
    _order = 'monthly_price asc'

    name = fields.Char(required=True)
    code = fields.Char(required=True, copy=False)
    monthly_price = fields.Monetary(string='Monthly Price')
    yearly_price = fields.Monetary(string='Yearly Price')
    # One-time membership price (#471). A separate field rather than reusing
    # monthly/yearly, because a perpetual licence is not a period price: reusing
    # one would make the plan read as "this much per month" everywhere the other
    # two are shown, and would put a recurring figure into an MRR that is zero
    # by definition for a one-time subscription.
    one_time_price = fields.Monetary(
        string='One-Time Price',
        help='Charged once when a One Time subscription on this plan is '
             'activated. Not a recurring amount — it never renews and it is '
             'excluded from MRR.')
    max_users = fields.Integer(string='Max Users', default=1)
    max_companies = fields.Integer(string='Max Companies', default=1)
    trial_days = fields.Integer(
        default=0,
        help='Length of the free trial for this plan, in days (0 = no trial).')
    grace_days = fields.Integer(
        default=15,
        help='Days after expiry during which access continues before suspension.')
    active = fields.Boolean(default=True)
    description = fields.Text()
    # Comma-separated technical module names (e.g. "crm,sale,stock").
    # Deliberately a Text field, NOT a Many2many to ir.module.module: the
    # platform DB must not couple to any tenant DB's module registry
    # (database-per-tenant isolation - see DELIVERABLE_1 P1-T07).
    allowed_module_names = fields.Text(
        string='Allowed Modules',
        help='Comma-separated technical module names included in this plan, '
             'e.g. "crm,sale,stock". Consumed by the tenant workspace '
             'visibility engine (P1-T09) and license enforcement (P1-T10).',
    )
    currency_id = fields.Many2one(
        'res.currency',
        default=lambda self: self.env.company.currency_id,
    )
    tenant_ids = fields.One2many('ncollection.tenant', 'plan_id', string='Tenants')
    subscription_ids = fields.One2many('ncollection.subscription', 'plan_id', string='Subscriptions')
    tenant_count = fields.Integer(compute='_compute_tenant_count', string='Tenants')

    def _compute_tenant_count(self):
        for plan in self:
            plan.tenant_count = len(plan.tenant_ids)

    @api.constrains('max_users')
    def _check_max_users(self):
        for plan in self:
            if plan.max_users <= 0:
                raise ValidationError(
                    self.env._('Max Users must be strictly positive (plan "%s").', plan.name)
                )

    @api.constrains('trial_days', 'grace_days')
    def _check_trial_grace_days(self):
        for plan in self:
            if plan.trial_days < 0 or plan.grace_days < 0:
                raise ValidationError(
                    self.env._('Trial Days and Grace Days cannot be negative (plan "%s").', plan.name)
                )

    # Installed into EVERY tenant regardless of plan. Mirrors
    # ncollection_saas.provisioning_job.CORE_TENANT_MODULES, which is the
    # authority; this copy exists because ncollection_subscription must not
    # import the SaaS layer (it is installable without it), and it is pinned
    # equal by test_module_catalog.test_the_core_list_matches_provisionings.
    CORE_TENANT_MODULES = ('base', 'ncollection_core', 'ncollection_branding',
                           'ncollection_auth')

    # Platform-layer addons: they run on the PLATFORM database and must never be
    # offered to a tenant (Rule 3 two-layer separation). Same set the CI
    # architecture guard calls PLATFORM_ADDONS.
    PLATFORM_ONLY_MODULES = ('ncollection_saas', 'ncollection_subscription',
                             'ncollection_billing', 'ncollection_reseller')

    # Country localization (#469). NOT a feature an operator buys — it is what
    # makes a tenant's books legal, and it is decided by the tenant's country at
    # provisioning. Offering it here would let someone deselect a live tenant's
    # chart of accounts (which cannot be un-loaded by re-ticking the box), or
    # select the wrong country's for a tenant. The tenant is still LICENSED for
    # these: the licensed set is the plan UNION the tenant's package
    # (ncollection.tenant._nc_effective_module_list), so Ring 1 shows the
    # localization menus without anyone picking a technical module.
    LOCALIZATION_MODULES = PLAN_EXCLUDED_MODULES

    @api.model
    def get_selectable_modules(self):
        """The real modules an admin may license, for the plan module picker (#457).

        Read straight off ``ir.module.module`` — the platform's actual addons
        path — so the picker can never offer something that does not exist, and
        never needs a catalog table to be kept in step (``ncollection.module``
        stays dead code, see its docstring). ``icon_image`` is each module's own
        official icon, the same one Odoo's Apps kanban renders.

        Returns ``{'core': [...], 'optional': [...]}``:

        * **core** — installed into every tenant whatever the plan says, so the
          UI can show them as always-included and refuse to toggle them.
        * **optional** — what the plan actually decides.

        ``application=True`` keeps the list to real apps rather than the
        hundreds of technical dependencies underneath them; a plan that names a
        wrapper module still licenses its dependencies, because Ring 1 expands
        the dependency closure at read time (`_ncollection_expand_dependencies`).
        """
        Module = self.env['ir.module.module'].sudo()
        fields_ = ['name', 'shortdesc', 'summary', 'icon_image', 'state',
                   'application', 'category_id']

        def payload(record):
            return {
                'name': record.name,
                'label': record.shortdesc or record.name,
                'summary': record.summary or '',
                'icon': record.icon_image.decode() if record.icon_image else '',
                'state': record.state,
                # Grouping/filtering only — the picker needs a stable label to
                # bucket a long catalog by. Never consulted for licensing.
                'category': record.category_id.display_name or '',
                # DIRECT manifest dependencies. The picker uses them to SHOW
                # that picking a wrapper already licenses what it needs, so an
                # operator can see why a card counts as included instead of
                # being able to save a plan that names a module without its
                # base. It is NOT a second resolver: the authoritative
                # expansion stays server-side in ncollection_core's Ring 1
                # (_ncollection_expand_dependencies), and provisioning installs
                # the closure whatever this list says.
                'depends': record.dependencies_id.mapped('name'),
            }

        core = Module.search_read(
            [('name', 'in', list(self.CORE_TENANT_MODULES))], fields_)
        never_offer = (list(self.CORE_TENANT_MODULES)
                       + list(self.PLATFORM_ONLY_MODULES)
                       + list(self.LOCALIZATION_MODULES))
        optional = Module.search_read([
            ('application', '=', True),
            ('state', '!=', 'uninstallable'),
            ('name', 'not in', never_offer),
        ], fields_)

        # search_read gives dicts; re-browse for the computed icon rather than
        # duplicating _get_icon_image's logic here.
        def rows(records, drop_uninstallable=False):
            out = []
            for data in records:
                module = Module.browse(data['id'])
                if drop_uninstallable and not self._nc_module_is_installable(module):
                    continue
                out.append(payload(module))
            return sorted(out, key=lambda m: m['label'].lower())

        return {'core': rows(core),
                'optional': rows(optional, drop_uninstallable=True)}

    @api.model
    def _nc_module_is_installable(self, module):
        """Can this module actually be installed on this platform?

        ``state != 'uninstallable'`` is NOT enough. A module whose manifest
        names a dependency that is not on the addons path stays plainly
        ``uninstalled`` — Odoo records the gap on the DEPENDENCY row instead,
        as ``state == 'unknown'`` (``ir.module.module.dependency._compute_state``:
        ``depend_id.state or 'unknown'``).

        ncollection_account_assets is exactly that case: it needs OCA
        ``account_asset_management``, which exists only once ``./oca`` has been
        aggregated (``make oca``), and ``./oca`` is generated and gitignored.
        Offering it on a platform without the engine would let an operator
        license a module whose install job can only fail — on every ready
        tenant of the plan, in the background, long after the click.

        The dependency ``state`` is computed and unstored, so this is a Python
        filter rather than a search domain.
        """
        return not any(dep.state == 'unknown'
                       for dep in module.dependencies_id)

    def get_allowed_module_list(self):
        """Return the plan's allowed modules as a clean, de-duplicated list.

        Parses ``allowed_module_names`` ("crm, sale,stock") into
        ``['crm', 'sale', 'stock']`` - whitespace stripped, empties dropped,
        order preserved, duplicates removed.
        """
        self.ensure_one()
        if not self.allowed_module_names:
            return []
        seen = set()
        result = []
        for raw in self.allowed_module_names.split(','):
            name = raw.strip()
            if name and name not in seen:
                seen.add(name)
                result.append(name)
        return result

    # Odoo 19 silently ignores `_sql_constraints` — use models.Constraint so the
    # unique index is actually created (constraint ncollection_subscription_plan_code_unique).
    _code_unique = models.Constraint(
        'unique(code)', 'The plan code must be unique.')
