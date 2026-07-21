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

import re

from odoo import api, fields, models
from odoo.exceptions import ValidationError

# A colour is exactly a 6-digit hex triplet. Nothing else may reach the
# <style> block, so ';', '}', '<', 'url(', etc. are structurally impossible.
_HEX_COLOR_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")

# Fields that must pass the hex check. nc_login_background is an Image, not a
# colour, so it is validated by Odoo's binary handling, not here.
_COLOR_FIELDS = ("nc_primary_color", "nc_secondary_color", "nc_sidebar_color")


class ResCompany(models.Model):
    _inherit = "res.company"

    # Defaults live on the fields (not in a data record) so a module upgrade
    # never overwrites a tenant's chosen colours — mirroring the once-only
    # posture of res_company_logo.xml. An empty value means "fall back to the
    # NCollection default baked into the SCSS var() fallback".
    nc_primary_color = fields.Char(
        string="Primary Colour",
        default="#1F5F8F",
        help="Primary brand colour — drives the navbar and primary buttons. "
             "6-digit hex, e.g. #1F5F8F. Leave empty for the NCollection default.",
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
                if value and not _HEX_COLOR_RE.match(value):
                    raise ValidationError(self.env._(
                        "%(value)r is not a valid colour for '%(field)s'. "
                        "Use a 6-digit hex colour such as #1F5F8F.",
                        value=value,
                        field=self._fields[fname].string,
                    ))
