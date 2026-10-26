# -*- coding: utf-8 -*-
"""Per-tenant branding fields on res.company (P1-T16).

Each tenant runs in its own database (database-per-tenant), so a tenant's
branding is simply its company record. These fields drive the runtime CSS
custom properties injected by views/branding_theme_templates.xml; the strict
hex constraint is the ORM/RPC-level enforcement (Standing Rule 4/7) that makes
the injection safe against CSS/XSS regardless of the write path (UI, RPC,
import) — a validated value can never contain the characters that would let it
break out of a CSS declaration.
"""

import base64
import binascii
import io
import re

from odoo import api, fields, models, tools
from odoo.exceptions import ValidationError

# A colour is exactly a 6-digit hex triplet. Nothing else may reach the
# <style> block, so ';', '}', '<', 'url(', etc. are structurally impossible.
_HEX_COLOR_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")

# The company stamp is a PNG, stored AS UPLOADED (a transparent seal must stay transparent and must not be
# re-encoded or downsized), so it is validated instead of being run through an image field's processing.
NC_STAMP_MAX_BYTES = 2 * 1024 * 1024
NC_STAMP_MAX_SIDE_PX = 4000
_PNG_MAGIC = b'\x89PNG\r\n\x1a\n'

# Fields that must pass the hex check. nc_login_background is an Image, not a
# colour, so it is validated by Odoo's binary handling, not here.
_COLOR_FIELDS = ("nc_primary_color", "nc_secondary_color", "nc_sidebar_color")


class ResCompany(models.Model):
    _inherit = "res.company"

    # Defaults live on the fields (not in a data record) so a module upgrade
    # never overwrites a tenant's chosen colours — mirroring the once-only
    # posture of res_company_logo.xml. An empty value means "fall back to the
    # NCollection default baked into the SCSS var() fallback".
    # TEMPORARY presentation (until the company-branding design is decided): the Workspace Administration "Company"
    # screen shows the SYSTEM's logo (System Branding) instead of the company's own ``logo``, which is not touched -
    # documents and the company switcher keep the company's identity. Read-only, never stored.
    nc_system_logo = fields.Binary(string="Logo", compute='_compute_nc_system_logo')

    def _compute_nc_system_logo(self):
        raw, _mime = self.env['ncollection.product.branding']._nc_image('logo')
        image = base64.b64encode(raw)
        for company in self:
            company.nc_system_logo = image

    # A company's logo is the COMPANY's identity (the letterhead of its invoices). The vendor marks a database is born
    # with - Odoo's default company logo, and the NCollection mark this module used to seed - are not: printed on an
    # invoice they read as the company's own. They are removed (the letterhead then shows the company's name); a logo
    # the Owner uploaded is never touched. Exact bytes only, so nothing is guessed.
    _NC_VENDOR_LOGOS = ('base/static/img/res_company_logo.png', 'ncollection_branding/static/src/img/logo.png')

    @api.model
    def _nc_clear_vendor_logos(self):
        marks = set()
        for path in self._NC_VENDOR_LOGOS:
            try:
                with tools.file_open(path, 'rb') as handle:
                    marks.add(handle.read())
            except FileNotFoundError:
                continue
        cleared = self.sudo().search([]).filtered(
            lambda company: company.logo and base64.b64decode(company.logo) in marks)
        if cleared:
            cleared.write({'logo': False})
        return cleared

    nc_primary_color = fields.Char(
        string="Primary Colour",
        default="#1F5F8F",
        help="Primary brand colour — drives the navbar and primary buttons. "
             "6-digit hex, e.g. #1F5F8F. Leave empty for the system default.",
    )
    nc_secondary_color = fields.Char(
        string="Secondary Colour",
        default="#2D7AB7",
        help="Secondary/accent colour — links and highlights. 6-digit hex.",
    )
    nc_sidebar_color = fields.Char(
        string="Sidebar Colour",
        default="#12233A",
        help="Sidebar/side-navigation background colour. 6-digit hex.",
    )
    nc_login_background = fields.Image(
        string="Login Background",
        max_width=1920,
        max_height=1080,
        help="Optional full-page background image for the login screen.",
    )

    @api.constrains(*_COLOR_FIELDS)
    def _check_nc_brand_colors(self):
        """Reject anything that is not a strict 6-digit hex colour.

        This is the security control for the CSS injection: it runs on every
        create/write (UI, XML-RPC/JSON-RPC, data import), so a malicious or
        malformed value can never be stored and therefore can never be emitted
        into the injected <style> block.
        """
        for company in self:
            for fname in _COLOR_FIELDS:
                value = company[fname]
                if value and not _HEX_COLOR_RE.fullmatch(value):
                    raise ValidationError(self.env._(
                        "%(value)r is not a valid colour for '%(field)s'. "
                        "Use a 6-digit hex colour such as #1F5F8F.",
                        value=value,
                        field=self._fields[fname].string,
                    ))

    def _nc_document_color(self):
        """The brand colour printed documents use: the company's report colour, else its NCollection primary colour,
        else the NCollection blue, and ONLY ever a validated 6-digit hex. ``primary_color`` is a free-text field in
        Odoo; it is written into a ``<style>`` block, where ``;`` or ``}`` would break out of the declaration."""
        self.ensure_one()
        for candidate in (self.primary_color, self.nc_primary_color):
            if candidate and _HEX_COLOR_RE.fullmatch(candidate):
                return candidate
        return '#1F5F8F'

    nc_stamp = fields.Binary(
        string="Company Stamp", attachment=True, copy=False, groups='ncollection_branding.group_stamp_manager',
        help="The company's official stamp / seal, a PNG (transparency is kept). It is printed at the end of the "
             "company's documents, by the shared print layout, for this company only.")

    @api.model
    def _nc_validate_stamp(self, value):
        """Only a genuine PNG of a sane size is stored: the file is decoded, not trusted by its name."""
        if not value:
            return
        try:
            raw = base64.b64decode(value, validate=True)
        except (binascii.Error, ValueError, TypeError):
            raise ValidationError(self.env._("The company stamp is not a valid file."))
        if not raw.startswith(_PNG_MAGIC):
            raise ValidationError(self.env._("The company stamp must be a PNG image."))
        if len(raw) > NC_STAMP_MAX_BYTES:
            raise ValidationError(self.env._(
                "The company stamp is too large (the limit is %(limit)s MB).",
                limit=NC_STAMP_MAX_BYTES // (1024 * 1024)))
        from PIL import Image, UnidentifiedImageError
        try:
            Image.open(io.BytesIO(raw)).verify()                 # the whole file, not just the signature
            image = Image.open(io.BytesIO(raw))
            width, height = image.size
        except (UnidentifiedImageError, OSError, SyntaxError, ValueError, Image.DecompressionBombError):
            raise ValidationError(self.env._("The company stamp is not a readable PNG image."))
        if image.format != 'PNG':
            raise ValidationError(self.env._("The company stamp must be a PNG image."))
        if max(width, height) > NC_STAMP_MAX_SIDE_PX:
            raise ValidationError(self.env._(
                "The company stamp is too large (at most %(side)s pixels on a side).", side=NC_STAMP_MAX_SIDE_PX))

    # Validated BEFORE it is stored (a malformed value must not reach the attachment layer), on every write path.
    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            self._nc_validate_stamp(vals.get('nc_stamp'))
        return super().create(vals_list)

    def write(self, vals):
        if 'nc_stamp' in vals:
            self._nc_validate_stamp(vals['nc_stamp'])
        return super().write(vals)


# ---------------------------------------------------------------------------------------------------------------------
# The NCollection document layout as the default
# ---------------------------------------------------------------------------------------------------------------------
# Odoo gives every company the "Light" layout, and a company that never opened the layout configurator has none yet.
# Neither is a CHOICE somebody made, so both move to the NCollection layout; a company that picked Boxed, Bold,
# Striped... on purpose keeps it. Used on install and on upgrade; it only ever fills in a default, so running it
# again changes nothing.
_STOCK_DEFAULT_LAYOUT = 'web.external_layout_standard'


def adopt_nc_layout(env):
    layout = env.ref('ncollection_branding.report_layout_nc', raise_if_not_found=False)
    if not layout:
        return 0
    companies = env['res.company'].sudo().with_context(active_test=False).search([])
    adopting = companies.filtered(
        lambda company: not company.external_report_layout_id
        or company.external_report_layout_id.key == _STOCK_DEFAULT_LAYOUT)
    if adopting:
        adopting.write({'external_report_layout_id': layout.view_id.id})
    return len(adopting)
