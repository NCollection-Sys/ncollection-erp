# -*- coding: utf-8 -*-
"""Business Settings of the Workspace Administration Center (Option A).

The tenant Owner is not base.group_erp_manager, so Odoo's own General Settings (``res.config.settings``) is refused to
them - deliberately: that wizard also installs modules and edits technical configuration. The business settings a
company owner legitimately changes (quotation validity, purchase approval, default taxes, ...) are offered here
instead, and only those.

  * The settings that can be changed are an explicit, code-defined list (``_nc_settings_spec``), extended by the module
    that owns each setting (sales, purchase, accounting). A client can submit nothing else: each one is a field of
    this model, and no model, field, method, domain or expression name is ever taken from the request.
  * Every value is validated against the native field (type, selection, sign) before it is applied.
  * Each setting is applied where Odoo itself stores it: a company field (through res.company's Workspace
    Administrator delegation, so its own-company and many2one checks apply), a configuration parameter, or a feature
    group (Odoo's own ``res.groups._apply_group`` / ``_remove_group``). ``res.config.settings.execute()`` is never
    called, so no module is ever installed from here.
  * Always the current company (``env.company``), never a company sent by the client. Every change is audited.
  * Each setting belongs to a SECTION of the unified Settings (``_nc_setting_sections``: Sales, Purchase, Inventory;
    anything else is Business Settings). The platform's level for that section, narrowed to the user, decides it:
    hidden = not offered and not readable from here, view = shown read-only, edit = saved. Checked server-side for
    every changed value, so a delegate of Sales Settings can change sales settings and nothing else.
"""
from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

TENANT_ADMIN = 'ncollection_core.group_tenant_admin'


class NcBusinessSettings(models.TransientModel):
    _name = 'ncollection.business.settings'
    _description = 'Business Settings'

    company_id = fields.Many2one('res.company', readonly=True, default=lambda self: self.env.company)
    # The user's level on each section this page shows ('hidden' outside the page's own section, when opened from
    # one section's menu). Drives the view only; the server checks again on save.
    nc_level_business = fields.Char(compute='_compute_nc_levels')
    nc_level_sales = fields.Char(compute='_compute_nc_levels')
    nc_level_purchase = fields.Char(compute='_compute_nc_levels')
    nc_level_inventory = fields.Char(compute='_compute_nc_levels')

    _nc_page_sections = ('business', 'sales', 'purchase', 'inventory')

    @api.model
    def _nc_setting_sections(self):
        """{setting: section} for settings that are not Business Settings. Extended by the owning modules."""
        return {}

    @api.model
    def _nc_section_of(self, name):
        return self._nc_setting_sections().get(name, 'business')

    @api.model
    def _nc_page_levels(self):
        """{section: the user's level}, restricted to the one section of the menu the page was opened from."""
        policy = self.env['ncollection.settings.policy']
        only = self.env.context.get('nc_settings_section')
        return {key: (policy._nc_level(key) if (not only or only == key) else 'hidden')
                for key in self._nc_page_sections}

    def _compute_nc_levels(self):
        levels = self._nc_page_levels()
        for page in self:
            for key in self._nc_page_sections:
                page['nc_level_%s' % key] = levels[key]

    @api.model
    def _nc_settings_spec(self):
        """{setting: how it is applied}. Each setting is also a field of res.config.settings with the same meaning,
        and a field of this model. ``'native'``: where Odoo stores it (company field, configuration parameter or
        feature group). Any other value names a method of this model taking (company, value), for a setting Odoo's
        settings page stores another way. Modules add their own entries; nothing outside this list is ever written."""
        return {}

    @api.model
    def _nc_require_tenant_admin(self):
        """At least one section of this page is open to the user (a Workspace Administrator, or a delegate); a page
        opened for ONE section (its menu, its URL, an RPC with that context) needs at least view on that section, so a
        hidden section is refused outright rather than opened empty."""
        only = self.env.context.get('nc_settings_section')
        if only in self._nc_page_sections and not self.env.su:
            self.env['ncollection.settings.policy']._nc_require(only, 'view')
        if self.env.su or self.env.user.has_group(TENANT_ADMIN):
            return
        if not any(level != 'hidden' for level in self._nc_page_levels().values()):
            raise AccessError(self.env._("Only a workspace administrator can change the business settings."))

    @api.model
    def _nc_native_values(self, names):
        """The current values, read the way Odoo's own settings page reads them (an unsaved settings record)."""
        if not names:
            return {}
        current = self.env['res.config.settings'].sudo().with_company(self.env.company).create({})
        values = {}
        for name in names:
            value = current[name]
            values[name] = value.id if isinstance(value, models.BaseModel) else value
        return values

    @api.model
    def default_get(self, fields_list):
        self._nc_require_tenant_admin()
        res = super().default_get(fields_list)
        levels = self._nc_page_levels()
        res.update(self._nc_native_values([name for name in self._nc_settings_spec() if name in fields_list
                                           and levels[self._nc_section_of(name)] != 'hidden']))
        return res

    def _nc_check_value(self, name, value):
        field = self.env['res.config.settings']._fields[name]
        if field.type == 'boolean' and not isinstance(value, bool):
            raise ValidationError(self.env._("%(setting)s must be on or off.", setting=field.string))
        if field.type == 'selection' and value and value not in field.get_values(self.env):
            raise ValidationError(self.env._("%(setting)s has no such option.", setting=field.string))
        if field.type in ('integer', 'float', 'monetary') and (value or 0) < 0:
            raise ValidationError(self.env._("%(setting)s cannot be negative.", setting=field.string))

    def _nc_apply_native(self, company, name, value):
        field = self.env['res.config.settings']._fields[name]
        related = field.related
        if related:
            path = related.split('.') if isinstance(related, str) else list(related)
            if len(path) == 2 and path[0] == 'company_id':
                company.write({path[1]: value})        # res.company's Workspace Administrator delegation
                return
        elif getattr(field, 'config_parameter', None):
            self.env['ir.config_parameter'].sudo().set_param(field.config_parameter, value)
            return
        elif getattr(field, 'implied_group', None):
            # ``group`` may name several groups, comma-separated (Odoo's own res_config reads it the same way).
            groups = self.env['res.groups'].sudo()
            for xmlid in (getattr(field, 'group', None) or 'base.group_user').split(','):
                groups |= self.env.ref(xmlid.strip()).sudo()
            implied = self.env.ref(field.implied_group).sudo()
            if value:
                groups._apply_group(implied)
            else:
                groups._remove_group(implied)
            return
        elif getattr(field, 'default_model', None) and name.startswith('default_'):
            # A default value Odoo's settings page stores as ir.default (res_config.py ``set_values`` does the same).
            self.env['ir.default'].sudo().set(field.default_model, name[len('default_'):], value)
            return
        raise UserError(self.env._("%(setting)s cannot be changed here.", setting=field.string))

    def action_nc_save(self):
        self.ensure_one()
        self._nc_require_tenant_admin()
        spec = self._nc_settings_spec()
        company = self.env.company                     # never a client-supplied company
        levels = self._nc_page_levels()
        visible = [name for name in spec if levels[self._nc_section_of(name)] != 'hidden']
        before = self._nc_native_values(visible)
        changed = []
        policy = self.env['ncollection.settings.policy']
        for name, how in spec.items():
            if name not in visible:
                continue                               # never read from here, never written from here
            value = self[name]
            value = value.id if isinstance(value, models.BaseModel) else value
            if value == before.get(name) or (not value and not before.get(name)):
                continue
            policy._nc_require(self._nc_section_of(name), 'edit')
            self._nc_check_value(name, value)
            if how == 'native':
                self._nc_apply_native(company, name, value)
            else:
                getattr(self, how)(company, value)
            changed.append(name)
        if changed:
            self.env.user._nc_audit('admin_settings_change', 'business settings of company %s: %s' % (
                company.id, ', '.join(sorted(changed))))
        action = self.env['ir.actions.act_window']._for_xml_id('ncollection_core.action_nc_business_settings')
        only = self.env.context.get('nc_settings_section')
        if only in self._nc_page_sections:
            action['context'] = {'nc_settings_section': only}
        return action
