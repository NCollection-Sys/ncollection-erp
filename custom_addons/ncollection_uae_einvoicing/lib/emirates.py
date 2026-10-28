# -*- coding: utf-8 -*-
"""Odoo emirate (``res.country.state`` of the UAE) → PINT-AE country subdivision.

Odoo's state codes are NOT the PINT-AE codes (``AZ``≠``AUH``, ``DU``≠``DXB``, ``SH``≠``SHJ``, ``UQ``≠``UAQ``,
``FU``≠``FUJ``, ``AJ``≠``AJM``, ``RK``≠``RAK``), so the map is explicit and complete; nothing is derived from a code's
letters. The allowed PINT values are the seven of official rule ``ibr-128-ae`` (``spec.SpecProfile.emirate_codes``); a
test proves the map's targets are exactly that set. An unmapped state is a readiness error, never a guess.
"""

from .spec import get_profile

# Odoo res.country.state.code (country AE)  ->  PINT-AE subdivision. One-to-one by emirate.
ODOO_STATE_TO_PINT = {
    'AZ': 'AUH',  # Abu Dhabi
    'DU': 'DXB',  # Dubai
    'SH': 'SHJ',  # Sharjah
    'UQ': 'UAQ',  # Umm Al Quwain
    'FU': 'FUJ',  # Fujairah
    'AJ': 'AJM',  # Ajman
    'RK': 'RAK',  # Ras Al Khaimah
}


def pint_subdivision(odoo_state_code, profile=None):
    """The PINT-AE subdivision for an Odoo AE state code, or ``None`` when it is empty or unmapped."""
    pint = ODOO_STATE_TO_PINT.get((odoo_state_code or '').strip().upper())
    return pint if pint in (profile or get_profile()).emirate_codes else None
