# -*- coding: utf-8 -*-
"""Customer Workspace Dashboard — data service (P1-T17).

One RPC (:meth:`get_dashboard_payload`) returns everything the OWL client needs,
so the landing page costs a single round-trip (acceptance: loads under 2s).

Three rules shape this module:

1. **Role gating is server-side.** The payload contains ONLY the widgets the
   caller's role permits. The client renders what it is given and filters
   nothing — UI hiding alone is not security (Standing Rule 4). A widget denied
   by role is absent from the response, not merely hidden.

2. **Availability is checked twice, for two different reasons.** A provider is
   skipped when its model is not in the registry (the app is not installed) AND
   when reading it raises ``AccessError`` (the app is installed but the plan
   does not license it — P1-T10 Ring 2). The second case is not hypothetical:
   on a Basic-plan tenant ``sale.order`` exists but a regular user reading it
   gets *"The 'sale.order' feature is not included in your NCollection plan"*.
   Without catching that, the dashboard would 500 for exactly the users
   licensing is meant to constrain. Catching it means the dashboard inherits
   P1-T10's enforcement rather than duplicating license logic.

3. **Dependencies stay soft.** ``ncollection_core`` depends on ``base`` and
   ``web`` only, so the dashboard never enters a tenant's module set and
   provisioning, P1-T09 menu visibility and P1-T10 enforcement are untouched.

Financial providers are interim. FPA §7 assigns them permanently to
``ncollection_account_dashboard``; each carries a HANDOFF marker for #119
(F3-T01) and stays thin — aggregation only, never financial business logic.
"""

import logging
from datetime import date, timedelta

from dateutil.relativedelta import relativedelta

from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError

_logger = logging.getLogger(__name__)

# --- widget groups ---------------------------------------------------------
# Mirrors demo/src/lib/roles.ts, the design reference for this screen
# (demo/README.md maps "Customer Dashboard" to P1-T17).
GROUP_FINANCIAL = 'financial'
GROUP_PIPELINE = 'pipeline'
GROUP_OPERATIONS = 'operations'
GROUP_PERSONAL = 'personal'

ALL_WIDGET_GROUPS = (GROUP_FINANCIAL, GROUP_PIPELINE, GROUP_OPERATIONS, GROUP_PERSONAL)

TYPE_KPI = 'kpi'
TYPE_CHART = 'chart'
TYPE_STATEMENT = 'statement'

# The two financial statements the Summary shows come from the NCollection report engine (the single source of
# accounting numbers). Soft dependency: absent when ncollection_account_reports is not installed.
STATEMENT_BALANCE_SHEET = 'ncollection.account.report.balance.sheet'
STATEMENT_PROFIT_LOSS = 'ncollection.account.report.profit.loss'
# Statement rows whose amount is derived (no accounts of their own): which accounts a click on them opens.
_RUNNING_RESULT_KEYS = ('gross_profit', 'net_profit')
_SECTION_TOTAL_KEYS = ('total_income', 'assets_total')
# Totals that include a bucket with no accounts of its own (Accumulated Earnings is computed, not posted), so no
# list of accounts adds up to them.
_NO_DRILL_KEYS = ('liabilities_equity_total',)
# Most accounts one drill-down may name (the report service caps it at the same figure).
DRILL_MAX_ACCOUNTS = 500
# The model a drill-down opens; statement rows are only clickable for a user who can read it.
MOVE_LINE_MODEL = 'account.move.line'

# Which widget groups each of the 8 P1-T08 roles grants (security/role_groups.xml).
#
# Resolution is a UNION over every role the user holds, which is correct for two
# independent reasons:
#   - the groups declare implied_ids (owner -> ceo -> manager -> employee), so
#     has_group() is true transitively, and
#   - roles are additive by design: one user may hold e.g. Manager + Accountant.
# The union reproduces the demo's per-role sets exactly; verified in tests.
ROLE_WIDGET_GROUPS = {
    'ncollection_core.group_role_employee': (GROUP_PERSONAL,),
    'ncollection_core.group_role_hr': (GROUP_PERSONAL,),
    'ncollection_core.group_role_sales': (GROUP_PIPELINE, GROUP_PERSONAL),
    'ncollection_core.group_role_warehouse': (GROUP_OPERATIONS, GROUP_PERSONAL),
    'ncollection_core.group_role_accountant': (GROUP_FINANCIAL, GROUP_PERSONAL),
    'ncollection_core.group_role_manager': (GROUP_PIPELINE, GROUP_OPERATIONS, GROUP_PERSONAL),
    'ncollection_core.group_role_ceo': ALL_WIDGET_GROUPS,
    'ncollection_core.group_role_owner': ALL_WIDGET_GROUPS,
}

# Quick actions offered in the header. Each names the model it acts on so the
# button only appears when that app is installed AND licensed for this user.
QUICK_ACTIONS = (
    {'key': 'new_quotation', 'label': 'New Quotation', 'model': 'sale.order',
     'action': 'sale.action_quotations_with_onboarding', 'group': GROUP_PIPELINE},
    {'key': 'new_invoice', 'label': 'New Invoice', 'model': 'account.move',
     'action': 'account.action_move_out_invoice_type', 'group': GROUP_FINANCIAL},
    {'key': 'new_po', 'label': 'New Purchase Order', 'model': 'purchase.order',
     'action': 'purchase.purchase_form_action', 'group': GROUP_OPERATIONS},
)


class NCollectionDashboardData(models.AbstractModel):
    """Read-only aggregation service for the customer landing dashboard."""

    _name = 'ncollection.dashboard.data'
    _description = 'NCollection Customer Dashboard Data'

    # -- role resolution ----------------------------------------------------

    @api.model
    def _widget_groups_for_user(self):
        """Return the set of widget groups the current user may see."""
        user = self.env.user
        groups = set()
        for role_xmlid, widget_groups in ROLE_WIDGET_GROUPS.items():
            if user.has_group(role_xmlid):
                groups.update(widget_groups)

        # A system administrator holding no NCollection role still administers
        # the workspace; mirror Owner rather than serving an empty dashboard.
        if not groups and user.has_group('base.group_system'):
            groups.update(ALL_WIDGET_GROUPS)

        # Employee is "the floor every other role stands on" (role_groups.xml),
        # so any internal user sees at least their personal widgets.
        if user.has_group('base.group_user'):
            groups.add(GROUP_PERSONAL)
        return groups

    # -- availability -------------------------------------------------------

    @api.model
    def _model_available(self, model_name):
        """True when ``model_name`` exists in this database's registry."""
        return model_name in self.env

    @api.model
    def _model_readable(self, model_name):
        """True when the model is installed AND this user may actually read it.

        Two distinct negatives collapse into one answer, because for the
        dashboard they mean the same thing — "this widget is not for you":
          * not in the registry  -> the plan never installed the app,
          * AccessError on read  -> installed, but P1-T10 says the plan does not
            license it for this user.
        A cheap ``search([], limit=1)`` is what triggers Ring 2 enforcement.
        """
        if not self._model_available(model_name):
            return False
        try:
            self.env[model_name].search([], limit=1)
            return True
        except (AccessError, UserError):
            return False

    # -- providers ----------------------------------------------------------

    @api.model
    def _provider_specs(self):
        """Declare the widgets. One dict per widget; order is display order."""
        return [
            {
                'key': 'sales_this_month', 'type': TYPE_KPI, 'group': GROUP_PIPELINE,
                'label': self.env._('Sales This Month'), 'icon': 'sales',
                'model': 'sale.order', 'compute': '_compute_sales_this_month',
            },
            # HANDOFF: #119 / F3-T01 -> ncollection_account_dashboard (FPA §7).
            # Aggregation only; no financial business logic belongs here.
            {
                'key': 'receivables', 'type': TYPE_KPI, 'group': GROUP_FINANCIAL,
                'label': self.env._('Receivables'), 'icon': 'invoicing',
                'model': 'account.move.line', 'compute': '_compute_receivables',
            },
            # HANDOFF: #119 / F3-T01 -> ncollection_account_dashboard (FPA §7).
            {
                'key': 'payables', 'type': TYPE_KPI, 'group': GROUP_FINANCIAL,
                'label': self.env._('Payables'), 'icon': 'wallet',
                'model': 'account.move.line', 'compute': '_compute_payables',
            },
            # HANDOFF: #119 / F3-T01 -> ncollection_account_dashboard (FPA §7).
            {
                'key': 'cash_bank', 'type': TYPE_KPI, 'group': GROUP_FINANCIAL,
                'label': self.env._('Cash & Bank'), 'icon': 'wallet',
                'model': 'account.move.line', 'compute': '_compute_cash_bank',
            },
            # Expenses and Net Profit are DERIVED in get_dashboard_payload from the same P&L statement pass the
            # Profit & Loss card uses: no second engine run, no recomputation here.
            {
                'key': 'open_quotations', 'type': TYPE_KPI, 'group': GROUP_PIPELINE,
                'label': self.env._('Open Quotations'), 'icon': 'sales',
                'model': 'sale.order', 'compute': '_compute_open_quotations',
            },
            {
                'key': 'purchases_this_month', 'type': TYPE_KPI, 'group': GROUP_OPERATIONS,
                'label': self.env._('Purchases This Month'), 'icon': 'purchase',
                'model': 'purchase.order', 'compute': '_compute_purchases_this_month',
            },
            {
                'key': 'stock_on_hand', 'type': TYPE_KPI, 'group': GROUP_OPERATIONS,
                'label': self.env._('Stock on Hand'), 'icon': 'inventory',
                'model': 'stock.quant', 'compute': '_compute_stock_on_hand',
            },
            {
                'key': 'pending_deliveries', 'type': TYPE_KPI, 'group': GROUP_OPERATIONS,
                'label': self.env._('Pending Transfers'), 'icon': 'inventory',
                'model': 'stock.picking', 'compute': '_compute_pending_deliveries',
            },
            {
                'key': 'open_activities', 'type': TYPE_KPI, 'group': GROUP_PERSONAL,
                'label': self.env._('Open Activities'), 'icon': 'activity',
                'model': 'mail.activity', 'compute': '_compute_open_activities',
            },
            # Absent in Community (no approvals app) — a real, honest example of
            # a widget that simply does not render on a tenant lacking the app.
            {
                'key': 'pending_approvals', 'type': TYPE_KPI, 'group': GROUP_OPERATIONS,
                'label': self.env._('Pending Approvals'), 'icon': 'check',
                'model': 'approval.request', 'compute': '_compute_pending_approvals',
            },
            # HANDOFF: #119 / F3-T01 -> ncollection_account_dashboard (FPA §7).
            {
                'key': 'revenue_6m', 'type': TYPE_CHART, 'group': GROUP_FINANCIAL,
                'label': self.env._('Revenue (last 6 months)'), 'chart': 'line',
                'model': 'account.move.line', 'compute': '_compute_revenue_6m',
            },
            {
                'key': 'top_customers', 'type': TYPE_CHART, 'group': GROUP_PIPELINE,
                'label': self.env._('Top Customers'), 'chart': 'bar',
                'model': 'sale.order', 'compute': '_compute_top_customers',
            },
            # The financial statements: the report engine's own rows, never a second calculation.
            {
                'key': 'balance_sheet_summary', 'type': TYPE_STATEMENT, 'group': GROUP_FINANCIAL,
                'label': self.env._('Balance Sheet'), 'model': STATEMENT_BALANCE_SHEET,
                'compute': '_compute_balance_sheet_summary',
            },
            {
                'key': 'profit_loss_summary', 'type': TYPE_STATEMENT, 'group': GROUP_FINANCIAL,
                'label': self.env._('Profit & Loss'), 'model': STATEMENT_PROFIT_LOSS,
                'compute': '_compute_profit_loss_summary',
            },
        ]

    # -- KPI computations ---------------------------------------------------

    # -- the period -----------------------------------------------------------
    #
    # The selected period is read from the context (set once, in get_dashboard_payload) by the figures that are
    # genuinely about a PERIOD. Each figure's time meaning is decided here, not by the client:
    #
    #   period total   Profit & Loss, Expenses, Net Profit (the engine's own rows over [From, To]);
    #                  Top Customers (confirmed orders dated within [From, To])
    #   as of To       Balance Sheet (cumulative to the To date; the From date has no meaning for it)
    #   trend ending   Revenue (last 6 months): the six months that END at the To date
    #                  at To
    #   fixed window   Sales / Purchases This Month: "this month" is the figure's definition
    #   current state  Receivables, Payables, Cash & Bank, Open Quotations, Stock on Hand, Pending Transfers, Open
    #                  Activities, Pending Approvals: what is open NOW; the books cannot be rewound for them
    #
    # The period only ever narrows or moves the window INSIDE what the user may already read: it never touches a
    # company, a record rule or a licence, and every figure still goes through the same gated, access-checked paths.

    @api.model
    def _default_summary_period(self):
        today = date.today()
        return today.replace(month=1, day=1), today

    @api.model
    def _summary_period(self):
        """The (From, To) the figures use: the selected one, else the current year to today."""
        default_from, default_to = self._default_summary_period()
        return (self.env.context.get('nc_summary_from') or default_from,
                self.env.context.get('nc_summary_to') or default_to)

    @api.model
    def _parse_summary_period(self, date_from, date_to):
        """Validate what the client sent. Nothing at all means the default; otherwise both dates, From <= To."""
        if not date_from and not date_to:
            return None
        if not (date_from and date_to):
            raise UserError(self.env._("Choose both the From date and the To date."))
        try:
            start, end = fields.Date.to_date(date_from), fields.Date.to_date(date_to)
        except (TypeError, ValueError):
            raise UserError(self.env._("The period is not a valid date range."))
        if not (start and end):
            raise UserError(self.env._("The period is not a valid date range."))
        if start > end:
            raise UserError(self.env._("The From date cannot be after the To date."))
        if not (date(1900, 1, 1) <= start and end <= date(2200, 12, 31)):
            raise UserError(self.env._("The period is outside the supported range."))
        return start, end

    @api.model
    def _period_caption(self):
        """The small line under a period figure: "Year to date" by default, else "Selected period" (the dates are
        in the figure's own period tag)."""
        if self._summary_period() == self._default_summary_period():
            return self.env._('Year to date')
        return self.env._('Selected period')

    @api.model
    def _engine(self):
        """The P4-T01 aggregation engine — the single choke point (§9)."""
        return self.env['ncollection.aggregation.engine']

    @api.model
    def _sum(self, model_name, domain, aggregate):
        """Single-number aggregate, routed through the P4-T01 engine.

        Every KPI below funnels through here, so routing this one method puts
        the whole dashboard behind the shared cache. The engine returns None
        for an absent/unlicensed model, which collapses to 0.0 exactly as the
        previous direct `_read_group` did when nothing matched.
        """
        result = self._engine().aggregate({
            'key': 'dash:%s:%s' % (model_name, aggregate),
            'model': model_name,
            'domain': domain,
            'aggregates': [aggregate],
        })
        if not result or not result['rows']:
            return 0.0
        return result['rows'][0][0] or 0.0

    @api.model
    def _compute_sales_this_month(self):
        """Confirmed sales this month, with the trend against last month."""
        today = date.today()
        this_start = today.replace(day=1)
        last_start = this_start - relativedelta(months=1)

        def _total(domain):
            return self._sum('sale.order', domain, 'amount_total:sum')

        current = _total(self._domain_sales_this_month())
        previous = _total([('state', '=', 'sale'), ('date_order', '>=', last_start),
                           ('date_order', '<', this_start)])
        trend = ((current - previous) / previous * 100.0) if previous else None
        return {
            'value': current, 'format': 'currency', 'trend': trend,
            'sub': self.env._('vs last month'),
        }

    # The domains below are the ONE definition of what each KPI counts: the number (via _sum / search_count) and
    # its drill-down (drill_kpi) both read them, so the list a click opens is exactly the figure on the card.
    @api.model
    def _domain_residual(self, account_type):
        return [('account_id.account_type', '=', account_type),
                ('parent_state', '=', 'posted'),
                ('amount_residual', '!=', 0)]

    @api.model
    def _domain_cash_bank(self):
        return [('account_id.account_type', '=', 'asset_cash'), ('parent_state', '=', 'posted')]

    @api.model
    def _domain_sales_this_month(self):
        start = date.today().replace(day=1)
        return [('state', '=', 'sale'), ('date_order', '>=', start),
                ('date_order', '<', start + relativedelta(months=1))]

    @api.model
    def _domain_purchases_this_month(self):
        start = date.today().replace(day=1)
        return [('state', 'in', ('purchase', 'done')), ('date_approve', '>=', start),
                ('date_approve', '<', start + relativedelta(months=1))]

    @api.model
    def _residual_by_account_type(self, account_type):
        """Sum of open residual amounts for one account type. Thin by design."""
        return abs(self._sum(
            'account.move.line', self._domain_residual(account_type), 'amount_residual:sum'))

    @api.model
    def _compute_receivables(self):
        # HANDOFF: #119 / F3-T01 -> ncollection_account_dashboard.
        return {
            'value': self._residual_by_account_type('asset_receivable'),
            'format': 'currency', 'sub': self.env._('Open invoices'),
        }

    @api.model
    def _compute_payables(self):
        # HANDOFF: #119 / F3-T01 -> ncollection_account_dashboard.
        return {
            'value': self._residual_by_account_type('liability_payable'),
            'format': 'currency', 'sub': self.env._('Open bills'),
        }

    @api.model
    def _compute_cash_bank(self):
        # HANDOFF: #119 / F3-T01 -> ncollection_account_dashboard.
        #
        # Filter on the ACCOUNT type, not the journal. Every journal entry
        # balances, so summing all lines of the bank/cash journals always nets
        # to exactly 0: an opening balance of 250k posts +250k to the bank
        # account and -250k to equity, and BOTH lines carry that journal. Only
        # the cash/bank ledger accounts represent money actually held.
        # Found by seeding real data — an empty tenant reports 0 either way,
        # so the bug was invisible until INFRA-07.
        total = self._sum('account.move.line', self._domain_cash_bank(), 'balance:sum')
        return {'value': total, 'format': 'currency', 'sub': self.env._('Across cash & bank accounts')}

    @api.model
    def _compute_open_quotations(self):
        count = self.env['sale.order'].search_count([('state', 'in', ('draft', 'sent'))])
        return {'value': count, 'format': 'integer', 'sub': self.env._('Awaiting customer confirmation')}

    @api.model
    def _compute_purchases_this_month(self):
        """Confirmed purchases this month, with the trend against last month."""
        start = date.today().replace(day=1)
        last = start - relativedelta(months=1)

        def _total(domain):
            return self._sum('purchase.order', domain, 'amount_total:sum')

        current = _total(self._domain_purchases_this_month())
        previous = _total([('state', 'in', ('purchase', 'done')), ('date_approve', '>=', last),
                           ('date_approve', '<', start)])
        trend = ((current - previous) / previous * 100.0) if previous else None
        return {'value': current, 'format': 'currency', 'trend': trend, 'sub': self.env._('vs last month')}

    @api.model
    def _compute_stock_on_hand(self):
        """Units held in the company's own (internal) locations."""
        total = self._sum('stock.quant', [('location_id.usage', '=', 'internal')], 'quantity:sum')
        return {'value': total, 'format': 'integer', 'sub': self.env._('Units in your warehouses')}

    @api.model
    def _compute_pending_deliveries(self):
        count = self.env['stock.picking'].search_count(
            [('state', 'in', ('assigned', 'confirmed', 'waiting'))])
        return {'value': count, 'format': 'integer', 'sub': self.env._('Transfers waiting to be processed')}

    @api.model
    def _compute_open_activities(self):
        count = self.env['mail.activity'].search_count([('user_id', '=', self.env.uid)])
        return {'value': count, 'format': 'integer', 'sub': self.env._('Assigned to you')}

    @api.model
    def _compute_pending_approvals(self):
        count = self.env['approval.request'].search_count([('request_status', '=', 'pending')])
        return {'value': count, 'format': 'integer', 'sub': self.env._('Awaiting sign-off')}

    # -- chart computations -------------------------------------------------

    @api.model
    def _compute_revenue_6m(self):
        """Posted customer-invoice revenue per month, oldest first."""
        # HANDOFF: #119 / F3-T01 -> ncollection_account_dashboard.
        # A TREND window, not a period total: the six months that END at the selected To date (today by default).
        _period_from, period_to = self._summary_period()
        start = period_to.replace(day=1) - relativedelta(months=5)
        result = self._engine().aggregate({
            'key': 'dash:revenue_6m',
            'model': 'account.move.line',
            'domain': [('account_id.account_type', '=', 'income'),
                       ('parent_state', '=', 'posted'),
                       ('date', '>=', start), ('date', '<=', period_to)],
            'groupby': ['date:month'],
            'aggregates': ['balance:sum'],
        })
        rows = result['rows'] if result else []
        # `date:month` yields a date (first of month) in the modern API, but
        # normalise defensively so a string key cannot silently zero the chart.
        by_month = {}
        for key, total in rows:
            if hasattr(key, 'year'):
                by_month[(key.year, key.month)] = abs(total or 0.0)
        labels, data = [], []
        for offset in range(6):
            month = start + relativedelta(months=offset)
            labels.append(month.strftime('%b'))
            data.append(by_month.get((month.year, month.month), 0.0))
        return {'labels': labels, 'data': data, 'format': 'currency'}

    @api.model
    def _compute_top_customers(self):
        """Top 5 customers by confirmed order value in the selected period (the current year by default)."""
        period_from, period_to = self._summary_period()
        # The modern ORM groups to a RECORDSET; the engine flattens that to an
        # (id, display_name) tuple so the payload is cacheable inert data (a
        # recordset would be bound to an environment that is gone by the next
        # request). Hence the tuple unpacking here rather than `.display_name`.
        result = self._engine().aggregate({
            'key': 'dash:top_customers',
            'model': 'sale.order',
            'domain': [('state', '=', 'sale'), ('date_order', '>=', period_from),
                       ('date_order', '<', period_to + timedelta(days=1))],
            'groupby': ['partner_id'],
            'aggregates': ['amount_total:sum'],
            'limit': 5,
            'order': 'amount_total:sum desc',
        })
        rows = result['rows'] if result else []
        return {
            'labels': [
                partner[1] if partner else self.env._('Unknown')
                for partner, _total in rows
            ],
            'data': [total or 0.0 for _partner, total in rows],
            'format': 'currency',
        }

    # -- financial statements (from the report engine) ----------------------

    @api.model
    def _statement_summary(self, model_name, action_xmlid):
        """One engine statement as inert data for the client: its OWN rows, the way the full report shows them.

        ONE engine pass per statement (``_nc_compute_lines``); nothing is added up here, so the Summary can only ever
        show what the report shows, to the cent. Zero-balance accounts are left out (the engine's own
        ``exclude_zero`` option) so the card stays short; totals are the engine's. Every row carries the account ids
        it is made of, so a click can open exactly those journal items through the report service's drill.
        """
        period_from, period_to = self._summary_period()
        vals = {'date_from': period_from, 'date_to': period_to,
                'target_move': 'posted', 'comparison_type': 'none'}
        Wizard = self.env[model_name]
        if 'exclude_zero' in Wizard._fields:
            vals['exclude_zero'] = True
        wizard = Wizard.create(vals)
        lines = wizard._nc_compute_lines()
        can_drill = self._model_readable(MOVE_LINE_MODEL)
        rows = []
        for line, account_ids in zip(lines, self._statement_row_accounts(lines)):
            row = {
                'label': line['label'],
                'value': line.get('current_amount') or 0.0,
                'level': line.get('level') or 0,
                'key': line.get('key') or '',
            }
            if can_drill and account_ids:
                row['account_ids'] = account_ids
            rows.append(row)
        action = self.env.ref(action_xmlid, raise_if_not_found=False)
        return {
            'rows': rows,
            'empty': not any(row['value'] for row in rows),
            'action_id': action.id if action else False,
            # a Balance Sheet is a position AS OF a date (cumulative to it): say so, not a range it does not use
            'period': (self.env._('As of %s', wizard.date_to) if model_name == STATEMENT_BALANCE_SHEET
                       else '%s → %s' % (wizard.date_from, wizard.date_to)),
            'as_of': wizard.date_to,
            'wizard': {'model': model_name, 'id': wizard.id} if can_drill else False,
        }

    @api.model
    def _statement_row_accounts(self, lines):
        """For each statement row, the account ids its amount is made of (the scope of its drill-down).

        An account row is itself; a group or section is the accounts beneath it; a derived total takes the last
        section's accounts, except the profit lines (gross / net), which are running results and take every
        account above them. Capped at DRILL_MAX_ACCOUNTS (the report service refuses more)."""
        out, seen_above, last_section = [], [], []
        for index, line in enumerate(lines):
            level = line.get('level') or 0
            own = [line['account_id']] if line.get('account_id') else []
            beneath = []
            for later in lines[index + 1:]:
                if (later.get('level') or 0) <= level:
                    break
                if later.get('account_id'):
                    beneath.append(later['account_id'])
            ids = own or beneath
            if (line.get('key') or '').startswith('section_') or line.get('key') in _NO_DRILL_KEYS:
                ids = []                              # a heading, or a total no account list can reproduce
            elif not ids and line.get('key') in _RUNNING_RESULT_KEYS:
                ids = seen_above                      # a running result: every account above it
            elif not ids and line.get('key') in _SECTION_TOTAL_KEYS:
                ids = last_section                    # a section total: the accounts of the section it closes
            if level == 0 and beneath:
                last_section = beneath
            seen_above = seen_above + own
            ids = list(dict.fromkeys(ids))
            # more accounts than a drill may carry: no drill, never a silently truncated (wrong) one
            out.append(ids if len(ids) <= DRILL_MAX_ACCOUNTS else [])
        return out

    @api.model
    def _compute_balance_sheet_summary(self):
        return self._statement_summary(
            STATEMENT_BALANCE_SHEET, 'ncollection_account_reports.action_nc_view_balance_sheet')

    @api.model
    def _compute_profit_loss_summary(self):
        return self._statement_summary(
            STATEMENT_PROFIT_LOSS, 'ncollection_account_reports.action_nc_view_profit_loss')

    @api.model
    def _add_derived_kpis(self, widgets):
        """Expenses and Net Profit as KPI cards, read off the Profit & Loss statement already in ``widgets``.

        They are the engine's own rows (cost of sales + operating expenses, and the NET PROFIT row), so they can
        never disagree with the statement beneath them and cost no engine pass of their own. Placed right after
        Cash & Bank so the financial cards read together.
        """
        pl = next((w for w in widgets if w['key'] == 'profit_loss_summary'), None)
        if not pl:
            return
        by_key = {row['key']: row for row in pl['rows'] if row.get('key')}
        net = by_key.get('net_profit')
        if not net:
            return
        expense_rows = [by_key[k] for k in ('cogs', 'operating_expenses') if k in by_key]
        period = pl['period']
        derived = [
            {'key': 'expenses', 'type': TYPE_KPI, 'group': GROUP_FINANCIAL, 'label': self.env._('Expenses'),
             'icon': 'wallet', 'value': sum(r['value'] for r in expense_rows), 'format': 'currency',
             'sub': self._period_caption(), 'tone': 'neutral', 'period': period,
             'drill': self._derived_drill(pl, expense_rows, self.env._('Expenses'))},
            {'key': 'net_profit', 'type': TYPE_KPI, 'group': GROUP_FINANCIAL, 'label': self.env._('Net Profit'),
             'icon': 'chart', 'value': net['value'], 'format': 'currency',
             'sub': self._period_caption(), 'tone': 'positive' if net['value'] > 0 else (
                 'negative' if net['value'] < 0 else 'neutral'), 'period': period,
             'drill': self._derived_drill(pl, [net], self.env._('Net Profit'))},
        ]
        position = next((i for i, w in enumerate(widgets) if w['key'] == 'cash_bank'), len(widgets) - 1) + 1
        widgets[position:position] = derived

    @api.model
    def _derived_drill(self, statement, rows, label):
        """The drill descriptor of a KPI built from statement rows: the statement's wizard + the rows' accounts."""
        accounts = list(dict.fromkeys(a for row in rows for a in row.get('account_ids', ())))
        if not (statement.get('wizard') and accounts) or len(accounts) > DRILL_MAX_ACCOUNTS:
            return False
        return {'kind': 'accounts', 'wizard': statement['wizard'], 'account_ids': accounts, 'label': label}

    # -- KPI drill-down -----------------------------------------------------

    @api.model
    def _kpi_drill_specs(self):
        """What each KPI opens: the model, the SAME domain the figure counts, and a title."""
        env = self.env
        return {
            'receivables': (MOVE_LINE_MODEL, self._domain_residual('asset_receivable'), env._('Open receivables')),
            'payables': (MOVE_LINE_MODEL, self._domain_residual('liability_payable'), env._('Open payables')),
            'cash_bank': (MOVE_LINE_MODEL, self._domain_cash_bank(), env._('Cash & bank')),
            'sales_this_month': ('sale.order', self._domain_sales_this_month(), env._('Sales this month')),
            'open_quotations': ('sale.order', [('state', 'in', ('draft', 'sent'))], env._('Open quotations')),
            'purchases_this_month': ('purchase.order', self._domain_purchases_this_month(),
                                     env._('Purchases this month')),
            'stock_on_hand': ('stock.quant', [('location_id.usage', '=', 'internal')], env._('Stock on hand')),
            'pending_deliveries': ('stock.picking', [('state', 'in', ('assigned', 'confirmed', 'waiting'))],
                                   env._('Pending transfers')),
            'open_activities': ('mail.activity', [('user_id', '=', env.uid)], env._('My activities')),
        }

    @api.model
    def drill_kpi(self, key):
        """The action behind one KPI card.

        The key must be a widget this user may see (role group AND readable model, the same gates as the
        payload), and the domain comes from the server-side table above, never from the client; the returned
        action is a plain list, so the user's ACL and record rules still apply when it opens.
        """
        spec = self._kpi_drill_specs().get(key)
        provider = next((p for p in self._provider_specs() if p['key'] == key), None)
        if not spec or not provider or provider['group'] not in self._widget_groups_for_user():
            raise AccessError(self.env._("This figure has no detail for you."))
        model_name, domain, title = spec
        if not self._model_readable(model_name):
            raise AccessError(self.env._("This figure has no detail for you."))
        return {
            'type': 'ir.actions.act_window', 'name': title, 'res_model': model_name,
            'view_mode': 'list,form', 'views': [(False, 'list'), (False, 'form')],
            'domain': domain, 'target': 'current',
        }

    # -- quick actions ------------------------------------------------------

    @api.model
    def _quick_actions_for_user(self, allowed_groups):
        """Header buttons, filtered by role AND by what the plan actually allows."""
        actions = []
        for spec in QUICK_ACTIONS:
            if spec['group'] not in allowed_groups:
                continue
            if not self._model_readable(spec['model']):
                continue
            action = self.env.ref(spec['action'], raise_if_not_found=False)
            if not action:
                continue
            actions.append({'key': spec['key'], 'label': self.env._(spec['label']), 'action_id': action.id})
        return actions

    # -- payload ------------------------------------------------------------

    @api.model
    def get_dashboard_payload(self, date_from=None, date_to=None):
        """Return every widget this user is allowed to see, in display order.

        Gating happens HERE, not in the client: a widget the role does not
        permit, or that the plan does not license, never reaches the browser.

        ``date_from`` / ``date_to`` select the period of the figures that are about a period (see the table above
        ``_summary_period``); both absent means the current year to today. They are validated here and only ever
        move the window inside what the caller may already read.
        """
        chosen = self._parse_summary_period(date_from, date_to)
        if chosen:
            self = self.with_context(nc_summary_from=chosen[0], nc_summary_to=chosen[1])
        period_from, period_to = self._summary_period()
        allowed_groups = self._widget_groups_for_user()
        widgets = []
        kpi_drills = self._kpi_drill_specs()

        for spec in self._provider_specs():
            if spec['group'] not in allowed_groups:
                continue  # role gating (Standing Rule 4)
            model_name = spec.get('model')
            if model_name and not self._model_readable(model_name):
                continue  # app not installed, or not licensed for this user
            try:
                computed = getattr(self, spec['compute'])()
            except (AccessError, UserError):
                # Enforcement can also bite inside the aggregation itself
                # (e.g. a related model). Drop the widget rather than fail the
                # whole page for one tile.
                continue
            except Exception:  # pragma: no cover - defensive
                _logger.exception("dashboard widget %r failed; omitting it", spec['key'])
                continue
            widget = {
                'key': spec['key'],
                'type': spec.get('type', TYPE_KPI),
                'group': spec['group'],
                'label': spec['label'],
                'icon': spec.get('icon', 'activity'),
            }
            if spec.get('chart'):
                widget['chart'] = spec['chart']
            widget.update(computed)
            # A card with a destination is a button: the server says so only for a figure whose source list this
            # user may open (drill_kpi re-checks, and builds the domain itself).
            if (widget['type'] == TYPE_KPI and spec['key'] in kpi_drills and 'drill' not in widget
                    and self._model_readable(kpi_drills[spec['key']][0])):
                widget['drill'] = {'kind': 'kpi'}
            widgets.append(widget)

        self._add_derived_kpis(widgets)
        return {
            'widgets': widgets,
            'actions': self._quick_actions_for_user(allowed_groups),
            'meta': {
                'user_name': self.env.user.name,
                'company_name': self.env.company.name,
                'currency': self.env.company.currency_id.symbol or '',
                'widget_groups': sorted(allowed_groups),
                'period': {
                    'date_from': fields.Date.to_string(period_from), 'date_to': fields.Date.to_string(period_to),
                    'is_default': (period_from, period_to) == self._default_summary_period(),
                },
            },
        }
