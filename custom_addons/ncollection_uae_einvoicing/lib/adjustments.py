# -*- coding: utf-8 -*-
"""Document-level allowances and charges, from a per-DOCUMENT-LINE toggle (D-13).

Odoo has no document-level allowance/charge; a "global discount" is simply a line (usually negative). Whether a line is
an item or a document-level adjustment is a property of THAT document, so the choice is made per line on the e-invoice
snapshot, never on the product (which would contaminate master data). A line is exactly one of:

* an ITEM line (``adjustment`` empty): becomes ``cac:InvoiceLine``;
* ``allowance``: becomes a document-level ``cac:AllowanceCharge`` with ``ChargeIndicator=false`` (BG-20);
* ``charge``: becomes a document-level ``cac:AllowanceCharge`` with ``ChargeIndicator=true`` (BG-21).

Representation follows the official files exactly (Standard tax invoice.xml, Doc-level-*-category.xml): a positive
``Amount`` in the document currency, a mandatory VAT category (``aligned-ibrp-032/037``), and
``MultiplierFactorNumeric`` + ``BaseAmount`` either both present or both absent (``aligned-ibrp-057/058``) with
``Amount`` = base × percentage / 100 (``ibr-131/146-ae``). Reason codes come only from the official lists (UNCL5189 for
allowances, UNCL7161 for charges) and are optional; this module never invents one. Category ``N`` (margin scheme)
cannot carry a document allowance or charge (``ibr-114/115-ae``). An unflagged line with a negative price, quantity or
amount is refused (PINT forbids negative prices, ``ibr-027/028``, and negative invoices) instead of being guessed into
an allowance.
"""

from . import codelists, issues as _issues
from .money import dec, r2

ALLOWANCE = 'allowance'
CHARGE = 'charge'


def split_lines(lines, profile=None):
    """``(item_lines, adjustments, issues)`` from the snapshot lines.

    Each adjustment is ``{'charge': bool, 'amount': Decimal>0, 'base_amount', 'percent', 'reason_code', 'reason',
    'category', 'rate', 'exemption_code', 'odoo_tax': signed Decimal, 'source_line': id}``."""
    items, adjustments, found = [], [], []
    allowance_reasons = codelists.codes('allowance_reason', profile)
    charge_reasons = codelists.codes('charge_reason', profile)
    for line in lines:
        kind = line.get('adjustment') or None
        subject = 'line %s' % line.get('id')
        net = dec(line.get('net'))
        if kind is None:
            if net < 0 or dec(line.get('price_unit')) < 0 or dec(line.get('qty')) < 0:
                found.append(
                    _issues.make(
                        'NCOLLECTION:unclassified-negative-line',
                        subject,
                        "Line %(line)s has a negative amount, price or quantity. A UAE e-invoice cannot carry "
                        "negative "
                        "lines: mark it as a document allowance (or charge) on the e-invoice, or correct the invoice.",
                        {'line': line.get('id')},
                    )
                )
                continue
            items.append(line)
            continue
        if kind not in (ALLOWANCE, CHARGE):
            found.append(
                _issues.make(
                    'NCOLLECTION:unknown-adjustment-kind',
                    subject,
                    "Line %(line)s has an unknown adjustment kind.",
                    {'line': line.get('id')},
                )
            )
            continue
        is_charge = kind == CHARGE
        if (is_charge and net < 0) or (not is_charge and net > 0):
            found.append(
                _issues.make(
                    'NCOLLECTION:adjustment-direction',
                    subject,
                    "Line %(line)s is marked as a document %(kind)s but its amount goes the other way.",
                    {'line': line.get('id'), 'kind': kind},
                )
            )
            continue
        category = line.get('category')
        if not category:
            found.append(
                _issues.make(
                    'OFFICIAL:aligned-ibrp-037' if is_charge else 'OFFICIAL:aligned-ibrp-032',
                    subject,
                    "Document %(kind)s on line %(line)s needs a VAT category.",
                    {'line': line.get('id'), 'kind': kind},
                )
            )
            continue
        if category == 'N':
            found.append(
                _issues.make(
                    'OFFICIAL:ibr-114-ae' if is_charge else 'OFFICIAL:ibr-115-ae',
                    subject,
                    "A document %(kind)s cannot use the 'Standard rate additional VAT' category.",
                    {'kind': kind},
                )
            )
            continue
        reason_code = line.get('reason_code') or None
        if reason_code and reason_code not in (charge_reasons if is_charge else allowance_reasons):
            found.append(
                _issues.make(
                    'NCOLLECTION:reason-code-not-in-official-list',
                    subject,
                    "Reason code %(code)s is not in the official %(kind)s reason list.",
                    {'code': reason_code, 'kind': kind},
                )
            )
            continue
        amount = abs(net)
        base, percent = line.get('base_amount'), line.get('percent')
        if (base in (None, '')) != (percent in (None, '')):
            found.append(
                _issues.make(
                    'OFFICIAL:aligned-ibrp-058' if is_charge else 'OFFICIAL:aligned-ibrp-057',
                    subject,
                    "Give both the base amount and the percentage of line %(line)s, or neither.",
                    {'line': line.get('id')},
                )
            )
            continue
        if base not in (None, '') and amount not in (
            dec(base) * dec(percent) / 100,
            r2(dec(base) * dec(percent) / 100),
        ):
            found.append(
                _issues.make(
                    'OFFICIAL:ibr-146-ae' if is_charge else 'OFFICIAL:ibr-131-ae',
                    subject,
                    "The amount of line %(line)s must equal base amount × percentage / 100.",
                    {'line': line.get('id')},
                )
            )
            continue
        adjustments.append(
            {
                'charge': is_charge,
                'amount': amount,
                'base_amount': None if base in (None, '') else dec(base),
                'percent': None if percent in (None, '') else dec(percent),
                'reason_code': reason_code,
                'reason': line.get('reason') or None,
                'category': category,
                'rate': line.get('rate'),
                'exemption_code': line.get('exemption_code'),
                'odoo_tax': dec(line.get('odoo_tax')),
                'source_line': line.get('id'),
            }
        )
    return items, adjustments, found
