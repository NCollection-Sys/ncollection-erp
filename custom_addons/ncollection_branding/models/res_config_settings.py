# -*- coding: utf-8 -*-
from odoo import api, fields, models

# Odoo Community renders every Enterprise-only option of every app's Settings with one of these widgets; clicking
# it opens the "Odoo Enterprise - Get this feature and much more - Upgrade now" dialog. 45 options in 13 apps
# (account, purchase, sale, stock, mrp, point_of_sale, hr, ...) measured on 19.0.
UPSELL_WIDGETS = ('upgrade_boolean', 'upgrade_selection')


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    @api.model
    def _get_view(self, view_id=None, view_type='form', **options):
        """Close the Enterprise upsell surface of Settings, for every module at once: a setting whose only control
        is an upsell widget is hidden (its block too, once nothing in it is left), so neither the "Enterprise"
        badge nor the upgrade dialog is reachable. Nothing is faked or enabled: the field stays False and keeps its
        meaning (the module it would install is not available). The web client half (static/src/js/
        no_enterprise_upsell.js) makes the two widgets inert wherever another view still uses them."""
        arch, view = super()._get_view(view_id, view_type, **options)
        if view_type != 'form':
            return arch, view
        xpath = ' | '.join("//field[@widget='%s']" % widget for widget in UPSELL_WIDGETS)
        for field in arch.xpath(xpath):
            holder = next((node for node in field.iterancestors() if node.tag == 'setting'), None)
            target = holder if holder is not None and not holder.xpath(
                ".//field[not(@widget='upgrade_boolean') and not(@widget='upgrade_selection')"
                " and not(@invisible='1') and not(@invisible='True')]") else field
            target.set('invisible', '1')
        for block in arch.xpath('//block'):
            settings = block.xpath('./setting')
            if settings and all(node.get('invisible') == '1' for node in settings):
                block.set('invisible', '1')
        return arch, view

    nc_product_name = fields.Char(compute='_compute_nc_product_name')

    def _compute_nc_product_name(self):
        name = self.env['ncollection.product.branding'].nc_brand()['name']
        for settings in self:
            settings.nc_product_name = name

    # Odoo's Languages block shows only a COUNT ("2 Languages") and two links,
    # so a user cannot tell WHICH languages are installed without opening the
    # list. This is a read-only view of the real, active res.lang records — the
    # same set the preferences dialog offers — not a second language registry.
    nc_active_language_ids = fields.Many2many(
        'res.lang', string='Installed languages',
        compute='_compute_nc_active_language_ids', readonly=True)

    @api.depends_context('lang')
    def _compute_nc_active_language_ids(self):
        langs = self.env['res.lang'].search([('active', '=', True)])
        for settings in self:
            settings.nc_active_language_ids = langs
