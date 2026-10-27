# -*- coding: utf-8 -*-
"""Hands the product identity to the web client (tab title, error dialogs) from the same source as the server
templates, and words the web client's own product-naming strings in the System Branding name."""
import re

from odoo import api, models

# Web-client strings that NAME THE PRODUCT ("Install Odoo", "Odoo will send notifications on this device!"). They are
# answered through Odoo's own translation channel (/web/webclient/translations), so the lazy ``_t`` of the core
# component shows the system's name in every language and no core file is patched. The list is deliberate: only the
# product's identity, never a technical string that merely mentions Odoo. Error dialog titles are static class
# properties, not ``_t`` lookups at render time, and stay in static/src/js/white_label.js.
WEB_IDENTITY_TERMS = {
    'mail': (
        "Install Odoo",
        "Odoo will not send notifications on this device.",
        "Odoo will send notifications on this device!",
    ),
    'web': (
        "Odoo needs your authorization first.",
        "Odoo Session Expired",
        "Your Odoo session expired. The current page is about to be refreshed.",
        "Odoo Push notifications have been blocked. Go to your browser settings to allow them.",
    ),
}
# The product's name as the source and Odoo's own Arabic translations write it. An Arabic prefix glued to the name
# ("لأودو", "بأودو") is kept and joined with a tatweel, so a Latin system name never runs into it ("لـ" + the name).
_ODOO_NAME = re.compile(r'\bOdoo\b|([؀-ۿ])?أودو')


def system_named(text, name):
    """``text`` with the product's name replaced by the system's."""
    return _ODOO_NAME.sub(lambda match: '%sـ %s' % (match[1], name) if match[1] else name, text)


class IrHttp(models.AbstractModel):
    _inherit = 'ir.http'

    def session_info(self):
        info = super().session_info()
        info['nc_product_brand'] = self.env['ncollection.product.branding'].nc_brand()
        return info

    @api.model
    def _get_translations_for_webclient(self, modules, lang):
        """Odoo's per-module web translations, with the product-naming terms worded in the System Branding name.

        The result feeds ``_get_web_translations_hash`` (ormcached): a System Branding write clears the registry
        cache (models/product_branding.py), so a renamed system reaches the next web client load."""
        translations, lang_params = super()._get_translations_for_webclient(modules, lang)
        name = self.env['ncollection.product.branding'].nc_brand()['name']
        for module, terms in WEB_IDENTITY_TERMS.items():
            if module not in translations:
                continue
            messages = {row['id']: row['string'] for row in translations[module].get('messages', ())}
            for source in terms:
                messages[source] = system_named(messages.get(source) or source, name)
            translations[module] = dict(translations[module], messages=tuple(
                {'id': source, 'string': value} for source, value in messages.items()))
        return translations, lang_params
