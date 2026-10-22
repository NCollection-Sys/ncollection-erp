# -*- coding: utf-8 -*-
"""Vendor wording in MODULE-AUTHORED data that cannot be inherited: mail templates and the empty-state help of actions.

Mail templates and `ir.actions.act_window.help` are stored records (mostly `noupdate`), so a view inheritance cannot
reach them. The product name they should show is the one in Product Branding. This applies a short, explicit list of
replacements to the records that a MODULE shipped (never to a user's own template or text), is idempotent (a second run
changes nothing), and runs when the module is installed or upgraded and when the registry loads, so a module installed
later is covered too. It does not touch technical content (URLs of the application, model names, tokens).
"""
import logging
import re

from odoo import api, models

_logger = logging.getLogger(__name__)

_BRAND_EMAIL = "{{ object.env['ncollection.product.branding'].nc_brand()['name'] }}"
_BRAND_QWEB = '<t t-out="object.env[\'ncollection.product.branding\'].nc_brand()[\'name\']"/>'

_ANCHOR = r'<a\b[^>]*href\s*=\s*["\'][^"\']*odoo\.com[^"\']*["\'][^>]*>.*?</a>\s*'
# (pattern, replacement, flags): applied to the body (and subject) of module-shipped mail templates
MAIL_RULES = (
    # the credit line: "Powered by <a href=odoo.com>Odoo</a>"  ->  "Powered by <product>"
    (r'(Powered by\s*)' + _ANCHOR, r'\1' + _BRAND_QWEB, re.S | re.I),
    # the vendor logo image in the e-invoice e-mails -> the product name
    (r'<img[^>]*alt="Odoo"[^>]*src="/web/static/img/logo[^"]*"[^>]*/?>',
     '<span style="font-weight: bold; color: #ffffff;">' + _BRAND_QWEB + '</span>', re.S),
    # the invitation e-mail
    (r'<span style="font-size: 10px;">Welcome to Odoo</span>',
     '<span style="font-size: 10px;">Welcome to ' + _BRAND_QWEB + '</span>', 0),
    (r'to connect on Odoo\.', 'to connect on ' + _BRAND_QWEB + '.', 0),
    (r'Your Odoo domain is:', 'Your domain is:', 0),
    (r'Never heard of Odoo\?.*?productivity\.\s*(<br\s*/?>\s*){0,2}', '', re.S),
    (r'Have a look at the\s*' + _ANCHOR + r'to discover the tool\.\s*(<br\s*/?>\s*){0,2}', '', re.S),
    (r'Enjoy Odoo!', 'Enjoy!', 0),
)
SUBJECT_RULES = (
    (r'invites you to connect to Odoo', 'invites you to connect to ' + _BRAND_EMAIL, 0),
    (r'on your Odoo account', 'on your ' + _BRAND_EMAIL + ' account', 0),
)
# the empty-state text of the lists: the vendor's "sample" / documentation buttons are removed
HELP_RULES = (
    (r'<a\b[^>]*href\s*=\s*["\'][^"\']*odoo\.com[^"\']*["\'][^>]*>.*?</a>\s*', '', re.S | re.I),
)


def _apply(rules, text):
    for pattern, replacement, flags in rules:
        text = re.sub(pattern, replacement, text, flags=flags)
    return text


class NcVendorContent(models.AbstractModel):
    _name = 'ncollection.vendor.content'
    _description = 'Vendor wording in module-authored data'

    @api.model
    def _nc_module_records(self, model):
        """Records of `model` that a module shipped (they have an external id other than an export)."""
        data = self.env['ir.model.data'].sudo().search(
            [('model', '=', model), ('module', 'not in', ('__export__', '__import__'))])
        return self.env[model].sudo().browse(data.mapped('res_id')).exists()

    @api.model
    def _nc_write(self, record, vals):
        """Write one record; during a partial registry load (an action whose model is not loaded yet) skip it - the
        registry-load hook below finishes the job once every model exists."""
        try:
            with self.env.cr.savepoint():
                record.write(vals)
        except KeyError:
            return 0
        except Exception:                                  # noqa: BLE001 - one record never stops the rest
            _logger.warning("Could not apply the product wording to %s", record, exc_info=True)
            return 0
        return 1

    @api.model
    def _nc_bot_name(self, short_name):
        return '%s Bot' % short_name

    @api.model
    def _nc_sync_bot(self, old_short_name=None):
        """The system user that signs automatic messages is named after the product (its short name), not the vendor.
        It is renamed only while it still carries the vendor's name or the name this product gave it, never a name an
        administrator chose."""
        partner = self.env.ref('base.partner_root', raise_if_not_found=False)
        if not partner:
            return 0
        brand = self.env['ncollection.product.branding'].nc_brand()
        current = partner.sudo().name
        ours = (self._nc_bot_name(old_short_name),) if old_short_name else ()
        if current in ('OdooBot',) + ours and current != self._nc_bot_name(brand['short_name']):
            return self._nc_write(partner.sudo(), {'name': self._nc_bot_name(brand['short_name'])})
        return 0

    @api.model
    def _nc_unbrand(self):
        changed = self._nc_sync_bot()
        brand = self.env['ncollection.product.branding'].nc_brand()['name']
        for model in ('res.groups', 'digest.digest'):
            if model not in self.env:
                continue
            for record in self._nc_module_records(model):
                if 'Odoo' in brand or not re.search(r'\bOdoo\b', record.name or ''):
                    continue            # nothing to replace, or the product name itself says it: never grow a name
                changed += self._nc_write(record, {'name': re.sub(r'\bOdoo\b', lambda _m: brand, record.name)})
        for template in self._nc_module_records('mail.template'):
            vals = {}
            body = template.body_html or ''
            if body and re.search(r'odoo', body, re.I):
                new = _apply(MAIL_RULES, body)
                if new != body:
                    vals['body_html'] = new
            subject = template.subject or ''
            if subject and 'Odoo' in subject:
                new = _apply(SUBJECT_RULES, subject)
                if new != subject:
                    vals['subject'] = new
            if vals:
                changed += self._nc_write(template, vals)
        for action in self._nc_module_records('ir.actions.act_window'):
            help_text = action.help or ''
            if 'odoo.com' in help_text:
                new = _apply(HELP_RULES, help_text)
                if new != help_text:
                    changed += self._nc_write(action, {'help': new})
        return changed

    def _register_hook(self):
        super()._register_hook()
        # While this module is being installed the data file runs the same rules at the right moment, and its table may
        # not exist yet: the hook is for registries of databases that already have it.
        self.env.cr.execute("SELECT to_regclass('ncollection_product_branding')")
        if not self.env.cr.fetchone()[0]:
            return
        try:
            with self.env.cr.savepoint():
                self._nc_unbrand()
        except Exception as error:                         # noqa: BLE001 - never stop a registry from loading
            # one line, no traceback: an unreadable log line is not worth failing a setup that checks logs for them
            _logger.warning("Could not apply the product wording to module-authored data: %s", error)
