# -*- coding: utf-8 -*-
"""UAE TRN validation (F5-T01).

The UAE Tax Registration Number (TRN) is a 15-digit numeric identifier issued
by the Federal Tax Authority; it has no published checksum, so validation is a
format/length check. We plug into Odoo's own base_vat dispatch rather than add
a parallel constraint: base_vat's ``_check_vat_number`` calls
``check_vat_<country_code>`` when it exists (this is exactly how core validates
Saudi Arabia via ``check_vat_sa``). Defining ``check_vat_ae`` here therefore
EXTENDS the mechanism Odoo already owns — we do not replace it.

Applies to companies too: ``res.company.vat`` is ``related='partner_id.vat'``,
so a company's TRN is validated through this same partner path.
"""
import re

from odoo import models

# UAE TRN: exactly 15 digits (FTA-issued, no checksum). Matched with fullmatch,
# NOT match()+'$' — Python's '$' also matches just before a trailing '\n', so a
# pasted/CSV-imported TRN with a stray newline would otherwise slip through as
# "valid" and poison downstream FTA invoice/QR formatting (#49).
_TRN_RE = re.compile(r'[0-9]{15}')


class ResPartner(models.Model):
    _inherit = 'res.partner'

    def check_vat_ae(self, vat):
        """base_vat dispatch hook for AE. Return a truthy match on a valid
        15-digit TRN, else False (mirrors core's ``check_vat_sa`` shape, but
        anchored with fullmatch so a trailing newline is rejected)."""
        return _TRN_RE.fullmatch(vat or '') or False
