# -*- coding: utf-8 -*-
"""Platform terminology on the Odoo PYTHON strings that a method RETURNS (five of them).

Odoo returns ``_()`` text verbatim in English (odoo/tools/translate.py ``get_translation``: ``if lang == 'en_US':
translation = source``), so no translation can reword it. These are reworded by extending the method that
produces them: Odoo's own method runs first, then the platform's ACTIVE terminology rules for the user's language
(ncollection.label.rule, the one source) are applied to its text result. Wording only: no value, key or record changes.
Only methods that RETURN the text are extended; a string raised in an error, posted inside a method or built in
JavaScript is left as Odoo wrote it (intercepting those would mean catching exceptions or messages by their text).
Every other code string is listed in docs/markdown/LABELS_AND_TERMINOLOGY.md with where it appears.
"""
from odoo import api, models


def _reword(env, text):
    if not text:
        return text
    lang = env.lang or 'en_US'
    rules = env['ncollection.label.rule'].sudo().search([('lang', '=', lang)])
    replace = rules._replacer() if rules else None
    return replace(text) if replace else text


class AccountJournal(models.Model):
    _inherit = 'account.journal'

    @api.depends('type')
    def _compute_name_placeholder(self):
        """The placeholder of a new purchase journal ("Vendor Bills")."""
        result = super()._compute_name_placeholder()
        for journal in self:
            journal.name_placeholder = _reword(self.env, journal.name_placeholder)
        return result


class AccountMove(models.Model):
    _inherit = 'account.move'

    def _get_move_display_name(self, show_ref=False):
        """The name of a draft supplier refund ("Draft Vendor Credit Note"), in lists, breadcrumbs and links."""
        return _reword(self.env, super()._get_move_display_name(show_ref=show_ref))


class AccountMoveCreation(models.Model):
    _inherit = 'account.move'

    def _creation_message(self):
        """The chatter line of a new supplier bill ("Vendor Bill Created")."""
        return _reword(self.env, super()._creation_message())


class AccountAnalyticAccount(models.Model):
    _inherit = 'account.analytic.account'

    def action_view_vendor_bill(self):
        """The title of the analytic account's bills list ("Vendor Bills")."""
        action = super().action_view_vendor_bill()
        if isinstance(action, dict) and action.get('name'):
            action['name'] = _reword(self.env, action['name'])
        return action


class ProductSupplierinfo(models.Model):
    _inherit = 'product.supplierinfo'

    @api.model
    def get_import_templates(self):
        """The import template label ("Import Template for Vendor Pricelists")."""
        templates = super().get_import_templates()
        for template in templates:
            if template.get('label'):
                template['label'] = _reword(self.env, template['label'])
        return templates
