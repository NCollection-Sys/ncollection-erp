# -*- coding: utf-8 -*-
"""Per-company e-invoicing configuration, the provider record, and the two explicit maps (tax, payment means).

Nothing here is seeded with a guess: the tax map and the payment map start EMPTY and are filled by an administrator,
because
Odoo's own data cannot say what a ``0% EXT`` tax or a ``manual`` payment method means in PINT-AE terms."""

from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError

from ..lib import codelists, spec
from .guards import without_unsafe_defaults

SERVICE_GROUP = 'ncollection_uae_einvoicing.group_einvoice_service'


def _codelist_selection(name):
    def selection(self):
        rows = codelists.load(name).rows
        return [(code, '%s — %s' % (code, row.get('name', ''))) for code, row in sorted(rows.items())]

    return selection


class EInvoiceConfig(models.Model):
    _name = 'ncollection.einvoice.config'
    _description = 'UAE e-invoicing configuration (per company)'
    _rec_name = 'company_id'

    company_id = fields.Many2one(
        'res.company', required=True, default=lambda self: self.env.company, index=True, ondelete='cascade'
    )
    enabled = fields.Boolean(
        default=False,
        help="Switches e-invoicing on for THIS company. Installing the module never enables it: an administrator "
        "does, "
        "company by company.",
    )
    spec_version = fields.Char(
        string='PINT-AE release',
        compute='_compute_spec_version',
        help="The pinned official release every e-invoice of this company is built and validated "
        "against. It is stored on each e-invoice.",
    )
    default_payment_means_code = fields.Selection(
        _codelist_selection('payment_means'),
        string='Default payment means',
        help="Used only when neither the document nor a payment-method map says otherwise. There is no built-in "
        "default: "
        "an e-invoice with no payment means configured is blocked, not guessed.",
    )
    default_bank_account_id = fields.Many2one(
        'res.partner.bank',
        string='Payee bank account',
        domain="[('partner_id', '=', company_partner_id)]",
        help="The account written as the payee account of a credit transfer (payment means 30).",
    )
    company_partner_id = fields.Many2one(related='company_id.partner_id')
    effective_date = fields.Date(
        string='E-invoicing effective date',
        help="The date the UAE e-invoicing system became effective for this company (its mandatory implementation "
        "date under MD 244/2025 as amended by MR 66/2026, or its voluntary start). Used only by the 24-month airline "
        "air-waybill exclusion of MD 243/2025 Art 4(1)(d); never assumed when empty.",
    )
    vat_issue_days = fields.Integer(
        string='VAT Law issuance period (days)',
        help="MD 243/2025 Art 6(4): a Registrant issues within the timeline of the VAT Law. That period has not been "
        "confirmed from the VAT Decree-Law by this project (OCQ-23), so it is configuration: empty means the VAT-Law "
        "deadline is not computed. The 14-Business-Day deadline of Art 6(5) is computed regardless.",
    )

    @api.constrains('vat_issue_days')
    def _check_vat_issue_days(self):
        for config in self:
            if config.vat_issue_days < 0:
                raise ValidationError(self.env._("The VAT Law issuance period cannot be negative."))

    @api.constrains('default_bank_account_id', 'company_id')
    def _check_bank_account_is_the_companys(self):
        for config in self:
            account = config.default_bank_account_id
            if account and account.partner_id != config.company_id.partner_id:
                raise ValidationError(self.env._("The payee bank account must belong to the company."))

    _company_unique = models.Constraint(
        'unique(company_id)', 'There is already an e-invoicing configuration for this company.'
    )

    def _compute_spec_version(self):
        for config in self:
            config.spec_version = spec.get_profile().key


class EInvoiceHoliday(models.Model):
    """An official holiday of the Federal Government: not a Business Day (MD 243/2025 Art 1, "Business Day").

    Holidays are announced every year and several follow the lunar calendar, so they are data, maintained by an
    e-invoicing manager, never a list in code. A company-less row applies to every company of the workspace."""

    _name = 'ncollection.einvoice.holiday'
    _description = 'UAE official holiday (e-invoicing business days)'
    _order = 'date'

    date = fields.Date(required=True, index=True)
    name = fields.Char(required=True)
    company_id = fields.Many2one(
        'res.company', index=True, ondelete='cascade', help="Empty: the holiday applies to every company."
    )

    _date_company_unique = models.Constraint('unique(date, company_id)', 'This holiday is already in the calendar.')

    @api.model
    def _nc_dates(self, company):
        """The holiday dates that apply to ``company``."""
        rows = self.sudo().search(['|', ('company_id', '=', False), ('company_id', '=', company.id)])
        return frozenset(rows.mapped('date'))


class EInvoiceProvider(models.Model):
    """The ASP, as the engine knows it: a name, the adapter that talks to it, and what the ASP reported back.

    The core defines NO adapter and NO transport: ``adapter`` is a selection extended by a provider module
    (``ncollection_uae_einvoicing_<asp>``). Participant registration is the ASP's act, so ``asp_participant_id`` is a
    network
    field written from the adapter's result, never typed or derived here."""

    _name = 'ncollection.einvoice.provider'
    _description = 'UAE e-invoicing provider (ASP)'

    name = fields.Char(required=True)
    company_id = fields.Many2one(
        'res.company', required=True, default=lambda self: self.env.company, index=True, ondelete='cascade'
    )
    adapter = fields.Selection([], help="Provided by an ncollection_uae_einvoicing_<asp> module.")
    asp_participant_id = fields.Char(string='Participant ID (registered by the ASP)', readonly=True, copy=False)
    state = fields.Selection(
        [('draft', 'Not configured'), ('configured', 'Configured'), ('verified', 'Credentials verified')],
        default='draft',
        readonly=True,
        copy=False,
    )

    _company_unique = models.Constraint(
        'unique(company_id)', 'A company has one ASP: it sends and receives through the same provider.'
    )

    @api.model_create_multi
    def create(self, vals_list):
        creator = self
        if not self.env.su:
            creator = without_unsafe_defaults(self, {'name', 'company_id', 'adapter'})
            for vals in vals_list:
                if {'asp_participant_id', 'state'} & set(vals) and not self.env.user.has_group(SERVICE_GROUP):
                    raise UserError(
                        self.env._("Participant and credential state are written by the provider adapter.")
                    )
        return super(EInvoiceProvider, creator).create(vals_list)

    def write(self, vals):
        if not self.env.su and {'asp_participant_id', 'state'} & set(vals):
            if not self.env.user.has_group(SERVICE_GROUP):
                raise UserError(self.env._("Participant and credential state are written by the provider adapter."))
        return super().write(vals)


class EInvoiceTaxMap(models.Model):
    _name = 'ncollection.einvoice.tax.map'
    _description = 'Odoo tax → PINT-AE VAT category'
    _order = 'company_id, tax_id'

    company_id = fields.Many2one('res.company', required=True, default=lambda self: self.env.company, index=True)
    tax_id = fields.Many2one(
        'account.tax', required=True, domain="[('company_id', '=', company_id)]", ondelete='cascade'
    )
    pint_category = fields.Selection(
        [
            ('S', 'S — Standard rate'),
            ('Z', 'Z — Zero rated'),
            ('E', 'E — Exempt'),
            ('O', 'O — Not subject to VAT'),
            ('AE', 'AE — Reverse charge'),
            ('N', 'N — Standard rate additional VAT (margin scheme)'),
        ],
        required=True,
    )
    exemption_code = fields.Char(help="Required for category E (the reason the supply is exempt).")
    rcm_goods_type = fields.Selection(
        _codelist_selection('rcm_goods_type'), string='Reverse-charge goods type', help="Required for category AE."
    )
    pint_rate = fields.Float(
        string='Rate shown for reverse charge',
        digits=(16, 2),
        help="Reverse charge is booked at 0% in Odoo but PINT-AE shows the rate on the line. Used only for category "
        "AE.",
    )

    _tax_unique = models.Constraint('unique(company_id, tax_id)', 'This tax is already mapped.')

    @api.constrains('pint_category', 'exemption_code', 'rcm_goods_type', 'pint_rate')
    def _check_category_requirements(self):
        for record in self:
            if record.pint_category not in spec.get_profile().tax_categories:
                raise ValidationError(self.env._("'%s' is not a valid PINT-AE VAT category.", record.pint_category))
            if record.pint_category == 'E' and not record.exemption_code:
                raise ValidationError(self.env._("An exempt tax needs its exemption reason."))
            if record.pint_category == 'AE' and not (record.rcm_goods_type and record.pint_rate > 0):
                raise ValidationError(
                    self.env._("A reverse-charge tax needs its goods type and the rate shown on the line.")
                )

    @api.constrains('company_id', 'tax_id')
    def _check_company(self):
        for record in self:
            if record.tax_id.company_id and record.tax_id.company_id != record.company_id:
                raise ValidationError(self.env._("The tax belongs to another company."))


class EInvoicePaymentMap(models.Model):
    _name = 'ncollection.einvoice.payment.map'
    _description = 'Journal → PINT-AE payment means'
    _order = 'company_id, journal_id'

    company_id = fields.Many2one('res.company', required=True, default=lambda self: self.env.company, index=True)
    journal_id = fields.Many2one(
        'account.journal', required=True, domain="[('company_id', '=', company_id)]", ondelete='cascade'
    )
    pint_code = fields.Selection(_codelist_selection('payment_means'), string='Payment means', required=True)

    _journal_unique = models.Constraint('unique(company_id, journal_id)', 'This journal is already mapped.')

    @api.constrains('company_id', 'journal_id')
    def _check_company(self):
        for record in self:
            if record.journal_id.company_id != record.company_id:
                raise ValidationError(self.env._("The journal belongs to another company."))
