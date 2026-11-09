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
        ('sales', 'orders', 'revenue', 'top customer', 'best customer', 'selling'),
        ('مبيعات', 'المبيعات', 'طلبات', 'الطلبات', 'إيرادات', 'أفضل العملاء'),
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
