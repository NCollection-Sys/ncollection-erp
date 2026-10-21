# -*- coding: utf-8 -*-
"""Customer / Vendor tags for a partner, read from the accounting classification Odoo already keeps.

The source of truth is Odoo's own ``customer_rank`` / ``supplier_rank`` (raised when a customer document or a
vendor document is first posted for the partner, or set by a module): a partner with a rank above zero IS that
kind of partner, and one with both IS both. Nothing here stores a second classification, infers a role from a name
or an address, or changes a partner. A contact is classified by its commercial partner too (the company it belongs
to), because that is who invoices are about.

Two presentations of the same facts:

* ``nc_is_customer`` / ``nc_is_vendor``: plain computed booleans a view can show as badges (the document form);
* the partner autocomplete (dropdown) line, through Odoo's own formatted display name (``odoomark``: a back-tick
  pair is a badge). It is added ONLY to that formatted name, never to ``display_name`` as stored or exported, so
  no list, report or search sees a different name; and ONLY where the document opted in (the
  ``nc_partner_roles`` context key, see ``nc.partner.roles.mixin``), never in CRM, Helpdesk or contacts.

The labels are translatable terms, so they follow the language of the user.
"""
from odoo import api, fields, models
from odoo.addons.ncollection_core.models.partner_roles_mixin import NC_PARTNER_ROLES_CTX


class ResPartner(models.Model):
    _inherit = 'res.partner'

    nc_is_customer = fields.Boolean(compute='_compute_nc_roles', string='Is a customer')
    nc_is_vendor = fields.Boolean(compute='_compute_nc_roles', string='Is a vendor')

    @api.depends('customer_rank', 'supplier_rank', 'commercial_partner_id.customer_rank',
                 'commercial_partner_id.supplier_rank')
    def _compute_nc_roles(self):
        for partner in self:
            commercial = partner.commercial_partner_id or partner
            partner.nc_is_customer = (partner.customer_rank or 0) > 0 or (commercial.customer_rank or 0) > 0
            partner.nc_is_vendor = (partner.supplier_rank or 0) > 0 or (commercial.supplier_rank or 0) > 0

    def _nc_role_labels(self):
        """The badge labels of this partner, in the user's language."""
        self.ensure_one()
        labels = []
        if self.nc_is_customer:
            labels.append(self.env._("Customer"))
        if self.nc_is_vendor:
            labels.append(self.env._("Vendor"))
        return labels

    @api.depends('customer_rank', 'supplier_rank', 'commercial_partner_id.customer_rank',
                 'commercial_partner_id.supplier_rank')
    @api.depends_context(NC_PARTNER_ROLES_CTX)
    def _compute_display_name(self):
        super()._compute_display_name()
        if not (self.env.context.get('formatted_display_name') and self.env.context.get(NC_PARTNER_ROLES_CTX)):
            # the stored / exported name is never touched, and the badges appear only in the dropdowns of the
            # documents that opt in (nc.partner.roles.mixin: invoices / bills, payments, sales and purchase orders)
            return
        for partner in self:
            labels = partner._nc_role_labels()
            if labels:
                # odoomark: `text` renders as a badge. The labels are fixed translated words (no user input).
                tags = ' '.join('`%s`' % label for label in labels)
                partner.display_name = '%s \t %s' % (partner.display_name, tags)
