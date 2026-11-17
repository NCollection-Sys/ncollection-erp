# -*- coding: utf-8 -*-
"""Three independent footer zones for printed documents (owner review 2026-11): Left, Center, Right.

Stored per company beside Odoo's own ``report_footer`` (same type: translatable, sanitized HTML), edited in Odoo's own
Configure Document Layout (``base.document.layout``, related fields), printed by
``ncollection_branding.nc_footer_zones``
in every company layout (views/footer_zones_templates.xml). The zones are PHYSICAL positions (left | center | right of
the page) in LTR and RTL documents alike; inside each zone the text runs in its own direction.

Compatibility: a company whose zones are all empty keeps printing its single legacy Footer exactly as before. The
migration copies a legacy Footer into the Center zone once (only where no zone was ever set), and a layout prints the
zones INSTEAD of the legacy Footer when any zone has content, so nothing is printed twice.
"""
from odoo import api, fields, models

ZONES = ('nc_footer_left', 'nc_footer_center', 'nc_footer_right')


class ResCompany(models.Model):
    _inherit = 'res.company'

    nc_footer_left = fields.Html(string='Left Footer', translate=True,
                                 help="Printed at the bottom left of every page of the company's documents.")
    nc_footer_center = fields.Html(string='Center Footer', translate=True,
                                   help="Printed at the bottom centre of every page of the company's documents.")
    nc_footer_right = fields.Html(string='Right Footer', translate=True,
                                  help="Printed at the bottom right of every page of the company's documents.")


class BaseDocumentLayout(models.TransientModel):
    _inherit = 'base.document.layout'

    nc_footer_left = fields.Html(related='company_id.nc_footer_left', readonly=False)
    nc_footer_center = fields.Html(related='company_id.nc_footer_center', readonly=False)
    nc_footer_right = fields.Html(related='company_id.nc_footer_right', readonly=False)

    @api.depends('nc_footer_left', 'nc_footer_center', 'nc_footer_right')
    def _compute_preview(self):
        """The live preview follows the three zones too (same template as the PDF, the wizard standing in for the
        company, exactly as Odoo renders its own preview)."""
        return super()._compute_preview()
