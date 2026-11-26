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

``page`` rows have side effects in their module's own ``res.config.settings.set_values`` (e.g. Manufacturing work
orders switch the operation type, CRM leads switch every sales team, pricelists create the default pricelist). They
are applied by Odoo's own ``set_values`` on a settings record holding today's values plus the one change, after
every check of this page, so the module's own logic runs. ``execute()`` (which installs modules) is never called.

Deliberately NOT offered (see TENANT_SETTINGS_COVERAGE.md): ``module_*`` (installs apps; platform entitlement),
mail servers, OAuth / signup / 2FA / geolocation / Unsplash / digest / profiling parameters (platform or technical),
paid IAP enrichment, online payment of invoices without a provider, the restrictive audit trail (irreversible),
the Point of Sale shop configuration (``pos_*``: the shop's own form, for Point of Sale managers).
"""
from odoo import api, fields, models
from odoo.exceptions import AccessError

from ..models.settings_policy import COMPANY_FIELD_SECTION

TENANT_ADMIN = 'ncollection_core.group_tenant_admin'

# setting: (module that owns it, Settings section, label, how: 'native' | 'page', admin_only)
APP_SETTINGS = {
    # ---- Sales section: CRM, quotation templates, pricelists, delivery lead time
    'group_use_lead': ('crm', 'sales', 'Leads', 'page', False),
    'group_use_recurring_revenues': ('crm', 'sales', 'Recurring Revenues', 'native', False),
    'is_membership_multi': ('crm', 'sales', 'Multi Teams', 'native', False),
    'group_sale_order_template': ('sale_management', 'sales', 'Quotation Templates', 'page', False),
    'group_product_pricelist': ('product', 'sales', 'Pricelists', 'page', False),
    'default_picking_policy': ('sale_stock', 'sales', 'Picking Policy', 'native', False),
    'use_security_lead': ('sale_stock', 'sales', 'Security Lead Time for Sales', 'native', False),
    'security_lead': ('sale_stock', 'sales', 'Security Lead Time (days)', 'native', False),
    # ---- Purchase section
    'days_to_purchase': ('purchase_stock', 'purchase', 'Days to Purchase', 'native', False),
    # ---- Inventory section
    'group_stock_multi_locations': ('stock', 'inventory', 'Storage Locations', 'page', False),
    'group_stock_adv_location': ('stock', 'inventory', 'Multi-Step Routes', 'page', False),
    'group_stock_lot_print_gs1': ('stock', 'inventory', 'Print GS1 Barcodes for Lots & Serial Numbers', 'native',
                                  False),
    'horizon_days': ('stock', 'inventory', 'Replenishment Horizon (days)', 'native', False),
    'stock_move_email_validation': ('stock', 'inventory', 'Email Confirmation of Deliveries', 'native', False),
    'group_lot_on_invoice': ('stock_account', 'inventory', 'Display Lots & Serial Numbers on Invoices', 'native',
                             False),
    'product_weight_in_lbs': ('product', 'inventory', 'Weight unit of measure', 'native', False),
    'product_volume_volume_in_cubic_feet': ('product', 'inventory', 'Volume unit of measure', 'native', False),
    # ---- Business Settings section: the other applications
    'group_mrp_routings': ('mrp', 'business', 'Work Orders', 'page', False),
    'group_mrp_workorder_dependencies': ('mrp', 'business', 'Work Order Dependencies', 'page', False),
    'group_mrp_byproducts': ('mrp', 'business', 'By-Products', 'native', False),
    'group_unlocked_by_default': ('mrp', 'business', 'Unlock Manufacturing Orders', 'native', False),
    'group_mrp_reception_report': ('mrp', 'business', 'Allocation Report for Manufacturing Orders', 'native', False),
    'group_project_stages': ('project', 'business', 'Project Stages', 'page', False),
    'contract_expiration_notice_period': ('hr', 'business', 'Contract Expiry Notice Period (days)', 'native', False),
    'work_permit_expiration_notice_period': ('hr', 'business', 'Work Permit Expiry Notice Period (days)', 'native',
                                             False),
    'hr_presence_control_login': ('hr', 'business', 'Presence based on user status', 'native', False),
    'hr_presence_control_email': ('hr', 'business', 'Presence based on emails sent', 'native', False),
    'hr_presence_control_email_amount': ('hr', 'business', 'Number of emails', 'native', False),
    'hr_presence_control_ip': ('hr', 'business', 'Presence based on IP address', 'native', False),
    'hr_presence_control_ip_list': ('hr', 'business', 'Valid IP addresses', 'native', False),
    'attendance_kiosk_mode': ('hr_attendance', 'business', 'Attendance Mode', 'page', False),
    'attendance_barcode_source': ('hr_attendance', 'business', 'Barcode Source', 'page', False),
    'attendance_kiosk_delay': ('hr_attendance', 'business', 'Kiosk Delay (seconds)', 'page', False),
    'attendance_kiosk_use_pin': ('hr_attendance', 'business', 'Employee PIN Identification', 'page', False),
    'attendance_from_systray': ('hr_attendance', 'business', 'Attendance From Systray', 'page', False),
    'attendance_device_tracking': ('hr_attendance', 'business', 'Device & Location Tracking', 'page', False),
    'attendance_overtime_validation': ('hr_attendance', 'business', 'Extra Hours Validation', 'page', False),
    'hr_attendance_display_overtime': ('hr_attendance', 'business', 'Display Extra Hours', 'page', False),
    'auto_check_out': ('hr_attendance', 'business', 'Automatic Check Out', 'page', False),
    'auto_check_out_tolerance': ('hr_attendance', 'business', 'Auto Check Out Tolerance (hours)', 'page', False),
    'absence_management': ('hr_attendance', 'business', 'Absence Management', 'page', False),
    'hr_expense_use_mailgateway': ('hr_expense', 'business', 'Record expenses by email', 'page', False),
    'group_pos_preset': ('point_of_sale', 'business', 'Presets', 'native', False),
    'update_stock_quantities': ('point_of_sale', 'business', 'Update quantities in stock', 'native', False),
    'point_of_sale_use_ticket_qr_code': ('point_of_sale', 'business', 'Self-service invoicing', 'native', False),
    'point_of_sale_ticket_unique_code': ('point_of_sale', 'business', 'Generate a code on ticket', 'native', False),
    'group_mass_mailing_campaign': ('mass_mailing', 'business', 'Mailing Campaigns', 'page', False),
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


def _native_selection(name):
    """The native field's own options, read at runtime; none while its module is not installed."""
    def selection(self):
        field = self.env['res.config.settings']._fields.get(name)
        return field._description_selection(self.env) if field else []
    return selection


def _declare(name):
    label = APP_SETTINGS[name][2]
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
            spec[name] = 'native' if row[3] == 'native' else 'settings_page'
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
        return super()._nc_check_value(name, value)

    def _nc_apply_via_settings_page(self, company, name, value):
        """Odoo's own ``set_values`` on a settings record of THIS company holding today's values plus this one change:
        the module's side effects run exactly as on its own settings page. Elevated only here, after the level, the
        type and (for accounting rows) the administrator checks of ``action_nc_save``; never ``execute()``."""
        page = self.env['res.config.settings'].sudo().with_company(company).with_context(
            allowed_company_ids=[company.id]).create({name: value})
        page.set_values()



# The res.company field behind each 'native' company setting above (Odoo stores them as related fields). Only these
# join the Workspace Administrator's company delegation; 'page' rows are applied by Odoo's own set_values and are
# never writable on res.company directly.
COMPANY_FIELDS = {
    'security_lead': 'security_lead', 'days_to_purchase': 'days_to_purchase', 'horizon_days': 'horizon_days',
    'stock_move_email_validation': 'stock_move_email_validation',
    'contract_expiration_notice_period': 'contract_expiration_notice_period',
    'work_permit_expiration_notice_period': 'work_permit_expiration_notice_period',
    'hr_presence_control_login': 'hr_presence_control_login', 'hr_presence_control_email': 'hr_presence_control_email',
    'hr_presence_control_email_amount': 'hr_presence_control_email_amount',
    'hr_presence_control_ip': 'hr_presence_control_ip', 'hr_presence_control_ip_list': 'hr_presence_control_ip_list',
    'update_stock_quantities': 'point_of_sale_update_stock_quantities',
    'point_of_sale_use_ticket_qr_code': 'point_of_sale_use_ticket_qr_code',
    'point_of_sale_ticket_unique_code': 'point_of_sale_ticket_unique_code',
    'snailmail_color': 'snailmail_color', 'snailmail_cover': 'snailmail_cover', 'snailmail_duplex': 'snailmail_duplex',
    'vat_check_vies': 'vat_check_vies', 'l10n_gcc_dual_language_invoice': 'l10n_gcc_dual_language_invoice',
    'reconcile_aggregate': 'reconcile_aggregate',
}
ADMIN_ONLY_COMPANY_FIELDS = frozenset(COMPANY_FIELDS[name] for name, row in APP_SETTINGS.items()
                                      if row[4] and name in COMPANY_FIELDS)
# the Settings section of each company field (settings_policy.company_field_section), so a delegate of Sales /
# Purchase / Inventory Settings may change exactly its own section's fields
COMPANY_FIELD_SECTION.update({field: APP_SETTINGS[name][1] for name, field in COMPANY_FIELDS.items()
                              if APP_SETTINGS[name][1] != 'business'})


class ResCompany(models.Model):
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
        if set(vals) & ADMIN_ONLY_COMPANY_FIELDS and not self.env.user.has_group(TENANT_ADMIN):
            # accounting controls: never a section delegate, also on a direct company write
            raise AccessError(self.env._("Only a workspace administrator can change the accounting settings."))
        return result
