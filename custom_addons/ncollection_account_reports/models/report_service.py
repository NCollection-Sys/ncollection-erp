# -*- coding: utf-8 -*-
"""The reporting hub: ONE catalog, ONE viewer service.

THE CATALOG IS THE SINGLE SOURCE OF TRUTH for how reports are organised. It
drives three things from the same data so they cannot drift apart:

* the menu tree (``_nc_arrange_menus``): every report menu is filed under its
  business category, whichever module owns it;
* the Reports landing page (``nc_report_catalog``): category cards listing only
  what the CURRENT user may actually open;
* the duplicates that are hidden (OCA reports that NCollection's native engine
  supersedes — ADR #15 / #117 retire them).

Nothing here computes an accounting figure. ``nc_report_run`` builds the same
engine wizard the PDF / XLSX exports use and returns its rows, so the screen,
the PDF and the XLSX cannot disagree. Odoo's accounting data is the only source.
"""
import logging

from odoo import api, models
from odoo.exceptions import AccessError, UserError, ValidationError

_logger = logging.getLogger(__name__)

# Filter fields the engine wizards share. Anything else a caller sends is ignored.
_SCALAR_FILTERS = ('company_id', 'date_from', 'date_to', 'target_move',
                   'comparison_type', 'comparison_date_from', 'comparison_date_to')
_M2M_FILTERS = ('journal_ids', 'account_ids', 'partner_ids')

# OCA reports the native engine replaces. Hidden (never deleted, never
# uninstalled): the OCA modules are still installed and still work by URL/RPC.
_SUPERSEDED_OCA_MENUS = (
    'account_financial_report.menu_general_ledger_wizard',
    'account_financial_report.menu_trial_balance_wizard',
    'account_financial_report.menu_aged_partner_balance_wizard',
)

# Containers that were only ever a folder for the reports above. Deactivated
# once EMPTY — never while they still hold something a user can open.
_FOLDER_MENUS = (
    'account_financial_report.menu_oca_reports',
    'account.account_reports_legal_statements_menu',
    'account.account_reports_partners_reports_menu',
    'account.account_reports_taxes_and_fiscal_menu',
    'account.account_reports_management_menu',
    'mis_builder.mis_report_finance_menu',
    'account.menu_finance_reports',
    'sale.menu_sale_report',
    'purchase.purchase_report_main',
    'stock.menu_warehouse_report',
    'crm.crm_menu_report',
    'project.menu_project_report',
    'hr.hr_menu_hr_reports',
)

# Arabic for the report names this module sets on OTHER modules' menus. A menu
# name is record data, not a code string, so renaming it in English alone leaves
# Odoo's own Arabic name behind - and Odoo's names are the wrong ones here ("Sales"
# for what is now "Sales Analysis"). Keyed by the English name written above.
_ARABIC_NAMES = {
    'Account Balances': 'أرصدة الحسابات',
    'Fixed Asset Register': 'سجل الأصول الثابتة',
    'Sales Analysis': 'تحليل المبيعات',
    'Sales by Customer': 'المبيعات حسب العميل',
    'Sales by Product': 'المبيعات حسب المنتج',
    'Sales by Salesperson': 'المبيعات حسب مندوب المبيعات',
    'Invoice Analysis': 'تحليل الفواتير',
    'Purchase Analysis': 'تحليل المشتريات',
    'Purchases by Vendor': 'المشتريات حسب المورّد',
    'Purchases by Product': 'المشتريات حسب المنتج',
    'Stock Analysis': 'تحليل المخزون',
    'Inventory Valuation': 'تقييم المخزون',
    'Stock Movement History': 'سجل حركة المخزون',
    'Stock Move Analysis': 'تحليل حركات المخزون',
    'VAT Report': 'تقرير ضريبة القيمة المضافة',
    'UAE VAT 201 Return': 'إقرار ضريبة القيمة المضافة 201 — الإمارات',
    'Sales Pipeline': 'مسار المبيعات',
    'Lead Analysis': 'تحليل العملاء المحتملين',
    'Activity Analysis': 'تحليل الأنشطة',
    'Sales Forecast': 'توقعات المبيعات',
    'Task Analysis': 'تحليل المهام',
    'Budget vs Actual': 'الموازنة مقابل الفعلي',
    'MIS Reports': 'تقارير MIS',
    'Last Reports Generated': 'آخر التقارير المُنشأة',
    'Journal Ledger': 'دفتر اليومية',
    'Open Items': 'البنود المفتوحة',
}
_ARABIC_LANG = 'ar_001'

_CATEGORY_ICONS = {
    'financial': 'fa-balance-scale',
    'receivables': 'fa-exchange',
    'sales': 'fa-line-chart',
    'purchases': 'fa-shopping-basket',
    'inventory': 'fa-cubes',
    'tax': 'fa-percent',
    'management': 'fa-bar-chart',
}


# A menu is visible only if its PARENT is. Re-filing a report out of its app's
# "Reporting" folder therefore silently widens who sees it (a Sales user would
# suddenly see Sales Analysis, which natively needs Sales / Administrator). The
# folder a menu used to live under is known by the module that owns it, and the
# groups on that folder are carried onto the menu so moving it changes where it
# is shown, never who is shown it. The underlying ACL/record rules already
# refused such users; this keeps the menu consistent with them.
_ORIGIN_FOLDER_BY_MODULE = {
    'sale': 'sale.menu_sale_report',
    'purchase': 'purchase.purchase_report_main',
    'stock': 'stock.menu_warehouse_report',
    'crm': 'crm.crm_menu_report',
    'project': 'project.menu_project_report',
    'hr': 'hr.hr_menu_hr_reports',
    'hr_skills': 'hr.hr_menu_hr_reports',
    'mis_builder': 'mis_builder.mis_report_finance_menu',
    'account': 'account.menu_finance_reports',
    'account_financial_report': 'account.menu_finance_reports',
    'ncollection_account_assets': 'account.menu_finance_reports',
    'ncollection_account_budget': 'account.menu_finance_reports',
}


class NcReportService(models.AbstractModel):
    _name = 'ncollection.report.service'
    _description = 'NCollection report hub and viewer service'

    # ------------------------------------------------------------------
    # The catalog
    # ------------------------------------------------------------------
    @api.model
    def _nc_catalog(self):
        """[(category_key, [ (menu xmlid, new English name or None), ... ])].

        Order is display order. A report that exists in only one place is listed
        once. Menus whose module is not installed are skipped at use time.
        """
        return [
            ('financial', [
                ('ncollection_account_reports.menu_report_trial_balance', None),
                ('ncollection_account_reports.menu_report_general_ledger', None),
                ('ncollection_account_reports.menu_report_balance_sheet', None),
                ('ncollection_account_reports.menu_report_profit_loss', None),
                ('ncollection_account_reports.menu_report_cash_flow', None),
                ('ncollection_account_reports.menu_report_equity_changes', None),
                ('ncollection_account_reports.menu_report_reference', 'Account Balances'),
                ('ncollection_account_reports.menu_report_financial_summary', None),
                ('account_financial_report.menu_journal_ledger_wizard', None),
                ('ncollection_account_assets.menu_report_asset_register', 'Fixed Asset Register'),
            ]),
            ('receivables', [
                ('ncollection_account_reports.menu_report_partner_ledger', None),
                ('ncollection_account_reports.menu_report_partner_statement', None),
                ('ncollection_account_reports.menu_report_aged_partner', None),
                ('account_financial_report.menu_open_items_wizard', None),
            ]),
            ('sales', [
                ('sale.menu_reporting_sales', 'Sales Analysis'),
                ('sale.menu_reporting_customer', 'Sales by Customer'),
                ('sale.menu_reporting_product', 'Sales by Product'),
                ('sale.menu_reporting_salespeople', 'Sales by Salesperson'),
                ('account.menu_action_account_invoice_report_all', 'Invoice Analysis'),
            ]),
            ('purchases', [
                ('purchase.purchase_report', 'Purchase Analysis'),
                ('ncollection_account_reports.menu_purchase_by_vendor', 'Purchases by Vendor'),
                ('ncollection_account_reports.menu_purchase_by_product', 'Purchases by Product'),
            ]),
            ('inventory', [
                ('stock.menu_product_stock', 'Stock Analysis'),
                ('stock.menu_valuation', 'Inventory Valuation'),
                ('stock.stock_move_line_menu', 'Stock Movement History'),
                ('stock.stock_move_menu', 'Stock Move Analysis'),
            ]),
            ('tax', [
                ('account_financial_report.menu_vat_report_wizard', 'VAT Report'),
                ('ncollection_account_reports.menu_report_uae_vat', 'UAE VAT 201 Return'),
            ]),
            ('management', [
                ('ncollection_account_reports.menu_report_profitability', None),
                ('ncollection_account_reports.menu_report_revenue_analysis', None),
                ('ncollection_account_reports.menu_report_expense_analysis', None),
                ('ncollection_account_reports.menu_report_cost_center', None),
                ('ncollection_account_reports.menu_report_profit_center', None),
                ('ncollection_account_reports.menu_report_department', None),
                ('ncollection_account_budget.menu_report_budget_actual', None),
                ('account.menu_action_analytic_reporting', None),
                ('mis_builder.mis_report_instance_view_menu', None),
                ('mis_builder.last_wizard_mis_report_instance_view_menu', None),
                ('crm.crm_opportunity_report_menu', 'Sales Pipeline'),
                ('crm.crm_opportunity_report_menu_lead', 'Lead Analysis'),
                ('crm.crm_activity_report_menu', 'Activity Analysis'),
                ('crm.crm_menu_forecast', 'Sales Forecast'),
                ('project.menu_project_report_task_analysis', 'Task Analysis'),
                ('project.rating_rating_menu_project', None),
            ]),
        ]

    @api.model
    def _nc_category_meta(self):
        """Display text, in the user's language (literals so they are translatable)."""
        _ = self.env._
        return {
            'financial': (_("Financial & Accounting"),
                          _("Statements, ledgers and account balances")),
            'receivables': (_("Receivables & Payables"),
                            _("Partner ledgers, statements and ageing")),
            'sales': (_("Sales"), _("Revenue by customer, product and salesperson")),
            'purchases': (_("Purchases"), _("Spend by vendor and product")),
            'inventory': (_("Inventory"), _("Stock levels, valuation and movements")),
            'tax': (_("Tax"), _("VAT returns and tax reporting")),
            'management': (_("Management & Analytics"),
                           _("Profitability, budgets and analytical views")),
        }

    # ------------------------------------------------------------------
    # Menu arrangement (data-level: idempotent, tolerant of absent modules)
    # ------------------------------------------------------------------
    # Purchase has one analysis menu where Sales has four. These two are the same
    # report grouped the way the Sales ones are; they are created from the
    # existing action (never a second report) and only when purchase is installed.
    _GROUPED_PURCHASE = (
        ('action_purchase_by_vendor', 'menu_purchase_by_vendor', 'Purchases by Vendor', 'partner_id'),
        ('action_purchase_by_product', 'menu_purchase_by_product', 'Purchases by Product', 'product_id'),
    )

    @api.model
    def _nc_ensure_grouped_menus(self):
        base = self.env.ref('purchase.purchase_report', raise_if_not_found=False)
        if not base or not base.action:
            return
        IMD = self.env['ir.model.data'].sudo()
        for action_name, menu_name, title, group in self._GROUPED_PURCHASE:
            existing = self.env.ref(
                'ncollection_account_reports.%s' % menu_name, raise_if_not_found=False)
            if existing:
                # menus created before they carried the base menu's groups
                if not existing.group_ids and base.group_ids:
                    existing.sudo().write({'group_ids': [(6, 0, base.group_ids.ids)]})
                continue
            context = dict(self._nc_eval_ctx(base.action.context), group_by=group)
            action = base.action.sudo().copy({'name': title, 'context': repr(context), 'path': False})
            menu = self.env['ir.ui.menu'].sudo().create({
                'name': title, 'parent_id': base.parent_id.id,
                'action': '%s,%s' % (action._name, action.id), 'sequence': 90,
                'group_ids': [(6, 0, base.group_ids.ids)]})
            for name, rec in ((action_name, action), (menu_name, menu)):
                IMD.create({'module': 'ncollection_account_reports', 'name': name,
                            'model': rec._name, 'res_id': rec.id, 'noupdate': True})

    @api.model
    def _nc_arrange_menus(self):
        """File every catalogued report under its category and hide what the
        native engine supersedes. Safe to run repeatedly: it writes only what
        differs, and does nothing for modules that are not installed.

        Run at every registry load (see ``ir.ui.menu._register_hook``) because
        upgrading the module that owns a menu (``-u sale``) resets that menu's
        parent to the one in its own data file.
        """
        Menu = self.env['ir.ui.menu'].sudo().with_context(active_test=False)
        changed = 0
        self._nc_ensure_grouped_menus()
        installed_langs = {code for code, _name in self.env['res.lang'].get_installed()}
        for key, entries in self._nc_catalog():
            category = self.env.ref(
                'ncollection_account_reports.menu_nc_reports_%s' % key,
                raise_if_not_found=False)
            if not category:
                continue
            for seq, (xmlid, new_name) in enumerate(entries, start=1):
                menu = self.env.ref(xmlid, raise_if_not_found=False)
                if not menu or menu._name != 'ir.ui.menu':
                    continue
                menu = Menu.browse(menu.id)
                vals = {}
                if menu.parent_id != category:
                    vals['parent_id'] = category.id
                if menu.sequence != seq * 10:
                    vals['sequence'] = seq * 10
                inherited = self._nc_origin_groups(xmlid, menu)
                if inherited:
                    vals['group_ids'] = [(6, 0, inherited.ids)]
                if not menu.active:
                    # A report we catalogued must be reachable; if an operator
                    # hid it on purpose they hide it via groups, not archiving.
                    vals['active'] = True
                if vals:
                    menu.write(vals)
                    changed += 1
                if new_name and menu.with_context(lang='en_US').name != new_name:
                    menu.with_context(lang='en_US').write({'name': new_name})
                    changed += 1
                english = new_name or menu.with_context(lang='en_US').name
                arabic = _ARABIC_NAMES.get(english)
                if arabic and _ARABIC_LANG in installed_langs \
                        and menu.with_context(lang=_ARABIC_LANG).name != arabic:
                    menu.update_field_translations('name', {_ARABIC_LANG: arabic})
                    changed += 1
        for xmlid in _SUPERSEDED_OCA_MENUS:
            menu = self.env.ref(xmlid, raise_if_not_found=False)
            if menu and menu.active:
                Menu.browse(menu.id).write({'active': False})
                changed += 1
        for xmlid in _FOLDER_MENUS:
            folder = self.env.ref(xmlid, raise_if_not_found=False)
            if folder and folder.active:
                folder = Menu.browse(folder.id)
                if not folder.child_id.filtered('active') and not folder.action:
                    folder.write({'active': False})
                    changed += 1
        if changed:
            _logger.info("ncollection_account_reports: re-filed %s report menu field(s)",
                         changed)
        return changed

    @api.model
    def _nc_origin_groups(self, xmlid, menu):
        """Groups to carry onto a menu from the folder it was filed out of.
        Only when the menu has none of its own (its own groups, if any, were
        always part of the effective rule and stay)."""
        if menu.group_ids:
            return self.env['res.groups']
        folder_xmlid = _ORIGIN_FOLDER_BY_MODULE.get(xmlid.split('.')[0])
        folder = folder_xmlid and self.env.ref(folder_xmlid, raise_if_not_found=False)
        return folder.sudo().group_ids if folder else self.env['res.groups']

    @api.model
    def _nc_report_purposes(self):
        """One line on what each report is FOR (shown on the hub card). Literals,
        so they are translatable; a report without an entry simply shows none."""
        _ = self.env._
        p = 'ncollection_account_reports.menu_report_'
        return {
            p + 'trial_balance': _("Opening, movement and closing balance of every account for a period."),
            p + 'general_ledger': _("Every journal item by account, with a running balance."),
            p + 'balance_sheet': _("Assets, liabilities and equity as of a date, with comparison."),
            p + 'profit_loss': _("Income and expenses for a period, with comparison."),
            p + 'cash_flow': _("Cash movements by operating, investing and financing activity."),
            p + 'equity_changes': _("How equity moved over the period."),
            p + 'reference': _("Debit, credit and balance per account - the quickest check."),
            p + 'financial_summary': _("The key financial figures on one page."),
            p + 'asset_register': _("Fixed assets with depreciation and net book value."),
            p + 'partner_ledger': _("Receivable and payable movements per customer or vendor."),
            p + 'partner_statement': _("A printable statement to send to a customer or vendor."),
            p + 'aged_partner': _("Outstanding receivables or payables, by age."),
            p + 'profitability': _("Revenue, cost and margin."),
            p + 'revenue_analysis': _("Where revenue comes from, with comparison."),
            p + 'expense_analysis': _("Where the money goes, with comparison."),
            p + 'cost_center': _("Results by cost centre."),
            p + 'profit_center': _("Results by profit centre."),
            p + 'department': _("Results by department."),
            p + 'budget_actual': _("Approved budget against actual, with variance."),
            p + 'uae_vat': _("The FTA VAT 201 return, box by box."),
            'sale.menu_reporting_sales': _("Sales revenue and quantities over time."),
            'sale.menu_reporting_customer': _("Sales grouped by customer."),
            'sale.menu_reporting_product': _("Sales grouped by product."),
            'sale.menu_reporting_salespeople': _("Sales grouped by salesperson."),
            'account.menu_action_account_invoice_report_all': _("Invoiced amounts by period, partner and product."),
            'purchase.purchase_report': _("Purchase orders and spend over time."),
            'ncollection_account_reports.menu_purchase_by_vendor': _("Purchases grouped by vendor."),
            'ncollection_account_reports.menu_purchase_by_product': _("Purchases grouped by product."),
            'stock.menu_product_stock': _("Stock on hand by product and location."),
            'stock.menu_valuation': _("Value of the stock on hand."),
            'stock.stock_move_line_menu': _("Every stock movement, line by line."),
            'stock.stock_move_menu': _("Stock movements analysed by product, location and period."),
            'account.menu_action_analytic_reporting': _("Analytic items by account and plan."),
            'account_financial_report.menu_journal_ledger_wizard': _("Every entry of a journal, in order."),
            'account_financial_report.menu_open_items_wizard': _("Unreconciled items per partner at a date."),
            'account_financial_report.menu_vat_report_wizard': _("Tax base and tax amount per tax code."),
        }

    @api.model
    def _nc_report_badges(self, action):
        """What a user can DO with a report, as short labels - derived from the
        action, never declared: the viewer reports offer PDF, Excel and
        drill-down; Odoo's analysis views offer pivot, graph and export."""
        _ = self.env._
        if action._name == 'ir.actions.client' and 'report_viewer' in (action.tag or ''):
            return [_("PDF"), _("Excel"), _("Drill-down")]
        modes = (getattr(action, 'view_mode', '') or '').split(',')
        badges = []
        if 'pivot' in modes:
            badges.append(_("Pivot"))
        if 'graph' in modes:
            badges.append(_("Graph"))
        if modes and badges:
            badges.append(_("Export"))
        return badges

    # ------------------------------------------------------------------
    # Landing page
    # ------------------------------------------------------------------
    @api.model
    def nc_report_catalog(self):
        """What the Reports hub renders: only reports THIS user may open.

        Visibility comes from Odoo's own menu filter (groups + the Ring 1 plan
        licensing ``ncollection_core`` adds), plus read access on the model the
        action opens — so a user never sees a card that ends in an access error.
        """
        meta = self._nc_category_meta()
        purposes = self._nc_report_purposes()
        Menu = self.env['ir.ui.menu']
        visible = Menu._visible_menu_ids(self.env.user.has_group('base.group_no_one'))
        categories = []
        for key, entries in self._nc_catalog():
            reports = []
            for xmlid, _new_name in entries:
                rec = self.env.ref(xmlid, raise_if_not_found=False)
                if not rec or rec._name != 'ir.ui.menu':
                    continue
                menu = rec.sudo()
                if not menu.active or menu.id not in visible or not menu.action:
                    continue
                if not self._nc_can_open(menu.action):
                    continue
                reports.append({
                    'menu_id': menu.id,
                    'xmlid': xmlid,
                    'name': menu.name,
                    'action_id': menu.action.id,
                    'action_type': menu.action._name,
                    'purpose': purposes.get(xmlid, ''),
                    'badges': self._nc_report_badges(menu.action),
                })
            if reports:
                name, description = meta[key]
                categories.append({
                    'key': key, 'name': name, 'description': description,
                    'icon': _CATEGORY_ICONS[key], 'reports': reports,
                })
        return {'categories': categories}

    @api.model
    def _nc_can_open(self, action):
        model = getattr(action, 'res_model', False)
        if action._name == 'ir.actions.client':
            model = (action.context and self._nc_eval_ctx(action.context)
                     .get('nc_report_model')) or False
        if not model or model not in self.env:
            return True   # server/URL/other action: Odoo's own menu filter decided
        # a report run CREATES its (private, transient) wizard, so that is the
        # right to test for the engine viewer; a plain action only needs read
        right = 'create' if action._name == 'ir.actions.client' else 'read'
        return self.env[model].has_access(right)

    @api.model
    def _nc_eval_ctx(self, text):
        from odoo.tools.safe_eval import safe_eval
        try:
            return safe_eval(text or '{}') or {}
        except Exception:  # noqa: BLE001 - a malformed context is just "no model"
            return {}

    # ------------------------------------------------------------------
    # Viewer
    # ------------------------------------------------------------------
    @api.model
    def _nc_engine_model(self, model):
        engine = type(self.env['ncollection.account.report'])
        Model = self.env.get(model)
        if Model is None or not isinstance(Model, engine) or Model._abstract:
            raise UserError(self.env._("This is not a financial report."))
        return Model

    @api.model
    def nc_report_run(self, model, filters=None):
        """Build the engine wizard for ``filters`` and return its rows.

        The wizard is the SAME object the PDF/XLSX exports read (returned id),
        so what the user sees is what they export. Access is the wizard's own:
        a user without the report's ACL gets the normal AccessError.
        """
        Model = self._nc_engine_model(model)
        vals = self._nc_clean_filters(Model, filters or {})
        try:
            wizard = Model.create(vals)
            columns = wizard._nc_columns()
            rows = wizard._nc_compute_lines()
        except (UserError, ValidationError) as err:   # AccessError is a UserError
            if isinstance(err, AccessError):
                raise
            return {'error': str(err.args[0] if err.args else err)}
        return self._nc_payload(wizard, columns, rows)

    @api.model
    def _nc_filter_names(self, Model):
        """The filters a report offers: the shared ones it has, then any extra
        stored user field its wizard defines (Aged: receivable/payable and
        bucket size; asset register: profiles ...). Derived from the wizard, so
        a report that gains a filter shows it with no change here."""
        shared = [n for n in _SCALAR_FILTERS + _M2M_FILTERS if n in Model._fields]
        skip = set(shared) | set(models.MAGIC_COLUMNS) | {'display_name', 'id'}
        extras = [
            n for n, f in Model._fields.items()
            if n not in skip and f.store and not f.compute and not f.related
            and f.type in ('selection', 'integer', 'float', 'boolean', 'char',
                           'date', 'many2many')
        ]
        return shared + extras

    @api.model
    def _nc_clean_filters(self, Model, filters):
        vals = {}
        for name in self._nc_filter_names(Model):
            if name not in filters:
                continue
            value = filters[name]
            field = Model._fields[name]
            if field.type == 'many2many':
                vals[name] = [(6, 0, [int(i) for i in (value or [])])]
            elif field.type == 'many2one':
                vals[name] = int(value) if value else False
            elif field.type == 'boolean':
                vals[name] = bool(value)
            elif field.type in ('integer', 'float'):
                vals[name] = field.convert_to_cache(value, Model) if value not in (None, '') else 0
            else:
                vals[name] = value or False
        return vals

    @api.model
    def _nc_payload(self, wizard, columns, rows):
        env = self.env
        currency = wizard.company_id.currency_id
        account_ids = {r['account_id'] for r in rows if r.get('account_id')}
        partner_ids = {r['partner_id'] for r in rows if r.get('partner_id')}
        accounts = {a.id: {'code': a.code or '', 'name': a.name}
                    for a in env['account.account'].browse(account_ids)}
        partners = {p.id: p.display_name
                    for p in env['res.partner'].browse(partner_ids)}
        fields_meta = []
        names = self._nc_filter_names(wizard)
        for name in names:
            field = wizard._fields[name]
            item = {'name': name, 'type': field.type,
                    'label': field.get_description(env, ['string'])['string']}
            if field.type == 'selection':
                item['options'] = [list(o) for o in
                                   field._description_selection(env)]
            if field.type in ('many2one', 'many2many'):
                item['relation'] = field.comodel_name
            if name in _SCALAR_FILTERS or name in _M2M_FILTERS:
                item['shared'] = True
            fields_meta.append(item)
        values = {}
        for name in names:
            val = wizard[name]
            ftype = wizard._fields[name].type
            if ftype == 'many2many':
                values[name] = [{'id': r.id, 'name': r.display_name} for r in val]
            elif ftype == 'many2one':
                values[name] = val.id
            elif ftype in ('integer', 'float', 'boolean'):
                values[name] = val
            else:
                values[name] = str(val) if val else False
        target = dict(wizard._fields['target_move']._description_selection(env)) \
            if 'target_move' in wizard._fields else {}
        return {
            'wizard': {'model': wizard._name, 'id': wizard.id},
            'title': wizard._nc_report_title(),
            'company': wizard.company_id.name,
            'currency': {'id': currency.id, 'symbol': currency.symbol,
                         'name': currency.name, 'position': currency.position,
                         'digits': currency.decimal_places},
            'period': {'from': str(wizard.date_from) if wizard.date_from else False,
                       'to': str(wizard.date_to) if wizard.date_to else False},
            'target_move': target.get(wizard.target_move, '') if 'target_move' in wizard._fields else '',
            'detail': wizard._nc_has_row_detail(),
            'columns': columns,
            'rows': [self._nc_row_out(r) for r in rows],
            'accounts': accounts,
            'partners': partners,
            'filters': fields_meta,
            'values': values,
        }

    @api.model
    def _nc_row_out(self, row):
        out = {}
        for key, value in row.items():
            out[key] = str(value) if hasattr(value, 'isoformat') else value
        return out

    @api.model
    def nc_report_detail(self, model, wizard_id, row_key):
        """Expand one summary row of the run the user is looking at."""
        Model = self._nc_engine_model(model)
        wizard = Model.browse(int(wizard_id)).exists()
        if not wizard or not wizard._nc_has_row_detail():
            raise UserError(self.env._("The report run expired — apply the filters again."))
        return wizard._nc_row_detail(row_key)

    @api.model
    def nc_report_export(self, model, wizard_id, kind):
        """The wizard the user is LOOKING at, exported — never a re-run."""
        Model = self._nc_engine_model(model)
        wizard = Model.browse(int(wizard_id)).exists()
        if not wizard:
            raise UserError(self.env._("The report run expired — apply the filters again."))
        return wizard.action_export_pdf() if kind == 'pdf' else wizard.action_export_xlsx()


class IrUiMenu(models.Model):
    _inherit = 'ir.ui.menu'

    def _register_hook(self):
        result = super()._register_hook()
        try:
            self.env['ncollection.report.service']._nc_arrange_menus()
        except Exception:                                   # pragma: no cover
            # A registry load that dies here takes the database down; a report
            # filed in the wrong folder is cosmetic. Log and let it finish.
            _logger.exception("ncollection_account_reports: arranging report menus failed")
        return result
