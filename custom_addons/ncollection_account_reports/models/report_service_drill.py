# -*- coding: utf-8 -*-
"""The drill-down entry point of the report service, in its own file (report_service.py is past its size limit)."""
from odoo import api, models
from odoo.exceptions import AccessError, UserError

_MAX_DRILL_ACCOUNTS = 500


class NcollectionReportServiceDrill(models.AbstractModel):
    _inherit = 'ncollection.report.service'

    @api.model
    def nc_report_drill(self, model, wizard_id, account_id=False, partner_id=False, label='', account_ids=None):
        """The journal items behind one row of the run the user is looking at, as an action.

        The same decision as the transient line's ``action_drill_down`` button, reachable from the OWL viewer and
        the Summary: the wizard must be an engine wizard the caller may read, and the returned domain is the
        report's own (company, posted/draft, period, filters) scoped to the row. The action is a plain
        ``account.move.line`` list, so the user's ACL and record rules apply when it opens: nothing is
        revealed that the list itself would not show.
        """
        Model = self._nc_engine_model(model)
        wizard = Model.browse(self._nc_int(wizard_id)).exists()
        if not wizard:
            raise UserError(self.env._("The report run expired — apply the filters again."))
        wizard.check_access("read")          # exists() skips record rules; see nc_report_detail
        if wizard.company_id not in self.env.user.company_ids:
            raise AccessError(self.env._("You are not allowed to report on this company."))
        requested = list(account_ids or [])
        if len(requested) > _MAX_DRILL_ACCOUNTS:
            raise UserError(self.env._("This row covers too many accounts to open at once."))
        ids = [self._nc_int(i) for i in requested] + ([self._nc_int(account_id)] if account_id else [])
        account = self.env['account.account'].browse(ids).exists() if ids else None
        partner = self.env['res.partner'].browse(self._nc_int(partner_id)).exists() if partner_id else None
        if not account and not partner:                 # (a recordset of several accounts is a valid scope)
            raise UserError(self.env._("This row has no journal items to open."))
        return {
            'type': 'ir.actions.act_window',
            'name': self.env._("Journal Items — %s", str(label or '')[:120]),
            'res_model': 'account.move.line',
            'view_mode': 'list,form',
            'views': [(False, 'list'), (False, 'form')],
            'domain': wizard._nc_drill_domain(account=account, partner=partner),
            'target': 'current',
        }

    @api.model
    def _nc_int(self, value):
        try:
            return int(value)
        except (TypeError, ValueError):
            raise UserError(self.env._("This request is not valid."))
