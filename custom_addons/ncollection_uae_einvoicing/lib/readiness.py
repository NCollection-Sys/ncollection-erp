# -*- coding: utf-8 -*-
"""Readiness of a document snapshot: everything that can be checked from data alone, before any XML is built.

Each finding names whose rule it is: ``OFFICIAL:<assert id>`` for a rule of the pinned Schematron (so an official run
would report the same thing), ``NCOLLECTION:<name>`` for a rule this engine adds (never presented as official).
Findings are ``error`` (the document cannot be sent) or ``warning`` (advisory: the official rule says "should", or the
requirement is an open question).

Master-data states are the three of ``ncollection_account_localization_uae`` (missing / invalid / valid) plus the
engine's own fourth, NOT APPLICABLE, which only this module can decide because it depends on the document (e.g. an
export buyer that has no TIN by rule). Conditional requirements are evaluated HERE, at e-invoice time; they are never
forced onto a partner or product form.
"""

import re

from . import codelists, identity, payment_means, issues as _issues
from .emirates import pint_subdivision
from .money import dec
from .spec import get_profile

NOT_APPLICABLE = 'not_applicable'

_DIGITS = re.compile(r'[0-9]+')


def check(snapshot, profile=None):
    """All findings for ``snapshot`` (see the Phase 1A specification for its shape)."""
    profile = profile or get_profile()
    found = []
    doc = snapshot['doc']
    flags = doc.get('flags', {})
    categories = {line.get('category') for line in snapshot.get('lines', [])} | {
        adj.get('category') for adj in snapshot.get('adjustments', [])
    }
    categories.discard(None)
    _document(doc, flags, categories, profile, found)
    _party('seller', snapshot['seller'], doc, flags, profile, found)
    _party('buyer', snapshot['buyer'], doc, flags, profile, found)
    for line in snapshot.get('lines', []):
        _line(line, snapshot['buyer'], profile, found)
    return found


# ---------------------------------------------------------------------------------------------------------------------
def _document(doc, flags, categories, profile, found):
    kind, type_code = doc['kind'], doc.get('type_code')
    allowed = {
        'invoice': {profile.invoice_type_tax, profile.invoice_type_out_of_scope},
        'credit_note': {profile.credit_type_tax, profile.credit_type_out_of_scope},
    }[kind]
    if type_code not in allowed:
        found.append(
            _issues.make(
                'NCOLLECTION:document-type-code',
                'document',
                "Document type code %(code)s is not valid for this kind of document.",
                {'code': type_code},
            )
        )
    out_of_scope = type_code in (profile.invoice_type_out_of_scope, profile.credit_type_out_of_scope)
    if out_of_scope and not categories <= {'E', 'O'}:
        found.append(
            _issues.make(
                'OFFICIAL:ibr-122-ae',
                'document',
                "An out-of-scope document may only use the Exempt or Not-subject VAT categories.",
            )
        )
    if type_code in (profile.invoice_type_tax, profile.credit_type_tax) and categories and categories <= {'E', 'O'}:
        found.append(
            _issues.make(
                'OFFICIAL:ibr-151-ae',
                'document',
                "A tax invoice / tax credit note cannot use only the Exempt and Not-subject VAT categories: "
                "such a document is an out-of-scope one (types 480 / 81).",
            )
        )
    if out_of_scope and (flags.get('summary') or flags.get('deemed_supply') or flags.get('margin_scheme')):
        found.append(
            _issues.make(
                'OFFICIAL:ibr-157-ae',
                'document',
                "Summary, deemed-supply and margin-scheme flags are not allowed on an out-of-scope " "document.",
            )
        )
    if (doc.get('currency') or 'AED') != 'AED':
        rate = doc.get('exchange_rate')
        if not rate:
            found.append(
                _issues.make(
                    'OFFICIAL:ibr-159-ae',
                    'document',
                    "The exchange rate to AED is required when the document currency is not AED.",
                )
            )
        elif -dec(rate).as_tuple().exponent > profile.max_exchange_rate_decimals:
            found.append(
                _issues.make(
                    'OFFICIAL:ibr-002-ae',
                    'document',
                    "The exchange rate may have at most %(n)s decimals.",
                    {'n': profile.max_exchange_rate_decimals},
                )
            )
    if kind == 'credit_note':
        reason = doc.get('credit_reason')
        if not reason:
            found.append(_issues.make('OFFICIAL:ibr-158-ae', 'document', "A credit note needs a credit-note reason."))
        elif reason not in codelists.codes('credit_reason', profile):
            found.append(
                _issues.make(
                    'OFFICIAL:ibr-001-ae',
                    'document',
                    "Credit-note reason %(code)s is not in the official list.",
                    {'code': reason},
                )
            )
        if reason == 'VD' and doc.get('preceding'):
            found.append(
                _issues.make(
                    'OFFICIAL:ibr-055-ae',
                    'document',
                    "A volume-discount credit note must NOT reference an invoice. A credit note made by reversing an "
                    "invoice always does, so choose another credit-note reason or issue it without the reversal link.",
                )
            )
        if reason != 'VD' and not doc.get('preceding'):
            found.append(
                _issues.make(
                    'OFFICIAL:ibr-055-ae',
                    'document',
                    "A credit note needs a reference to the original invoice (except a volume discount).",
                )
            )
        if doc.get('vat_point_date'):
            found.append(
                _issues.make('OFFICIAL:ibr-124-ae', 'document', "A credit note cannot carry a VAT point date.")
            )
    if doc.get('vat_point_date') and doc.get('issue_date') and not doc['vat_point_date'] < doc['issue_date']:
        found.append(
            _issues.make(
                'OFFICIAL:ibr-141-ae',
                'document',
                "The VAT point date must be before the issue date; omit it for a same-day supply.",
            )
        )
    exempt = payment_means.exempt_from_payment_means(type_code, doc.get('transaction_type_code'))
    if dec(doc.get('payable')) > 0 and not doc.get('due_date') and not exempt:
        found.append(_issues.make('OFFICIAL:ibr-127-ae', 'document', "A payment due date is required."))
    if flags.get('free_zone') and not doc.get('beneficiary_id'):
        found.append(_issues.make('OFFICIAL:ibr-007-ae', 'document', "A free-zone supply needs the beneficiary."))
    if flags.get('agent_billing') and not doc.get('principal_id'):
        found.append(_issues.make('OFFICIAL:ibr-137-ae', 'document', "Disclosed agent billing needs the principal."))
    if flags.get('summary') and not (doc.get('period_start') and doc.get('period_end')):
        found.append(_issues.make('OFFICIAL:ibr-138-ae', 'document', "A summary invoice needs its invoicing period."))
    if doc.get('billing_frequency') and doc['billing_frequency'] not in codelists.codes('billing_frequency', profile):
        found.append(_issues.make('OFFICIAL:ibr-005-ae', 'document', "Billing frequency is not in the official list."))
    if flags.get('export'):
        delivery = doc.get('delivery') or {}
        to_uae = (delivery.get('country') or '').upper() == 'AE'
        complete = delivery.get('street') and delivery.get('city') and delivery.get('subdivision')
        if not delivery or not (to_uae or complete):
            found.append(
                _issues.make(
                    'OFFICIAL:ibr-152-ae',
                    'document',
                    "An export needs the deliver-to address (street, city and region) unless the goods are delivered "
                    "in the UAE: set the document's delivery address.",
                )
            )
    if flags.get('margin_scheme') and categories - {'N'}:
        found.append(
            _issues.make(
                'OFFICIAL:ibr-116-ae',
                'document',
                "A margin-scheme document must use only the 'Standard rate additional VAT' category.",
            )
        )


def _party(role, party, doc, flags, profile, found):
    subject = role
    kind = doc.get('type_code')
    out_of_scope = kind in (profile.invoice_type_out_of_scope, profile.credit_type_out_of_scope)
    if not party.get('name'):
        found.append(
            _issues.make(
                'OFFICIAL:ibr-006' if role == 'seller' else 'OFFICIAL:ibr-007',
                subject,
                "The %(role)s's legal name is required.",
                {'role': role},
            )
        )
    # --- endpoint
    if role == 'seller':
        endpoint, issues_ = identity.seller_endpoint(party.get('tin_state', 'missing'), party.get('tin'), profile)
    else:
        endpoint, issues_ = identity.buyer_endpoint(
            flags=flags,
            peppol_eas=party.get('peppol_eas'),
            peppol_endpoint=party.get('peppol_endpoint'),
            tin_state=party.get('tin_state', 'missing'),
            tin=party.get('tin'),
            not_onboarded=party.get('not_onboarded', False),
            profile=profile,
        )
    found.extend(issues_)
    # --- TRN / VAT identifier
    trn_state = party.get('trn_state', 'missing')
    if trn_state == 'invalid':
        found.append(
            _issues.make(
                'NCOLLECTION:trn-must-be-15-digits', subject, "The %(role)s's TRN is not 15 digits.", {'role': role}
            )
        )
    elif trn_state == 'valid' and party.get('trn_advisory'):
        found.append(
            _issues.make(
                'OFFICIAL:ibr-132-ae',
                subject,
                "The %(role)s's TRN should be 15 digits starting with 1 and ending with 03.",
                {'role': role},
                severity=_issues.WARNING,
            )
        )
    if role == 'seller' and trn_state == 'missing' and not out_of_scope:
        found.append(_issues.make('OFFICIAL:ibr-134-ae', subject, "The seller's TRN is required."))
    if (
        role == 'buyer'
        and endpoint is not None
        and not flags.get('export')
        and endpoint.scheme == profile.participant_scheme
        and not re.fullmatch(r'1[0-9]{9}', endpoint.id)
        and trn_state != 'valid'
    ):
        found.append(_issues.make('OFFICIAL:ibr-135-ae', subject, "The buyer's TRN (or identifier) is required."))
    # --- legal registration (scheme 0235 only, as the official rules)
    if endpoint is not None and endpoint.scheme == profile.participant_scheme:
        needs_legal = role == 'seller' or not re.match(r'[19]', endpoint.id)
        if needs_legal and not party.get('legal_id'):
            found.append(
                _issues.make(
                    'OFFICIAL:ibr-150-ae' if role == 'seller' else 'OFFICIAL:ibr-149-ae',
                    subject,
                    "The %(role)s's legal registration number (Company ID) is required.",
                    {'role': role},
                )
            )
        if party.get('legal_id'):
            legal_type = party.get('legal_type')
            if not legal_type:
                found.append(
                    _issues.make(
                        'OFFICIAL:ibr-181-ae' if role == 'seller' else 'OFFICIAL:ibr-180-ae',
                        subject,
                        "The %(role)s's legal registration type is required.",
                        {'role': role},
                    )
                )
            elif legal_type not in ('TL', 'EID', 'PAS', 'CD'):
                found.append(
                    _issues.make(
                        'OFFICIAL:ibr-173-ae' if role == 'seller' else 'OFFICIAL:ibr-183-ae',
                        subject,
                        "The %(role)s's legal registration type is not valid.",
                        {'role': role},
                    )
                )
            if legal_type == 'TL' and not party.get('legal_authority'):
                found.append(
                    _issues.make(
                        'OFFICIAL:ibr-172-ae' if role == 'seller' else 'OFFICIAL:ibr-101-ae',
                        subject,
                        "A trade licence needs the registration authority.",
                    )
                )
            if legal_type == 'PAS' and not party.get('legal_country'):
                found.append(
                    _issues.make(
                        'OFFICIAL:ibr-012-ae' if role == 'seller' else 'OFFICIAL:ibr-010-ae',
                        subject,
                        "A passport registration needs the issuing country.",
                    )
                )
    # --- address
    street, city = party.get('street'), party.get('city')
    country = (party.get('country') or '').upper()
    rule = 'OFFICIAL:ibr-143-ae' if role == 'seller' else 'OFFICIAL:ibr-144-ae'
    if not (street and city):
        found.append(_issues.make(rule, subject, "The %(role)s's address needs a street and a city.", {'role': role}))
    if not country:
        found.append(
            _issues.make('NCOLLECTION:country-missing', subject, "The %(role)s's country is required.", {'role': role})
        )
    if country == 'AE':
        if not party.get('state_code'):
            found.append(_issues.make(rule, subject, "The %(role)s's address needs the emirate.", {'role': role}))
        elif pint_subdivision(party['state_code'], profile) is None:
            found.append(
                _issues.make(
                    'NCOLLECTION:subdivision-unmapped',
                    subject,
                    "The %(role)s's emirate %(state)s is not one of the seven the e-invoice accepts.",
                    {'role': role, 'state': party['state_code']},
                )
            )
    elif country and not party.get('state_code') and not party.get('subdivision_text'):
        found.append(_issues.make(rule, subject, "The %(role)s's address needs the region/state.", {'role': role}))


def _line(line, buyer, profile, found):
    subject = 'line %s' % line.get('id')
    category, rate = line.get('category'), line.get('rate')
    if category not in profile.tax_categories:
        found.append(
            _issues.make(
                'OFFICIAL:ibr-139-ae',
                subject,
                "Line %(line)s has no valid VAT category (map its tax).",
                {'line': line.get('id')},
            )
        )
    elif category == 'S' and (rate is None or dec(rate) != profile.standard_rate):
        found.append(
            _issues.make(
                'OFFICIAL:ibr-190-ae',
                subject,
                "A Standard-rated line must carry the standard rate (%(rate)s).",
                {'rate': profile.standard_rate},
            )
        )
    elif category == 'Z' and (rate is None or dec(rate) != 0):
        found.append(_issues.make('OFFICIAL:aligned-ibrp-z-05', subject, "A zero-rated line must carry rate 0."))
    elif category in ('E', 'O') and rate not in (None, ''):
        found.append(
            _issues.make(
                'OFFICIAL:aligned-ibrp-e-05' if category == 'E' else 'OFFICIAL:aligned-ibrp-o-05',
                subject,
                "An exempt / not-subject line carries no rate.",
            )
        )
    elif category in ('AE', 'N') and (rate is None or dec(rate) <= 0):
        found.append(
            _issues.make(
                'OFFICIAL:aligned-ibrp-ae-05-ae' if category == 'AE' else 'OFFICIAL:ibr-111-ae',
                subject,
                "This VAT category needs its rate shown on the line.",
            )
        )
    if category == 'E':
        # ibr-167-ae demands the reason's PRESENCE. Membership of the shipped four-code list is NOT enforced by the
        # official validator (an official sample uses VATEX-AE-EDU and validates clean), so a code outside the list is
        # only an NCollection warning (see lib/upstream.py, 'exemption-code-outside-list').
        if not line.get('exemption_code'):
            found.append(_issues.make('OFFICIAL:ibr-167-ae', subject, "An exempt line needs an exemption reason."))
        elif line['exemption_code'] not in codelists.codes('tax_exemption', profile):
            found.append(
                _issues.make(
                    'NCOLLECTION:exemption-code-outside-shipped-list',
                    subject,
                    "The exemption reason %(code)s is not in the shipped list; the official validator "
                    "does not check it.",
                    {'code': line['exemption_code']},
                    severity=_issues.WARNING,
                )
            )
    if not (line.get('description') or line.get('name')):
        found.append(_issues.make('OFFICIAL:ibr-125-ae', subject, "The item needs a description."))
    if line.get('unit_code') not in codelists.codes('unit_code', profile):
        found.append(
            _issues.make(
                'NCOLLECTION:unit-code-unmapped',
                subject,
                "The unit of measure of line %(line)s has no official UN/ECE code.",
                {'line': line.get('id')},
            )
        )
    # --- HS / SAC are CONDITIONAL on the declared item type (ibr-184/185/186-ae); never forced on an undeclared item
    item_type = line.get('item_type')
    if not item_type:
        found.append(
            _issues.make(
                'NCOLLECTION:item-type-not-declared',
                subject,
                "The item has no e-invoice item type; no HS/SAC requirement is applied.",
                severity=_issues.WARNING,
            )
        )
    elif item_type not in codelists.codes('item_type', profile):
        found.append(_issues.make('NCOLLECTION:item-type-invalid', subject, "The item type is not valid."))
    else:
        if item_type in ('G', 'B') and not line.get('hs'):
            found.append(
                _issues.make(
                    'OFFICIAL:ibr-184-ae' if item_type == 'G' else 'OFFICIAL:ibr-186-ae',
                    subject,
                    "Goods need an HS code.",
                )
            )
        if item_type in ('S', 'B') and not line.get('sac'):
            found.append(
                _issues.make(
                    'OFFICIAL:ibr-185-ae' if item_type == 'S' else 'OFFICIAL:ibr-186-ae',
                    subject,
                    "Services need a SAC code.",
                )
            )
        for label, value in (('HS', line.get('hs')), ('SAC', line.get('sac'))):
            if value and not _DIGITS.fullmatch(value):
                found.append(
                    _issues.make(
                        'NCOLLECTION:classification-format',
                        subject,
                        "The %(label)s code should be digits only (the official minimum length is " "undefined).",
                        {'label': label},
                        severity=_issues.WARNING,
                    )
                )
    if category == 'AE':
        # Guidelines V2.0 §10.5.1: domestic reverse charge between two registrants; no VAT, the goods type and a
        # narrative of the reason on the invoice
        if line.get('goods_type') == codelists.METAL_SCRAP:
            found.append(
                _issues.make(
                    'NCOLLECTION:rcm-goods-type-not-in-release',
                    subject,
                    "Metal scrap is a reverse-charge goods type (Guidelines V2.0 §10.5.1) but the pinned PINT-AE "
                    "release has no code for it (fatal rule ibr-006-ae accepts five codes only): this document cannot "
                    "be issued as an e-invoice until a release adds the code.",
                )
            )
        elif line.get('goods_type') not in codelists.codes('rcm_goods_type', profile):
            found.append(_issues.make('OFFICIAL:ibr-166-ae', subject, "A reverse-charge line needs its goods type."))
        if not (line.get('rc_narrative') or '').strip():
            found.append(
                _issues.make(
                    'NCOLLECTION:rcm-narrative-required',
                    subject,
                    "A reverse-charge e-invoice must state why the supply is subject to the reverse charge "
                    "(Guidelines V2.0 §10.5.1): set the narrative on the tax map.",
                )
            )
        if dec(line.get('aed_vat') or 0) != 0:
            found.append(
                _issues.make(
                    'OFFICIAL:ibr-162-ae',
                    subject,
                    "A reverse-charge line carries no VAT: the tax booked on it must be zero.",
                )
            )
        if not line.get('gtin'):
            found.append(
                _issues.make('OFFICIAL:ibr-174-ae', subject, "A reverse-charge line needs the item's GTIN (barcode).")
            )
        if buyer.get('trn_state') != 'valid':
            found.append(
                _issues.make('OFFICIAL:ibr-103-ae', 'buyer', "A reverse-charge supply needs the buyer's TRN.")
            )
