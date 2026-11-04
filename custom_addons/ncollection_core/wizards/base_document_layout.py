# -*- coding: utf-8 -*-
"""Configure Document Layout for the Workspace Administrator (Option A).

Odoo's wizard reads layout VIEW records (``ir.ui.view``: the layout's key, its template for the live preview), and
``ir.ui.view`` is administrator-only, so the Owner's wizard opened and then failed. Only the READS the wizard makes
on its own are elevated, and only for a workspace administrator who is not a system administrator:

* the wizard must be for one of THEIR companies;
* when the preview is rendered over a document (``active_model`` / ``active_id``, the invoice preview) the document
  must be one they can read.

Nothing is written by this path; the save still goes through the company delegation (own company, allowlisted fields,
an installed layout only), and every other model keeps its own rules."""
from odoo import api, models
from odoo.exceptions import AccessError


class BaseDocumentLayout(models.TransientModel):
    _inherit = 'base.document.layout'

    def _nc_reader(self):
        """``self``, or its superuser twin for a workspace administrator after the guards above."""
        user = self.env.user
        if (self.env.su or user.has_group('base.group_system')
                or not user.has_group('ncollection_core.group_tenant_admin')):
            return self
        for wizard in self:
            if wizard.company_id and wizard.company_id not in user.company_ids:
                raise AccessError(self.env._("You can only configure the layout of your own company."))
        context = self.env.context
        if context.get('active_model') == 'account.move' and context.get('active_id'):
            self.env['account.move'].browse(context['active_id']).check_access('read')
        return self.sudo()

    def _compute_preview(self):
        return super(BaseDocumentLayout, self._nc_reader())._compute_preview()

    @api.onchange('company_id')
    def _onchange_company_id(self):
        return super(BaseDocumentLayout, self._nc_reader())._onchange_company_id()

    @api.onchange('report_layout_id')
    def _onchange_report_layout_id(self):
        return super(BaseDocumentLayout, self._nc_reader())._onchange_report_layout_id()
