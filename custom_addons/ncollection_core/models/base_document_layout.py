# -*- coding: utf-8 -*-
"""The live preview of Configure Document Layout, for the Workspace Administrator (Option A).

Odoo renders the preview with ``ir.ui.view._render_template``, which reads the layout's view records; ``ir.ui.view`` is
administrator-only, so the Owner's wizard opened and then failed on the preview. Only the RENDER is elevated, and only
for a workspace administrator who is not a system administrator: the wizard must be for one of THEIR companies, and when
the preview is rendered over a document (``active_model`` / ``active_id``, the invoice preview) the document must be one
they can read. Nothing is written by this path; the save still goes through the company delegation."""
from odoo import models
from odoo.exceptions import AccessError


class BaseDocumentLayout(models.TransientModel):
    _inherit = 'base.document.layout'

    def _compute_preview(self):
        user = self.env.user
        if (self.env.su or user.has_group('base.group_system')
                or not user.has_group('ncollection_core.group_tenant_admin')):
            return super()._compute_preview()
        for wizard in self:
            if wizard.company_id and wizard.company_id not in user.company_ids:
                raise AccessError(self.env._("You can only preview the layout of your own company."))
        context = self.env.context
        if context.get('active_model') == 'account.move' and context.get('active_id'):
            document = self.env['account.move'].browse(context['active_id'])
            document.check_access('read')
        return super(BaseDocumentLayout, self.sudo())._compute_preview()
