# -*- coding: utf-8 -*-
"""Settings of OPTIONAL applications on the unified tenant Settings page (``ncollection.business.settings``).

No NCollection module depends on CRM, Employees, Attendances, Expenses, Manufacturing, Project, Point of Sale,
Email Marketing, Snail Mail, quotation templates, sale/purchase stock links, stock valuation, VAT verification or
the GCC invoice layout, so their settings had nowhere to live: a tenant that has the app installed could not reach
them under Option A (the Owner is refused Odoo's own ``res.config.settings``).

ONE table (``APP_SETTINGS``) declares them. A row is offered only while the native setting exists on
``res.config.settings`` (= its module is installed for THIS tenant), so a tenant without the app sees nothing of it
and cannot write it. Everything else is the business settings page's own machinery: the code-defined list, the
platform's section level checked for every changed value, the current company only, the audit.

Only scalar settings are declared here (on/off, choices, numbers, short text): a many2one to a model of an optional
module cannot be a field of a model whose own module does not depend on it. Those records (journals, templates,
working hours, nomenclatures) keep their own native screens and rights.

``admin_only`` rows are accounting controls: like the Accounting app, a Workspace Administrator only, never a
delegate of the Business Settings section.

``effect`` rows have a side effect in their module's own ``res.config.settings.set_values``. Odoo's whole
``set_values`` is NEVER run from here: it runs every installed module's save logic, rewrites configuration
parameters and archives records. Each such row has its own ``_nc_set_<setting>`` that applies the setting where Odoo
stores it and then ONLY the side effect that belongs to that one setting, and only when the value really changes.
Where Odoo's side effect would archive or rewrite business records (pricelists, work-center operations, BoM
dependencies), the switch-off is REFUSED while such records exist, with the reason; the user archives them on their
own screen, deliberately. ``execute()`` (which installs modules) is never called.

Settings that are stored on the company apply to the current company only. Feature groups and configuration
parameters are tenant-wide in Odoo; their label says "(all companies)".

Deliberately NOT offered (see TENANT_SETTINGS_COVERAGE.md): ``module_*`` (installs apps; platform entitlement),
mail servers, OAuth / signup / 2FA / geolocation / Unsplash / digest / profiling parameters (platform or technical),
paid IAP enrichment, online payment of invoices without a provider, the restrictive audit trail (irreversible),
the Point of Sale shop configuration (``pos_*``: the shop's own form, for Point of Sale managers).
"""
from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

from ..models.settings_policy import COMPANY_FIELD_SECTION

TENANT_ADMIN = 'ncollection_core.group_tenant_admin'
STOCK_MANAGER = 'stock.group_stock_manager'

# setting: (module that owns it, Settings section, label, how: 'native' | 'effect', admin_only)
APP_SETTINGS = {
    # ---- Sales section: CRM, quotation templates, pricelists, delivery lead time
    'group_use_lead': ('crm', 'sales', 'Leads', 'effect', False),
    'group_use_recurring_revenues': ('crm', 'sales', 'Recurring Revenues', 'native', False),
    'is_membership_multi': ('crm', 'sales', 'Multi Teams', 'native', False),
    'group_sale_order_template': ('sale_management', 'sales', 'Quotation Templates', 'effect', False),
    'group_product_pricelist': ('product', 'sales', 'Pricelists', 'effect', False),
    'default_picking_policy': ('sale_stock', 'sales', 'Picking Policy', 'native', False),
    'use_security_lead': ('sale_stock', 'sales', 'Security Lead Time for Sales', 'effect', False),
    'security_lead': ('sale_stock', 'sales', 'Security Lead Time (days)', 'native', False),
    # ---- Purchase section
    'days_to_purchase': ('purchase_stock', 'purchase', 'Days to Purchase', 'native', False),
    # ---- Inventory section
    'group_stock_multi_locations': ('stock', 'inventory', 'Storage Locations', 'effect', False),
    'group_stock_adv_location': ('stock', 'inventory', 'Multi-Step Routes', 'native', False),
    'group_stock_lot_print_gs1': ('stock', 'inventory', 'Print GS1 Barcodes for Lots & Serial Numbers', 'native',
                                  False),
    'horizon_days': ('stock', 'inventory', 'Replenishment Horizon (days)', 'native', False),
    'stock_move_email_validation': ('stock', 'inventory', 'Email Confirmation of Deliveries', 'native', False),
    'group_lot_on_invoice': ('stock_account', 'inventory', 'Display Lots & Serial Numbers on Invoices', 'native',
                             False),
    'product_weight_in_lbs': ('product', 'inventory', 'Weight unit of measure', 'native', False),
    'product_volume_volume_in_cubic_feet': ('product', 'inventory', 'Volume unit of measure', 'native', False),
    # ---- Business Settings section: the other applications
    'group_mrp_routings': ('mrp', 'business', 'Work Orders', 'effect', False),
    'group_mrp_workorder_dependencies': ('mrp', 'business', 'Work Order Dependencies', 'effect', False),
    'group_mrp_byproducts': ('mrp', 'business', 'By-Products', 'native', False),
    'group_unlocked_by_default': ('mrp', 'business', 'Unlock Manufacturing Orders', 'effect', False),
    'group_mrp_reception_report': ('mrp', 'business', 'Allocation Report for Manufacturing Orders', 'native', False),
    'group_project_stages': ('project', 'business', 'Project Stages', 'effect', False),
    'contract_expiration_notice_period': ('hr', 'business', 'Contract Expiry Notice Period (days)', 'native', False),
    'work_permit_expiration_notice_period': ('hr', 'business', 'Work Permit Expiry Notice Period (days)', 'native',
                                             False),
    'hr_presence_control_login': ('hr', 'business', 'Presence based on user status', 'native', False),
    'hr_presence_control_email': ('hr', 'business', 'Presence based on emails sent', 'native', False),
    'hr_presence_control_email_amount': ('hr', 'business', 'Number of emails', 'native', False),
    'hr_presence_control_ip': ('hr', 'business', 'Presence based on IP address', 'native', False),
    'hr_presence_control_ip_list': ('hr', 'business', 'Valid IP addresses', 'native', False),
    'attendance_kiosk_mode': ('hr_attendance', 'business', 'Attendance Mode', 'native', False),
    'attendance_barcode_source': ('hr_attendance', 'business', 'Barcode Source', 'native', False),
    'attendance_kiosk_delay': ('hr_attendance', 'business', 'Kiosk Delay (seconds)', 'native', False),
    'attendance_kiosk_use_pin': ('hr_attendance', 'business', 'Employee PIN Identification', 'native', False),
    'attendance_from_systray': ('hr_attendance', 'business', 'Attendance From Systray', 'native', False),
    'attendance_device_tracking': ('hr_attendance', 'business', 'Device & Location Tracking', 'native', False),
    'attendance_overtime_validation': ('hr_attendance', 'business', 'Extra Hours Validation', 'native', False),
    'hr_attendance_display_overtime': ('hr_attendance', 'business', 'Display Extra Hours', 'native', False),
    'auto_check_out': ('hr_attendance', 'business', 'Automatic Check Out', 'native', False),
    'auto_check_out_tolerance': ('hr_attendance', 'business', 'Auto Check Out Tolerance (hours)', 'native', False),
    'absence_management': ('hr_attendance', 'business', 'Absence Management', 'native', False),
    'hr_expense_use_mailgateway': ('hr_expense', 'business', 'Record expenses by email', 'native', False),
    'group_pos_preset': ('point_of_sale', 'business', 'Presets', 'native', False),
    'update_stock_quantities': ('point_of_sale', 'business', 'Update quantities in stock', 'native', False),
    'point_of_sale_use_ticket_qr_code': ('point_of_sale', 'business', 'Self-service invoicing', 'native', False),
    'point_of_sale_ticket_unique_code': ('point_of_sale', 'business', 'Generate a code on ticket', 'native', False),
    'group_mass_mailing_campaign': ('mass_mailing', 'business', 'Mailing Campaigns', 'effect', False),
    'show_blacklist_buttons': ('mass_mailing', 'business', 'Blacklist Option when Unsubscribing', 'native', False),
    'mass_mailing_reports': ('mass_mailing', 'business', '24H Stat Mailing Reports', 'native', False),
    'snailmail_color': ('snailmail', 'business', 'Print In Color', 'native', False),
    'snailmail_cover': ('snailmail', 'business', 'Add a Cover Page', 'native', False),
    'snailmail_duplex': ('snailmail', 'business', 'Print Both Sides', 'native', False),
    # ---- accounting controls (Workspace Administrator only)
    'vat_check_vies': ('base_vat', 'business', 'Verify VAT Numbers', 'native', True),
    'l10n_gcc_dual_language_invoice': ('l10n_gcc_invoice', 'business', 'GCC Formatted Invoices', 'native', True),
    'reconcile_aggregate': ('account_reconcile_oca', 'business', 'Reconcile Aggregate', 'native', True),
}
APP_MODULES = tuple(sorted({row[0] for row in APP_SETTINGS.values()}))

_TYPES = {
    'group_use_lead': 'boolean', 'group_use_recurring_revenues': 'boolean', 'is_membership_multi': 'boolean',
    'group_sale_order_template': 'boolean', 'group_product_pricelist': 'boolean',
    'default_picking_policy': 'selection', 'use_security_lead': 'boolean', 'security_lead': 'float',
    'days_to_purchase': 'float', 'group_stock_multi_locations': 'boolean', 'group_stock_adv_location': 'boolean',
    'group_stock_lot_print_gs1': 'boolean', 'horizon_days': 'float', 'stock_move_email_validation': 'boolean',
    'group_lot_on_invoice': 'boolean', 'product_weight_in_lbs': 'selection',
    'product_volume_volume_in_cubic_feet': 'selection', 'group_mrp_routings': 'boolean',
    'group_mrp_workorder_dependencies': 'boolean', 'group_mrp_byproducts': 'boolean',
    'group_unlocked_by_default': 'boolean', 'group_mrp_reception_report': 'boolean',
    'group_project_stages': 'boolean', 'contract_expiration_notice_period': 'integer',
    'work_permit_expiration_notice_period': 'integer', 'hr_presence_control_login': 'boolean',
    'hr_presence_control_email': 'boolean', 'hr_presence_control_email_amount': 'integer',
    'hr_presence_control_ip': 'boolean', 'hr_presence_control_ip_list': 'char',
    'attendance_kiosk_mode': 'selection', 'attendance_barcode_source': 'selection',
    'attendance_kiosk_delay': 'integer', 'attendance_kiosk_use_pin': 'boolean', 'attendance_from_systray': 'boolean',
    'attendance_device_tracking': 'boolean', 'attendance_overtime_validation': 'selection',
    'hr_attendance_display_overtime': 'boolean', 'auto_check_out': 'boolean', 'auto_check_out_tolerance': 'float',
    'absence_management': 'boolean', 'hr_expense_use_mailgateway': 'boolean', 'group_pos_preset': 'boolean',
    'update_stock_quantities': 'selection', 'point_of_sale_use_ticket_qr_code': 'boolean',
    'point_of_sale_ticket_unique_code': 'boolean', 'group_mass_mailing_campaign': 'boolean',
    'show_blacklist_buttons': 'boolean', 'mass_mailing_reports': 'boolean', 'snailmail_color': 'boolean',
    'snailmail_cover': 'boolean', 'snailmail_duplex': 'boolean', 'vat_check_vies': 'boolean',
    'l10n_gcc_dual_language_invoice': 'boolean', 'reconcile_aggregate': 'selection',
}

# Upper bounds of the numeric settings (the native fields have none; the page already refuses a negative number).
MAXIMUM = {
    'security_lead': 365, 'days_to_purchase': 365, 'horizon_days': 365,
    'contract_expiration_notice_period': 365, 'work_permit_expiration_notice_period': 365,
    'hr_presence_control_email_amount': 1000, 'attendance_kiosk_delay': 3600, 'auto_check_out_tolerance': 24,
}
MAX_TEXT = 1000

# The res.company field behind each company setting above (Odoo stores them as related fields): these apply to the
# CURRENT company only, and only these join the Workspace Administrator's company delegation. Every other row is a
# feature group or a configuration parameter, tenant-wide in Odoo.
COMPANY_FIELDS = {
    'security_lead': 'security_lead', 'days_to_purchase': 'days_to_purchase', 'horizon_days': 'horizon_days',
    'stock_move_email_validation': 'stock_move_email_validation',
    'contract_expiration_notice_period': 'contract_expiration_notice_period',
    'work_permit_expiration_notice_period': 'work_permit_expiration_notice_period',
    'hr_presence_control_login': 'hr_presence_control_login', 'hr_presence_control_email': 'hr_presence_control_email',
    'hr_presence_control_email_amount': 'hr_presence_control_email_amount',
    'hr_presence_control_ip': 'hr_presence_control_ip', 'hr_presence_control_ip_list': 'hr_presence_control_ip_list',
    'attendance_kiosk_mode': 'attendance_kiosk_mode', 'attendance_barcode_source': 'attendance_barcode_source',
    'attendance_kiosk_delay': 'attendance_kiosk_delay', 'attendance_kiosk_use_pin': 'attendance_kiosk_use_pin',
    'attendance_from_systray': 'attendance_from_systray', 'attendance_device_tracking': 'attendance_device_tracking',
    'attendance_overtime_validation': 'attendance_overtime_validation',
    'hr_attendance_display_overtime': 'hr_attendance_display_overtime', 'auto_check_out': 'auto_check_out',
    'auto_check_out_tolerance': 'auto_check_out_tolerance', 'absence_management': 'absence_management',
    'update_stock_quantities': 'point_of_sale_update_stock_quantities',
    'point_of_sale_use_ticket_qr_code': 'point_of_sale_use_ticket_qr_code',
    'point_of_sale_ticket_unique_code': 'point_of_sale_ticket_unique_code',
    'snailmail_color': 'snailmail_color', 'snailmail_cover': 'snailmail_cover', 'snailmail_duplex': 'snailmail_duplex',
    'vat_check_vies': 'vat_check_vies', 'l10n_gcc_dual_language_invoice': 'l10n_gcc_dual_language_invoice',
    'reconcile_aggregate': 'reconcile_aggregate',
}
TENANT_WIDE = frozenset(name for name in APP_SETTINGS if name not in COMPANY_FIELDS)
ADMIN_ONLY_COMPANY_FIELDS = frozenset(COMPANY_FIELDS[name] for name, row in APP_SETTINGS.items()
                                      if row[4] and name in COMPANY_FIELDS)
# the Settings section of each company field (settings_policy.company_field_section), so a delegate of Sales /
# Purchase / Inventory Settings may change exactly its own section's fields
COMPANY_FIELD_SECTION.update({field: APP_SETTINGS[name][1] for name, field in COMPANY_FIELDS.items()
                              if APP_SETTINGS[name][1] != 'business'})


def _native_selection(name):
    """The native field's own options, read at runtime; none while its module is not installed."""
    def selection(self):
        field = self.env['res.config.settings']._fields.get(name)
        return field._description_selection(self.env) if field else []
    return selection


def _label(name):
    label = APP_SETTINGS[name][2]
    return '%s (all companies)' % label if name in TENANT_WIDE else label


def _declare(name):
    label = _label(name)
    kind = _TYPES[name]
    if kind == 'selection':
        return fields.Selection(_native_selection(name), string=label)
    return {'boolean': fields.Boolean, 'integer': fields.Integer, 'float': fields.Float,
            'char': fields.Char}[kind](string=label)


class NcBusinessSettings(models.TransientModel):
    _inherit = 'ncollection.business.settings'

    for _name in APP_SETTINGS:
        locals()[_name] = _declare(_name)
    del _name
    # space-separated list of the optional applications installed here (drives the view only)
    nc_apps = fields.Char(compute='_compute_nc_apps')

    def _compute_nc_apps(self):
        apps = ' %s ' % ' '.join(self._nc_installed_app_modules())   # padded: "' mrp ' in nc_apps" in the view
        for page in self:
            page.nc_apps = apps

    @api.model
    def _nc_installed_app_modules(self):
        native = self.env['res.config.settings']._fields
        return sorted({APP_SETTINGS[name][0] for name in APP_SETTINGS if name in native})

    @api.model
    def _nc_app_settings(self):
        """The rows whose native setting exists here (their module is installed for this tenant)."""
        native = self.env['res.config.settings']._fields
        return {name: row for name, row in APP_SETTINGS.items()
                if name in native and native[name].type == _TYPES[name]}

    @api.model
    def _nc_settings_spec(self):
        spec = super()._nc_settings_spec()
        for name, row in self._nc_app_settings().items():
            spec[name] = 'native' if row[3] == 'native' else '_nc_set_%s' % name
        return spec

    @api.model
    def _nc_setting_sections(self):
        sections = super()._nc_setting_sections()
        sections.update({name: row[1] for name, row in self._nc_app_settings().items() if row[1] != 'business'})
        return sections

    def _nc_check_value(self, name, value):
        row = APP_SETTINGS.get(name)
        if row and row[4] and not (self.env.su or self.env.user.has_group(TENANT_ADMIN)):
            raise AccessError(self.env._("Only a workspace administrator can change the accounting settings."))
        others = self.env['res.company'].sudo().search_count([('id', 'not in', self.env.user.company_ids.ids)])
        if name in TENANT_WIDE and not self.env.su and others:
            # its effect reaches every company: never decided by someone who does not belong to all of them
            raise AccessError(self.env._(
                "%(setting)s applies to every company: only a user of all the companies can change it.",
                setting=self.env['res.config.settings']._fields[name].string))
        result = super()._nc_check_value(name, value)
        string = self.env['res.config.settings']._fields[name].string
        if name in MAXIMUM and (value or 0) > MAXIMUM[name]:
            raise ValidationError(self.env._("%(setting)s cannot be more than %(maximum)s.", setting=string,
                                             maximum=MAXIMUM[name]))
        if row and _TYPES[name] == 'char' and value and len(value) > MAX_TEXT:
            raise ValidationError(self.env._("%(setting)s is too long.", setting=string))
        return result

    # ------------------------------------------------------------------ settings with a side effect
    # Each one runs only when the value changed (action_nc_save skips unchanged values), after the section level,
    # type and administrator checks. The setting itself is applied where Odoo stores it (_nc_apply_native); the
    # side effect is the one Odoo's own set_values attaches to THIS setting, nothing else.

    def _nc_set_group_use_lead(self, company, value):
        """crm: every team that uses opportunities follows the Leads switch, and its mail alias is refreshed."""
        self._nc_apply_native(company, 'group_use_lead', value)
        teams = self.env['crm.team'].sudo().search([('id', '!=', False)])
        teams.filtered('use_opportunities').use_leads = value
        for team in teams:
            team.alias_id.write(team._alias_get_creation_values())

    def _nc_set_group_sale_order_template(self, company, value):
        """sale_management: with templates off, no company keeps a default template (they could no longer be seen)."""
        if not value:
            self.env['res.company'].sudo().search([('sale_order_template_id', '!=', False)]).write(
                {'sale_order_template_id': False})
        self._nc_apply_native(company, 'group_sale_order_template', value)

    def _nc_set_group_product_pricelist(self, company, value):
        """product: switching on creates / reactivates each company's default pricelist (Odoo's own helper).
        Switching off would make Odoo archive EVERY pricelist; instead it is refused while a pricelist that changes
        prices (rules, or another currency) is active, and nothing is archived: an empty pricelist in the company's
        currency prices exactly like no pricelist."""
        if value:
            self._nc_apply_native(company, 'group_product_pricelist', value)
            self.env['res.company'].sudo()._activate_or_create_pricelists()
            return
        pricing = self.env['product.pricelist'].sudo().search([('id', '!=', False)]).filtered(
            lambda pl: pl.item_ids or pl.currency_id != (pl.company_id or company).currency_id)
        if pricing:
            own = pricing.filtered(lambda pl: not pl.company_id or pl.company_id in self.env.user.company_ids)
            raise UserError(self.env._(
                "Pricelists cannot be switched off while pricelists with price rules or another currency are active "
                "(%(names)s). Archive them in Sales > Products > Pricelists first.",
                names=', '.join(own[:5].mapped('display_name')) or '...'))
        self._nc_apply_native(company, 'group_product_pricelist', value)
        if 'pos.config' in self.env:
            # point_of_sale: a shop never keeps using a feature that is switched off
            self.env['pos.config'].sudo().search([('use_pricelist', '=', True)]).use_pricelist = False

    def _nc_set_use_security_lead(self, company, value):
        """sale_stock: switching the lead time off sets it to 0 for this company (Odoo's own page does this in an
        onchange); otherwise deliveries would still be planned that many days early."""
        self._nc_apply_native(company, 'use_security_lead', value)
        if not value and company.security_lead:
            company.write({'security_lead': 0.0})

    def _nc_set_group_unlocked_by_default(self, company, value):
        """mrp: open manufacturing orders follow the setting, as on Odoo's own page (its onchange)."""
        self._nc_apply_native(company, 'group_unlocked_by_default', value)
        self.env['mrp.production'].sudo().search([('state', 'not in', ('cancel', 'done')),
                                                  ('is_locked', '=', bool(value))]).is_locked = not value

    def _nc_set_group_stock_multi_locations(self, company, value):
        """stock: the warehouses' internal operation type and the location screens follow Storage Locations, exactly
        as in Odoo; an Inventory Manager only (Odoo skips that part for anyone else, leaving a half-applied
        setting)."""
        if not self.env.user.has_group(STOCK_MANAGER):
            raise AccessError(self.env._(
                "Only an Inventory Manager can change the warehouse structure (storage locations)."))
        implied = self.env.ref('base.group_user').implied_ids
        both = {self.env.ref('stock.group_stock_multi_warehouses'), self.env.ref('stock.group_stock_multi_locations')}
        if not value and all(group in implied for group in both):
            raise UserError(self.env._(
                "You can't deactivate the multi-location if you have more than once warehouse by company"))
        self._nc_apply_native(company, 'group_stock_multi_locations', value)
        warehouses = self.env['stock.warehouse'].sudo().with_context(active_test=True)
        views = [self.env.ref(xmlid, raise_if_not_found=False) for xmlid in (
            'stock.stock_location_view_tree2_editable', 'stock.stock_location_view_form_editable')]
        if value:
            warehouses.search([('id', '!=', False)]).int_type_id.active = True
        else:
            warehouses.search([('reception_steps', '=', 'one_step'),
                               ('delivery_steps', '=', 'ship_only')]).int_type_id.active = False
        for view in views:
            if view:
                view.sudo().active = not value

    def _nc_set_group_mrp_routings(self, company, value):
        """mrp: switching Work Orders off would make Odoo archive EVERY operation of every bill of materials;
        refused instead while operations are active (archive them on the bill of materials, deliberately)."""
        if not value and self.env['mrp.routing.workcenter'].sudo().search_count([], limit=1):
            raise UserError(self.env._(
                "Work Orders cannot be switched off while bills of materials have operations. Remove or archive the "
                "operations first."))
        self._nc_apply_native(company, 'group_mrp_routings', value)

    def _nc_set_group_mrp_workorder_dependencies(self, company, value):
        """mrp: switching dependencies off would make Odoo clear them on every bill of materials; refused instead."""
        if not value and self.env['mrp.bom'].sudo().search_count([('allow_operation_dependencies', '=', True)],
                                                                 limit=1):
            raise UserError(self.env._(
                "Work Order Dependencies cannot be switched off while bills of materials use them. Turn them off on "
                "those bills of materials first."))
        self._nc_apply_native(company, 'group_mrp_workorder_dependencies', value)

    def _nc_set_group_project_stages(self, company, value):
        """project: the "Project Stage Changed" message type is shown only while stages are in use."""
        self._nc_apply_native(company, 'group_project_stages', value)
        subtype = self.env.ref('project.mt_project_stage_change', raise_if_not_found=False)
        subtype = subtype.sudo() if subtype else subtype
        if subtype and subtype.hidden == value:
            subtype.hidden = not value

    def _nc_set_group_mass_mailing_campaign(self, company, value):
        """mass_mailing: the A/B testing scheduler runs only while campaigns are in use."""
        self._nc_apply_native(company, 'group_mass_mailing_campaign', value)
        cron = self.env.ref('mass_mailing.ir_cron_mass_mailing_ab_testing', raise_if_not_found=False)
        cron = cron.sudo() if cron else cron
        if cron and cron.active != value:
            cron.active = value


# the company side of THIS table, kept next to it rather than merged into res_company_delegation.py
class ResCompany(models.Model):  # pylint: disable=consider-merging-classes-inherited
    _inherit = 'res.company'

    @api.model
    def _nc_tenant_admin_company_fields(self):
        return super()._nc_tenant_admin_company_fields() | set(COMPANY_FIELDS.values())

    @api.model
    def _nc_delegated_writable_fields(self):
        allowed = super()._nc_delegated_writable_fields()
        if not self.env.user.has_group(TENANT_ADMIN):
            allowed -= ADMIN_ONLY_COMPANY_FIELDS
        return allowed

    def _nc_check_delegated_values(self, vals):
        result = super()._nc_check_delegated_values(vals)
        for name, field in COMPANY_FIELDS.items():
            if field not in vals:
                continue
            value = vals[field]
            if name in MAXIMUM and isinstance(value, (int, float)) and not 0 <= value <= MAXIMUM[name]:
                raise ValidationError(self.env._("%(setting)s must be between 0 and %(maximum)s.",
                                                 setting=APP_SETTINGS[name][2], maximum=MAXIMUM[name]))
            if isinstance(value, str) and len(value) > MAX_TEXT:
                raise ValidationError(self.env._("%(setting)s is too long.", setting=APP_SETTINGS[name][2]))
        if set(vals) & ADMIN_ONLY_COMPANY_FIELDS and not self.env.user.has_group(TENANT_ADMIN):
            # accounting controls: never a section delegate, also on a direct company write
            raise AccessError(self.env._("Only a workspace administrator can change the accounting settings."))
        return result
