# -*- coding: utf-8 -*-
"""Invoice line column order and visibility: a company default plus a per-user override.

One row = one (company, user, list) preference. ``user_id`` empty is the COMPANY DEFAULT (changed only by Accounting
Managers); a row with ``user_id`` is that user's own override. Resolution: user row, else company row, else the view's
own order. Only column NAMES are stored (validated against ``account.move.line``); no code, no layout markup.
The same preference serves customer invoices and vendor bills (one invoice-lines list, one key).
Changes to a company default are tracked in the record's chatter (who, when, old / new).
"""

from odoo import api, fields, models
from odoo.exceptions import AccessError, ValidationError

LIST_KEYS = [('invoice_lines', 'Invoice lines (customer invoices, vendor bills, credit notes)')]


class InvoiceColumnPreference(models.Model):
    _name = 'ncollection.invoice.column.pref'
    _description = 'Invoice line column order and visibility'
    _inherit = ['mail.thread']
    _order = 'company_id, user_id, list_key'

    company_id = fields.Many2one('res.company', required=True, default=lambda self: self.env.company, index=True)
    user_id = fields.Many2one(
        'res.users', string='User', index=True, ondelete='cascade', help="Empty = the company default."
    )
    list_key = fields.Selection(LIST_KEYS, required=True, default='invoice_lines')
    column_order = fields.Char(
        string='Column order', tracking=True, help="Comma-separated field names, left to right."
    )
    column_hidden = fields.Char(string='Hidden columns', tracking=True, help="Comma-separated field names to hide.")
    scope = fields.Selection(
        [('company', 'Company default'), ('user', 'User')], compute='_compute_scope', store=True, tracking=False
    )

    _unique_pref = models.Constraint(
        'unique(company_id, user_id, list_key)', 'There is already a preference for this company, user and list.'
    )

    @api.depends('user_id')
    def _compute_scope(self):
        for rec in self:
            rec.scope = 'user' if rec.user_id else 'company'

    @api.constrains('column_order', 'column_hidden')
    def _check_names(self):
        known = self.env['account.move.line']._fields
        for rec in self:
            for value in (rec.column_order, rec.column_hidden):
                for name in self._split(value):
                    if name not in known:
                        raise ValidationError(self.env._("Unknown invoice line column: %s", name))

    @staticmethod
    def _split(value):
        return [part for part in (value or '').split(',') if part]

    @api.model
    def _clean(self, names):
        out = []
        for name in names or []:
            if isinstance(name, str) and name and ',' not in name and name not in out:
                out.append(name)
        return out

    @api.model
    def nc_get(self, list_key='invoice_lines'):
        """Resolved preference for the current user and company, for the list renderer."""
        domain = [('company_id', '=', self.env.company.id), ('list_key', '=', list_key)]
        mine = self.search(domain + [('user_id', '=', self.env.uid)], limit=1)
        company = self.search(domain + [('user_id', '=', False)], limit=1)
        chosen = mine or company
        return {
            'order': self._split(chosen.column_order),
            'hidden': self._split(chosen.column_hidden),
            'has_hidden': bool(chosen) and chosen.column_hidden is not False,
            'source': 'user' if mine else ('company' if company else 'view'),
            'can_company': self.env.user.has_group('account.group_account_manager'),
        }

    @api.model
    def nc_save(self, list_key, order, hidden, scope='user'):
        """Save the preference. ``scope='company'`` needs the Accounting Manager group (checked here AND by the ACL)."""
        values = {
            'column_order': ','.join(self._clean(order)),
            'column_hidden': ','.join(self._clean(hidden)),
        }
        if scope == 'company':
            if not self.env.user.has_group('account.group_account_manager'):
                raise AccessError(self.env._("Only an Accounting Manager can change the company default."))
            user_id = False
        else:
            user_id = self.env.uid
        domain = [('company_id', '=', self.env.company.id), ('list_key', '=', list_key), ('user_id', '=', user_id)]
        rec = self.search(domain, limit=1)
        if rec:
            rec.write(values)
        else:
            rec = self.create(dict(values, company_id=self.env.company.id, list_key=list_key, user_id=user_id))
        return self.nc_get(list_key)

    @api.model
    def nc_reset(self, list_key='invoice_lines'):
        """Drop the CURRENT USER's override (back to the company default / the view)."""
        self.search(
            [('company_id', '=', self.env.company.id), ('list_key', '=', list_key), ('user_id', '=', self.env.uid)]
        ).unlink()
        return self.nc_get(list_key)
