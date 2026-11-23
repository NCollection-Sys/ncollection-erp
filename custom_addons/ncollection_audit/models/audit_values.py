# -*- coding: utf-8 -*-
"""Reading the values the trail stored, and showing them to a person.

Both capture paths store a value the way OCA ``auditlog`` does: ``str()`` of ``convert_to_read`` in a Text column
(``(5, 'Name')`` for a many2one, ``[1, 2]`` for a many2many, ``2026-11-23`` for a date, NULL for False / empty). The
parser is TYPED BY THE FIELD, never by the look of the text: a Char whose value is ``"123"`` stays the text ``123``.
"""
import ast
import datetime

from odoo import fields
from odoo.tools import format_date, format_datetime, formatLang
from odoo.tools.translate import LazyTranslate

from .audit_policy import MAX_TEXT, SECRET_NAME

_lt = LazyTranslate(__name__)

TRUNCATED = '…[truncated]'
SUPPORTED = {'char', 'text', 'html', 'selection', 'boolean', 'integer', 'float', 'monetary', 'date', 'datetime',
             'many2one', 'many2many'}

# User-facing document types. Keys are `nc_doc_type` values (model, or model:subtype). Anything else falls back to
# the model's own (translated) description - never a technical name when a label exists.
DOC_LABELS = {
    'res.partner:customer': _lt("Customer"),
    'res.partner:supplier': _lt("Supplier"),
    'res.partner:contact': _lt("Contact"),
    'res.partner': _lt("Contact"),
    'res.partner.bank': _lt("Bank Account"),
    'res.company': _lt("Company Settings"),
    'res.users': _lt("User Access"),
    'ncollection.role': _lt("Role & Permissions"),
    'ncollection.workspace.config': _lt("Business Settings"),
    'account.move:out_invoice': _lt("Customer Invoice"),
    'account.move:in_invoice': _lt("Supplier Bill"),
    'account.move:out_refund': _lt("Customer Credit Note"),
    'account.move:in_refund': _lt("Supplier Credit Note"),
    'account.move:out_receipt': _lt("Sales Receipt"),
    'account.move:in_receipt': _lt("Purchase Receipt"),
    'account.move:entry': _lt("Journal Entry"),
    'account.move': _lt("Journal Entry"),
    'account.payment:inbound': _lt("Receipt"),
    'account.payment:outbound': _lt("Payment"),
    'account.payment': _lt("Payment"),
    'account.journal': _lt("Bank / Cash Journal"),
    'account.account': _lt("Account"),
    'account.tax': _lt("Tax"),
    'account.payment.term': _lt("Payment Terms"),
    'account.analytic.account': _lt("Cost / Profit Centre"),
    'account.asset': _lt("Fixed Asset"),
    'ncollection.internal.transfer': _lt("Internal Transfer"),
    'ncollection.journal.transaction': _lt("Bank Transaction"),
    'ncollection.bank.payment.batch': _lt("Payment Batch"),
    'product.template': _lt("Product"),
    'product.category': _lt("Product Category"),
    'sale.order': _lt("Sales Order"),
    'purchase.order': _lt("Purchase Order"),
    'stock.picking:incoming': _lt("Goods Receipt"),
    'stock.picking:outgoing': _lt("Delivery"),
    'stock.picking:internal': _lt("Internal Stock Move"),
    'stock.picking': _lt("Inventory Movement"),
    'stock.warehouse': _lt("Warehouse"),
    'stock.location': _lt("Stock Location"),
    'hr.employee': _lt("Employee"),
}


def doc_label(env, key, model_name=None):
    if key in DOC_LABELS:
        return env._(DOC_LABELS[key])
    model = (key or '').split(':')[0]
    if model and model in env:
        return env['ir.model']._get(model).name or model_name or model
    return model_name or model or ''


def parse(field, raw):
    """The stored text -> a Python value usable in a write. Raises ValueError when it cannot be read."""
    if raw is None or raw is False or raw == '' or raw == 'False':
        return False if field.type != 'many2many' else []
    kind = field.type
    if kind in ('char', 'text', 'html', 'selection'):
        return raw
    if kind == 'boolean':
        return raw in ('True', '1', 'true')
    if kind == 'integer':
        return int(float(raw))
    if kind in ('float', 'monetary'):
        return float(raw)
    if kind == 'date':
        return fields.Date.to_date(str(raw)[:10])
    if kind == 'datetime':
        return fields.Datetime.to_datetime(str(raw)[:19])
    if kind == 'many2one':
        value = ast.literal_eval(raw) if isinstance(raw, str) else raw
        if isinstance(value, (tuple, list)):
            value = value[0] if value else False
        return int(value) if value else False
    if kind == 'many2many':
        value = ast.literal_eval(raw) if isinstance(raw, str) else raw
        return sorted(int(v) for v in (value or []) if not isinstance(v, (tuple, list))) if value else []
    raise ValueError(kind)


def current(record, field):
    """The record's present value in the same shape `parse` returns."""
    value = field.convert_to_read(record[field.name], record)
    if field.type == 'many2one':
        return value[0] if value else False
    if field.type == 'many2many':
        return sorted(value or [])
    if field.type == 'date':
        return value or False
    if field.type == 'datetime':
        return value or False
    if field.type in ('char', 'text', 'html', 'selection'):
        return value or False
    return value


def same(field, a, b):
    if field.type in ('float', 'monetary'):
        return round(float(a or 0.0), 6) == round(float(b or 0.0), 6)
    if field.type in ('char', 'text', 'html', 'selection'):
        return (a or False) == (b or False)
    return a == b


def is_truncated(raw):
    return isinstance(raw, str) and raw.endswith(TRUNCATED)


def masked(env, model, field):
    """True when this user may not see the field's value (restricted field, or a credential by name)."""
    if SECRET_NAME.search(field.name):
        return True
    if field.groups and not env.su:
        return not env.user.has_groups(field.groups)
    return False


def _names(env, field, raw, raw_text):
    """Display names for relational values, from what was recorded (the record may be gone)."""
    try:
        text = ast.literal_eval(raw_text) if isinstance(raw_text, str) else raw_text
    except (ValueError, SyntaxError):
        text = None
    if field.type == 'many2one':
        if isinstance(text, (tuple, list)) and len(text) == 2 and isinstance(text[1], str):
            return [text[1]]
        ident = parse(field, raw)
        if not ident:
            return []
        rec = env[field.comodel_name].sudo().with_context(active_test=False).browse(ident).exists()
        return [rec.display_name if rec else env._("#%(id)s (deleted)", id=ident)]
    ids = parse(field, raw)
    pairs = {}
    if isinstance(text, list):
        for item in text:
            if isinstance(item, (tuple, list)) and len(item) == 2:
                pairs[item[0]] = item[1] if item[1] != 'DELETED' else env._("#%(id)s (deleted)", id=item[0])
    missing = [i for i in ids if i not in pairs]
    if missing:
        recs = env[field.comodel_name].sudo().with_context(active_test=False).browse(missing).exists()
        for rec in recs:
            pairs[rec.id] = rec.display_name
    return [pairs.get(i) or env._("#%(id)s (deleted)", id=i) for i in ids]


def display(env, model, field, raw, raw_text=None, currency=None):
    """(text shown, kind) for a stored value. kind: empty | value | masked | truncated."""
    if masked(env, model, field):
        return env._("Hidden (restricted field)"), 'masked'
    if raw is None or raw is False or raw == '' or (raw == 'False' and field.type != 'boolean'):
        if field.type == 'boolean':
            return env._("No"), 'value'
        return '', 'empty'
    if is_truncated(raw):
        return raw[:300] + '…', 'truncated'
    try:
        kind = field.type
        if kind == 'boolean':
            return (env._("Yes") if parse(field, raw) else env._("No")), 'value'
        if kind == 'selection':
            labels = dict(field._description_selection(env))
            return str(labels.get(raw, raw)), 'value'
        if kind in ('many2one', 'many2many'):
            names = _names(env, field, raw, raw_text)
            return (', '.join(names) if names else ''), ('value' if names else 'empty')
        if kind == 'monetary':
            return formatLang(env, parse(field, raw), currency_obj=currency) if currency else formatLang(
                env, parse(field, raw), digits=2), 'value'
        if kind == 'float':
            digits = field.get_digits(env)
            return formatLang(env, parse(field, raw), digits=digits[1] if digits else 2), 'value'
        if kind == 'integer':
            return formatLang(env, parse(field, raw), digits=0), 'value'
        if kind == 'date':
            return format_date(env, parse(field, raw)), 'value'
        if kind == 'datetime':
            value = parse(field, raw)
            return format_datetime(env, value, tz=env.user.tz or 'UTC', dt_format='medium'), 'value'
        if kind == 'html':
            from odoo.tools import html2plaintext
            return html2plaintext(raw)[:MAX_TEXT], 'value'
    except (ValueError, SyntaxError, TypeError):
        pass
    return str(raw), 'value'


def display_current(env, record, field, currency=None):
    """Display of the record's present value (for the undo preview)."""
    value = field.convert_to_read(record[field.name], record)
    if field.type == 'many2one':
        raw, text = (str(value[0]) if value else None), (str(value) if value else None)
    elif field.type == 'many2many':
        raw, text = str(list(value or [])), None
    elif isinstance(value, (datetime.date, datetime.datetime)):
        raw, text = str(value), None
    elif value is False or value is None:
        raw, text = None, None
    else:
        raw, text = str(value), None
    return display(env, record._name, field, raw, text, currency)


def currency_of(record):
    """The currency to show a monetary value in: the record's own, else its company's."""
    if record is None:
        return None
    for name in ('currency_id', 'company_currency_id'):
        field = record._fields.get(name)
        if field is not None and field.type == 'many2one' and field.comodel_name == 'res.currency':
            if record[name]:
                return record[name]
    company = record._fields.get('company_id') and record.company_id
    return company.currency_id if company else record.env.company.currency_id
