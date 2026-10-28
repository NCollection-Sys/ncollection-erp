# -*- coding: utf-8 -*-
"""A document whose e-invoice has been submitted is final: it can no longer be reset to draft or cancelled.

The correction is a credit note or a new invoice (MD 243 Art 6(2)). Until submission exists (``locked`` is set when a
document leaves ``not_submitted``) nothing here changes how accounting behaves; the e-invoice is rebuilt from the books
and ``action_generate_xml`` refuses a stale payload."""

from odoo import models
from odoo.exceptions import UserError


class AccountMove(models.Model):
    _inherit = 'account.move'

    def _nc_refuse_if_einvoice_locked(self):
        if self.ids and self.env['ncollection.einvoice'].sudo().search_count(
            [('move_id', 'in', self.ids), ('locked', '=', True)]
        ):
            raise UserError(
                self.env._(
                    "This document has been submitted as a UAE e-invoice and is final. Issue a credit note or a new "
                    "invoice instead."
                )
            )

    def button_draft(self):
        self._nc_refuse_if_einvoice_locked()
        return super().button_draft()

    def button_cancel(self):
        self._nc_refuse_if_einvoice_locked()
        return super().button_cancel()
