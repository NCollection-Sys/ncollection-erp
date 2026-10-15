# -*- coding: utf-8 -*-
from markupsafe import Markup, escape

from odoo import api, fields, models
from odoo.exceptions import UserError

from ..models.field_registry import OUTPUT_LANGS
from ..models.print_job import _BLOCKED_PAYMENT_STATES


class ChequePrintWizard(models.TransientModel):
    """Choose the payment, the cheque layout and the printer profile, check it, then print.

    It is the one way to create a print job, from the payment's Print Cheque button or from New in Print Jobs. The
    job is built from the payment and the template as they are NOW; nothing is taken from an earlier job."""
    _name = 'ncollection.cheque.print.wizard'
    _description = 'Print cheque'

    payment_id = fields.Many2one(
        'account.payment', required=True,
        domain="[('payment_type', '=', 'outbound'), ('state', 'not in', ('canceled', 'rejected'))]")
    company_id = fields.Many2one(related='payment_id.company_id')
    # related fields take the label of their SOURCE field ("Name"): the string is what makes this one read "Payee"
    # pylint: disable-next=attribute-string-redundant
    payee = fields.Char(related='payment_id.partner_id.name', string='Payee')
    payment_date = fields.Date(related='payment_id.date', string='Payment date')
    amount = fields.Monetary(related='payment_id.amount', currency_field='currency_id')
    currency_id = fields.Many2one(related='payment_id.currency_id')
    # Only what can really be printed for THIS payment: active, of its company (or shared), and cheque paper in its
    # currency (or currency-neutral).
    template_id = fields.Many2one(
        'ncollection.cheque.template', string='Cheque template',
        domain="[('active', '=', True), '|', ('company_id', '=', False), ('company_id', '=', company_id), "
               "'|', ('currency_id', '=', False), ('currency_id', '=', currency_id)]")
    template_confirmed = fields.Boolean(related='template_id.dimensions_confirmed', string='Dimensions confirmed')
    profile_id = fields.Many2one(
        'ncollection.cheque.printer.profile', string='Printer profile',
        domain="[('active', '=', True), '|', ('company_id', '=', False), ('company_id', '=', company_id)]")
    output_lang = fields.Selection(
        OUTPUT_LANGS, string='Print language', required=True,
        help="The language the cheque is printed in: the month name of the date and an automatic amount in words. "
             "It is chosen here, whatever language the screen is in.")
    cheque_number = fields.Char(help="The number of the physical cheque leaf, if you want it printed or recorded.")
    notice = fields.Html(compute='_compute_notice', sanitize=False)

    @api.model
    def _usable_templates(self, payment):
        """The templates this payment can be printed on."""
        return self.env['ncollection.cheque.template'].search([
            ('active', '=', True), '|', ('company_id', '=', False), ('company_id', '=', payment.company_id.id),
            '|', ('currency_id', '=', False), ('currency_id', '=', payment.currency_id.id)])

    @api.model
    def _pick_template(self, payment):
        """The journal's bank first, else the first usable one."""
        usable = self._usable_templates(payment)
        bank = (payment.journal_id.bank_account_id.bank_id.name or '').strip().lower()
        matching = usable.filtered(lambda t: bank and t.bank_name and bank in t.bank_name.lower())
        return (matching or usable)[:1]

    @api.model
    def default_get(self, fields_list):
        values = super().default_get(fields_list)
        values.setdefault('output_lang', self.env['ncollection.cheque.values'].default_lang())
        payment = self.env['account.payment'].browse(
            values.get('payment_id') or self.env.context.get('default_payment_id'))
        if payment:
            pick = self._pick_template(payment)
            if pick:
                values.setdefault('template_id', pick.id)
                values.setdefault('profile_id', pick.printer_profile_id.id or False)
        return values

    @api.onchange('payment_id')
    def _onchange_payment_id(self):
        """Choosing the payment (New in Print Jobs) fills the template and profile as the payment button would."""
        pick = self._pick_template(self.payment_id) if self.payment_id else self.env['ncollection.cheque.template']
        self.template_id = pick
        self.profile_id = pick.printer_profile_id

    @api.onchange('template_id')
    def _onchange_template_id(self):
        if self.template_id.printer_profile_id:
            self.profile_id = self.template_id.printer_profile_id

    @api.depends('template_id', 'payment_id', 'profile_id')
    def _compute_notice(self):
        for wizard in self:
            notes = []
            template = wizard.template_id
            if wizard.payment_id and not wizard._usable_templates(wizard.payment_id):
                notes.append(self.env._(
                    "No cheque template can be used for this payment. Add one under Cheque Printing > Cheque "
                    "Templates, or unarchive one, for this company and for %s.", wizard.payment_id.currency_id.name))
            if template and not template.dimensions_confirmed:
                notes.append(self.env._(
                    "The size and positions of this template are PROVISIONAL. Print the calibration sheet on plain "
                    "paper, check it against a physical cheque, and confirm the template before printing a real one."))
            Job = self.env['ncollection.cheque.print.job']
            open_jobs = Job._open_jobs(wizard.payment_id) if wizard.payment_id else []
            if open_jobs:
                notes.append(self.env._(
                    "A cheque is already prepared for this payment (%s). Open it to reprint with a reason or to void "
                    "it.",
                    open_jobs[:1].name))
            wizard.notice = Markup('').join(
                Markup('<div class="alert alert-warning mb-2">%s</div>') % escape(note) for note in notes) or False

    def _check_ready(self):
        self.ensure_one()
        if not self.payment_id:
            raise UserError(self.env._("Choose the payment to print a cheque for."))
        if not self.template_id:
            raise UserError(self.env._(
                "Choose a cheque template. If the list is empty, no template fits this payment's company and "
                "currency: add or unarchive one under Cheque Printing > Cheque Templates."))
        if self.payment_id.state in _BLOCKED_PAYMENT_STATES:
            raise UserError(self.env._("A cancelled or rejected payment cannot be paid by cheque."))

    def _preview(self, mode):
        self._check_ready()
        return self.template_id._preview_action(
            mode, payment=self.payment_id.id, profile=self.profile_id.id, cheque_number=self.cheque_number or '',
            lang=self.output_lang)

    def action_preview(self):
        return self._preview('print')

    def action_preview_design(self):
        return self._preview('design')

    def action_calibration(self):
        return self._preview('calibration')

    def action_print(self):
        self._check_ready()
        Job = self.env['ncollection.cheque.print.job']
        job = Job.create_for_payment(
            self.payment_id, self.template_id, self.profile_id, self.cheque_number or '', self.output_lang)
        return job.action_print()
