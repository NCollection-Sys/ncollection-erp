# -*- coding: utf-8 -*-
"""The contact form's Website placeholder names the platform's own domain (CONTACT-01).

The domain is the one the platform builds every address from: SaaS Settings > Base Domain
(``ncollection_saas.base_domain``, set on the platform database), else the deployment's ``NC_BASE_DOMAIN``
(docs/markdown/DEPLOYMENT_DOMAIN_CONTRACT.md). Nothing is hardcoded and nothing is stored on the contact: it is a
placeholder (visual guidance), never a default value.

A placeholder is a static attribute of the view and Odoo caches the processed view, so it is written into the arch
``get_views`` returns, after that cache, on every request: a changed domain shows on the next form that opens.
"""
import os
import re

from lxml import etree

from odoo import api, models

_DOMAIN = re.compile(r'^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+$')


class ResPartner(models.Model):
    _inherit = 'res.partner'

    @api.model
    def _nc_platform_domain(self):
        """The platform's base domain, or '' when none is configured. Only a well-formed host name is returned: it
        is written into a view attribute."""
        value = self.env['ir.config_parameter'].sudo().get_param('ncollection_saas.base_domain') or \
            os.environ.get('NC_BASE_DOMAIN') or ''
        value = value.strip().lower().rstrip('.')
        return value if _DOMAIN.fullmatch(value) else ''

    @api.model
    def get_views(self, views, options=None):
        result = super().get_views(views, options=options)
        domain = self._nc_platform_domain()
        if not domain:
            return result                                   # Odoo's own placeholder stays
        placeholder = self.env._("e.g. https://www.%(domain)s", domain=domain)
        for view in (result.get('views') or {}).values():
            arch = view.get('arch')
            if not arch or 'name="website"' not in arch:
                continue
            node = etree.fromstring(arch)
            changed = False
            for field in node.iter('field'):
                if field.get('name') == 'website':
                    field.set('placeholder', placeholder)
                    changed = True
            if changed:
                view['arch'] = etree.tostring(node, encoding='unicode')
        return result
