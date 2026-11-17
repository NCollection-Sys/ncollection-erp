# -*- coding: utf-8 -*-
"""READ-ONLY data topics the assistant can answer from (English and Arabic).

HOW THE ASSISTANT GETS ITS FACTS. It never queries the database itself and no model output is ever executed. A question
is matched, by plain keywords, to a few TOPICS below. Each topic is a list of aggregation specs; they are run by
``ncollection.aggregation.engine`` AS THE ASKING USER (no sudo): Odoo's access rights, record rules, the active
companies
and the plan's licence gate decide what comes back, and a spec the user cannot read simply yields nothing. The result
is
labelled, pseudonymised by ``ncollection.ai.pii`` and only then placed in the prompt.

NOTHING HERE WRITES. A topic is a ``read_group`` and nothing else; the assistant cannot create, post, cancel, delete or
modify a record. A future write-capable action would need its own permission check, an explicit confirmation by the
user,
an audit entry and a separate approval - it is deliberately not a topic.

The keyword lists are data, not code: adding a topic is adding a dict, never a new execution path.
"""

import datetime

# (key, English keywords, Arabic keywords, spec factory(today) -> list of specs)


def _payables(_today):
    return [
        {
            'key': 'payables',
            'model': 'account.move',
            'domain': [('move_type', '=', 'in_invoice'), ('state', '=', 'posted'), ('payment_state', '!=', 'paid')],
            'groupby': ['partner_id'],
            'aggregates': ['amount_residual:sum'],
            'limit': 10,
            'order': 'amount_residual:sum desc',
        }
    ]


def _overdue(today):
    return [
        {
            'key': 'overdue_receivables',
            'model': 'account.move',
            'domain': [
                ('move_type', '=', 'out_invoice'),
                ('state', '=', 'posted'),
                ('payment_state', '!=', 'paid'),
                ('invoice_date_due', '<', today.isoformat()),
            ],
            'groupby': ['partner_id'],
            'aggregates': ['amount_residual:sum'],
            'limit': 10,
            'order': 'amount_residual:sum desc',
        }
    ]


def _sales(_today):
    return [
        {
            'key': 'sales_by_month',
            'model': 'sale.order',
            'domain': [('state', '=', 'sale')],
            'groupby': ['date_order:month'],
            'aggregates': ['amount_total:sum'],
            'limit': 12,
            'order': 'date_order:month desc',
        }
    ]


def _purchases(_today):
    return [
        {
            'key': 'purchases_by_supplier',
            'model': 'purchase.order',
            'domain': [('state', '=', 'purchase')],
            'groupby': ['partner_id'],
            'aggregates': ['amount_total:sum'],
            'limit': 10,
            'order': 'amount_total:sum desc',
        }
    ]


def _payments(_today):
    return [
        {
            'key': 'customer_payments_by_month',
            'model': 'account.payment',
            'domain': [('payment_type', '=', 'inbound'), ('state', 'in', ('in_process', 'paid'))],
            'groupby': ['date:month'],
            'aggregates': ['amount:sum'],
            'limit': 12,
            'order': 'date:month desc',
        }
    ]


def _inventory(_today):
    return [
        {
            'key': 'stock_on_hand',
            'model': 'stock.quant',
            'domain': [('location_id.usage', '=', 'internal')],
            'groupby': ['product_id'],
            'aggregates': ['quantity:sum'],
            'limit': 10,
            'order': 'quantity:sum desc',
        }
    ]


def _revenue(today):
    """REVENUE = posted customer invoices net of credit notes, untaxed, company currency, by invoice month. Not
    sales orders (a commitment) and not receipts (cash): the three are different questions."""
    return [
        {
            'key': 'revenue_by_month',
            'model': 'account.move',
            'domain': [('move_type', 'in', ('out_invoice', 'out_refund')), ('state', '=', 'posted'),
                       ('invoice_date', '>=', (today.replace(day=1) - datetime.timedelta(days=365)).isoformat())],
            'groupby': ['invoice_date:month'],
            'aggregates': ['amount_untaxed_signed:sum'],
            'limit': 12,
            'order': 'invoice_date:month desc',
        }
    ]


def _expenses(today):
    """Supplier bills net of refunds, untaxed, by bill month (an accrual view, not cash paid)."""
    return [
        {
            'key': 'supplier_bills_by_month',
            'model': 'account.move',
            'domain': [('move_type', 'in', ('in_invoice', 'in_refund')), ('state', '=', 'posted'),
                       ('invoice_date', '>=', (today.replace(day=1) - datetime.timedelta(days=365)).isoformat())],
            'groupby': ['invoice_date:month'],
            'aggregates': ['amount_untaxed_signed:sum'],
            'limit': 12,
            'order': 'invoice_date:month desc',
        }
    ]


_PENDING = [
    ('payment_id', '!=', False),
    ('payment_id.state', 'in', ('in_process', 'paid')),
    ('parent_state', '=', 'posted'),
    ('journal_id.type', 'in', ('bank', 'cash')),
    ('account_id.reconcile', '=', True),
    ('reconciled', '=', False),
    ('account_id.account_type', 'not in', ('asset_receivable', 'liability_payable', 'asset_cash')),
]


def _bank_cash(_today):
    """Posted ledger balance of every bank / cash account (what the journal card calls Current Balance), and the
    money still pending on outstanding accounts, as two separate facts."""
    return [
        {
            'key': 'bank_cash_balances',
            'model': 'account.move.line',
            'domain': [('account_id.account_type', '=', 'asset_cash'), ('parent_state', '=', 'posted')],
            'groupby': ['account_id'],
            'aggregates': ['balance:sum'],
            'limit': 12,
            'order': 'balance:sum desc',
        },
        # PENDING = the Journal Operations card's definition, not Odoo's payment state: the residual of posted
        # payment lines still waiting on an OUTSTANDING account (reconcilable, unreconciled, neither the
        # receivable / payable counterpart nor a liquidity account). A receipt Odoo Community marks "Paid" because
        # it settled an invoice is still here until the bank confirms it. Incoming and outgoing apart.
        {
            'key': 'receipts_pending_bank',
            'model': 'account.move.line',
            'domain': _PENDING + [('amount_residual', '>', 0)],
            'groupby': ['journal_id'],
            'aggregates': ['amount_residual:sum'],
            'limit': 12,
            'order': 'amount_residual:sum desc',
        },
        {
            'key': 'payments_pending_bank',
            'model': 'account.move.line',
            'domain': _PENDING + [('amount_residual', '<', 0)],
            'groupby': ['journal_id'],
            'aggregates': ['amount_residual:sum'],
            'limit': 12,
            'order': 'amount_residual:sum asc',
        },
    ]


def _reconciliation(_today):
    return [
        {
            'key': 'bank_lines_to_reconcile',
            'model': 'account.bank.statement.line',
            'domain': [('is_reconciled', '=', False)],
            'groupby': ['journal_id'],
            'aggregates': ['amount:sum'],
            'limit': 12,
            'order': 'amount:sum desc',
        }
    ]


def _top_products(today):
    return [
        {
            'key': 'top_products_12_months',
            'model': 'sale.order.line',
            'domain': [('state', '=', 'sale'),
                       ('order_id.date_order', '>=', (today - datetime.timedelta(days=365)).isoformat())],
            'groupby': ['product_id'],
            'aggregates': ['price_subtotal:sum', 'product_uom_qty:sum'],
            'limit': 10,
            'order': 'price_subtotal:sum desc',
        }
    ]


TOPICS = (
    (
        'payables',
        ('payable', 'payables', 'vendor bill', 'bills', 'supplier', 'suppliers', 'we owe', 'owe '),
        ('الموردين', 'المورد', 'فواتير الموردين', 'الذمم الدائنة', 'مستحقات الموردين'),
        _payables,
    ),
    (
        'overdue',
        ('overdue', 'late', 'past due', 'collect', 'outstanding', 'unpaid'),
        ('متأخر', 'المتأخرة', 'مستحق', 'مستحقة', 'غير مسددة', 'غير المدفوعة'),
        _overdue,
    ),
    (
        'sales',
        ('sales', 'orders', 'top customer', 'best customer', 'selling'),
        ('مبيعات', 'المبيعات', 'طلبات', 'الطلبات', 'أفضل العملاء'),
        _sales,
    ),
    ('purchases', ('purchase', 'purchases', 'purchase order'), ('مشتريات', 'المشتريات', 'أوامر الشراء'), _purchases),
    (
        'payments',
        ('payment', 'payments', 'paid', 'pay ', 'collected', 'received', 'cash in'),
        ('مدفوعات', 'الدفعات', 'المقبوضات', 'تحصيل', 'مقبوض'),
        _payments,
    ),
    (
        'inventory',
        ('inventory', 'stock', 'on hand', 'warehouse', 'quantity'),
        ('مخزون', 'المخزون', 'مستودع', 'الكمية', 'البضاعة'),
        _inventory,
    ),
    ('revenue', ('revenue', 'income', 'turnover', 'invoiced', 'compare', 'growth', 'trend'),
     ('إيرادات', 'الإيرادات', 'دخل', 'مقارنة', 'نمو', 'اتجاه'), _revenue),
    ('expenses', ('expense', 'expenses', 'bills', 'costs', 'spending', 'supplier bill'),
     ('مصروفات', 'المصروفات', 'فواتير الموردين', 'تكاليف'), _expenses),
    ('bank_cash', ('bank', 'cash', 'balance', 'liquidity', 'pending'),
     ('بنك', 'البنك', 'نقد', 'الصندوق', 'رصيد', 'معلق'), _bank_cash),
    ('reconciliation', ('reconcile', 'reconciliation', 'unmatched', 'statement'),
     ('مطابقة', 'تسوية', 'كشف الحساب'), _reconciliation),
    ('top_products', ('top product', 'best product', 'best-selling', 'best selling', 'product'),
     ('أفضل المنتجات', 'المنتجات الأكثر', 'منتج'), _top_products),
)


def select_specs(question, today=None, limit=3):
    """The aggregation specs for the topics ``question`` mentions (at most ``limit`` topics, in table order)."""
    text = (question or '').lower()
    today = today or datetime.date.today()
    specs = []
    chosen = 0
    for _key, english, arabic, factory in TOPICS:
        if any(word in text for word in english) or any(word in (question or '') for word in arabic):
            specs.extend(factory(today))
            chosen += 1
            if chosen >= limit:
                break
    return specs
