# -*- coding: utf-8 -*-
"""Messages the SYSTEM writes speak in the system's name, not the vendor's.

* A browser notification for a message with no author falls back to Odoo's own icon file
  (``mail.thread._notify_by_web_push_prepare_payload``); it gets System Branding's app icon instead.
* The system user (``base.partner_root``, renamed "<short name> Bot" by vendor_content.py) posts the onboarding
  messages of Discuss: "Odoo's chat helps employees collaborate efficiently...", "@OdooBot", "Enjoy exploring Odoo!".
  Those strings live in ``mail_bot`` (which this module does not depend on), so the system's name is put in when such
  a message is CREATED: only messages authored by the system user, only the vendor's product name and bot name.
  ``odoo.com`` addresses are lower-case and untouched.
"""
import re

from odoo import api, models

ODOO_PUSH_ICON = '/web/static/img/odoo-icon-192x192.png'


def system_wording(body, name, bot_name):
    """``body`` with the vendor's bot and product names replaced (idempotent: the result names neither)."""
    if not body or 'Odoo' not in body:
        return body
    body = re.sub(r'\bOdooBot\b', bot_name, body)
    return re.sub(r'\bOdoo\b', name, body)


def renamed(body, old_name, name, old_bot_name, bot_name):
    """``body`` after the system was renamed: its previous name and bot name become the new ones."""
    for old, new in ((old_bot_name, bot_name), (old_name, name)):
        if old and new and old != new and old in body:
            body = re.sub(r'(?<!\w)%s(?!\w)' % re.escape(old), new.replace('\\', r'\\'), body)
    return body


class MailThread(models.AbstractModel):
    _inherit = 'mail.thread'

    def _notify_by_web_push_prepare_payload(self, message, msg_vals=False, force_record_name=False):
        payload = super()._notify_by_web_push_prepare_payload(
            message, msg_vals=msg_vals, force_record_name=force_record_name)
        options = payload.get('options') if isinstance(payload, dict) else None
        if isinstance(options, dict) and options.get('icon') == ODOO_PUSH_ICON:
            brand = self.env['ncollection.product.branding'].nc_brand()
            options['icon'] = '/ncollection/brand/app-icon/192?v=%s' % brand['version']
        return payload


class MailMessage(models.Model):
    _inherit = 'mail.message'

    @api.model_create_multi
    def create(self, vals_list):
        root = self.env['ir.model.data']._xmlid_to_res_id('base.partner_root', raise_if_not_found=False)
        system_vals = [vals for vals in vals_list if root and vals.get('author_id') == root and vals.get('body')]
        if system_vals:
            brand = self.env['ncollection.product.branding'].nc_brand()
            bot_name = self.env['ncollection.vendor.content']._nc_bot_name(brand['short_name'])
            for vals in system_vals:
                body = vals['body']             # str or Markup: the same type goes back, so escaping is unchanged
                vals['body'] = type(body)(system_wording(str(body), brand['name'], bot_name))
        return super().create(vals_list)

    @api.model
    def _nc_reword_system_messages(self, old_name=None, old_bot_name=None):
        """Every message the system user wrote, in the system's CURRENT name: the vendor's names, and the system's
        previous name / bot name when it was just renamed (e.g. a tenant receiving the platform's identity). Through
        the ORM, idempotent; returns how many messages changed."""
        root = self.env.ref('base.partner_root', raise_if_not_found=False)
        if not root:
            return 0
        brand = self.env['ncollection.product.branding'].nc_brand()
        bot_name = self.env['ncollection.vendor.content']._nc_bot_name(brand['short_name'])
        terms = [term for term in ('Odoo', old_name, old_bot_name) if term]
        domain = [('author_id', '=', root.id)] + ['|'] * (len(terms) - 1) + [('body', 'ilike', term) for term in terms]
        changed = 0
        for message in self.sudo().search(domain):
            body = message.body
            text = renamed(str(body), old_name, brand['name'], old_bot_name, bot_name)
            text = system_wording(text, brand['name'], bot_name)
            if text != str(body):
                message.write({'body': type(body)(text)})
                changed += 1
        return changed
