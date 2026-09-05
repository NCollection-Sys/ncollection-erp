# -*- coding: utf-8 -*-
"""SaaS platform settings (#476).

The base domain has been configurable since P2-T06 — as an `ir.config_parameter`
with no UI, which meant the one value every tenant URL is built from could only
be changed from the technical Parameters list. Surfacing it here does not add a
setting; it makes the existing one findable.

`config_parameter=` binds the field straight to the SAME key the domain model
reads, so there is no second store and nothing to keep in step.
"""
from odoo import api, fields, models
from odoo.exceptions import ValidationError


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    nc_base_domain = fields.Char(
        string='Base Domain',
        config_parameter='ncollection_saas.base_domain',
        default='ncollectionerp.com',
        help="The platform's root domain. Every tenant is reachable at "
             "<subdomain>.<base domain>, e.g. atlas.ncollectionerp.com.")

    @api.constrains('nc_base_domain')
    def _check_nc_base_domain(self):
        """A malformed base domain silently breaks EVERY tenant URL at once.

        Checked at the point of entry rather than left to fail per-tenant later:
        this value is not per-record, so a typo here is a platform-wide outage
        of the links operators and customers navigate by.
        """
        for record in self:
            value = (record.nc_base_domain or '').strip().lower()
            if not value:
                continue
            labels = value.split('.')
            if len(labels) < 2 or not all(
                    label and label.replace('-', '').isalnum()
                    and not label.startswith('-') and not label.endswith('-')
                    for label in labels):
                raise ValidationError(self.env._(
                    "'%s' is not a valid base domain. Use a hostname with at "
                    "least two labels, e.g. ncollectionerp.com.",
                    record.nc_base_domain))
