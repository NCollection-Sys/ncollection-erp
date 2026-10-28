# -*- coding: utf-8 -*-
"""Plain-Python payload builders shared by the engine tests and by the out-of-Odoo end-to-end run against the official
Schematron. No Odoo import on purpose: the end-to-end script loads this file directly."""

import copy
from decimal import Decimal as D

from ..lib import reconcile
from ..lib.identity import transaction_type_code

SELLER = {
    'name': 'Seller Legal Name LLC',
    'trading_name': 'Seller Trade Name',
    'endpoint_scheme': '0235',
    'endpoint_id': '1987654321',
    'tin': '1987654321',
    'trn': '198765432102003',
    'legal_id': '112345678900003',
    'legal_type': 'TL',
    'legal_authority': 'Dubai Department of Economy',
    'street': 'Al Maktoum Road',
    'city': 'Dubai',
    'subdivision': 'DXB',
    'country': 'AE',
}
BUYER = {
    'name': 'Noor Electronics LLC',
    'trading_name': 'Noor Electronics',
    'endpoint_scheme': '0235',
    'endpoint_id': '1345678901',
    'tin': '1345678901',
    'trn': '134567890123003',
    'legal_id': '112345679000001',
    'legal_type': 'TL',
    'legal_authority': 'Abu Dhabi Department of Economic Development',
    'street': 'Hamdan Street',
    'city': 'Abu Dhabi',
    'subdivision': 'AUH',
    'country': 'AE',
}


def _line(number, qty, price, rate='5', category='S', **extra):
    net = (D(qty) * D(price)).quantize(D('0.01'))
    tax = (net * D(rate) / 100).quantize(D('0.01'), rounding='ROUND_HALF_UP') if category == 'S' else D('0.00')
    line = {
        'id': str(number),
        'name': 'Item %s' % number,
        'description': 'Item %s description' % number,
        'qty': D(qty),
        'unit_code': 'H87',
        'price_gross': D(price),
        'price_discount': D('0'),
        'price_net': D(price),
        'base_qty': D('1'),
        'net': net,
        'category': category,
        'rate': D(rate) if category in ('S', 'Z', 'AE', 'N') else None,
        'odoo_tax': tax,
        'aed_amount': net,
        'aed_vat': tax,
        'item_type': 'G',
        'hs': '88098432324',
        'sac': None,
        'goods_type': None,
        'gtin': None,
        'exemption_code': None,
    }
    line.update(extra)
    return line


def totals_for(lines, adjustments=()):
    untaxed = sum((line['net'] for line in lines), D('0'))
    untaxed += sum((a['amount'] if a['charge'] else -a['amount'] for a in adjustments), D('0'))
    tax = sum((line['odoo_tax'] for line in lines), D('0')) + sum((a['odoo_tax'] for a in adjustments), D('0'))
    return {'untaxed': untaxed, 'tax': tax, 'total': untaxed + tax}


def standard_payload(**overrides):
    flags = {}
    lines = [_line(1, 2, '100.00'), _line(2, 1, '50.50')]
    payload = {
        'kind': 'invoice',
        'type_code': '380',
        'number': 'INV/2026/00001',
        'uuid': 'f12f329f-6430-4399-b661-7c5cd9c3a9e6',
        'issue_date': '2026-10-01',
        'issue_time': '09:30:00+04:00',
        'due_date': '2026-10-31',
        'currency': 'AED',
        'exchange_rate': None,
        'flags': flags,
        'transaction_type_code': transaction_type_code(flags),
        'seller': copy.deepcopy(SELLER),
        'buyer': copy.deepcopy(BUYER),
        'payment': {'means_code': '30', 'account_id': 'AE070331234567890123456'},
        'lines': lines,
        'adjustments': [],
        'odoo_totals': totals_for(lines),
    }
    payload.update(overrides)
    return finish(payload)


def finish(payload):
    """Attach the reconciliation figures (the XML carries Odoo's values) to a payload."""
    payload['odoo_totals'] = payload.get('odoo_totals') or totals_for(payload['lines'], payload.get('adjustments', []))
    payload['figures'] = reconcile.reconcile(payload).figures
    return payload


def credit_note_payload(**overrides):
    lines = [_line(1, 1, '100.00')]
    payload = standard_payload(
        kind='credit_note',
        type_code='381',
        number='RINV/2026/00001',
        uuid='f1c9d83d-e31f-43fc-919f-10b961f599e7',
        due_date=None,
        payment=None,
        lines=lines,
        odoo_totals=totals_for(lines),
        credit_reason='DL8.61.1.E',
        preceding=[{'number': 'INV/2026/00001', 'date': '2026-10-01'}],
    )
    payload.update(overrides)
    return finish(payload)
