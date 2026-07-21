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
from datetime import date

from dateutil.relativedelta import relativedelta

from odoo import api, models
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
        ]

    # -- KPI computations ---------------------------------------------------

    @api.model
    def _sum(self, model_name, domain, aggregate):
        """Single-number aggregate. `_read_group` returns [(value,)], and that
        value is False (not 0) when nothing matched."""
        rows = self.env[model_name]._read_group(domain, [], [aggregate])
        return (rows[0][0] or 0.0) if rows else 0.0

    @api.model
    def _compute_sales_this_month(self):
        """Confirmed sales this month, with the trend against last month."""
        today = date.today()
        this_start = today.replace(day=1)
        last_start = this_start - relativedelta(months=1)

        def _total(start, end):
            return self._sum(
                'sale.order',
                [('state', '=', 'sale'), ('date_order', '>=', start), ('date_order', '<', end)],
                'amount_total:sum',
            )

        current = _total(this_start, this_start + relativedelta(months=1))
        previous = _total(last_start, this_start)
        trend = ((current - previous) / previous * 100.0) if previous else None
        return {
            'value': current, 'format': 'currency', 'trend': trend,
            'sub': self.env._('vs last month'),
        }

    @api.model
    def _residual_by_account_type(self, account_type):
        """Sum of open residual amounts for one account type. Thin by design."""
        return abs(self._sum(
            'account.move.line',
            [('account_id.account_type', '=', account_type),
             ('parent_state', '=', 'posted'),
             ('amount_residual', '!=', 0)],
            'amount_residual:sum',
        ))

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
        total = self._sum(
            'account.move.line',
            [('account_id.account_type', '=', 'asset_cash'), ('parent_state', '=', 'posted')],
            'balance:sum',
        )
        return {'value': total, 'format': 'currency', 'sub': self.env._('Across cash & bank accounts')}

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
        today = date.today()
        start = today.replace(day=1) - relativedelta(months=5)
        rows = self.env['account.move.line']._read_group(
            [('account_id.account_type', '=', 'income'),
             ('parent_state', '=', 'posted'),
             ('date', '>=', start)],
            ['date:month'], ['balance:sum'],
        )
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
        """Top 5 customers by confirmed order value this year."""
        start = date.today().replace(month=1, day=1)
        # Modern API groups to a RECORDSET, not an (id, name) tuple.
        rows = self.env['sale.order']._read_group(
            [('state', '=', 'sale'), ('date_order', '>=', start)],
            ['partner_id'], ['amount_total:sum'],
            limit=5, order='amount_total:sum desc',
        )
        return {
            'labels': [partner.display_name if partner else self.env._('Unknown') for partner, _total in rows],
            'data': [total or 0.0 for _partner, total in rows],
            'format': 'currency',
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
    def get_dashboard_payload(self):
        """Return every widget this user is allowed to see, in display order.

        Gating happens HERE, not in the client: a widget the role does not
        permit, or that the plan does not license, never reaches the browser.
        """
        allowed_groups = self._widget_groups_for_user()
        widgets = []

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
            widgets.append(widget)

        return {
            'widgets': widgets,
            'actions': self._quick_actions_for_user(allowed_groups),
            'meta': {
                'user_name': self.env.user.name,
                'company_name': self.env.company.name,
                'currency': self.env.company.currency_id.symbol or '',
                'widget_groups': sorted(allowed_groups),
            },
        }
