# -*- coding: utf-8 -*-
"""Self-billing agreements: the ONLY thing that lets this company (the buyer) issue its supplier's e-invoice.

Guidelines V2.0 §10.3: "Subject to an agreement between the supplier and the buyer, the buyer may issue the Electronic
Invoice on behalf of the supplier"; self-billing applies only for VAT purposes and "is not available for suppliers who
are not registered for VAT"; it "will require the buyer to be on the Electronic Invoicing System".

So a self-billed e-invoice is never the result of a checkbox. A manager records the agreement (supplier, its reference,
the period it covers) and ACTIVATES it; activation is refused for a supplier without a valid TRN. Each preparation of a
self-billed document then asks :meth:`_nc_for` whether exactly one active agreement covers the transaction date, and
lib/readiness.py blocks the document otherwise. An agreement is never deleted (a self-billed document references it);
it is terminated, with the date and the person recorded.
"""

from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

MANAGER_GROUP = 'ncollection_uae_einvoicing.group_einvoice_manager'


class SelfBillingAgreement(models.Model):
    _name = 'ncollection.einvoice.self.billing.agreement'
    _description = 'UAE self-billing agreement'
    _order = 'date_from desc, id desc'

    name = fields.Char(string='Agreement reference', required=True)
    company_id = fields.Many2one(
        'res.company', required=True, index=True, default=lambda self: self.env.company, ondelete='restrict'
    )
    partner_id = fields.Many2one(
        'res.partner',
        string='Partner',
        required=True,
        index=True,
        ondelete='restrict',
        domain="[('is_company', '=', True)]",
        help="The supplier this company self-bills (Issue), or the customer that self-bills this company (Receive).",
    )
    direction = fields.Selection(
        [
            ('issue', "We issue our supplier's documents (we are the buyer)"),
            ('receive', "Our customer issues our documents (we are the supplier)"),
        ],
        default='issue',
        required=True,
        help="Issue: this company self-bills the partner (outbound 389 / 261). Receive: the partner self-bills this "
        "company, and its documents arrive as received e-invoices that become DRAFT customer invoices.",
    )
    date_from = fields.Date(string='Valid from', required=True)
    date_to = fields.Date(string='Valid until', help="Empty: open-ended until terminated.")
    state = fields.Selection(
        [('draft', 'Draft'), ('active', 'Active'), ('terminated', 'Terminated')],
        default='draft',
        required=True,
        readonly=True,
        copy=False,
    )
    activated_by_id = fields.Many2one('res.users', readonly=True, copy=False)
    activated_at = fields.Datetime(readonly=True, copy=False)
    terminated_by_id = fields.Many2one('res.users', readonly=True, copy=False)
    terminated_at = fields.Datetime(readonly=True, copy=False)
    note = fields.Text()

    _period_check = models.Constraint(
        'CHECK(date_to IS NULL OR date_to >= date_from)', 'An agreement cannot end before it starts.'
    )

    # ------------------------------------------------------------------ guards (mirrored in the ACL: manager-only)
    def _nc_require_manager(self):
        if not self.env.su and not self.env.user.has_group(MANAGER_GROUP):
            raise AccessError(self.env._("Only an e-invoicing manager can manage self-billing agreements."))

    @api.model_create_multi
    def create(self, vals_list):
        self._nc_require_manager()
        for vals in vals_list:
            if not self.env.su and {'state', 'activated_by_id', 'activated_at', 'terminated_by_id',
                                    'terminated_at'} & set(vals):
                raise UserError(self.env._("An agreement is created as a draft and activated with its button."))
        return super().create(vals_list)

    def write(self, vals):
        self._nc_require_manager()
        if not self.env.su:
            if {'state', 'activated_by_id', 'activated_at', 'terminated_by_id', 'terminated_at'} & set(vals):
                raise UserError(self.env._("The agreement's state changes only through its buttons."))
            if any(record.state != 'draft' for record in self):
                raise UserError(
                    self.env._("An active or terminated agreement cannot be changed; terminate it, record a new one.")
                )
        return super().write(vals)

    @api.ondelete(at_uninstall=False)
    def _nc_never_delete(self):
        if any(record.state != 'draft' for record in self):
            raise UserError(self.env._("An agreement that was active is evidence and is never deleted."))

    @api.constrains('partner_id', 'company_id')
    def _check_partner(self):
        for record in self:
            if record.partner_id != record.partner_id.commercial_partner_id:
                raise ValidationError(self.env._("A self-billing agreement is made with the supplier company itself."))
            if record.partner_id.company_id and record.partner_id.company_id != record.company_id:
                raise ValidationError(self.env._("The supplier belongs to another company."))
            if record.partner_id == record.company_id.partner_id:
                raise ValidationError(self.env._("A company cannot self-bill itself."))

    # ------------------------------------------------------------------ lifecycle
    def action_activate(self):
        self._nc_require_manager()
        for record in self:
            if record.state != 'draft':
                raise UserError(self.env._("Only a draft agreement can be activated."))
            # the SUPPLIER of the self-billed documents must be VAT-registered: the partner when we issue, this company
            # when the customer issues for us
            supplier = record.partner_id if record.direction == 'issue' else record.company_id.partner_id
            if supplier._nc_uae_identity_states()['trn'].state != 'valid':
                raise UserError(
                    self.env._(
                        "Self-billing is not available for a supplier that is not registered for VAT: %s has no "
                        "valid TRN (Guidelines V2.0 §10.3).",
                        supplier.display_name,
                    )
                )
            overlapping = self.search(
                [
                    ('id', '!=', record.id),
                    ('company_id', '=', record.company_id.id),
                    ('partner_id', '=', record.partner_id.id),
                    ('direction', '=', record.direction),
                    ('state', '=', 'active'),
                ]
            ).filtered(lambda other: _overlaps(other, record))
            if overlapping:
                raise UserError(
                    self.env._("Another active agreement with this supplier covers part of the same period.")
                )
            record.sudo().write(
                {'state': 'active', 'activated_by_id': self.env.uid, 'activated_at': fields.Datetime.now()}
            )
        return True

    def action_terminate(self):
        self._nc_require_manager()
        for record in self:
            if record.state != 'active':
                raise UserError(self.env._("Only an active agreement can be terminated."))
            record.sudo().write(
                {'state': 'terminated', 'terminated_by_id': self.env.uid, 'terminated_at': fields.Datetime.now()}
            )
        return True

    # ------------------------------------------------------------------ the one question the engine asks
    @api.model
    def _nc_for(self, company, supplier, on_date, direction='issue'):
        """``{'state', 'agreement', 'any'}`` for ``supplier`` of ``company`` on ``on_date`` (None: any date).

        ``state`` is ``valid`` (exactly one active agreement covers the date), ``missing``, ``not_yet``, ``expired`` or
        ``ambiguous``; ``any`` says whether ANY active agreement exists (the creation gate)."""
        supplier = supplier.commercial_partner_id
        active = self.sudo().search(
            [
                ('company_id', '=', company.id),
                ('partner_id', '=', supplier.id),
                ('direction', '=', direction),
                ('state', '=', 'active'),
            ]
        )
        result = {'any': bool(active), 'agreement': self.browse(), 'state': 'missing'}
        if not active or on_date is None:
            result['state'] = 'valid' if len(active) == 1 else ('missing' if not active else 'ambiguous')
            result['agreement'] = active if len(active) == 1 else self.browse()
            return result
        covering = active.filtered(lambda a: a.date_from <= on_date and (not a.date_to or on_date <= a.date_to))
        if len(covering) == 1:
            result.update(state='valid', agreement=covering)
        elif len(covering) > 1:
            result['state'] = 'ambiguous'
        elif all(a.date_from > on_date for a in active):
            result['state'] = 'not_yet'
        else:
            result['state'] = 'expired'
        return result


def _overlaps(first, second):
    far = fields.Date.from_string('9999-12-31')
    return first.date_from <= (second.date_to or far) and second.date_from <= (first.date_to or far)
