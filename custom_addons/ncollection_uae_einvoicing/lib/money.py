# -*- coding: utf-8 -*-
"""Decimal helpers. Money is NEVER a float here: Odoo values arrive as Decimal (or strings) and leave as exact text.

Rounding is half-up to the cent, like Odoo's ``float_round`` default and like the official Schematron's ``round(x *
100) div 100``. The helpers only ever ROUND FOR COMPARISON AND FORMATTING; they never change what Odoo booked
(see reconcile.py: the XML carries Odoo's figures)."""

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

CENT = Decimal('0.01')
ZERO = Decimal('0')


def dec(value):
    """``value`` as a Decimal; None/'' ⇒ 0. A float is refused: it would smuggle binary rounding into a compliance
    value."""
    if value is None or value == '':
        return ZERO
    if isinstance(value, float):
        raise TypeError('floats are not allowed in e-invoicing amounts')
    try:
        return value if isinstance(value, Decimal) else Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError('not a decimal amount: %r' % (value,)) from exc


def r2(value):
    """Half-up to two decimals."""
    return dec(value).quantize(CENT, rounding=ROUND_HALF_UP)


def fmt_amount(value):
    """An amount as official-sample text: exactly two decimals ('10486.00')."""
    return format(r2(value), 'f')


def fmt_plain(value):
    """A quantity, rate or unit price without trailing zeros ('5', '4.9', '0.125')."""
    value = dec(value)
    text = format(value.normalize(), 'f')
    return '0' if text in ('-0', '') else text
