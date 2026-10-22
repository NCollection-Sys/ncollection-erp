# -*- coding: utf-8 -*-
"""UAE identifier formats and readiness states (pure functions, no ORM).

TRN and TIN are TWO identifiers (Mandatory Fields V1.0 §3; Guidelines V1.1 §2), held in two separate fields and never
derived from each other or from ``vat``: the official documents disagree on how the TIN relates to the TRN (Corporate
Tax TRN vs the person's own TRN, OCQ-1), so nothing here, or anywhere, computes one from the other.

A value has exactly one of three READINESS STATES here: ``missing``, ``invalid`` or ``valid``. "Not applicable" is a
fourth state the E-INVOICING ENGINE assigns from the document (e.g. an export buyer who has no TIN by rule); master
data cannot know that, so it is never returned from this module.

Notes carry their authority in their prefix so nobody reads an NCollection rule as an official one:
``OFFICIAL:<rule id>`` is a rule of the pinned PINT-AE Schematron; ``NCOLLECTION:<name>`` is ours.
"""

import re
from collections import namedtuple

# Hard formats (the only thing master data ever refuses). fullmatch, never match()+'$': '$' also matches before a
# trailing newline, which would let a pasted/CSV value with a stray '\n' through.
TRN_RE = re.compile(r'[0-9]{15}')
TIN_RE = re.compile(r'[0-9]{10}')

# Soft (advisory) patterns taken from the official Schematron; they never block a save.
_TRN_IBR132_RE = re.compile(r'1[0-9]{12}03')  # ibr-132-ae: VAT id is 15 digits, starts with 1, ends with 03
_TIN_IBR148_RE = re.compile(r'1[0-9]{9}')  # ibr-148-ae: seller tax REGISTRATION id (IBT-032, a TIN) is 1XXXXXXXXX

MISSING = 'missing'
INVALID = 'invalid'
VALID = 'valid'

IdentifierState = namedtuple('IdentifierState', 'state notes')


def _blank(value):
    return value is None or value is False or value == ''


def trn_state(value):
    """Readiness of a UAE TRN: ``(state, notes)``. The format is the same 15-digit rule ``check_vat_ae`` enforces on
    ``vat``; the "starts with 1, ends with 03" shape is official rule IBR-132-AE and is only an advisory note here."""
    if _blank(value):
        return IdentifierState(MISSING, ())
    if not TRN_RE.fullmatch(value):
        return IdentifierState(INVALID, ('NCOLLECTION:trn-must-be-15-digits',))
    notes = () if _TRN_IBR132_RE.fullmatch(value) else ('OFFICIAL:ibr-132-ae',)
    return IdentifierState(VALID, notes)


def tin_state(value):
    """Readiness of a UAE TIN: ``(state, notes)``. The official Schematron has NO format rule for the endpoint, so the
    10-digit requirement is an NCollection rule. The leading ``1`` is official rule IBR-148-AE, which applies when a
    TIN is used as a seller's TAX REGISTRATION id (IBT-032, a seller without a TRN); as an endpoint only IBR-149-AE
    looks at
    the first digit. Either way it is an advisory note here, never a refusal."""
    if _blank(value):
        return IdentifierState(MISSING, ())
    if not TIN_RE.fullmatch(value):
        return IdentifierState(INVALID, ('NCOLLECTION:tin-must-be-10-digits',))
    notes = () if _TIN_IBR148_RE.fullmatch(value) else ('OFFICIAL:ibr-148-ae',)
    return IdentifierState(VALID, notes)
