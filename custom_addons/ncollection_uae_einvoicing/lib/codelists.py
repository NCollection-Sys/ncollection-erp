# -*- coding: utf-8 -*-
"""Loader for the OFFICIAL genericode code lists shipped with the pinned PINT-AE resources.

The lists are reference data: read as published, never edited. The loader exposes them exactly as published (including
any upstream defect, see lib/upstream.py); where the engine needs a different reading it says so explicitly at the call
site instead of changing the data here."""

import os
from collections import namedtuple
from functools import lru_cache

from lxml import etree

from .spec import get_profile

_GC = '{http://docs.oasis-open.org/codelist/ns/genericode/1.0/}'

CodeList = namedtuple('CodeList', 'name version rows')

# Guidelines V2.0 §10.5.1 lists metal scrap trading (Cabinet Decision No. 153 of 2025) among the domestic
# reverse-charge goods, but the pinned GoodsType list (and fatal rule ibr-006-ae) has no code for it (upstream
# finding 'metal-scrap-goods-type-missing', OCQ-6). It is a recognised choice in the tax map that readiness
# BLOCKS: it is NOT an official code, is never invented as one and never reaches the XML.
METAL_SCRAP = 'NC-METAL-SCRAP'

# logical name -> shipped file
FILES = {
    'tax_category': 'Aligned-TaxCategoryCodes.gc',
    'tax_exemption': 'Aligned-TaxExemptionCodes.gc',
    'credit_reason': 'CreditReason.gc',
    'billing_frequency': 'FreqBilling.gc',
    'rcm_goods_type': 'GoodsType.gc',
    'item_type': 'ItemType.gc',
    'payment_means': 'UNCL4461.gc',
    'transaction_type': 'transactiontype.gc',
    'invoice_type': 'UNCL1001-inv.gc',
    'credit_type': 'UNCL1001-cn.gc',
    'unit_code': 'UNECERec20.gc',
    'currency': 'ISO4217.gc',
    'country': 'ISO3166.gc',
    'endpoint_scheme': 'eas.gc',
    'allowance_reason': 'UNCL5189.gc',
    'charge_reason': 'UNCL7161.gc',
}


def _parser():
    # reference data from the pinned, checksum-verified folder, but parsed defensively all the same
    return etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False, huge_tree=False)


@lru_cache(maxsize=None)
def _load_file(path):
    root = etree.parse(path, _parser()).getroot()
    name = root.findtext('%sIdentification/%sShortName' % (_GC, _GC)) or ''
    version = root.findtext('%sIdentification/%sVersion' % (_GC, _GC)) or ''
    rows = {}
    for row in root.iter('%sRow' % _GC):
        values = {}
        for value in row.findall('%sValue' % _GC):
            simple = value.find('%sSimpleValue' % _GC)
            values[value.get('ColumnRef')] = (simple.text or '').strip() if simple is not None else ''
        if 'id' in values:
            rows[values['id']] = values
    return CodeList(name, version, rows)


def load(logical_name, profile=None):
    """The code list ``logical_name`` (a key of :data:`FILES`) of the given (default: active) profile."""
    profile = profile or get_profile()
    return _load_file(os.path.join(profile.codelist_dir, FILES[logical_name]))


def codes(logical_name, profile=None):
    """The ids exactly as published."""
    return frozenset(load(logical_name, profile).rows)


@lru_cache(maxsize=8)
def _icd_codes(schematron_dir):
    import re

    with open(os.path.join(schematron_dir, 'PINT-UBL-validation-preprocessed.sch'), encoding='utf-8') as handle:
        text = handle.read()
    match = re.search(r'id="ibr-cl-10"[^>]*?test="[^"]*?contains\(\' ([0-9 ]+) \'', text)
    if not match:
        raise ValueError('the ICD list of ibr-cl-10 was not found in the pinned rules')
    return frozenset(match.group(1).split())


def icd_codes(profile=None):
    """The ISO/IEC 6523 ICD scheme codes the pinned official rule ``ibr-cl-10`` accepts for a party identifier
    (IBT-046-1). The ICD code-list FILE is not vendored; the list the validator actually enforces is read from the
    unmodified rule itself, so the two can never disagree."""
    profile = profile or get_profile()
    return _icd_codes(profile.schematron_dir)
