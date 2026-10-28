# -*- coding: utf-8 -*-
"""Discrepancies found IN the official PINT-AE 1.0.4 artifacts, recorded rather than silently "fixed".

The official files are the compliance source of truth and are never edited. When one of them is inconsistent, or lacks
a rule the business needs, the fact is registered here with its evidence and with how the engine behaves meanwhile.
Every entry has a ``still_present`` check against the pinned files: when a future PINT-AE release fixes one, that check
turns false and the test suite forces a human to re-read the entry and the engine's handling before the new profile is
adopted. Where the engine adds a rule of its own to cover a gap, it is labelled ``NCOLLECTION:`` everywhere it is
reported, never "official".
"""

import os
import re
from collections import namedtuple

from . import codelists
from .spec import get_profile

UpstreamArtifact = namedtuple('UpstreamArtifact', 'id summary evidence handling still_present')


def _read(profile, *parts):
    with open(os.path.join(profile.resource_dir, *parts), encoding='utf-8') as handle:
        return handle.read()


_LT = chr(60)  # a literal angle bracket: no string in this file reads as markup to the translation guard


def _asserts(profile):
    """``{assert id: test expression}`` over both shipped Schematron files."""
    result = {}
    for name in ('PINT-jurisdiction-aligned-rules.sch', 'PINT-UBL-validation-preprocessed.sch'):
        text = _read(profile, 'schematron', name)
        for match in re.finditer(r'[<]assert id="([^"]+)"[^>]*? test="([^"]*)"', text):
            result[match.group(1)] = match.group(2)
    return result


def _emirate_rule_shadowed(profile):
    text = _read(profile, 'schematron', 'PINT-jurisdiction-aligned-rules.sch')
    for pattern in re.findall(r'[<]pattern id="[^"]+">(.*?)[<]/pattern>', text, re.S):
        buyer = pattern.find(_LT + 'rule context="cac:AccountingCustomerParty/cac:Party/cac:PostalAddress">')
        seller = pattern.find(_LT + 'rule context="cac:AccountingSupplierParty/cac:Party/cac:PostalAddress">')
        generic = pattern.find(_LT + 'rule context="cac:PostalAddress">')
        rule_at = pattern.find('id="ibr-128-ae"')
        if 0 <= buyer < generic and 0 <= seller < generic < rule_at:
            return True
    return False


def _greek_nu_margin_code(profile):
    ids = codelists.codes('tax_category', profile)
    rules = _read(profile, 'schematron', 'PINT-jurisdiction-aligned-rules.sch')
    sample = _read(profile, 'examples', 'invoice', 'Margin scheme.xml')
    return (
        'Ν' in ids
        and 'N' not in ids
        and 'normalize-space(cbc:ID) = &#34;N&#34;' in rules
        and _LT + 'cbc:ID>N' + _LT + '/cbc:ID>' in sample
    )


def _hs_sac_min_digits_placeholder(profile):
    return _asserts(profile).get('ibr-187-ae') == 'true()'


def _self_billing_type_codes_missing(profile):
    return (
        '389' not in codelists.codes('invoice_type', profile)
        and '261' not in codelists.codes('credit_type', profile)
        and '261' in _asserts(profile).get('ibr-191-ae', '')
    )


def _no_endpoint_format_rule(profile):
    for test in _asserts(profile).values():
        if 'EndpointID' in test and (re.search(r'\{10\}', test) or 'string-length' in test):
            return False
    return True


def _payment_means_list_has_nine_codes(profile):
    return codelists.codes('payment_means', profile) == frozenset(
        {'1', '10', '20', '21', '30', '49', '54', '55', '68'}
    )


ARTIFACTS = (
    UpstreamArtifact(
        'greek-nu-margin-code',
        "The shipped tax-category code list gives the margin-scheme category as GREEK capital Nu (U+039D); the "
        "Schematron "
        "and the official margin-scheme sample use LATIN 'N' (U+004E).",
        'codelist/Aligned-TaxCategoryCodes.gc vs schematron/PINT-jurisdiction-aligned-rules.sch (ibr-102-ae) and '
        'examples/invoice/Margin scheme.xml',
        "The engine emits Latin 'N' (what the Schematron and the sample accept); the code-list file is read as "
        "published "
        "and never edited; spec.SpecProfile.tax_categories is the explicit, tested set.",
        _greek_nu_margin_code,
    ),
    UpstreamArtifact(
        'hs-sac-min-digits-placeholder',
        "Rule ibr-187-ae (minimum digits of the HS item classification and the SAC code) is a placeholder: its "
        "message "
        "says 'X' digits and its test is true(), so the official validator enforces no length.",
        'schematron/PINT-jurisdiction-aligned-rules.sch, assert ibr-187-ae',
        "NCOLLECTION readiness checks digits-only on HS/SAC and enforces no length; the open question stays open "
        "(OCQ-5).",
        _hs_sac_min_digits_placeholder,
    ),
    UpstreamArtifact(
        'self-billing-type-codes-missing',
        "The shipped invoice/credit-note type-code lists contain only 380/480 and 381/81; the self-billing codes "
        "389/261 "
        "named on the PINT-AE website are absent (261 is referenced by ibr-191-ae, 389 appears nowhere in the "
        "Schematron).",
        'codelist/UNCL1001-inv.gc, codelist/UNCL1001-cn.gc, schematron ibr-191-ae',
        "Self-billing is out of scope for this phase; no self-billing type code is ever emitted (OCQ-11).",
        _self_billing_type_codes_missing,
    ),
    UpstreamArtifact(
        'no-endpoint-format-rule',
        "No official Schematron rule checks the FORMAT of a party endpoint id (a 9-digit seller TIN endpoint "
        "validates "
        "clean); ibr-148-ae checks the seller TAX REGISTRATION id, ibr-149/150-ae only react to the endpoint's scheme "
        "and "
        "first digit.",
        'schematron/*.sch (no assert over EndpointID tests length or digit count)',
        "NCOLLECTION:tin-must-be-10-digits is our own readiness rule and is reported as such, never as an official "
        "rule.",
        _no_endpoint_format_rule,
    ),
    UpstreamArtifact(
        'emirate-rule-shadowed',
        "Rule ibr-128-ae (country subdivision must be one of AUH/DXB/SHJ/UAQ/FUJ/AJM/RAK) lives in the generic "
        "postal-address rule (context cac:PostalAddress), which comes AFTER the party-specific rules for the "
        "seller's and "
        "the buyer's "
        "PostalAddress in the same Schematron pattern. A pattern fires only the FIRST matching rule per node, so "
        "ibr-128-ae never runs on a seller or buyer address: an invalid emirate such as 'DU' validates clean (shown "
        "by the "
        "end-to-end check).",
        'schematron/PINT-jurisdiction-aligned-rules.sch (pattern UBL-modelaligned: party PostalAddress rules precede '
        'the '
        'generic one that holds ibr-128-ae)',
        "The engine's own explicit emirate map (lib/emirates.py) is the ONLY enforcement; an unmapped emirate is the "
        "NCOLLECTION readiness error 'subdivision-unmapped', never an official one.",
        lambda profile: _emirate_rule_shadowed(profile),
    ),
    UpstreamArtifact(
        'exemption-code-outside-list',
        "An official sample (Doc-level-allowance-E-category.xml) uses the exemption reason code VATEX-AE-EDU, which "
        "is not "
        "in the shipped exemption list (DL8.46.1-4), and validates clean: the official validator only requires "
        "presence.",
        'examples/invoice/Doc-level-allowance-E-category.xml vs codelist/Aligned-TaxExemptionCodes.gc',
        "A missing reason is an official error (ibr-167-ae); a code outside the shipped list is an NCOLLECTION "
        "warning only.",
        lambda profile: (
            'VATEX-AE-EDU' in _read(profile, 'examples', 'invoice', 'Doc-level-allowance-E-category.xml')
            and 'VATEX-AE-EDU' not in codelists.codes('tax_exemption', profile)
        ),
    ),
    UpstreamArtifact(
        'payment-means-list-differs-from-website',
        "The shipped payment-means code list has nine codes (1, 10, 20, 21, 30, 49, 54, 55, 68); the website text "
        "lists "
        "30, 48, 54, 55.",
        'codelist/UNCL4461.gc',
        "The pinned artifact is authoritative for the pinned version; code 48 is never emitted (OCQ-17).",
        _payment_means_list_has_nine_codes,
    ),
)


def still_present(artifact_id, profile=None):
    profile = profile or get_profile()
    for artifact in ARTIFACTS:
        if artifact.id == artifact_id:
            return artifact.still_present(profile)
    raise KeyError(artifact_id)
