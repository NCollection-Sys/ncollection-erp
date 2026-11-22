# -*- coding: utf-8 -*-
"""Accounting's part of the Workspace Administration Center under Option A (see ncollection_core's
business_settings.py and res_company_delegation.py): the accounting settings and Accounting Defaults a workspace Owner
changes, now that Odoo's General Settings and a raw res.company write belong to the platform.

Every setting here is a field of Odoo's own ``res.config.settings`` with the same name and meaning, stored where Odoo
stores it (no second settings store). Three of them change the company's ACCOUNTING IDENTITY and get their own handler,
because Odoo's settings page applies them in ways a plain field write does not:

  * Main Currency (``currency_id``): Odoo itself refuses the change once journal items exist
    (account ``res.company.write``); the handler refuses it first, with the same reason, and never bypasses that check.
  * Fiscal Country (``account_fiscal_country_id``): Odoo allows it at any time, but it moves the tax reports and the
    taxes offered to another country. Once the company has accounting entries it is refused here (stricter than
    Odoo, on purpose: an unsafe localization change after posting).
  * Chart of Accounts (``chart_template``): loaded by Odoo's own ``account.chart.template.try_loading``, exactly as
    Odoo's settings page does, and only while the company has no accounting entries (Odoo's page makes it read-only
    then). A chart whose localization module is NOT installed is refused: installing a module is a subscription
    entitlement (ncollection_subscription), never a tenant setting.

The page shows all three read-only, with the reason, when they cannot be changed safely
(``nc_has_accounting_entries``).
Settings that install modules (``module_*``), the irreversible restrictive audit trail and platform-only options are
deliberately NOT offered (docs/markdown/TENANT_ACCOUNTING_SETTINGS.md).
"""
from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError


def _native_selection(name):
    def selection(self):
        return self.env['res.config.settings']._fields[name]._description_selection(self.env)
    return selection


# The company-identity settings and the handler applying each (see the module docstring).
IDENTITY_SETTINGS = {
    # applied in THIS order: the country first, then a chart that must match it, then a currency checked against both
    'account_fiscal_country_id': '_nc_apply_fiscal_country',
    'chart_template': '_nc_apply_chart_template',
    'currency_id': '_nc_apply_currency',
}

# Native settings applied where Odoo stores them ('native' in the spec).
NATIVE_ACCOUNTING_SETTINGS = (
    # Taxes
    'sale_tax_id', 'purchase_tax_id', 'tax_calculation_rounding_method', 'account_price_include',
    'tax_exigibility', 'tax_cash_basis_journal_id', 'account_cash_basis_base_account_id',
    # Currencies
    'group_multi_currency',
    # Invoices / bills
    'qr_code', 'display_invoice_amount_total_words', 'display_invoice_tax_company_currency',
    'incoterm_id', 'group_cash_rounding', 'group_sale_delivery_address', 'use_invoice_terms',
    'show_sale_receipts', 'quick_edit_mode', 'autopost_bills',
    # Customer payments
    'account_use_credit_limit',
    # Analytics
    'group_analytic_accounting',
    # Discount allocation
    'account_discount_income_allocation_id', 'account_discount_expense_allocation_id',
)


class NcBusinessSettings(models.TransientModel):
    _inherit = 'ncollection.business.settings'

    # ------------------------------------------------------------------ company accounting identity
    currency_id = fields.Many2one('res.currency', string='Main Currency')
    account_fiscal_country_id = fields.Many2one('res.country', string='Fiscal Country')
    chart_template = fields.Selection(_native_selection('chart_template'), string='Chart of Accounts')
    nc_has_accounting_entries = fields.Boolean(compute='_compute_nc_accounting_state')
    nc_has_chart = fields.Boolean(compute='_compute_nc_accounting_state')
    nc_is_branch = fields.Boolean(compute='_compute_nc_accounting_state')
    nc_display_account_storno = fields.Boolean(compute='_compute_nc_accounting_state')
    nc_localization_mismatch = fields.Boolean(compute='_compute_nc_accounting_state')
    nc_is_tenant_admin = fields.Boolean(compute='_compute_nc_accounting_state')   # the view hides the app otherwise
    account_storno = fields.Boolean(string='Storno Accounting')

    # ------------------------------------------------------------------ taxes
    sale_tax_id = fields.Many2one(
        'account.tax', string='Default Sales Tax',
        domain="[('type_tax_use', '=', 'sale'), ('company_id', 'parent_of', company_id)]")
    purchase_tax_id = fields.Many2one(
        'account.tax', string='Default Purchase Tax',
        domain="[('type_tax_use', '=', 'purchase'), ('company_id', 'parent_of', company_id)]")
    tax_calculation_rounding_method = fields.Selection(
        _native_selection('tax_calculation_rounding_method'), string='Tax Rounding Method')
    account_price_include = fields.Selection(
        _native_selection('account_price_include'), string='Default Sales Price Include')
    tax_exigibility = fields.Boolean(string='Cash Basis Taxes')
    tax_cash_basis_journal_id = fields.Many2one(
        'account.journal', string='Cash Basis Journal',
        domain="[('type', '=', 'general'), ('company_id', 'parent_of', company_id)]")
    account_cash_basis_base_account_id = fields.Many2one(
        'account.account', string='Cash Basis Base Account', domain="[('company_ids', 'parent_of', company_id)]")

    # ------------------------------------------------------------------ currencies
    group_multi_currency = fields.Boolean(string='Multi-Currencies')

    # ------------------------------------------------------------------ invoices / bills
    qr_code = fields.Boolean(string='QR Codes on Invoices')
    display_invoice_amount_total_words = fields.Boolean(string='Total Amount in Words')
    display_invoice_tax_company_currency = fields.Boolean(string='Taxes in Company Currency')
    incoterm_id = fields.Many2one('account.incoterms', string='Default Incoterm')
    group_cash_rounding = fields.Boolean(string='Cash Rounding')
    group_sale_delivery_address = fields.Boolean(string='Customer Delivery Addresses')
    use_invoice_terms = fields.Boolean(string='Default Terms & Conditions')
    show_sale_receipts = fields.Boolean(string='Sales Receipts')
    quick_edit_mode = fields.Selection(_native_selection('quick_edit_mode'), string='Quick Encoding')
    autopost_bills = fields.Boolean(string='Auto-validate Bills')

    # ------------------------------------------------------------------ customer payments
    account_use_credit_limit = fields.Boolean(string='Sales Credit Limit')
    account_default_credit_limit = fields.Float(string='Default Credit Limit')

    # ------------------------------------------------------------------ analytics, discounts
    group_analytic_accounting = fields.Boolean(string='Analytic Accounting')
    account_discount_income_allocation_id = fields.Many2one(
        'account.account', string='Discount Income Account', domain="[('company_ids', 'parent_of', company_id)]")
    account_discount_expense_allocation_id = fields.Many2one(
        'account.account', string='Discount Expense Account', domain="[('company_ids', 'parent_of', company_id)]")

    @api.depends('company_id')
    def _compute_nc_accounting_state(self):
        for page in self:
            company = self.env.company  # never a client-sent company (it is only ever the current one)
            # a read-only lookup across the company tree, exactly what Odoo's own settings page reads
            company = company.sudo()
            page.nc_has_accounting_entries = company.root_id._existing_accounting()
            page.nc_has_chart = bool(company.chart_template)
            page.nc_is_branch = bool(company.parent_id)
            page.nc_display_account_storno = company.display_account_storno
            page.nc_localization_mismatch = self._nc_localization_mismatch(company)
            page.nc_is_tenant_admin = self.env.su or self.env.user.has_group('ncollection_core.group_tenant_admin')

    # ------------------------------------------------------------------ localization consistency
    @api.model
    def _nc_compatible_charts(self, country):
        """Every chart that belongs to ``country`` (a country may have several, e.g. de_skr03 / de_skr04); the
        generic chart for a country with no localization of its own. Read from Odoo's own template registry."""
        mapping = self.env['account.chart.template'].sudo()._get_chart_template_mapping()
        own = {code for code, template in mapping.items() if country and template.get('country_id') == country.id}
        return own or {'generic_coa'}

    @api.model
    def _nc_localization_mismatch(self, company):
        """True when the company's chart is not the chart of its fiscal country (e.g. an Egyptian company on the
        generic chart with its US taxes). Its accounting identity is then frozen: only a controlled localization
        migration, run by the platform, can bring the two together without losing accounts, taxes or journals."""
        company = company.sudo()
        if not company.chart_template or not company.account_fiscal_country_id:
            return False
        return company.chart_template not in self._nc_compatible_charts(company.account_fiscal_country_id)

    def _nc_migration_required(self, company, detail):
        raise UserError(self.env._(
            "%(detail)s This needs a controlled localization migration (new chart of accounts, taxes and fiscal "
            "positions, with the existing ones mapped and kept), run by the platform administrator. Nothing was "
            "changed.", detail=detail))

    @api.model
    def _nc_settings_spec(self):
        spec = super()._nc_settings_spec()
        spec.update(dict.fromkeys(NATIVE_ACCOUNTING_SETTINGS, 'native'))
        spec.update(IDENTITY_SETTINGS)              # applied in this order: fiscal country, chart, currency ...
        spec['account_storno'] = '_nc_apply_storno'  # ... then storno, which depends on the fiscal country
        spec['account_default_credit_limit'] = '_nc_apply_default_credit_limit'
        return spec

    @api.model
    def _nc_accounting_setting_names(self):
        extra = {'account_storno', 'account_default_credit_limit'}
        return set(NATIVE_ACCOUNTING_SETTINGS) | set(IDENTITY_SETTINGS) | extra

    def _nc_check_value(self, name, value):
        # Accounting controls: a Workspace Administrator only. A delegate of the Business Settings section keeps
        # the sales / purchase / general business settings, never the company's accounting configuration.
        if name in self._nc_accounting_setting_names() and not (
                self.env.su or self.env.user.has_group('ncollection_core.group_tenant_admin')):
            raise AccessError(self.env._("Only a workspace administrator can change the accounting settings."))
        return super()._nc_check_value(name, value)

    # ------------------------------------------------------------------ handlers (called after the level check)
    def _nc_identity_guard(self, company, setting):
        # the company's accounting identity: the Workspace Administrator only, never a delegate of the section
        if not (self.env.su or self.env.user.has_group('ncollection_core.group_tenant_admin')):
            raise AccessError(self.env._(
                "Only a workspace administrator can change %(setting)s.", setting=self._fields[setting].string))
        if company.sudo().root_id._existing_accounting():
            raise UserError(self.env._(
                "%(setting)s cannot be changed: %(company)s already has accounting entries. Changing it now would "
                "make the posted entries, taxes and reports inconsistent.",
                setting=self._fields[setting].string, company=company.name))

    def _nc_apply_currency(self, company, value):
        self._nc_identity_guard(company, 'currency_id')
        if company.parent_id:
            # Odoo delegates the currency to the root company (res.company._get_company_root_delegated_field_names)
            raise UserError(self.env._("A branch uses its parent company's currency: change it on %(parent)s.",
                                       parent=company.parent_id.name))
        currency = self.env['res.currency'].sudo().browse(value).exists()
        if not currency:
            raise ValidationError(self.env._("Choose an existing currency."))
        company = company.sudo()
        if self._nc_localization_mismatch(company):
            self._nc_migration_required(company, self.env._(
                "%(company)s keeps the chart of accounts of another country than its fiscal country, so its main "
                "currency cannot be changed on its own.", company=company.name))
        country = company.account_fiscal_country_id
        if company.chart_template and company.chart_template != 'generic_coa' and country.currency_id \
                and currency != country.currency_id:
            # a country chart (its taxes, tax report, fiscal positions) keeps the books in that country's currency
            raise UserError(self.env._(
                "The %(country)s chart of accounts keeps its books in %(expected)s, not %(currency)s.",
                country=country.name, expected=country.currency_id.name, currency=currency.name))
        foreign = self.env['account.journal'].sudo().search(
            [('company_id', 'child_of', company.id), ('currency_id', '=', currency.id)])
        if foreign:
            raise UserError(self.env._(
                "These journals are kept in %(currency)s as a foreign currency: %(journals)s. Remove that currency "
                "from them first, or they would hold the company currency twice.",
                currency=currency.name, journals=', '.join(foreign.mapped('name'))))
        if not currency.active:
            currency.active = True
        # elevated only after the checks above; Odoo's own res.company.write validates again (refuses after entries)
        company.sudo().write({'currency_id': currency.id})

    def _nc_apply_fiscal_country(self, company, value):
        self._nc_identity_guard(company, 'account_fiscal_country_id')
        country = self.env['res.country'].sudo().browse(value).exists()
        if not country:
            raise ValidationError(self.env._("Choose an existing country."))
        company = company.sudo()
        if company.chart_template and company.chart_template not in self._nc_compatible_charts(country):
            # the existing accounts, taxes, tax report and fiscal positions belong to another country: changing the
            # label alone would make the books LOOK localized while they are not
            self._nc_migration_required(company, self.env._(
                "%(company)s uses a chart of accounts that is not the %(country)s chart; changing only the fiscal "
                "country would keep the other country's accounts and taxes.", company=company.name,
                country=country.name))
        company.write({'account_fiscal_country_id': country.id})

    def _nc_apply_chart_template(self, company, value):
        self._nc_identity_guard(company, 'chart_template')
        if company.chart_template:
            # Odoo's loader REPLACES an existing chart: it deletes the company's accounts, taxes, journals, fiscal
            # positions and draft entries (account.chart.template._load), for the company and its branches. That
            # is a platform operation, never a tenant setting.
            raise UserError(self.env._(
                "%(company)s already has a chart of accounts. Replacing it would delete its accounts, taxes and "
                "journals; ask the platform administrator.", company=company.name))
        Chart = self.env['account.chart.template'].sudo()
        template = Chart._get_chart_template_mapping().get(value)  # visible charts (Odoo's loader)
        if not template:
            raise ValidationError(self.env._("This chart of accounts does not exist."))
        # `installed`: the template's localization module (`module`) is installed in this database
        if not template.get('installed'):
            raise UserError(self.env._(
                "The %(chart)s chart of accounts needs a localization package your subscription does not include. "
                "Ask the platform administrator to enable it.", chart=template.get('name') or value))
        country = company.sudo().account_fiscal_country_id
        if country and value not in self._nc_compatible_charts(country):
            raise UserError(self.env._(
                "The %(chart)s chart of accounts does not belong to the fiscal country %(country)s. Choose the "
                "fiscal country first, then its own chart.", chart=template.get('name') or value,
                country=country.name))
        # Odoo's loader works on the whole company tree (every level of branches) and, with no entries, DELETES the
        # accounts, taxes, journals and fiscal positions it finds there: only a truly empty tree may load a chart.
        tree = self.env['res.company'].sudo().search([('id', 'child_of', company.id)])
        if tree - self.env.user.company_ids:
            raise AccessError(self.env._("The chart would also be loaded on branches you do not belong to."))
        if tree.filtered('chart_template') or any(
                self.env[model].sudo().with_context(active_test=False).search_count([(field, 'in', tree.ids)], limit=1)
                for model, field in (('account.account', 'company_ids'), ('account.tax', 'company_id'),
                                     ('account.journal', 'company_id'), ('account.fiscal.position', 'company_id'))):
            raise UserError(self.env._(
                "%(company)s already has accounts, taxes, journals or fiscal positions (or a branch with a chart). "
                "Loading a chart would delete them; ask the platform administrator.", company=company.name))
        # Odoo's own loader and onboarding, as its settings page calls them (account res_config_settings.set_values)
        Chart.try_loading(value, company=company.sudo())
        company.sudo()._initiate_account_onboardings()

    def _nc_apply_storno(self, company, value):
        company = company.sudo()
        if not company.display_account_storno:
            raise UserError(self.env._("Storno accounting is not available for %(country)s.",
                                       country=company.account_fiscal_country_id.name or company.name))
        if company.parent_id:
            raise UserError(self.env._("A branch follows its parent company's storno setting."))
        if company.root_id._existing_accounting():
            raise UserError(self.env._("Storno accounting cannot change once the company has accounting entries."))
        company.write({'account_storno': bool(value)})

    def _nc_apply_default_credit_limit(self, company, value):
        # the same ir.default row Odoo's settings page writes (_inverse_account_default_credit_limit)
        self.env['ir.default'].sudo().set('res.partner', 'credit_limit', value or 0.0, company_id=company.id)
