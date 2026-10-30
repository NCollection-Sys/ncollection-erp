# -*- coding: utf-8 -*-
"""Seller / buyer endpoint resolution and the 8-flag transaction type, with the sources kept apart.

TRN, TIN, endpoint id, scheme id and participant id are five different things (see the Phase 1A specification §6). This
module only decides the ENDPOINT (BT-034 / BT-049: a value plus its scheme) and records WHICH SOURCE produced it; it
never derives a TIN from a TRN or the other way round, never reads Odoo's ``vat``, and never touches a participant id
(that is what the ASP registers, and is stored separately as a network field).

Buyer endpoint, in this order (each branch is traced in ``source``):

1. deemed supply  → the predefined deemed-supply endpoint (Guidelines §10.4 #2); report-only;
2. export and the buyer has no registered identity → the predefined export endpoint (Guidelines §10.4 #8); report-only;
3. the buyer has an explicit Peppol endpoint recorded (Odoo core ``peppol_eas`` / ``peppol_endpoint``) → that one;
4. the buyer has a valid ``nc_tin`` → scheme 0235 + that TIN;
5. the buyer is EXPLICITLY marked "not on UAE e-invoicing" → the predefined not-onboarded endpoint (report-only);
   never assumed from a missing TIN;
6. the buyer is located OUTSIDE the UAE (its country is recorded and is not AE) and has a recorded FOREIGN tax
   identifier → the predefined not-onboarded endpoint as well (Guidelines V2.0 §18.2.3: "IBT-049 - Buyer Electronic
   Address: Populate with 9900000098"); its identifier then goes to IBT-046 with its ICD scheme;
7. otherwise → an error: the buyer identity is missing.
"""

from collections import namedtuple

from . import issues as _issues
from .spec import get_profile

Endpoint = namedtuple('Endpoint', 'scheme id source report_only')

# order is the official one (codelist/transactiontype.gc): FTZ, deemed, margin, summary, continuous, agent, e-commerce,
# export
FLAG_ORDER = (
    'free_zone',
    'deemed_supply',
    'margin_scheme',
    'summary',
    'continuous_supply',
    'agent_billing',
    'ecommerce',
    'export',
)


def transaction_type_code(flags):
    """The 8-character ``ProfileExecutionID`` for ``flags`` ({name: bool}); unknown flag names are refused."""
    unknown = set(flags) - set(FLAG_ORDER)
    if unknown:
        raise ValueError('unknown transaction flag(s): %s' % ', '.join(sorted(unknown)))
    return ''.join('1' if flags.get(name) else '0' for name in FLAG_ORDER)


def valid_gtin(value):
    """True for a GTIN-8/12/13/14 with a correct GS1 check digit. Any other barcode is NOT sent as a GTIN."""
    digits = str(value or '')
    if not digits.isascii() or not digits.isdigit() or len(digits) not in (8, 12, 13, 14):
        return False
    body, check = digits[:-1], int(digits[-1])
    total = sum(int(d) * (3 if i % 2 == 0 else 1) for i, d in enumerate(reversed(body)))
    return (10 - total % 10) % 10 == check


def parse_transaction_type(code):
    if len(code or '') != 8 or set(code) - {'0', '1'}:
        raise ValueError('a transaction type code is 8 characters of 0/1: %r' % (code,))
    return {name: char == '1' for name, char in zip(FLAG_ORDER, code)}


def seller_endpoint(tin_state, tin, profile=None):
    """``(Endpoint | None, issues)`` for the seller: its TIN with scheme 0235, or an error."""
    profile = profile or get_profile()
    if tin_state == 'valid':
        return Endpoint(profile.participant_scheme, tin, 'seller_tin', False), []
    rule = 'NCOLLECTION:seller-tin-missing' if tin_state == 'missing' else 'NCOLLECTION:tin-must-be-10-digits'
    return None, [
        _issues.make(
            rule,
            'seller',
            "The seller's TIN is %(state)s. The e-invoice endpoint is the seller's 10-digit TIN, maintained on the "
            "company.",
            {'state': tin_state},
        )
    ]


def buyer_endpoint(
    *, flags, peppol_eas=None, peppol_endpoint=None, tin_state='missing', tin=None, not_onboarded=False, profile=None,
    foreign_identified=False,
):
    """``(Endpoint | None, issues)`` for the buyer, following the order in the module docstring."""
    profile = profile or get_profile()
    scheme = profile.participant_scheme
    has_peppol = bool(peppol_eas and peppol_endpoint)
    has_identity = has_peppol or tin_state == 'valid'
    if flags.get('deemed_supply'):
        return Endpoint(scheme, profile.predefined_endpoint('deemed_supply'), 'predefined_deemed', True), []
    if flags.get('export') and not has_identity:
        return Endpoint(scheme, profile.predefined_endpoint('export'), 'predefined_export', True), []
    if has_peppol:
        return Endpoint(peppol_eas, peppol_endpoint, 'partner_peppol', False), []
    if tin_state == 'valid':
        return Endpoint(scheme, tin, 'buyer_tin', False), []
    if tin_state == 'invalid':
        return None, [
            _issues.make(
                'NCOLLECTION:tin-must-be-10-digits',
                'buyer',
                "The buyer's TIN is not 10 digits. Correct it, or leave it empty if the buyer has none.",
            )
        ]
    if not_onboarded:
        return Endpoint(scheme, profile.predefined_endpoint('not_onboarded'), 'predefined_not_onboarded', True), []
    if foreign_identified:
        return Endpoint(scheme, profile.predefined_endpoint('not_onboarded'), 'predefined_foreign_buyer', True), []
    return None, [
        _issues.make(
            'NCOLLECTION:buyer-identity-missing',
            'buyer',
            "The buyer has no TIN and no Peppol endpoint. Enter the buyer's TIN, or mark the buyer as not on UAE "
            "e-invoicing (they will then be reported to the tax authority only).",
        )
    ]
