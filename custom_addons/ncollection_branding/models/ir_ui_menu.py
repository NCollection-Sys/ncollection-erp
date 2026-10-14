# -*- coding: utf-8 -*-
from odoo import api, models


class IrUiMenu(models.Model):
    _inherit = 'ir.ui.menu'

    @api.model
    def _nc_set_menu_translation(self, xmlid, lang, value):
        """Write one menu's translated name, but only when that language is installed.

        For data files that must CHANGE a name a database already holds in another language: loading a catalogue never
        overwrites an existing translation, so the catalogue alone cannot rename a card on a database that was
        installed before. ``update_field_translations`` raises for a language that is not activated, which would fail
        the install of a fresh database (and the CI test database) that never loaded it, hence the guard. Idempotent.
        Lives in ncollection_branding because both layers (platform and tenant) depend on it."""
        if lang not in {code for code, _name in self.env['res.lang'].get_installed()}:
            return False
        menu = self.env.ref(xmlid, raise_if_not_found=False)
        if menu:
            menu.update_field_translations('name', {lang: value})
        return bool(menu)
