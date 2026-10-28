# -*- coding: utf-8 -*-
"""The official UAE regulatory sources the engine cites: the ONE place their titles, versions and dates live.

Every compliance behaviour that rests on a regulatory text (not on a PINT-AE Schematron rule, whose id is the citation)
names one of these keys plus the article or section it applies, through :func:`cite`. A behaviour whose source is not
in this registry has no business in the engine.

Peppol technical specifications (PINT AE Billing, Self-Billing, the UAE TDD) live in ``lib/spec.py`` with the pinned
release, because their version strings may appear nowhere else in ``lib/`` (tests/test_resources.py).

Verification state (2026-10-28): every entry below was read from the PDF published on the Ministry of Finance
eInvoicing page (https://mof.gov.ae/en/about-us/initiatives/einvoicing/). An instrument that is cited by an official
document but could NOT be located is listed in :data:`UNVERIFIED` and is never cited as a fact.
"""

from collections import namedtuple

Source = namedtuple('Source', 'key title version date url')

_MOF = 'https://mof.gov.ae/wp-content/uploads/'

SOURCES = {
    'G2.0': Source(
        'G2.0',
        'UAE Electronic Invoicing Guidelines',
        'V2.0',
        '2026-10',
        _MOF + '2026/10/UAE-Electronic-Invoicing-Guidelines_En.pdf',
    ),
    'MF1.1': Source(
        'MF1.1',
        'UAE Electronic Invoice Mandatory Fields',
        'V1.1',
        '2026-10-28',
        _MOF + '2026/10/UAE-Electronic-Invoice-Mandatory-Field-Requirements.pdf',
    ),
    'MD243': Source(
        'MD243',
        'Ministerial Decision No. 243 of 2025 on the Electronic Invoicing System',
        'as amended by MD 197/2026',
        '2026-10-01',
        _MOF + '2025/09/Ministerial-Decision-no.-243-of-2025-on-the-Electronic-Invoicing-System.pdf',
    ),
    'MD197': Source(
        'MD197',
        'Ministerial Decision No. 197 of 2026 amending certain provisions of MD No. 243 of 2025',
        'in force 2026-10-01',
        '2026-10-01',
        _MOF + '2026/10/Ministerial-Decision-No.-197-of-2026-%E2%80%93-Amending-Certain-Provisions-of-Ministerial-'
        'Decision-No.-243-of-2025-on-the-Electronic-Invoicing-System_En.pdf',
    ),
    'MD244': Source(
        'MD244',
        'Ministerial Decision No. 244 of 2025 on the Implementation of the Electronic Invoicing System',
        'as amended by MR 66/2026',
        '2026-05-14',
        _MOF + '2025/09/Ministerial-Decision-No.-244-of-2025-on-the-Implementation-of-the-Electronic-Invoicing-'
        'System.pdf',
    ),
    'MR66': Source(
        'MR66',
        'Ministerial Resolution No. 66 of 2026 amending MD No. 244 of 2025',
        'published 2026-05',
        '2026-05-14',
        _MOF + '2026/05/Ministerial-Resolution-No.-66-of-2026-Amending-Certain-Provisions-of-Ministerial-'
        'Resolution-No.-244-of-2025-Regarding-the-Implementation-of-the-Electronic-Invoicing-System-En-20260514.pdf',
    ),
    'MD168': Source(
        'MD168',
        'Ministerial Decision No. 168 of 2026 on the eligibility criteria and accreditation procedure for Service '
        'Providers',
        'in force 2026-10-01',
        '2026-10-01',
        _MOF + '2026/10/Ministerial-Decision-No-168-of-2026-on-Eligibility-criteria-and-Accreditation-Procedure-'
        'EN-FINAL.pdf',
    ),
    'CD106': Source(
        'CD106',
        'Cabinet Decision No. 106 of 2025 on the Violations and Administrative Penalties of the Electronic Invoicing '
        'System',
        'final version',
        '2025-12-08',
        _MOF + '2025/12/Cabinet-Decision-Violations-and-Penalties-eInvoicing-final-version-en-8.12.25.pdf',
    ),
}

# Cited by an official document, but not located as a published instrument: never cited as a fact (OCQ-19).
UNVERIFIED = {
    'CD173': "Cabinet Decision No. 173 of 2026 (penalties), cited by Guidelines V2.0 §11.2; not published on the "
    "MoF eInvoicing page as of 2026-10-28.",
}


def cite(key, where=''):
    """``'<title> <version>, <where>'`` for a verified source; KeyError for anything else (never a guess)."""
    source = SOURCES[key]
    text = '%s %s' % (source.title, source.version)
    return '%s, %s' % (text, where) if where else text
