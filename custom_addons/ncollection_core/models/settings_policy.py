# -*- coding: utf-8 -*-
"""Unified tenant Settings: ONE place per setting, and the platform decides what a workspace may do with it.

THE MODEL (docs/markdown/WORKSPACE_AND_TENANT_ADMINISTRATION.md, "Settings policy"):

  section   a page of the tenant's Settings app (Users, Roles, Company, Document Layout, ...). The page is the ONE
            management interface of its data; the data stays where it always lived (res.company, res.users,
            ncollection.business.settings, ...): this module stores no setting of its own, only who may do what.
  level     hidden < view < edit < administer.
              hidden      the page does not exist for this workspace (menu removed, writes refused)
              view        the page opens read-only (writes refused server-side)
              edit        Workspace Administrators change it
              administer  as edit, and they may DELEGATE it to named users (only for delegable sections)

EFFECTIVE LEVEL, deterministic, most restrictive wins:
  1. platform restriction  the plan's level, lowered (never raised) by a per-tenant override       (platform, synced)
  2. entitlement           a section whose application is not installed / licensed is hidden       (platform, synced)
  3. tenant delegation     possible only when (1) says administer AND the section is delegable     (tenant)
  4. user role             Odoo's own groups / ACLs / record rules still decide every read & write (tenant)
  5. company               records stay company-scoped by Odoo's rules and the delegation guard     (tenant)
A tenant can only ever narrow what the platform allows. With no policy pushed yet (a workspace synced before this
release) every section is "administer": today's behaviour, so the upgrade changes nothing until the platform decides.

The platform side (ncollection_subscription: plan defaults + tenant overrides, audited) computes the per-section
levels and config-sync pushes them as JSON into ``ncollection.workspace.config.settings_policy``. No cross-database
access: the tenant only ever reads its own config row.
"""
import json

from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError

LEVELS = ('hidden', 'view', 'edit', 'administer')
RANK = {level: i for i, level in enumerate(LEVELS)}
TENANT_ADMIN = 'ncollection_core.group_tenant_admin'

# key, label, menu xml ids, delegable. Security-bearing sections are never delegable: a delegate could otherwise
# grant themselves more than they were given.
SECTIONS = (
    ('overview', 'Overview', ('ncollection_core.menu_workspace_overview',), False),
    ('users', 'Users', ('ncollection_core.menu_workspace_users',), False),
    ('roles', 'Roles & Permissions', ('ncollection_core.menu_workspace_roles',), False),
    ('teams', 'Teams & Departments', ('ncollection_core.menu_workspace_teams',), False),
    ('security', 'Security', ('ncollection_core.menu_workspace_security',), False),
    ('company', 'Company', ('ncollection_core.menu_workspace_company',), True),
    ('business', 'Business Settings', ('ncollection_core.menu_workspace_business_settings',), True),
    ('accounting_defaults', 'Accounting Defaults', ('ncollection_account_core.menu_workspace_accounting_defaults',),
     True),
    ('document_layout', 'Document Layout', ('ncollection_core.menu_workspace_document_layout',), True),
    ('appearance', 'Appearance', ('ncollection_core.menu_workspace_appearance',), True),
    ('document_stamps', 'Document Stamps', ('ncollection_core.menu_workspace_document_stamps',), True),
    ('print_settings', 'Print Settings', ('ncollection_core.menu_workspace_print_settings',), True),
    ('interface_text', 'Interface Text', ('ncollection_core.menu_workspace_interface_text',), True),
    ('sales', 'Sales Settings', ('ncollection_sale_documents.menu_workspace_sales_settings',), True),
    ('purchase', 'Purchase Settings', ('ncollection_purchase_documents.menu_workspace_purchase_settings',), True),
    ('inventory', 'Inventory Settings', ('ncollection_stock_barcode.menu_workspace_inventory_settings',), True),
    ('audit_log', 'Audit Log', ('ncollection_audit.menu_nc_audit_root',), False),
    ('ai', 'AI Assistant', ('ncollection_ai.menu_ai_readiness',), False),
    ('system', 'System', ('ncollection_core.menu_workspace_system',), False),
)
SECTION_KEYS = tuple(key for key, _l, _m, _d in SECTIONS)
DELEGABLE = frozenset(key for key, _l, _m, delegable in SECTIONS if delegable)
# Delegation IS group membership (Odoo's own storage, ACLs and menu cache all key on groups): a delegable section's
# group carries exactly that section's access rights. Two sections reuse the group that already governed them.
SECTION_GROUP = {
    'company': 'ncollection_core.group_settings_company',
    'business': 'ncollection_core.group_settings_business',
    'accounting_defaults': 'ncollection_core.group_settings_accounting_defaults',
    'document_layout': 'ncollection_core.group_settings_document_layout',
    'appearance': 'ncollection_core.group_settings_appearance',
    'document_stamps': 'ncollection_branding.group_stamp_manager',
    'print_settings': 'ncollection_branding.group_stamp_manager',
    'interface_text': 'ncollection_core.group_settings_interface_text',
    'sales': 'ncollection_core.group_settings_sales',
    'purchase': 'ncollection_core.group_settings_purchase',
    'inventory': 'ncollection_core.group_settings_inventory',
}

# PLATFORM-GRANTED BUSINESS CAPABILITIES of the Workspace Administrator. Unlike a section (default administer, so an
# upgrade changes nothing), a capability is OFF unless the platform pushes 'granted': it widens what a Workspace
# Administrator may do, so only Balance Cloud Management may switch it on. Granting = the capability group is implied
# by group_tenant_admin (Odoo 19 computes implied membership, so revoking removes it at once). The group itself is
# Odoo's own access (its ACLs and record rules decide everything); no route or check is bypassed.
#   key -> (label, the group implied while granted)
CAPABILITIES = {
    'receipts': ('Receipts & accounting (read only)', 'ncollection_account_core.group_platform_receipt_access'),
}

# Company fields -> the section whose page owns them (res_company_delegation.py lists the same fields).
COMPANY_FIELD_SECTION = {
    **dict.fromkeys(('name', 'street', 'street2', 'city', 'state_id', 'zip', 'country_id', 'phone', 'email',
                     'website', 'vat', 'company_registry', 'logo'), 'company'),
    **dict.fromkeys(('nc_primary_color', 'nc_secondary_color', 'nc_sidebar_color', 'nc_login_background',
                     'nc_stamp'), 'appearance'),
    **dict.fromkeys(('external_report_layout_id', 'paperformat_id', 'font', 'primary_color', 'secondary_color',
                     'report_header', 'report_footer', 'company_details', 'layout_background',
                     'layout_background_image', 'nc_footer_left', 'nc_footer_center', 'nc_footer_right'),
                    'document_layout'),
    **dict.fromkeys(('nc_default_receivable_id', 'nc_default_payable_id', 'income_account_id', 'expense_account_id',
                     'transfer_account_id', 'account_journal_suspense_account_id',
                     'default_cash_difference_income_account_id', 'default_cash_difference_expense_account_id'),
                    'accounting_defaults'),
    # the company fields of the application sections (Sales / Purchase / Inventory Settings)
    **dict.fromkeys(('quotation_validity_days', 'portal_confirmation_sign', 'portal_confirmation_pay',
                     'prepayment_percent'), 'sales'),
    **dict.fromkeys(('po_lock', 'po_double_validation', 'po_double_validation_amount'), 'purchase'),
    **dict.fromkeys(('annual_inventory_month', 'annual_inventory_day'), 'inventory'),
}


def company_field_section(name):
    """The Settings section owning a delegated company field; any other delegated field is a Business Setting."""
    return COMPANY_FIELD_SECTION.get(name, 'business')


class SettingsPolicy(models.AbstractModel):
    _name = 'ncollection.settings.policy'
    _description = 'Workspace Settings policy (effective levels)'

    @api.model
    def _nc_platform_levels(self):
        """{section: level} as the platform pushed it; every section 'administer' when nothing was pushed."""
        raw = self.env['ncollection.workspace.config'].sudo().search([], limit=1).settings_policy or ''
        try:
            pushed = json.loads(raw) if raw else {}
        except ValueError:
            pushed = {}
        if not isinstance(pushed, dict):
            pushed = {}
        sections = {k: v for k, v in pushed.items() if k not in CAPABILITIES}
        return {key: (pushed.get(key) if pushed.get(key) in RANK else ('administer' if not sections else 'hidden'))
                for key in SECTION_KEYS}

    @api.model
    def _nc_capabilities(self):
        """{capability: bool} as the platform pushed it; every capability OFF when nothing was pushed."""
        raw = self.env['ncollection.workspace.config'].sudo().search([], limit=1).settings_policy or ''
        try:
            pushed = json.loads(raw) if raw else {}
        except ValueError:
            pushed = {}
        if not isinstance(pushed, dict):
            pushed = {}
        return {key: pushed.get(key) == 'granted' for key in CAPABILITIES}

    @api.model
    def _nc_apply_capabilities(self):
        """Make group_tenant_admin imply exactly the granted capability groups. Idempotent; a capability whose
        module is not installed is skipped (nothing to grant)."""
        admin = self.env.ref(TENANT_ADMIN).sudo()
        granted = self._nc_capabilities()
        for key, (_label, xmlid) in CAPABILITIES.items():
            group = self.env.ref(xmlid, raise_if_not_found=False)
            if not group:
                continue
            has = group in admin.implied_ids
            if granted[key] and not has:
                admin.write({'implied_ids': [(4, group.id)]})
            elif not granted[key] and has:
                admin.write({'implied_ids': [(3, group.id)]})
        return granted

    @api.model
    def _nc_level(self, section, user=None):
        """The level THIS user has on ``section`` (platform level, narrowed to the user's standing)."""
        if section not in RANK and section not in SECTION_KEYS:
            raise UserError(self.env._("Unknown settings section."))
        user = user or self.env.user
        platform = self._nc_platform_levels()[section]
        if self.env.su or user.has_group('base.group_system'):
            return platform if platform != 'hidden' else 'hidden'
        if user.has_group(TENANT_ADMIN):
            return platform
        if section in DELEGABLE and platform == 'administer' and user.has_group(SECTION_GROUP[section]):
            return 'edit'
        return 'hidden'

    @api.model
    def _nc_require(self, section, need='edit'):
        """Raise unless the current user reaches ``need`` on ``section``. The platform's own service channel
        (superuser, ``nc_platform_actor``) is the platform acting, and passes."""
        if self.env.su:
            return True
        if RANK[self._nc_level(section)] < RANK[need]:
            label = dict((k, label) for k, label, _m, _d in SECTIONS)[section]
            raise AccessError(self.env._(
                "%(section)s is not available to you on this workspace (%(need)s needed).",
                section=self.env._(label), need=self.env._(need)))
        return True

    @api.model
    def _nc_hidden_menu_ids(self):
        """Menu ids of the sections the platform hides for this workspace (for every user)."""
        hidden = set()
        levels = self._nc_platform_levels()
        for key, _label, menus, _d in SECTIONS:
            if levels[key] == 'hidden':
                for xmlid in menus:
                    menu = self.env.ref(xmlid, raise_if_not_found=False)
                    if menu:
                        hidden.add(menu.id)
        return hidden

    @api.model
    def _nc_menus_without_level(self):
        """Section menus the CURRENT user has no level on. Only filters inside Settings: a user who cannot open the
        Settings app is unaffected (the app root is group-gated already)."""
        if self.env.su:
            return set()
        out = set()
        for key, _label, menus, _d in SECTIONS:
            if self._nc_level(key) == 'hidden':
                for xmlid in menus:
                    menu = self.env.ref(xmlid, raise_if_not_found=False)
                    if menu:
                        out.add(menu.id)
        return out

    @api.model
    def nc_overview(self):
        """The Settings home: one card per section with the user's level (for the OWL page and the tests)."""
        out = []
        for key, label, menus, delegable in SECTIONS:
            menu = next((m for m in (self.env.ref(x, raise_if_not_found=False) for x in menus) if m), None)
            level = self._nc_level(key)
            if not menu or level == 'hidden':
                continue
            out.append({'key': key, 'label': self.env._(label), 'level': level, 'menu_id': menu.id,
                        'action': menu.action and '%s,%s' % (menu.action._name, menu.action.id) or False,
                        'delegable': delegable and level == 'administer'})
        return out


# The delegation screen sits beside the policy it edits.
class SettingsDelegation(models.TransientModel):  # pylint: disable=no-wizard-in-models
    """The delegation screen. Nothing is stored here: saving writes the section groups (the one store)."""
    _name = 'ncollection.settings.delegation'
    _description = 'Settings delegation'

    line_ids = fields.One2many('ncollection.settings.delegation.line', 'wizard_id')

    @api.model
    def _nc_check_admin(self):
        if not (self.env.user.has_group(TENANT_ADMIN) or self.env.user.has_group('base.group_system')):
            raise AccessError(self.env._("Only a Workspace Administrator delegates settings."))

    @api.model
    def action_open(self):
        self._nc_check_admin()
        levels = self.env['ncollection.settings.policy']._nc_platform_levels()
        lines = []
        for key, _label, _m, delegable in SECTIONS:
            group = self.env.ref(SECTION_GROUP.get(key, ''), raise_if_not_found=False) if delegable else None
            if not group or levels[key] == 'hidden':
                continue
            members = group.sudo().user_ids.filtered(lambda u: not u.share and not u.has_group(TENANT_ADMIN))
            lines.append((0, 0, {'section': key, 'allowed': levels[key] == 'administer',
                                 'user_ids': [(6, 0, members.ids)]}))
        wizard = self.create({'line_ids': lines})
        return {'type': 'ir.actions.act_window', 'res_model': self._name, 'res_id': wizard.id, 'view_mode': 'form',
                'target': 'current', 'name': self.env._('Delegated Settings')}

    def action_save(self):
        self._nc_check_admin()
        levels = self.env['ncollection.settings.policy']._nc_platform_levels()
        for line in self.line_ids:
            group = self.env.ref(SECTION_GROUP[line.section]).sudo()
            current = group.user_ids.filtered(lambda u: not u.share and not u.has_group(TENANT_ADMIN))
            wanted = line.user_ids.filtered(lambda u: not u.share)
            if current == wanted:
                continue
            if levels[line.section] != 'administer':
                raise AccessError(self.env._("The platform does not allow delegating %s.", line.section))
            if line.user_ids.filtered('share'):
                raise UserError(self.env._("Settings can only be delegated to internal users."))
            for user in wanted - current:
                user.sudo().write({'group_ids': [(4, group.id)]})
            for user in current - wanted:
                user.sudo().write({'group_ids': [(3, group.id)]})
            self.env['ncollection.label.audit'].sudo().create({
                'user_id': self.env.user.id, 'event': 'write', 'element': 'Delegation: %s' % line.section,
                'old_value': ', '.join(current.mapped('login'))[:250],
                'new_value': ', '.join(wanted.mapped('login'))[:250]})
        return self.action_open()


class SettingsDelegationLine(models.TransientModel):  # pylint: disable=no-wizard-in-models
    _name = 'ncollection.settings.delegation.line'
    _description = 'Settings delegation line'

    wizard_id = fields.Many2one('ncollection.settings.delegation', ondelete='cascade')
    section = fields.Selection([(k, label) for k, label, _m, d in SECTIONS if d], readonly=True)
    allowed = fields.Boolean(string='Platform allows delegation', readonly=True)
    user_ids = fields.Many2many('res.users', string='Delegated to', domain=[('share', '=', False)])


class WorkspaceConfig(models.Model):
    _inherit = 'ncollection.workspace.config'

    settings_policy = fields.Text(
        help="Per-section levels pushed by the platform (JSON). Empty: every section administer (the behaviour "
             "before the settings policy existed).")


class _SectionGuarded(models.AbstractModel):
    """Writes on a section's own records pass the section's policy level (reads are governed by the ACLs)."""
    _name = 'ncollection.settings.guarded'
    _description = 'Settings section guard'
    _nc_section = None

    def check_access(self, operation):
        """Hooked on Odoo's own access check (create / write / unlink all call it), NOT by overriding create /
        write: OCA auditlog patches those methods, and an extra layer there broke its per-row digests."""
        if operation in ('create', 'write', 'unlink') and self._nc_section and not self.env.su:
            self.env['ncollection.settings.policy']._nc_require(self._nc_section, 'edit')
        return super().check_access(operation)


class DocumentStamp(models.Model):
    _name = 'ncollection.document.stamp'
    _inherit = ['ncollection.document.stamp', 'ncollection.settings.guarded']
    _nc_section = 'document_stamps'


class DocumentType(models.Model):
    _name = 'ncollection.document.type'
    _inherit = ['ncollection.document.type', 'ncollection.settings.guarded']
    _nc_section = 'print_settings'
