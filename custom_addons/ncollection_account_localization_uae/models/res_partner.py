# -*- coding: utf-8 -*-
"""UAE TRN validation (F5-T01) and the UAE e-invoicing master data (``nc_trn``, ``nc_tin``, legal registration).

The UAE Tax Registration Number (TRN) is a 15-digit numeric identifier issued
by the Federal Tax Authority; it has no published checksum, so validation is a
format/length check. We plug into Odoo's own base_vat dispatch rather than add
a parallel constraint: base_vat's ``_check_vat_number`` calls
``check_vat_<country_code>`` when it exists (this is exactly how core validates
Saudi Arabia via ``check_vat_sa``). Defining ``check_vat_ae`` here therefore
EXTENDS the mechanism Odoo already owns — we do not replace it.

Applies to companies too: ``res.company.vat`` is ``related='partner_id.vat'``,
so a company's TRN is validated through this same partner path.

THE E-INVOICING FIELDS. Audit of ``vat`` (the existing field): it is Odoo's country-agnostic "Tax ID", checked per
country by base_vat, used by Odoo's fiscal-position logic and printed as the TRN by l10n_gcc_invoice; foreign
customers keep their own country's VAT number in it. It therefore does NOT carry the exact semantics "the UAE VAT
TRN" in every case, so ``nc_trn`` is a separate, explicit field. ``vat`` is untouched: nothing here reads, writes,
copies or falls back to it, and nothing derives ``nc_trn`` and ``nc_tin`` from each other.

NO SYNCHRONISATION. Unlike ``vat``, these fields are NOT in ``_commercial_fields`` / ``_synced_commercial_fields``:
a compliance identifier is maintained explicitly on the record it belongs to; a contact of a company does not silently
receive or overwrite it. They are also ``copy=False`` so duplicating a partner never duplicates a regulated identifier.
"""
from odoo import api, fields, models
from odoo.exceptions import ValidationError

from . import uae_identifiers
from .uae_identifiers import TRN_RE  # noqa: F401  (kept importable from here: check_vat_ae and the tests use it)

_TRN_RE = TRN_RE


class ResPartner(models.Model):
    _inherit = 'res.partner'

    def check_vat_ae(self, vat):
        """base_vat dispatch hook for AE. Return a truthy match on a valid
        15-digit TRN, else False (mirrors core's ``check_vat_sa`` shape, but
        anchored with fullmatch so a trailing newline is rejected)."""
        return _TRN_RE.fullmatch(vat or '') or False

    # ------------------------------------------------------------------ e-invoicing master data
    nc_trn = fields.Char(
        string='TRN / Tax Registration Number', copy=False, tracking=True,
        help="UAE VAT Tax Registration Number, 15 digits. Optional. Maintained explicitly: never derived from the TIN "
             "or from the Tax ID, and not synchronised between contacts.")
    nc_tin = fields.Char(
        string='TIN / Tax Identification Number', copy=False, tracking=True,
        help="UAE Tax Identification Number, 10 digits: the participant identifier used by UAE e-invoicing. Optional. "
             "Maintained explicitly: never derived from the TRN or from the Tax ID, and not synchronised between "
             "contacts.")
    nc_legal_reg_type = fields.Selection(
        [
            ('TL', 'Commercial / Trade licence'),
            ('EID', 'Emirates ID'),
            ('PAS', 'Passport'),
            ('CD', 'Cabinet Decision'),
        ],
        string='Legal registration type', copy=False, tracking=True,
        help="What kind of registration the Company ID (registry number) is. The official PINT-AE list.")
    nc_legal_reg_authority = fields.Char(
        string='Registration authority', copy=False,
        help="The authority that issued the registration. Needed for a trade licence on an e-invoice.")
    nc_legal_reg_country_id = fields.Many2one(
        'res.country', string='Passport issuing country', copy=False,
        help="Needed for a passport registration on an e-invoice.")
    # Scope of UAE e-invoicing (Guidelines V2.0 §6.1-6.2): supplies to natural persons who are not in Business are
    # outside the scope. Declared, never inferred: a person who is not a company may still be in Business.
    nc_einvoice_party_type = fields.Selection(
        [
            ('business', 'Business'),
            ('government', 'Government Entity'),
            ('consumer', 'Natural person not in business'),
        ],
        string='E-invoicing party type', default='business', required=True, copy=False, tracking=True,
        help="Business and Government Entity transactions are in the scope of UAE e-invoicing; a natural person who is "
             "not in business is not (UAE Electronic Invoicing Guidelines V2.0 §6.2).")
    # MD 243/2025 Art 4(1)(f) as inserted by MD 197/2026: transactions between members of the same Tax Group are
    # excluded from 2027-01-01 through 2028-12-31. The group is identified by its own TRN, explicitly maintained.
    nc_vat_group_trn = fields.Char(
        string='Tax Group TRN', copy=False, tracking=True,
        help="The 15-digit TRN of the VAT Tax Group this person belongs to, if any. Two parties with the same Tax "
             "Group TRN are members of the same Tax Group.")

    @api.constrains('nc_trn', 'nc_tin', 'nc_vat_group_trn')
    def _check_nc_uae_identifiers(self):
        """The only thing master data refuses: a NON-EMPTY value in the wrong shape. Empty is always fine."""
        for partner in self:
            if partner.nc_vat_group_trn and uae_identifiers.trn_state(partner.nc_vat_group_trn).state == \
                    uae_identifiers.INVALID:
                raise ValidationError(self.env._(
                    "Tax Group TRN must be exactly 15 digits (no spaces or letters). Leave it empty if the person is "
                    "not a member of a Tax Group."))
            if partner.nc_trn and uae_identifiers.trn_state(partner.nc_trn).state == uae_identifiers.INVALID:
                raise ValidationError(self.env._(
                    "TRN / Tax Registration Number must be exactly 15 digits (no spaces or letters). Leave it empty "
                    "if it is not known."))
            if partner.nc_tin and uae_identifiers.tin_state(partner.nc_tin).state == uae_identifiers.INVALID:
                raise ValidationError(self.env._(
                    "TIN / Tax Identification Number must be exactly 10 digits (no spaces or letters). Leave it "
                    "empty if it is not known."))

    def _nc_uae_identity_states(self):
        """Readiness of this partner's UAE identifiers as ``{'trn': (state, notes), 'tin': (state, notes)}``.

        Reads ``nc_trn`` and ``nc_tin`` ONLY. There is deliberately no fallback to ``vat`` and no cross-derivation:
        a partner whose TRN is only in ``vat`` is reported as ``missing`` here."""
        self.ensure_one()
        return {'trn': uae_identifiers.trn_state(self.nc_trn), 'tin': uae_identifiers.tin_state(self.nc_tin)}
