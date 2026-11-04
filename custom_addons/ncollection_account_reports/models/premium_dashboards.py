# -*- coding: utf-8 -*-
"""NCollection Sales and Invoicing dashboards: real records only, one company, one currency.

Design rules (each is tested in ``tests/test_premium_dashboards.py``):

* **Real records only.** Nothing here is ever invented: no records means zeros and empty lists, never a demonstration figure.
* **One company, one currency.** The payload speaks for the ACTIVE company, in its own functional currency. Other ticked
  companies are never added in (they may use other currencies); the payload says so. No cache: every call reads the
  records, so a switched company or a new document is visible at once and nothing can be served to the wrong company.
* **Orders, invoices and revenue are different things.** Orders are intent (sale orders), Invoiced is the posted customer
  invoice lines net of credit notes (what is billed), and no figure here claims to be recognised accounting revenue.
* **Foreign currency** is converted with Odoo's own rates (``res.currency._convert``) at the DOCUMENT date: the order date
  for orders, the invoice date for invoices (their ``*_signed`` columns already carry the company-currency amount booked).
* **Amounts exclude tax** except where a receivable is involved (a receivable is owed tax included).

Receivables, customer credit and DSO reuse ``ncollection.account.open.items`` (the same as-of-a-date position the Finance
dashboard shows), so the Invoicing dashboard cannot disagree with Finance.
"""
from datetime import timedelta

from dateutil.relativedelta import relativedelta

from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError
from odoo.tools.misc import format_date

from odoo.addons.ncollection_core.models.financial_gate import FINANCIAL_GATE_GROUPS

SALES_GROUPS = (
    'ncollection_core.group_role_sales', 'ncollection_core.group_role_ceo', 'ncollection_core.group_role_accountant',
    'base.group_system',
)
_TREND_MONTHS = 6
_TOP = 5
_RECENT = 8


class NcollectionPremiumDashboard(models.AbstractModel):
    _name = 'ncollection.premium.dashboard'
    _inherit = 'ncollection.financial.gate.mixin'
    _description = 'NCollection Sales and Invoicing Dashboards'

    # ------------------------------------------------------------------ scope, period, formatting
    def _scoped(self):
        """The environment of the ACTIVE company only (other ticked companies are never added in)."""
        company = self.env.company
        return self.with_company(company).with_context(allowed_company_ids=[company.id])

    @api.model
    def _period(self, date_from=None, date_to=None):
        """``(from, to, previous_from, previous_to)``: the selected window (default: this month so far, in the user's
        calendar) and the window of the same length right before it."""
        today = fields.Date.context_today(self)
        try:
            start = fields.Date.to_date(date_from) if date_from else None
            end = fields.Date.to_date(date_to) if date_to else None
        except ValueError:
            raise UserError(self.env._("The dates are not valid."))
        if not (start and end):
            start, end = today.replace(day=1), today
        if start > end:
            raise UserError(self.env._("The From date cannot be after the To date."))
        if (end - start).days > 366 * 5:
            raise UserError(self.env._("The period is longer than five years."))
        days = (end - start).days + 1
        return start, end, start - timedelta(days=days), start - timedelta(days=1)

    def _meta(self, start, end, prev_start, prev_end):
        company = self.env.company
        currency = company.currency_id
        others = self.env.user.company_ids - company
        return {
            'company': company.name,
            'currency': {'name': currency.name, 'symbol': currency.symbol or currency.name,
                         'position': currency.position, 'decimals': currency.decimal_places},
            'period': {'from': fields.Date.to_string(start), 'to': fields.Date.to_string(end),
                       'previous_from': fields.Date.to_string(prev_start), 'previous_to': fields.Date.to_string(prev_end),
                       'days': (end - start).days + 1},
            # honesty about what is NOT in the figures
            'other_companies': others.mapped('name'),
            'scope_note': self.env._(
                "Figures are for %(company)s only, in %(currency)s. Other companies are not added in, because they may "
                "use another currency.", company=company.name, currency=currency.name),
        }

    def _to_company(self, amount, currency, when):
        company = self.env.company
        if not amount:
            return 0.0
        if currency == company.currency_id:
            return amount
        return currency._convert(amount, company.currency_id, company, when or fields.Date.context_today(self))

    @staticmethod
    def _pct(value, previous):
        return ((value - previous) / abs(previous) * 100.0) if previous else None

    def _month_label(self, month_start):
        return format_date(self.env, month_start, date_format='MMM yyyy')

    def _months(self, end):
        first = end.replace(day=1) - relativedelta(months=_TREND_MONTHS - 1)
        return [first + relativedelta(months=i) for i in range(_TREND_MONTHS)]

    @staticmethod
    def _kpi(key, label, value, kind='currency', previous=None, polarity='up_good', tip='', drill=None, note=None):
        """One KPI card. ``polarity`` says whether an increase is favourable (``up_good``), unfavourable (``up_bad``) or
        neither (``neutral``): the arrow's colour follows the business meaning, not the sign of the change."""
        return {'key': key, 'label': label, 'value': value, 'kind': kind, 'previous': previous,
                'delta_pct': (None if value is None or previous is None else
                              (((value - previous) / abs(previous) * 100.0) if previous else None)),
                'polarity': polarity, 'tip': tip, 'drill': drill, 'note': note}

    # ------------------------------------------------------------------ SALES
    @api.model
    def get_sales(self, date_from=None, date_to=None):
        self._require_any_group(SALES_GROUPS, self.env._("The Sales dashboard is available to the Sales role, the "
                                                         "Accountant, the CEO and the workspace owner."))
        self = self._scoped()
        start, end, pstart, pend = self._period(date_from, date_to)
        meta = self._meta(start, end, pstart, pend)
        if 'sale.order' not in self.env:
            return {'meta': meta, 'available': False, 'kpis': [], 'charts': [], 'tables': [],
                    'reason': self.env._("The Sales app is not installed for this company.")}
        try:
            current = self._sales_window(start, end)
            previous = self._sales_window(pstart, pend)
            current['growth'] = self._pct(current['value'], previous['value'])
            trend = self._sales_trend(end)
            tables = self._sales_tables(start, end, current)
        except AccessError:
            return {'meta': meta, 'available': False, 'kpis': [], 'charts': [], 'tables': [],
                    'reason': self.env._("You do not have access to the sales records.")}
        orders_domain = self._order_domain(start, end, ('sale',))
        quote_domain = self._order_domain(start, end, ('draft', 'sent'))
        kpis = [
            self._kpi('quotations', self.env._("Open quotations"), current['quotes'], 'count', previous['quotes'],
                      'neutral', self.env._("Draft and sent quotations created in the period. Not yet orders."),
                      {'model': 'sale.order', 'domain': quote_domain, 'name': self.env._("Quotations")}),
            self._kpi('orders', self.env._("Confirmed orders"), current['orders'], 'count', previous['orders'], 'up_good',
                      self.env._("Sale orders confirmed in the period (state Sales Order)."),
                      {'model': 'sale.order', 'domain': orders_domain, 'name': self.env._("Confirmed orders")}),
            self._kpi('order_value', self.env._("Order value"), current['value'], 'currency', previous['value'],
                      'up_good', self.env._("Untaxed total of the confirmed orders, in the company currency. This is "
                                            "sales intent, not accounting revenue."),
                      {'model': 'sale.order', 'domain': orders_domain, 'name': self.env._("Confirmed orders")}),
            self._kpi('avg_order', self.env._("Average order value"), current['avg'], 'currency', previous['avg'],
                      'up_good', self.env._("Order value divided by the number of confirmed orders."),
                      note=None if current['orders'] else self.env._("No confirmed orders in the period.")),
            self._kpi('conversion', self.env._("Quotation conversion"), current['conversion'], 'percent',
                      previous['conversion'], 'up_good',
                      self.env._("Confirmed orders divided by (confirmed orders + open quotations) created in the "
                                 "period. Undefined when there are none."),
                      note=None if current['conversion'] is not None else self.env._("Not defined: no orders or "
                                                                                      "quotations in the period.")),
            self._kpi('growth', self.env._("Order value growth"), current['growth'], 'percent', None, 'up_good',
                      self.env._("Order value compared with the previous period of the same length."),
                      note=None if current['growth'] is not None else self.env._("Not defined: no order value in the "
                                                                                  "previous period.")),
            self._kpi('invoiced', self.env._("Invoiced sales"), current['invoiced'], 'currency', previous['invoiced'],
                      'up_good', self.env._("Posted customer invoices, untaxed, in the period."),
                      {'model': 'account.move', 'domain': self._invoice_domain(start, end, ('out_invoice',)),
                       'name': self.env._("Customer invoices")}),
            self._kpi('credit_notes', self.env._("Credit notes"), current['credit_notes'], 'currency',
                      previous['credit_notes'], 'up_bad', self.env._("Posted customer credit notes, untaxed, in the "
                                                                     "period."),
                      {'model': 'account.move', 'domain': self._invoice_domain(start, end, ('out_refund',)),
                       'name': self.env._("Credit notes")}),
            self._kpi('net_sales', self.env._("Net sales"), current['net'], 'currency', previous['net'], 'up_good',
                      self.env._("Invoiced sales minus credit notes (billing basis). Recognised accounting revenue is "
                                 "on the Accountant dashboard.")),
        ]
        charts = [{
            'key': 'sales_trend', 'label': self.env._("Order value and net sales, last 6 months"), 'type': 'bar',
            'labels': trend['labels'], 'unit': 'currency',
            'series': [{'name': self.env._("Order value"), 'data': trend['orders']},
                       {'name': self.env._("Net sales"), 'data': trend['net']}],
        }]
        return {'meta': meta, 'available': True, 'kpis': kpis, 'charts': charts, 'tables': tables,
                'empty': not (current['orders'] or current['quotes'] or current['invoiced'] or current['credit_notes'])}

    def _order_domain(self, start, end, states):
        return [('state', 'in', list(states)), ('company_id', '=', self.env.company.id),
                ('date_order', '>=', fields.Date.to_string(start)),
                ('date_order', '<', fields.Date.to_string(end + timedelta(days=1)))]

    def _invoice_domain(self, start, end, move_types):
        return [('move_type', 'in', list(move_types)), ('state', '=', 'posted'),
                ('company_id', '=', self.env.company.id),
                ('invoice_date', '>=', fields.Date.to_string(start)), ('invoice_date', '<=', fields.Date.to_string(end))]

    def _converted_orders(self, start, end, states):
        """``[(order, untaxed amount in company currency)]`` for the orders of the window."""
        orders = self.env['sale.order'].search_fetch(
            self._order_domain(start, end, states),
            ['name', 'partner_id', 'user_id', 'state', 'date_order', 'amount_untaxed', 'currency_id'])
        return [(o, self._to_company(o.amount_untaxed, o.currency_id, o.date_order.date() if o.date_order else None))
                for o in orders]

    def _net_invoiced(self, start, end, move_types):
        """Sum of the company-currency untaxed amount of the posted documents (credit notes carry their own sign)."""
        rows = self.env['account.move']._read_group(
            self._invoice_domain(start, end, move_types), ['move_type'], ['amount_untaxed_signed:sum'])
        return {move_type: total or 0.0 for move_type, total in rows}

    def _sales_window(self, start, end):
        confirmed = self._converted_orders(start, end, ('sale',))
        quotes = self._converted_orders(start, end, ('draft', 'sent'))
        value = sum(v for _o, v in confirmed)
        invoiced = self._net_invoiced(start, end, ('out_invoice', 'out_refund'))
        out_invoice, out_refund = invoiced.get('out_invoice', 0.0), -invoiced.get('out_refund', 0.0)
        count = len(confirmed)
        total_intent = count + len(quotes)
        return {
            'orders': count, 'quotes': len(quotes), 'value': value, 'avg': (value / count) if count else None,
            'conversion': (count / total_intent * 100.0) if total_intent else None,
            'invoiced': out_invoice, 'credit_notes': out_refund, 'net': out_invoice - out_refund, 'growth': None,
            'rows': confirmed, 'quote_rows': quotes,
        }

    def _sales_trend(self, end):
        months = self._months(end)
        first, last = months[0], months[-1] + relativedelta(months=1) - timedelta(days=1)
        confirmed = self._converted_orders(first, last, ('sale',))
        by_month = {m: 0.0 for m in months}
        for order, amount in confirmed:
            stamp = fields.Datetime.context_timestamp(self, order.date_order).date() if order.date_order else first
            key = stamp.replace(day=1)
            if key in by_month:
                by_month[key] += amount
        net = {}
        for month in months:
            month_end = month + relativedelta(months=1) - timedelta(days=1)
            got = self._net_invoiced(month, month_end, ('out_invoice', 'out_refund'))
            net[month] = got.get('out_invoice', 0.0) + got.get('out_refund', 0.0)
        return {'labels': [self._month_label(m) for m in months], 'orders': [by_month[m] for m in months],
                'net': [net[m] for m in months]}

    def _sales_tables(self, start, end, current):
        env = self.env
        customers, people = {}, {}
        for order, amount in current['rows']:
            key = (order.partner_id.id, order.partner_id.display_name or env._("Unknown"))
            customers[key] = customers.get(key, 0.0) + amount
            pkey = (order.user_id.id, order.user_id.name or env._("Unassigned"))
            people[pkey] = people.get(pkey, 0.0) + amount
        lines = env['sale.order.line'].search_fetch(
            [('order_id.state', '=', 'sale'), ('company_id', '=', env.company.id), ('display_type', '=', False),
             ('order_id.date_order', '>=', fields.Date.to_string(start)),
             ('order_id.date_order', '<', fields.Date.to_string(end + timedelta(days=1)))],
            ['product_id', 'price_subtotal', 'currency_id', 'order_id'])
        products = {}
        for line in lines:
            if not line.product_id:
                continue
            when = line.order_id.date_order.date() if line.order_id.date_order else None
            key = (line.product_id.id, line.product_id.display_name)
            products[key] = products.get(key, 0.0) + self._to_company(line.price_subtotal, line.currency_id, when)

        def ranking(key, label, store, model, empty):
            rows = sorted(store.items(), key=lambda item: -item[1])[:_TOP]
            return {'key': key, 'label': label, 'kind': 'ranking', 'model': model, 'empty': empty,
                    'rows': [{'id': rid, 'label': name, 'value': total} for (rid, name), total in rows]}

        def recent(key, label, rows):
            ordered = sorted(rows, key=lambda pair: pair[0].date_order or fields.Datetime.now(), reverse=True)[:_RECENT]
            return {'key': key, 'label': label, 'kind': 'documents', 'model': 'sale.order',
                    'empty': env._("No records in this period."),
                    'rows': [{'id': o.id, 'name': o.name, 'partner': o.partner_id.display_name or '',
                              'date': fields.Date.to_string(o.date_order.date()) if o.date_order else '',
                              'value': amount, 'state': o.state} for o, amount in ordered]}

        empty = env._("No confirmed orders in this period.")
        return [
            ranking('top_customers', env._("Top customers (order value)"), customers, 'res.partner', empty),
            ranking('top_products', env._("Top products (order value)"), products, 'product.product', empty),
            ranking('salespeople', env._("Salesperson ranking (order value)"), people, 'res.users', empty),
            recent('recent_orders', env._("Recent confirmed orders"), current['rows']),
            recent('recent_quotes', env._("Recent open quotations"), current['quote_rows']),
        ]

    # ------------------------------------------------------------------ INVOICING
    @api.model
    def get_invoicing(self, date_from=None, date_to=None):
        self._require_any_group(FINANCIAL_GATE_GROUPS, self.env._(
            "The Invoicing dashboard is available to the Accountant, the CEO and the workspace owner."))
        self = self._scoped()
        start, end, pstart, pend = self._period(date_from, date_to)
        meta = self._meta(start, end, pstart, pend)
        today = fields.Date.context_today(self)
        current, previous = self._invoicing_window(start, end, today), self._invoicing_window(pstart, pend, today)
        trend = self._invoicing_trend(end)
        tables = self._invoicing_tables(start, end, today)
        base = {'model': 'account.move'}
        issued = {**base, 'domain': self._invoice_domain(start, end, ('out_invoice',)),
                  'name': self.env._("Customer invoices")}
        overdue_domain = [('move_type', '=', 'out_invoice'), ('state', '=', 'posted'),
                          ('company_id', '=', self.env.company.id), ('payment_state', 'in', ['not_paid', 'partial']),
                          ('invoice_date_due', '<', fields.Date.to_string(today))]
        dso = current['dso']
        kpis = [
            self._kpi('issued', self.env._("Invoices issued"), current['issued'], 'count', previous['issued'],
                      'neutral', self.env._("Posted customer invoices dated in the period."), issued),
            self._kpi('invoiced', self.env._("Invoiced amount"), current['invoiced'], 'currency', previous['invoiced'],
                      'up_good', self.env._("Untaxed total of the posted customer invoices in the period."), issued),
            self._kpi('credit_notes', self.env._("Credit notes"), current['credit_notes'], 'currency',
                      previous['credit_notes'], 'up_bad',
                      self.env._("Untaxed total of the posted customer credit notes (refunds) in the period."),
                      {**base, 'domain': self._invoice_domain(start, end, ('out_refund',)),
                       'name': self.env._("Credit notes")}),
            self._kpi('paid', self.env._("Paid amount"), current['paid'], 'currency', previous['paid'], 'up_good',
                      self.env._("Tax-included amount already collected on the invoices issued in the period (to date)."),
                      issued),
            self._kpi('collection_rate', self.env._("Collection rate"), current['collection_rate'], 'percent',
                      previous['collection_rate'], 'up_good',
                      self.env._("Paid amount divided by the tax-included amount of the invoices issued in the period."),
                      note=None if current['collection_rate'] is not None else self.env._("Not defined: nothing "
                                                                                          "invoiced in the period.")),
            self._kpi('unpaid', self.env._("Unpaid invoices"), current['unpaid'], 'count', previous['unpaid'], 'up_bad',
                      self.env._("Invoices issued in the period with nothing paid yet."),
                      {**base, 'domain': issued['domain'] + [('payment_state', '=', 'not_paid')],
                       'name': self.env._("Unpaid invoices")}),
            self._kpi('partial', self.env._("Partially paid invoices"), current['partial'], 'count',
                      previous['partial'], 'neutral', self.env._("Invoices issued in the period that are partly paid."),
                      {**base, 'domain': issued['domain'] + [('payment_state', '=', 'partial')],
                       'name': self.env._("Partially paid invoices")}),
            self._kpi('outstanding', self.env._("Outstanding receivables"), current['outstanding'], 'currency', None,
                      'up_bad', self.env._("What customers owe today: open invoices, tax included. Customer credit is "
                                           "NOT netted in (it is shown separately)."),
                      {**base, 'domain': [('move_type', '=', 'out_invoice'), ('state', '=', 'posted'),
                                          ('company_id', '=', self.env.company.id),
                                          ('payment_state', 'in', ['not_paid', 'partial'])],
                       'name': self.env._("Open invoices")}, self.env._("As of today")),
            self._kpi('overdue', self.env._("Overdue receivables"), current['overdue'], 'currency', None, 'up_bad',
                      self.env._("Part of the open invoices whose due date has passed, tax included."),
                      {**base, 'domain': overdue_domain, 'name': self.env._("Overdue invoices")},
                      self.env._("As of today")),
            self._kpi('customer_credit', self.env._("Customer credit"), current['credit'], 'currency', None, 'neutral',
                      self.env._("Unapplied customer payments and credit notes held for customers. Shown apart from the "
                                 "receivables."), None, self.env._("As of today")),
            self._kpi('dso', self.env._("Average collection period (DSO)"), dso['days'], 'days', previous['dso']['days'],
                      'up_bad', self.env._(
                          "Days Sales Outstanding = average trade receivables ÷ net credit sales × days in the period. "
                          "Receivables are the open invoices at the start and end of the period (customer credit not "
                          "netted); net credit sales are the tax-included posted invoices minus credit notes of the "
                          "period."),
                      note=None if dso['days'] is not None else dso['reason']),
        ]
        charts = [
            {'key': 'invoicing_trend', 'label': self.env._("Net invoiced and payments received, last 6 months"),
             'type': 'bar', 'labels': trend['labels'], 'unit': 'currency',
             'series': [{'name': self.env._("Net invoiced"), 'data': trend['net']},
                        {'name': self.env._("Payments received"), 'data': trend['received']}]},
        ]
        return {'meta': meta, 'available': True, 'kpis': kpis, 'charts': charts, 'tables': tables,
                'dso': {**dso, 'previous_days': previous['dso']['days']},
                'empty': not (current['issued'] or current['credit_notes'] or current['outstanding'])}

    def _open_receivables(self, as_of):
        return self.env['ncollection.account.open.items'].position('asset_receivable', as_of, self.env.company)

    def _invoicing_window(self, start, end, today):
        env = self.env
        domain = self._invoice_domain(start, end, ('out_invoice',))
        invoices = env['account.move'].search_fetch(
            domain, ['amount_total_signed', 'amount_residual_signed', 'payment_state'])
        total = sum(invoices.mapped('amount_total_signed'))
        paid = total - sum(invoices.mapped('amount_residual_signed'))
        net = self._net_invoiced(start, end, ('out_invoice', 'out_refund'))
        gross = env['account.move']._read_group(
            self._invoice_domain(start, end, ('out_invoice', 'out_refund')), [], ['amount_total_signed:sum'])
        credit_sales = (gross[0][0] if gross else 0.0) or 0.0
        as_of = min(end, today)
        open_end, _credit = self._open_receivables(as_of)
        open_start, _c = self._open_receivables(start - timedelta(days=1))
        days = (end - start).days + 1
        if credit_sales <= 0:
            dso = {'days': None, 'reason': env._("Not defined: no net credit sales in the period."),
                   'average_receivables': (open_start + open_end) / 2.0, 'net_credit_sales': credit_sales,
                   'period_days': days}
        else:
            average = (open_start + open_end) / 2.0
            dso = {'days': average / credit_sales * days, 'reason': None, 'average_receivables': average,
                   'net_credit_sales': credit_sales, 'period_days': days}
        open_today, credit_today = self._open_receivables(today)
        overdue = env['account.move']._read_group(
            [('move_type', '=', 'out_invoice'), ('state', '=', 'posted'), ('company_id', '=', env.company.id),
             ('payment_state', 'in', ['not_paid', 'partial']), ('invoice_date_due', '<', fields.Date.to_string(today))],
            [], ['amount_residual_signed:sum'])
        return {
            'issued': len(invoices), 'invoiced': net.get('out_invoice', 0.0), 'credit_notes': -net.get('out_refund', 0.0),
            'paid': paid, 'collection_rate': (paid / total * 100.0) if total else None,
            'unpaid': len(invoices.filtered(lambda m: m.payment_state == 'not_paid')),
            'partial': len(invoices.filtered(lambda m: m.payment_state == 'partial')),
            'outstanding': open_today, 'overdue': (overdue[0][0] if overdue else 0.0) or 0.0, 'credit': credit_today,
            'dso': dso,
        }

    def _invoicing_trend(self, end):
        months = self._months(end)
        net, received = [], []
        for month in months:
            month_end = month + relativedelta(months=1) - timedelta(days=1)
            got = self._net_invoiced(month, month_end, ('out_invoice', 'out_refund'))
            net.append(got.get('out_invoice', 0.0) + got.get('out_refund', 0.0))
            paid = self.env['account.payment']._read_group(
                [('payment_type', '=', 'inbound'), ('partner_type', '=', 'customer'), ('state', 'in', ('paid', 'in_process')),
                 ('company_id', '=', self.env.company.id), ('date', '>=', fields.Date.to_string(month)),
                 ('date', '<=', fields.Date.to_string(month_end))], [], ['amount_company_currency_signed:sum'])
            received.append((paid[0][0] if paid else 0.0) or 0.0)
        return {'labels': [self._month_label(m) for m in months], 'net': net, 'received': received}

    def _invoicing_tables(self, start, end, today):
        env = self.env
        Move = env['account.move']
        overdue = Move.search_fetch(
            [('move_type', '=', 'out_invoice'), ('state', '=', 'posted'), ('company_id', '=', env.company.id),
             ('payment_state', 'in', ['not_paid', 'partial']), ('invoice_date_due', '<', fields.Date.to_string(today))],
            ['name', 'partner_id', 'invoice_date_due', 'amount_residual_signed'], order='amount_residual_signed desc',
            limit=_RECENT)
        by_customer = Move._read_group(
            self._invoice_domain(start, end, ('out_invoice', 'out_refund')), ['partner_id'],
            ['amount_untaxed_signed:sum'], order='amount_untaxed_signed:sum desc', limit=_TOP)
        recent = Move.search_fetch(
            self._invoice_domain(start, end, ('out_invoice', 'out_refund')),
            ['name', 'partner_id', 'invoice_date', 'amount_total_signed', 'payment_state'], order='invoice_date desc, id desc',
            limit=_RECENT)
        return [
            {'key': 'overdue_invoices', 'label': env._("Overdue invoices (as of today)"), 'kind': 'documents',
             'model': 'account.move', 'empty': env._("No overdue invoices."),
             'rows': [{'id': m.id, 'name': m.name, 'partner': m.partner_id.display_name or '',
                       'date': fields.Date.to_string(m.invoice_date_due), 'value': m.amount_residual_signed,
                       'state': 'overdue', 'days': (today - m.invoice_date_due).days} for m in overdue]},
            {'key': 'top_customers', 'label': env._("Top customers (net invoiced)"), 'kind': 'ranking',
             'model': 'res.partner', 'empty': env._("No invoices in this period."),
             'rows': [{'id': partner.id, 'label': partner.display_name or env._("Unknown"), 'value': total or 0.0}
                      for partner, total in by_customer]},
            {'key': 'recent_invoices', 'label': env._("Recent invoices and credit notes"), 'kind': 'documents',
             'model': 'account.move', 'empty': env._("No invoices in this period."),
             'rows': [{'id': m.id, 'name': m.name, 'partner': m.partner_id.display_name or '',
                       'date': fields.Date.to_string(m.invoice_date), 'value': m.amount_total_signed,
                       'state': m.payment_state} for m in recent]},
        ]

    # ------------------------------------------------------------------ one entry for the client and the exports
    @api.model
    def payload(self, kind, date_from=None, date_to=None):
        if kind == 'sales':
            return self.get_sales(date_from, date_to)
        if kind == 'invoicing':
            return self.get_invoicing(date_from, date_to)
        raise UserError(self.env._("Unknown dashboard."))
