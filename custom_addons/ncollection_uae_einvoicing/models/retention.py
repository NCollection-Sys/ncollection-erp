# -*- coding: utf-8 -*-
"""Retention-aware archive: legal holds, retention rules as DATA, and a fail-closed ``retain_until``.

NOTHING IS PURGED AND NO PERIOD IS HARD-CODED. Retention depends on who the Person is, the tax period and on extensions
(an FTA dispute, a tax audit, an audit notification, a voluntary disclosure) whose primary legal text (Cabinet Decision
74/2023 Art 3) has not been read by this project: only the Guidelines' summary of it (V1.1 §5.4). So the periods are
not code: they are rows of ``ncollection.einvoice.retention.rule``, each with its citation and a ``confirmed`` flag
that starts False. A record is purgeable only when a CONFIRMED rule matches its class, an anchor date is set, no hold
is open, and every closed hold's extension has run: otherwise ``retain_until`` is empty and the answer is "not
purgeable". This phase implements no purge at all; the archive can only grow.

TENANT OFFBOARDING. :meth:`ncollection.einvoice._nc_archive_status` answers, with COUNTS ONLY and no content, whether
the tenant holds regulated records that may not be destroyed yet. The platform's deactivation / deletion flows are
meant to call it and refuse; wiring that into the platform needs an explicit decision (see the specification §18), so
it is exposed here and not
yet consumed."""

from dateutil.relativedelta import relativedelta

from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError

from .guards import without_unsafe_defaults

HOLD_TYPES = [
    ('fta_dispute', 'FTA dispute'),
    ('tax_audit', 'Tax audit'),
    ('audit_notification', 'FTA audit notification'),
    ('voluntary_disclosure', 'Voluntary disclosure'),
    ('other', 'Other legal hold'),
]
_EXTENSION_FIELD = {
    'fta_dispute': 'ext_dispute_years',
    'tax_audit': 'ext_audit_years',
    'audit_notification': 'ext_notification_years',
    'voluntary_disclosure': 'ext_disclosure_years',
}


class EInvoiceRetentionRule(models.Model):
    _name = 'ncollection.einvoice.retention.rule'
    _description = 'UAE e-invoice retention rule (data, with its citation)'
    _order = 'retention_class, effective_from desc'

    retention_class = fields.Selection(
        [
            ('taxable_person', 'Taxable Person'),
            ('other_person', 'Other Person'),
            ('real_estate', 'Real estate record'),
        ],
        required=True,
    )
    base_years = fields.Integer(required=True)
    base_anchor = fields.Selection(
        [('tax_period_end', 'End of the Tax Period'), ('calendar_year_end', 'End of the calendar year')], required=True
    )
    ext_dispute_years = fields.Integer(string='Extension: FTA dispute (years)')
    ext_audit_years = fields.Integer(string='Extension: tax audit (years)')
    ext_notification_years = fields.Integer(string='Extension: audit notification (years)')
    ext_disclosure_years = fields.Integer(string='Extension: voluntary disclosure (years)')
    source_citation = fields.Char(required=True, help="The primary legal text this row is taken from.")
    effective_from = fields.Date(required=True)
    confirmed = fields.Boolean(
        default=False,
        help="A rule only counts once someone has checked it against the primary text. "
        "Unconfirmed rows never make a record purgeable.",
    )
    company_id = fields.Many2one('res.company', default=lambda self: self.env.company)

    def _retain_until_for(self, einvoice):
        """The earliest date ``einvoice`` may be purged, or False for "not purgeable"."""
        if einvoice.retention_class == 'unclassified' or not einvoice.retention_anchor_date:
            return False
        # the e-invoice's OWN company's rule, else a shared one (never another company's), most recent first; a
        # company-specific rule wins over a shared one with the same date
        rule = self.search(
            [
                ('retention_class', '=', einvoice.retention_class),
                ('confirmed', '=', True),
                ('effective_from', '<=', einvoice.retention_anchor_date),
                '|',
                ('company_id', '=', False),
                ('company_id', '=', einvoice.company_id.id),
            ],
            order='effective_from desc, company_id',
            limit=1,
        )
        if not rule:
            return False
        holds = self.env['ncollection.einvoice.hold'].search(
            [
                ('company_id', '=', einvoice.company_id.id),
                '|',
                ('einvoice_ids', '=', False),
                ('einvoice_ids', 'in', einvoice.ids),
            ]
        )
        until = einvoice.retention_anchor_date + relativedelta(years=rule.base_years)
        for hold in holds:
            if not hold.closed_on:
                return False  # an open hold: indefinite
            years = rule[_EXTENSION_FIELD[hold.hold_type]] if hold.hold_type in _EXTENSION_FIELD else 0
            until = max(until, hold.closed_on + relativedelta(years=years))
        return until


class EInvoiceHold(models.Model):
    _name = 'ncollection.einvoice.hold'
    _description = 'UAE e-invoice legal hold'
    _order = 'opened_on desc'

    company_id = fields.Many2one('res.company', required=True, default=lambda self: self.env.company, index=True)
    hold_type = fields.Selection(HOLD_TYPES, required=True)
    reference = fields.Char(required=True, help="The FTA reference or case number.")
    reason = fields.Text()
    opened_on = fields.Date(required=True, default=fields.Date.context_today)
    closed_on = fields.Date()
    opened_by = fields.Many2one('res.users', default=lambda self: self.env.user, readonly=True)
    closed_by = fields.Many2one('res.users', readonly=True)
    einvoice_ids = fields.Many2many(
        'ncollection.einvoice', string='Applies to', help="Empty = every e-invoice of the company."
    )

    @api.model_create_multi
    def create(self, vals_list):
        creator = self
        if not self.env.su:
            creator = without_unsafe_defaults(
                self, {'company_id', 'hold_type', 'reference', 'reason', 'opened_on', 'einvoice_ids'}
            )
            for vals in vals_list:
                if {'closed_on', 'closed_by', 'opened_by'} & set(vals):
                    raise UserError(
                        self.env._(
                            "A legal hold is opened open and the system records who opened it; close it afterwards."
                        )
                    )
                vals['opened_by'] = self.env.user.id
        return super(EInvoiceHold, creator).create(vals_list)

    @api.constrains('opened_on', 'closed_on')
    def _check_closing_is_not_before_opening(self):
        for hold in self:
            if hold.closed_on and hold.closed_on < hold.opened_on:
                raise ValidationError(self.env._("A legal hold cannot be closed before it was opened."))

    def write(self, vals):
        """A hold is evidence. Once closed it is final; while open only its closing date and reason may change, and
        closing it records who did."""
        keys = set(vals)
        if not self.env.su:
            if keys - {'closed_on', 'reason'}:
                raise UserError(self.env._("A legal hold can only be closed (or its reason edited), never rescoped."))
            if any(hold.closed_on for hold in self):
                raise UserError(self.env._("A closed legal hold is final."))
        if vals.get('closed_on'):
            vals = dict(vals, closed_by=self.env.user.id)
        return super().write(vals)

    @api.ondelete(at_uninstall=False)
    def _nc_hold_is_part_of_the_record(self):
        raise UserError(self.env._("A legal hold is part of the record: close it, do not delete it."))


class EInvoiceArchive(models.Model):
    _inherit = 'ncollection.einvoice'

    def _nc_archive_status(self):
        """Counts only (no content, no identifiers): what the tenant holds and whether any of it may be destroyed
        yet."""
        companies = self.env.companies.ids
        # regulated = every record that HAS an archived file, including one whose XML was superseded and is not
        # regenerated yet: the files stay in the archive, so the record still counts
        archived = self.env['ir.attachment'].sudo().search([('res_model', '=', self._name)])
        regulated = self.sudo().search([('id', 'in', archived.mapped('res_id')), ('company_id', 'in', companies)])
        retain = [d for d in regulated.mapped('retain_until') if d]
        today = fields.Date.context_today(self)
        return {
            'regulated_records': len(regulated),
            'archive_files': len(archived.filtered(lambda attachment: attachment.res_id in regulated.ids)),
            'purge_eligible': len([r for r in regulated if r.retain_until and r.retain_until < today]),
            'unclassified_or_unconfirmed': len([r for r in regulated if not r.retain_until]),
            'earliest_retain_until': min(retain).isoformat() if retain else False,
            'open_holds': self.env['ncollection.einvoice.hold']
            .sudo()
            .search_count([('closed_on', '=', False), ('company_id', 'in', companies)]),
        }
