# -*- coding: utf-8 -*-
"""Where a partner's Customer / Vendor badges may appear in the partner dropdown: an explicit opt-in per document.

The badges themselves are computed from the partner's own classification (``ncollection_account_core``,
``res.partner``) and drawn ONLY when the autocomplete runs with :data:`NC_PARTNER_ROLES_CTX` in its context. A
document model opts in by inheriting this mixin: its form view's ``partner_id`` fields then carry that key, merged
into the context Odoo already gives them (nothing upstream is replaced). Any other partner field (CRM, Helpdesk,
Project, contacts...) never carries it, so it shows the plain name.
"""
from odoo import api, models

NC_PARTNER_ROLES_CTX = 'nc_partner_roles'


def _with_roles_key(context):
    """``context`` (a field's context expression) with the opt-in key added, or None when it is an expression this
    does not rewrite (anything but a dict literal)."""
    context = (context or '').strip()
    if not context:
        return "{'%s': True}" % NC_PARTNER_ROLES_CTX
    if not (context.startswith('{') and context.endswith('}')):
        return None
    inner = context[1:-1].strip().rstrip(',').strip()
    return "{%s%s'%s': True}" % (inner, ', ' if inner else '', NC_PARTNER_ROLES_CTX)


class NcPartnerRolesMixin(models.AbstractModel):
    _name = 'nc.partner.roles.mixin'
    _description = 'Show the partner Customer / Vendor badges on this document'

    @api.model
    def _get_view(self, view_id=None, view_type='form', **options):
        arch, view = super()._get_view(view_id, view_type, **options)
        if view_type == 'form':
            for node in arch.xpath("//field[@name='partner_id']"):
                if NC_PARTNER_ROLES_CTX in (node.get('context') or ''):
                    continue
                context = _with_roles_key(node.get('context'))
                if context:
                    node.set('context', context)
        return arch, view
