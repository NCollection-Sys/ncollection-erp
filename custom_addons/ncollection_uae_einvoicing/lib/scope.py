# -*- coding: utf-8 -*-
"""Is an e-invoice REQUIRED for this transaction? Decided before any payload is built (pure: no database).

Every outcome names its official source (``lib/sources.py``). The rules, in the order they are applied:

1. **B2C** (Guidelines V2.0 §6.2): supplies to natural persons who are not in Business are outside the scope. The buyer's
   party type is master data the accountant declares (``res.partner.nc_einvoice_party_type``); a person who is not a
   company is NEVER assumed to be a consumer (a sole trader is in Business).
2. **Tax Group** (MD 243 Art 4(1)(f) as inserted by MD 197 Art 2): transactions between members of the same Tax Group are
   Excluded Transactions for 24 months starting 1 January 2027, i.e. a transaction dated 2027-01-01 through 2028-12-31.
   Both parties must carry the SAME Tax Group TRN. Guidelines V2.0 §6.3.2.1 calls the same window a grace period that
   changes timing only (OCQ-22): either way no e-invoice is required inside it, and one is required on either side of
   it.
3. **A declared exclusion** (MD 243 Art 4(1)(a)–(e)), chosen per document by an e-invoicing manager and verified here as
   far as data allows:

   * ``sovereign`` (a): the SELLER must be a Government Entity (its partner's party type is ``government``).
   * ``airline_passenger`` (b) / ``airline_ancillary`` (c): accepted as declared (the electronic ticket / EMD is the
     airline's own record, not data of this document).
   * ``airline_cargo`` (d): only for 24 months from the date the e-invoicing system becomes effective for the Person;
     that date is configuration (``effective_date``), never assumed (OCQ-21). Without it the declaration is refused.
   * ``financial_services`` (e): exempt or zero-rated financial services (Art 42 of the VAT Executive Regulation), so
     every line must be Exempt or Zero rated.

A refused declaration is an ERROR finding: the document stays in scope and is never silently excluded.
"""

import calendar
from collections import namedtuple
from datetime import date

from . import issues as _issues
from .sources import cite

IN_SCOPE = 'in_scope'
NOT_REQUIRED = 'not_required'

PARTY_TYPES = ('business', 'government', 'consumer')

TAX_GROUP_START = date(2027, 1, 1)
TAX_GROUP_END = date(2028, 12, 31)  # 24 months starting 2027-01-01

# declared exclusion -> (reason code, MD 243 article)
DECLARED = {
    'sovereign': ('sovereign-government-activity', 'Art 4(1)(a)'),
    'airline_passenger': ('airline-passenger-e-ticket', 'Art 4(1)(b)'),
    'airline_ancillary': ('airline-ancillary-emd', 'Art 4(1)(c)'),
    'airline_cargo': ('airline-cargo-air-waybill', 'Art 4(1)(d)'),
    'financial_services': ('exempt-or-zero-rated-financial-services', 'Art 4(1)(e)'),
}

Scope = namedtuple('Scope', 'state reason_code reason source window')


def _add_months(day, months):
    """The same day-of-month ``months`` later (clamped to the month's last day): the EXCLUSIVE end of a window that
    starts on ``day`` and lasts ``months`` months."""
    index = day.month - 1 + months
    year, month = day.year + index // 12, index % 12 + 1
    return date(year, month, min(day.day, calendar.monthrange(year, month)[1]))


def _in_scope():
    return Scope(IN_SCOPE, None, None, None, None)


def classify(
    transaction_date,
    buyer_party_type='business',
    seller_party_type='business',
    seller_vat_group=None,
    buyer_vat_group=None,
    declared_exclusion=None,
    categories=(),
    effective_date=None,
):
    """``(Scope, issues)``. ``transaction_date`` is the Date of Business Transaction (a ``date``)."""
    found = []
    if not isinstance(transaction_date, date):
        found.append(
            _issues.make(
                'NCOLLECTION:scope-transaction-date-missing',
                'document',
                "The date of the business transaction is needed to decide whether an e-invoice is required.",
            )
        )
        return _in_scope(), found
    for role, value in (('buyer', buyer_party_type), ('seller', seller_party_type)):
        if value not in PARTY_TYPES:
            found.append(
                _issues.make(
                    'NCOLLECTION:scope-party-type-invalid',
                    role,
                    "The %(role)s's e-invoicing party type is not set to business, government or consumer.",
                    {'role': role},
                )
            )
            return _in_scope(), found

    if buyer_party_type == 'consumer':
        return (
            Scope(
                NOT_REQUIRED,
                'b2c-natural-person',
                "The buyer is a natural person who is not in Business: outside the scope of e-invoicing.",
                cite('G2.0', '§6.2'),
                None,
            ),
            found,
        )

    if seller_vat_group and buyer_vat_group and seller_vat_group == buyer_vat_group:
        if TAX_GROUP_START <= transaction_date <= TAX_GROUP_END:
            return (
                Scope(
                    NOT_REQUIRED,
                    'tax-group-member',
                    "A transaction between members of the same Tax Group inside the temporary exclusion window.",
                    '%s; %s; %s'
                    % (cite('MD243', 'Art 4(1)(f)'), cite('MD197', 'Art 2'), cite('G2.0', '§6.3.2.1')),
                    '%s..%s' % (TAX_GROUP_START.isoformat(), TAX_GROUP_END.isoformat()),
                ),
                found,
            )

    if not declared_exclusion:
        return _in_scope(), found
    if declared_exclusion not in DECLARED:
        found.append(
            _issues.make(
                'NCOLLECTION:scope-exclusion-unknown',
                'document',
                "The declared exclusion %(value)s is not one of the exclusions of MD 243 Art 4.",
                {'value': declared_exclusion},
            )
        )
        return _in_scope(), found
    code, article = DECLARED[declared_exclusion]
    source = cite('MD243', article)
    window = None
    if declared_exclusion == 'sovereign' and seller_party_type != 'government':
        found.append(
            _issues.make(
                'NCOLLECTION:scope-sovereign-needs-government-seller',
                'seller',
                "The sovereign-activity exclusion (MD 243 Art 4(1)(a)) applies only to a Government Entity seller.",
            )
        )
        return _in_scope(), found
    if declared_exclusion == 'airline_cargo':
        if not isinstance(effective_date, date):
            found.append(
                _issues.make(
                    'NCOLLECTION:scope-airline-cargo-needs-effective-date',
                    'document',
                    "The air-waybill exclusion (MD 243 Art 4(1)(d)) runs for 24 months from the date e-invoicing "
                    "became effective; set that date in the e-invoicing configuration.",
                )
            )
            return _in_scope(), found
        end = _add_months(effective_date, 24)
        if not effective_date <= transaction_date < end:
            found.append(
                _issues.make(
                    'NCOLLECTION:scope-airline-cargo-outside-window',
                    'document',
                    "The air-waybill exclusion (MD 243 Art 4(1)(d)) does not cover this date (window %(start)s "
                    "to %(end)s, end excluded).",
                    {'start': effective_date.isoformat(), 'end': end.isoformat()},
                )
            )
            return _in_scope(), found
        window = '%s..%s' % (effective_date.isoformat(), end.isoformat())
    if declared_exclusion == 'financial_services':
        lines = set(categories) - {None}
        if not lines or not lines <= {'E', 'Z'}:
            found.append(
                _issues.make(
                    'NCOLLECTION:scope-financial-services-must-be-exempt-or-zero',
                    'document',
                    "The financial-services exclusion (MD 243 Art 4(1)(e)) covers only exempt or zero-rated "
                    "supplies: every line must be Exempt or Zero rated.",
                )
            )
            return _in_scope(), found
    reasons = {
        'sovereign': "A Government Entity's sovereign activity not in competition with the private sector.",
        'airline_passenger': "International passenger transport by an airline with an electronic ticket.",
        'airline_ancillary': "An airline's ancillary passenger service with an Electronic Miscellaneous Document.",
        'airline_cargo': "International goods transport by an airline with an air waybill, inside the 24 months.",
        'financial_services': "Financial services that are exempt or zero-rated under VAT Executive Regulation Art 42.",
    }
    return Scope(NOT_REQUIRED, code, reasons[declared_exclusion], source, window), found
