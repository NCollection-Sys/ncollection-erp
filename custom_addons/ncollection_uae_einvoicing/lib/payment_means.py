# -*- coding: utf-8 -*-
"""Payment means (BT-081) from Odoo/NCollection structures, never from a guess.

Odoo's payment-method code (``manual``…) carries no PINT meaning, and in the tenants audited every method line is
``manual`` and no journal has a bank account, so nothing can be inferred from the data. The code therefore comes only
from an explicit, admin-maintained map (journal / payment-method line → official code) or an explicit company default,
or a per-document override. Anything else is a readiness ERROR, not a default.

Official rules (the pinned PINT-AE release):

* ``ibr-191-ae``: a payment means code is REQUIRED, except when the document is a credit note (381, 81, 261) or its
  transaction type is deemed supply (``^[01]1[01]{6}$``);
* ``ibr-192-ae``: code ``30`` (credit transfer) requires a payee account identifier;
* the code must be in the official list (UNCL4461 as shipped: 1, 10, 20, 21, 30, 49, 54, 55, 68).

``Resolution.omitted`` distinguishes "legitimately absent by rule" from "missing": the first is a valid state, the
second
is an error. Ambiguity (the document's candidate sources disagree) is also an error."""

import re
from collections import namedtuple

from . import codelists, issues as _issues

Resolution = namedtuple('Resolution', 'code omitted issues')

_DEEMED_RE = re.compile(r'[01]1[01]{6}')
CREDIT_TYPES = ('381', '81', '261')
CREDIT_TRANSFER = '30'


def exempt_from_payment_means(type_code, transaction_type_code):
    """``ibr-191-ae`` exceptions."""
    return type_code in CREDIT_TYPES or bool(_DEEMED_RE.fullmatch(transaction_type_code or ''))


def resolve(
    *,
    type_code,
    transaction_type_code,
    document_override=None,
    mapped_codes=(),
    company_default=None,
    has_payee_account=False,
    profile=None
):
    """The payment means for one document. ``mapped_codes`` are the codes the payment-method map yields for the
    document's candidate method/journal (more than one distinct value ⇒ ambiguous)."""
    if exempt_from_payment_means(type_code, transaction_type_code):
        return Resolution(None, True, [])
    allowed = codelists.codes('payment_means', profile)
    found = []
    distinct_mapped = sorted({code for code in mapped_codes if code})
    if document_override:
        code = document_override
    elif len(distinct_mapped) > 1:
        return Resolution(
            None,
            False,
            [
                _issues.make(
                    'NCOLLECTION:payment-means-ambiguous',
                    'payment means',
                    "The payment methods of this document map to several different payment means (%(codes)s). Choose "
                    "one on the "
                    "e-invoice.",
                    {'codes': ', '.join(distinct_mapped)},
                )
            ],
        )
    elif distinct_mapped:
        code = distinct_mapped[0]
    elif company_default:
        code = company_default
    else:
        return Resolution(
            None,
            False,
            [
                _issues.make(
                    'OFFICIAL:ibr-191-ae',
                    'payment means',
                    "A payment means is required and none is configured. Map the payment method or journal, or set "
                    "the company "
                    "default, to an official payment means code.",
                )
            ],
        )
    if code not in allowed:
        found.append(
            _issues.make(
                'NCOLLECTION:payment-means-code-not-in-official-list',
                'payment means',
                "Payment means code %(code)s is not in the official list.",
                {'code': code},
            )
        )
        return Resolution(None, False, found)
    if code == CREDIT_TRANSFER and not has_payee_account:
        found.append(
            _issues.make(
                'OFFICIAL:ibr-192-ae',
                'payment means',
                "A credit transfer needs the payee account identifier (the company bank account).",
            )
        )
    return Resolution(code, False, found)
