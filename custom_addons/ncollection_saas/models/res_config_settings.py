# -*- coding: utf-8 -*-
"""SaaS platform settings (#476).

The base domain has been configurable since P2-T06 — as an `ir.config_parameter`
with no UI, which meant the one value every tenant URL is built from could only
be changed from the technical Parameters list. Surfacing it here does not add a
setting; it makes the existing one findable.

`config_parameter=` binds the field straight to the SAME key the domain model
reads, so there is no second store and nothing to keep in step.
"""
import ipaddress

from odoo import api, fields, models
from odoo.exceptions import ValidationError

from .domain import normalize_fqdn


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    nc_base_domain = fields.Char(
        string='Base Domain',
        config_parameter='ncollection_saas.base_domain',
        help="The platform's root domain. Every tenant is reachable at <subdomain>.<base domain>. Leave empty to use "
             "the deployment's NC_BASE_DOMAIN (local development: ncollection.localhost). There is no built-in "
             "default.")
    # Custom tenant domains (models/domain.py). Per platform; nothing here is specific to one platform.
    nc_custom_domains_enabled = fields.Boolean(
        string='Custom Domains', config_parameter='ncollection_saas.custom_domains_enabled',
        help="Let Platform Administrators give a tenant its own verified domain. Needs the edge agent on the host "
             "(docs/markdown/CUSTOM_TENANT_DOMAINS.md).")
    nc_custom_domain_ipv4 = fields.Char(
        string='Edge IPv4', config_parameter='ncollection_saas.custom_domain_ipv4',
        help="The address a customer's A record must point to.")
    nc_custom_domain_ipv6 = fields.Char(
        string='Edge IPv6', config_parameter='ncollection_saas.custom_domain_ipv6',
        help="The address a customer's AAAA record must point to (leave empty when the edge has no IPv6).")
    nc_custom_domain_cname = fields.Char(
        string='Edge CNAME Target', config_parameter='ncollection_saas.custom_domain_cname',
        help="The hostname a customer's CNAME may point to instead (subdomains only; never valid at a zone apex).")

    @api.constrains('nc_custom_domain_ipv4', 'nc_custom_domain_ipv6', 'nc_custom_domain_cname')
    def _check_nc_custom_domain_targets(self):
        for record in self:
            for value, version in ((record.nc_custom_domain_ipv4, 4), (record.nc_custom_domain_ipv6, 6)):
                if value:
                    try:
                        if ipaddress.ip_address(value.strip()).version != version:
                            raise ValueError
                    except ValueError as exc:
                        raise ValidationError(self.env._(
                            "%(value)s is not a valid IPv%(version)s address.", value=value, version=version)) from exc
            if record.nc_custom_domain_cname:
                try:
                    normalize_fqdn(record.nc_custom_domain_cname)
                except ValueError as exc:
                    raise ValidationError(self.env._("Invalid CNAME target: %s.", exc)) from exc

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
                    "least two labels, e.g. example.com.",
                    record.nc_base_domain))
