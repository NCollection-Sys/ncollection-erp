# -*- coding: utf-8 -*-
"""The workflow a person actually follows: Print Jobs > New (or the payment's button) > payment > template > Print >
Reprint. Synthetic data only."""
import io

from PyPDF2 import PdfReader

from odoo.addons.account.tests.common import AccountTestInvoicingCommon
from odoo.exceptions import UserError
from odoo.tests import Form, tagged


def _page_mm(pdf):
    box = PdfReader(io.BytesIO(pdf)).pages[0].mediabox
    return float(box.width) * 25.4 / 72, float(box.height) * 25.4 / 72


@tagged('post_install', '-at_install')
class TestChequePrintWorkflow(AccountTestInvoicingCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.aed = cls.env.ref('base.AED')
        cls.aed.active = True
        cls.company = cls.company_data['company']
        cls.partner = cls.env['res.partner'].create({'name': 'Synthetic Payee LLC'})
        cls.accountant = cls.env['res.users'].with_context(no_reset_password=True).create({
            'name': 'wf_acct', 'login': 'wf_acct', 'company_id': cls.company.id,
            'company_ids': [(6, 0, cls.company.ids)],
            'group_ids': [(6, 0, [cls.env.ref('base.group_user').id,
                                  cls.env.ref('account.group_account_user').id])]})
        base = cls.env.ref('ncollection_cheque_print.template_mashreq_neobiz')
        cls.template_a = base.copy({'name': 'Synthetic A', 'width_mm': 210.0, 'height_mm': 100.0})
        cls.template_b = base.copy({'name': 'Synthetic B', 'width_mm': 205.0, 'height_mm': 96.0})
        cls.payment = cls.env['account.payment'].create({
            'payment_type': 'outbound', 'partner_type': 'supplier', 'partner_id': cls.partner.id,
            'amount': 2750.25, 'currency_id': cls.aed.id, 'memo': 'WF-REF-9',
            'journal_id': cls.company_data['default_journal_bank'].id})

    def _wizard_from_print_jobs(self, payment=None):
        """New in Print Jobs: no default payment; the person picks it."""
        wizard = Form(self.env['ncollection.cheque.print.wizard'].with_user(self.accountant))
        wizard.payment_id = (payment or self.payment).with_user(self.accountant)
        return wizard

    # ------------------------------------------------------------------ New in Print Jobs
    def test_choosing_the_payment_fills_the_cheque(self):
        form = self._wizard_from_print_jobs()
        self.assertTrue(form.template_id, "a usable template is proposed once the payment is chosen")
        self.assertEqual(form.payee, 'Synthetic Payee LLC')
        self.assertEqual(form.payment_date, self.payment.date)
        self.assertEqual(form.amount, 2750.25)
        self.assertEqual(form.currency_id, self.aed)

    def test_the_templates_offered_are_only_the_usable_ones(self):
        Wizard = self.env['ncollection.cheque.print.wizard']
        self.assertIn(self.template_a, Wizard._usable_templates(self.payment))
        self.template_b.active = False
        self.assertNotIn(self.template_b, Wizard._usable_templates(self.payment))
        usd = self.env.ref('base.USD')
        self.template_a.currency_id = usd
        self.assertNotIn(self.template_a, Wizard._usable_templates(self.payment), "wrong cheque-paper currency")

    def test_the_chosen_template_is_the_one_printed(self):
        form = self._wizard_from_print_jobs()
        form.template_id = self.template_b
        form.save().action_print()
        job = self.env["ncollection.cheque.print.job"].search([("payment_id", "=", self.payment.id)])
        self.assertEqual(job.template_id, self.template_b, "never a silent fallback to another template")
        self.assertEqual(job.payee, 'Synthetic Payee LLC')
        self.assertEqual(job.date, self.payment.date)
        self.assertEqual(job.amount, 2750.25)
        self.assertEqual(job.currency_id, self.aed)
        self.assertEqual(job.reference, 'WF-REF-9')
        self.assertTrue(job.amount_words_en and job.amount_words_ar)

    def test_no_usable_template_gives_a_clear_message_and_no_job(self):
        self.env['ncollection.cheque.template'].search([]).write({'active': False})
        wizard = self.env['ncollection.cheque.print.wizard'].with_user(self.accountant).create({
            'payment_id': self.payment.id, 'output_lang': 'en'})
        self.assertIn('No cheque template can be used', wizard.notice)
        with self.assertRaisesRegex(UserError, 'Choose a cheque template'):
            wizard.action_print()
        self.assertFalse(self.env['ncollection.cheque.print.job'].search([('payment_id', '=', self.payment.id)]))

    # ---------------------------------------------------------- the confirmation is the template's CURRENT state
    def test_confirming_a_template_is_seen_by_the_job_and_the_wizard(self):
        template = self.template_a
        self.assertFalse(template.dimensions_confirmed)
        wizard = self.env['ncollection.cheque.print.wizard'].with_user(self.accountant).create({
            'payment_id': self.payment.id, 'template_id': template.id, 'output_lang': 'en'})
        self.assertIn('PROVISIONAL', wizard.notice)
        job = self.env['ncollection.cheque.print.job'].with_user(self.accountant).create_for_payment(
            self.payment.with_user(self.accountant), template)
        self.assertFalse(job.template_confirmed_now)
        template.action_confirm_dimensions()
        job.invalidate_recordset()
        wizard.invalidate_recordset()
        self.assertTrue(job.template_confirmed_now, "the job reads the template as it is now, not a stale copy")
        self.assertFalse(wizard.notice and 'PROVISIONAL' in wizard.notice)
        self.assertFalse(job.template_confirmed, "what was true when it was prepared stays in the snapshot")

    # ------------------------------------------------------------------ Print, then reprint
    def test_print_then_reprint_moves_the_state_and_the_audit(self):
        job = self.env['ncollection.cheque.print.job'].with_user(self.accountant).create_for_payment(
            self.payment.with_user(self.accountant), self.template_a)
        self.assertEqual((job.state, job.print_count), ('draft', 0))
        action = job.action_print()
        self.assertEqual(action['target'], 'self')
        self.assertTrue(action['url'].endswith('?download=1'))
        self.assertEqual((job.state, job.print_count), ('printed', 1))
        self.assertTrue(job.printed_at and job.last_printed_at)
        self.assertEqual(job.printed_at, job.last_printed_at)
        with self.assertRaises(UserError):
            job.action_reprint('no')                                   # a reason is required
        self.assertEqual((job.state, job.print_count), ('printed', 1))
        job.action_reprint('Paper jam on the first sheet')
        self.assertEqual((job.state, job.print_count), ('reprinted', 2))
        self.assertGreaterEqual(job.last_printed_at, job.printed_at)
        kinds = job.event_ids.sorted('id').mapped('kind')
        self.assertEqual(kinds, ['created', 'print', 'reprint'])
        self.assertEqual(job.event_ids.filtered(lambda e: e.kind == 'reprint').reason,
                         'Paper jam on the first sheet')

    # ------------------------------------------------------------------ dimensions are the saved ones
    def test_each_templates_saved_dimensions_set_its_pdf_page_size(self):
        Job = self.env['ncollection.cheque.print.job'].with_user(self.accountant)
        pay_b = self.payment.copy({'memo': 'WF-REF-B'})
        job_a = Job.create_for_payment(self.payment.with_user(self.accountant), self.template_a)
        job_b = Job.create_for_payment(pay_b.with_user(self.accountant), self.template_b)
        width_a, height_a = _page_mm(job_a._render('print')[0])
        width_b, height_b = _page_mm(job_b._render('print')[0])
        self.assertAlmostEqual(width_a, 210.0, places=1)
        self.assertAlmostEqual(height_a, 100.0, places=1)
        self.assertAlmostEqual(width_b, 205.0, places=1)
        self.assertAlmostEqual(height_b, 96.0, places=1)
        # change A: its canvas spec and its PDF follow; B does not move
        self.template_a.write({'width_mm': 230.0, 'height_mm': 110.0})
        self.assertEqual((self.template_a._spec()['width_mm'], self.template_a._spec()['height_mm']), (230.0, 110.0))
        width_a2, height_a2 = _page_mm(job_a._render('print')[0])
        self.assertAlmostEqual(width_a2, 230.0, places=1)
        self.assertAlmostEqual(height_a2, 110.0, places=1)
        width_b2, height_b2 = _page_mm(job_b._render('print')[0])
        self.assertAlmostEqual((width_b2, height_b2), (width_b, height_b), places=1)

    def test_a_payment_without_a_payee_is_refused_with_an_actionable_message(self):
        payment = self.payment.copy({'partner_id': False})
        with self.assertRaisesRegex(UserError, 'no payee to print'):
            self.env['ncollection.cheque.print.job'].with_user(self.accountant).create_for_payment(
                payment.with_user(self.accountant), self.template_a)
