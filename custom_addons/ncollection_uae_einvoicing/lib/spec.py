# -*- coding: utf-8 -*-
"""The PINT-AE specification profile: the ONE place the specification version, identifiers and constants live.

Why a profile and not constants scattered through the code: the official Schematron was revised six times in the first
twelve months (1.0.0 → 1.0.4), so a future 1.0.5 must be "add a profile and a resource folder", not a code search. A
guard test (tests/test_resources.py) fails if the version or the specification identifier appears anywhere in ``lib/``
outside this file. The version actually used is stored on every e-invoice (``spec_version``) so a document can always
be traced to the exact rules it was built and validated against.

The resources under ``resources/pint_ae/<version>/`` are UNMODIFIED copies of the official files; ``MANIFEST.txt``
records the sha256 of every one and the source archive's sha256, and :func:`verify_resources` proves they still match.
They are the compliance source of truth: where this engine needs a rule the official files do not contain, the rule is
labelled ``NCOLLECTION:`` and never presented as official (see lib/upstream.py for the discrepancies found in the
official files).
"""

import hashlib
import os
from dataclasses import dataclass
from decimal import Decimal

RESOURCE_ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), '..', 'resources', 'pint_ae'))

ACTIVE_PROFILE_KEY = 'pint-ae-1.0.4'


@dataclass(frozen=True)
class SpecProfile:
    key: str
    pint_version: str
    release_date: str
    status: str
    source_url: str
    source_zip_sha256: str
    customization_id: str
    profile_id: str
    participant_scheme: str
    # document type codes (UNCL1001 subsets, shipped code lists)
    invoice_type_tax: str
    invoice_type_out_of_scope: str
    credit_type_tax: str
    credit_type_out_of_scope: str
    # official tax categories (Latin letters; see lib/upstream.py for the Greek-Nu defect in the shipped list file)
    tax_categories: tuple
    standard_rate: Decimal  # ibr-190-ae: a Standard-rated 'S' rate MUST be exactly this
    tax_slack: Decimal  # u:slack(...) in aligned-ibrp-s-08 / s-09 / ibr-102-ae
    max_exchange_rate_decimals: int  # ibr-002-ae
    amount_decimals: int
    emirate_codes: tuple  # ibr-128-ae
    predefined_endpoints: tuple  # (reason, endpoint id) - report-only, never exchanged via C3

    @property
    def resource_dir(self):
        return os.path.join(RESOURCE_ROOT, self.pint_version)

    @property
    def schematron_dir(self):
        return os.path.join(self.resource_dir, 'schematron')

    @property
    def codelist_dir(self):
        return os.path.join(self.resource_dir, 'codelist')

    @property
    def examples_dir(self):
        return os.path.join(self.resource_dir, 'examples')

    def predefined_endpoint(self, reason):
        return dict(self.predefined_endpoints)[reason]


PROFILES = {
    'pint-ae-1.0.4': SpecProfile(
        key='pint-ae-1.0.4',
        pint_version='1.0.4',
        release_date='2026-06-02',
        status='Final',
        source_url='https://docs.peppol.eu/poac/ae/upcoming/pint-ae/resources.zip',
        source_zip_sha256='1e8b0bd595c672ac9d6fc886ddd0eb8027cb39a1d26bebdcb1ada37b7eee491f',
        customization_id='urn:peppol:pint:billing-1@ae-1',
        profile_id='urn:peppol:bis:billing',
        participant_scheme='0235',
        invoice_type_tax='380',
        invoice_type_out_of_scope='480',
        credit_type_tax='381',
        credit_type_out_of_scope='81',
        tax_categories=('S', 'E', 'O', 'AE', 'Z', 'N'),
        standard_rate=Decimal('5.00'),
        tax_slack=Decimal('0.02'),
        max_exchange_rate_decimals=6,
        amount_decimals=2,
        emirate_codes=('AUH', 'DXB', 'SHJ', 'UAQ', 'FUJ', 'AJM', 'RAK'),
        predefined_endpoints=(
            ('not_onboarded', '9900000098'),  # buyer not subject to / not onboarded on UAE e-invoicing
            ('deemed_supply', '9900000097'),  # deemed supply (transaction type X1XXXXXX)
            ('export', '9900000099'),  # export, receiver not registered in Peppol (XXXXXXX1)
        ),
    ),
}


def get_profile(key=None):
    """The profile for ``key`` (default: the active one). Unknown key ⇒ KeyError, never a silent fallback."""
    return PROFILES[key or ACTIVE_PROFILE_KEY]


def _parse_manifest(manifest_path):
    entries = {}
    with open(manifest_path, encoding='utf-8') as handle:
        for line in handle:
            line = line.rstrip('\n')
            if not line or line.startswith('#'):
                continue
            digest, _sep, relpath = line.partition('  ')
            entries[relpath] = digest
    return entries


def _sha256(path):
    with open(path, 'rb') as handle:
        return hashlib.sha256(handle.read()).hexdigest()


def verify_resources(profile=None, root=None):
    """Problems found between the vendored official files and their manifest, as a list of strings (empty = intact).

    Detects a changed file, a missing file and an UNLISTED extra file, so neither an edit nor an addition to the
    official resources can go unnoticed. ``root`` lets a test point at a tampered copy."""
    profile = profile or get_profile()
    root = root or profile.resource_dir
    manifest = os.path.join(root, 'MANIFEST.txt')
    if not os.path.isfile(manifest):
        return ['MANIFEST.txt is missing']
    problems = []
    entries = _parse_manifest(manifest)
    for relpath, digest in sorted(entries.items()):
        path = os.path.join(root, *relpath.split('/'))
        if not os.path.isfile(path):
            problems.append('missing: %s' % relpath)
        elif _sha256(path) != digest:
            problems.append('changed: %s' % relpath)
    on_disk = set()
    for folder, _dirs, files in os.walk(root):
        for name in files:
            rel = os.path.relpath(os.path.join(folder, name), root).replace(os.sep, '/')
            if rel != 'MANIFEST.txt':
                on_disk.add(rel)
    for rel in sorted(on_disk - set(entries)):
        problems.append('unlisted: %s' % rel)
    return problems


def manifest_sha256(profile=None):
    """sha256 of the vendored MANIFEST.txt: the fingerprint of the exact official files this engine is pinned to.

    The validator worker must report the same value for the rules it ran, so a worker holding a different release (or
    an
    edited rule) can never be taken for the pinned one."""
    profile = profile or get_profile()
    return _sha256(os.path.join(profile.resource_dir, 'MANIFEST.txt'))
