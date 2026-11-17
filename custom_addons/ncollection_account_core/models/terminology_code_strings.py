# -*- coding: utf-8 -*-
"""Platform terminology on the two Odoo PYTHON strings users meet most often.

Odoo returns ``_()`` text verbatim in English (odoo/tools/translate.py ``get_translation``: ``if lang == 'en_US':
translation = source``), so no translation can reword it. These two are reworded by extending the method that
produces them: Odoo's own method runs first, then the platform's ACTIVE terminology rules for the user's language
(ncollection.label.rule, the one source) are applied to its text result. Wording only: no value, key or record changes.
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
