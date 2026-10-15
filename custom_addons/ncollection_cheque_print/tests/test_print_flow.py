# -*- coding: utf-8 -*-
"""payment -> wizard -> job -> audited PDF: rights, companies, currencies, reprint audit, and the payment left
alone."""
import datetime
import io

from PyPDF2 import PdfReader

from odoo import fields
from odoo.addons.account.tests.common import AccountTestInvoicingCommon
from odoo.exceptions import AccessError, UserError
from odoo.tests import HttpCase, tagged
from odoo.tools import mute_logger


def _page_mm(pdf):
    box = PdfReader(io.BytesIO(pdf)).pages[0].mediabox
    return float(box.width) * 25.4 / 72, float(box.height) * 25.4 / 72


@tagged('post_install', '-at_install')
class TestChequeFlow(AccountTestInvoicingCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.aed = cls.env.ref('base.AED')
        cls.aed.active = True
        cls.company = cls.company_data['company']
        cls.template = cls.env.ref('ncollection_cheque_print.template_mashreq_neobiz')
        cls.partner = cls.env['res.partner'].create({'name': 'Synthetic Payee LLC'})
        cls.accountant = cls._user('chq_acct', 'account.group_account_user')
        cls.readonly = cls._user('chq_ro', 'account.group_account_readonly')
        cls.Job = cls.env['ncollection.cheque.print.job']

    @classmethod
    def _user(cls, login, group):
        return cls.env['res.users'].with_context(no_reset_password=True).create({
            'name': login, 'login': login, 'company_id': cls.company.id, 'company_ids': [(6, 0, cls.company.ids)],
            'group_ids': [(6, 0, [cls.env.ref('base.group_user').id, cls.env.ref(group).id])]})

    def _payment(self, amount=1500.5, currency=None, **kw):
        values = {'payment_type': 'outbound', 'partner_type': 'supplier', 'partner_id': self.partner.id,
                  'amount': amount, 'currency_id': (currency or self.aed).id,
                  'journal_id': self.company_data['default_journal_bank'].id, 'memo': 'SYN-REF-1'}
        values.update(kw)
        return self.env['account.payment'].create(values)

    def _job(self, payment=None, user=None):
        payment = payment or self._payment()
        env_user = user or self.accountant
        return self.Job.with_user(env_user).create_for_payment(payment.with_user(env_user), self.template)

    # ----------------------------------------------------------------------------------------- the happy path
    def test_print_creates_exact_size_pdf_and_leaves_payment_alone(self):
        payment = self._payment()
        before = (payment.state, payment.amount, payment.memo, payment.write_date)
        job = self._job(payment)
        self.assertEqual(job.state, 'draft')
        self.assertEqual(job.amount, 1500.5)
        self.assertIn('Dirham', job.amount_words_en)
        self.assertTrue(job.amount_words_ar)
        action = job.with_user(self.accountant).action_print()
        self.assertIn('/ncollection/cheque/download/', action['url'])
        self.assertEqual((job.state, job.print_count), ('printed', 1))
        pdf, warnings = job._render('print')
        width, height = _page_mm(pdf)
        self.assertAlmostEqual(width, self.template.width_mm, places=1)
        self.assertAlmostEqual(height, self.template.height_mm, places=1)
        self.assertEqual((payment.state, payment.amount, payment.memo, payment.write_date), before)

    def test_snapshot_survives_a_payment_change(self):
        payment = self._payment()
        job = self._job(payment)
        payment.write({'amount': 99.0})
        self.assertEqual(job.amount, 1500.5)
        with self.assertRaises(AccessError):
            job.with_user(self.accountant).write({'amount': 1.0})

    # ----------------------------------------------------------------------------------------- print language
    def test_print_language_is_snapshotted_and_drives_the_date_and_the_words(self):
        from ..models import renderer
        date_field = {'key': 'payment_date', 'date_format': 'dd mmm yyyy'}
        words_field = {'key': 'amount_words', 'words_lang': 'auto'}
        printed = {}
        for lang in ('en', 'ar'):
            payment = self._payment(date=datetime.date(2026, 10, 5))
            job = self.Job.with_user(self.accountant).create_for_payment(
                payment.with_user(self.accountant), self.template, lang=lang)
            self.assertEqual(job.output_lang, lang)
            raw = job._raw()
            printed[lang] = (renderer.field_text(date_field, raw), renderer.field_text(words_field, raw))
        self.assertEqual(printed['en'][0], '05 Oct 2026')
        self.assertEqual(printed['ar'][0], '05 أكتوبر 2026')
        self.assertIn('Dirham', printed['en'][1])
        self.assertIn('درهم', printed['ar'][1])

    def test_print_language_defaults_safely_and_is_part_of_the_snapshot(self):
        job = self.Job.with_user(self.accountant).create_for_payment(
            self._payment().with_user(self.accountant), self.template, lang='klingon')
        self.assertEqual(job.output_lang, 'en')                      # an unknown language never reaches the PDF
        with self.assertRaises(AccessError):
            job.with_user(self.accountant).write({'output_lang': 'ar'})

    def test_wizard_offers_the_users_language_and_carries_the_choice(self):
        self.env['res.lang']._activate_lang('ar_001')
        payment = self._payment()
        Wizard = self.env['ncollection.cheque.print.wizard'].with_user(self.accountant)
        arabic_user = Wizard.with_context(lang='ar_001', default_payment_id=payment.id)
        self.assertEqual(arabic_user.default_get(['output_lang'])['output_lang'], 'ar')
        english_user = Wizard.with_context(lang='en_US', default_payment_id=payment.id)
        self.assertEqual(english_user.default_get(['output_lang'])['output_lang'], 'en')
        # the choice made in the wizard beats the language of the screen
        wizard = english_user.create({'payment_id': payment.id, 'template_id': self.template.id,
                                      'output_lang': 'ar'})
        self.assertIn('lang=ar', wizard.action_preview()['url'])
        wizard.action_print()
        self.assertEqual(self.Job.search([('payment_id', '=', payment.id)]).output_lang, 'ar')

    def test_preview_labels_fall_back_when_the_print_language_is_not_installed(self):
        """A database without Arabic must still preview, calibrate and design: the labels use the user's language."""
        from unittest.mock import patch
        from ..models import renderer
        Lang = type(self.env['res.lang'])
        with patch.object(Lang, 'get_installed', return_value=[('en_US', 'English (US)')]):
            labels = self.env['ncollection.cheque.values'].with_context(lang='en_US').labels('ar')
        self.assertEqual(labels, renderer.DEFAULT_LABELS)

    def test_preview_labels_are_translatable_and_match_the_renderer_defaults(self):
        from ..models import renderer
        values = self.env['ncollection.cheque.values']
        self.assertEqual(values.labels('en'), renderer.DEFAULT_LABELS)       # the source strings cannot drift apart
        self.assertEqual(set(values.labels('ar')), set(renderer.DEFAULT_LABELS))

    # ----------------------------------------------------------------------------------------- reprint, void, cancel
    def test_reprint_needs_reason_and_is_audited(self):
        job = self._job()
        accountant_job = job.with_user(self.accountant)
        with self.assertRaises(UserError):
            accountant_job.action_reprint('Because')            # not printed yet
        accountant_job.action_print()
        with self.assertRaises(UserError):
            accountant_job.action_print()                          # the second print is a reprint, not a print
        with self.assertRaises(UserError):
            accountant_job.action_reprint('   ')
        with self.assertRaises(UserError):
            accountant_job.action_reprint('x')
        accountant_job.action_reprint('Paper jam on the first sheet')
        self.assertEqual((job.state, job.print_count), ('reprinted', 2))
        events = job.event_ids.sorted('id')
        self.assertEqual(events.mapped('kind'), ['created', 'print', 'reprint'])
        reprint = events[-1]
        self.assertEqual(reprint.reason, 'Paper jam on the first sheet')
        self.assertEqual(reprint.user_id, self.accountant)
        self.assertTrue(reprint.at and reprint.pdf_sha256)

    def test_events_are_append_only(self):
        job = self._job()
        job.with_user(self.accountant).action_print()
        event = job.event_ids[-1]
        with self.assertRaises(UserError):
            event.with_user(self.accountant).write({'reason': 'tamper'})
        with self.assertRaises(UserError):
            event.with_user(self.accountant).unlink()
        with self.assertRaises(UserError):
            job.with_user(self.accountant).unlink()

    def test_lifecycle_cannot_be_written_directly(self):
        job = self._job()
        now = fields.Datetime.now()
        for vals in ({'state': 'printed'}, {'print_count': 7}, {'name': 'CHQ-FAKE'}, {'printed_at': now}):
            with self.assertRaises(AccessError):
                job.with_user(self.accountant).write(vals)

    def test_void_and_cancel(self):
        job = self._job()
        accountant_job = job.with_user(self.accountant)
        with self.assertRaises(UserError):
            accountant_job.action_void('Spoiled cheque')            # a draft is cancelled, not voided
        accountant_job.action_print()
        accountant_job.action_void('Cheque spoiled on feeder')
        self.assertEqual(job.state, 'void')
        with self.assertRaises(UserError):
            accountant_job.action_reprint('Try again please')
        with self.assertRaises(UserError):
            accountant_job.action_cancel()
        second = self._job(self._payment())
        second.with_user(self.accountant).action_cancel()
        self.assertEqual(second.state, 'cancelled')
        # a voided cheque frees the payment for a new one
        self.assertTrue(self._job(job.payment_id))

    def test_no_second_cheque_for_a_payment_silently(self):
        payment = self._payment()
        self._job(payment)
        with self.assertRaises(UserError):
            self._job(payment)

    # ----------------------------------------------------------------------------------------- rights
    def test_readonly_user_cannot_print(self):
        payment = self._payment()
        job = self._job(payment)
        with self.assertRaises(AccessError):
            job.with_user(self.readonly).action_print()
        fresh = self._payment()
        with self.assertRaises(AccessError):
            self.Job.with_user(self.readonly).create_for_payment(fresh.with_user(self.readonly), self.template)

    def test_readonly_user_cannot_edit_templates(self):
        field = self.template.field_ids[:1]
        with self.assertRaises(AccessError):
            field.with_user(self.readonly).write({'x_mm': 1.0})
        with self.assertRaises(AccessError):
            self.template.with_user(self.readonly).write({'name': 'x'})
        self.assertTrue(self.template.with_user(self.readonly).field_ids)        # but may read

    # ----------------------------------------------------------------------------------------- payment rules
    def test_currency_mismatch_is_refused(self):
        usd = self.env.ref('base.USD')
        with self.assertRaises(UserError):
            self._job(self._payment(currency=usd))

    def test_inbound_cancelled_and_zero_payments_are_refused(self):
        with self.assertRaises(UserError):
            self._job(self._payment(payment_type='inbound', partner_type='customer'))
        cancelled = self._payment()
        cancelled.action_cancel()
        with self.assertRaises(UserError):
            self._job(cancelled)
        with self.assertRaises(UserError):
            self._job(self._payment(amount=0.0))

    def test_archived_template_is_refused(self):
        archived = self.template.copy({'name': 'Archived', 'active': False})
        with self.assertRaises(UserError):
            self.Job.create_for_payment(self._payment(), archived)

    def test_multi_company_isolation(self):
        company_b = self.setup_other_company()['company']
        other = self.env['ncollection.cheque.template'].create(
            {'name': 'Company B only', 'company_id': company_b.id, 'width_mm': 190, 'height_mm': 90})
        visible = self.env['ncollection.cheque.template'].with_user(self.accountant).search([])
        self.assertNotIn(other, visible)
        self.assertIn(self.template, visible, "A template without a company is shared")
        with self.assertRaises(UserError):
            self.Job.create_for_payment(self._payment(), other)

    def test_wizard_picks_a_usable_template_and_previews(self):
        payment = self._payment()
        wizard = self.env['ncollection.cheque.print.wizard'].with_user(self.accountant).with_context(
            default_payment_id=payment.id).create({'payment_id': payment.id, 'template_id': self.template.id})
        self.assertIn('PROVISIONAL', wizard.notice)
        self.assertEqual(wizard.action_preview()['type'], 'ir.actions.act_url')
        action = wizard.action_print()
        self.assertEqual(self.Job.search_count([('payment_id', '=', payment.id)]), 1)
        self.assertTrue(action)

    def test_payment_button_opens_the_wizard(self):
        action = self._payment().with_user(self.accountant).action_nc_print_cheque()
        self.assertEqual(action['res_model'], 'ncollection.cheque.print.wizard')


@tagged('post_install', '-at_install')
class TestChequeRoutes(HttpCase):

    def setUp(self):
        super().setUp()
        self.template = self.env.ref('ncollection_cheque_print.template_adcb')

    # ------------------------------------------------------------------ the PDF response itself
    def _url(self, mode, extra=''):
        return '/ncollection/cheque/preview?template=%d&mode=%s%s' % (self.template.id, mode, extra)

    def test_every_pdf_is_a_well_formed_file_response(self):
        """Design preview, print preview and calibration sheet, each as the file a viewer or a download manager gets:
        a valid PDF, the right type, an exact length, ranges advertised, never cached, opened inline."""
        self.authenticate('admin', 'admin')
        for mode in ('design', 'print', 'calibration'):
            response = self.url_open(self._url(mode))
            body = response.content
            self.assertEqual(response.status_code, 200, mode)
            self.assertEqual(response.headers['Content-Type'], 'application/pdf', mode)
            self.assertTrue(body.startswith(b'%PDF-'), mode)
            self.assertIn(b'%%EOF', body[-32:], "%s: truncated PDF" % mode)
            self.assertEqual(int(response.headers['Content-Length']), len(body), mode)
            self.assertEqual(response.headers['Accept-Ranges'], 'bytes', mode)
            self.assertTrue(response.headers['Content-Disposition'].startswith('inline;'), mode)
            self.assertIn('cheque-%s-%d.pdf' % (mode, self.template.id), response.headers['Content-Disposition'])
            self.assertIn('no-store', response.headers['Cache-Control'], "a cheque PDF must never be cached")
            self.assertEqual(response.headers['X-Content-Type-Options'], 'nosniff')
            self.assertNotIn(b'<html', body[:512].lower(), "the body is the PDF, never a wrapper page")
            width, height = _page_mm(body)
            self.assertAlmostEqual(width, self.template.width_mm, places=1, msg=mode)    # exact physical size,
            self.assertAlmostEqual(height, self.template.height_mm, places=1, msg=mode)  # not A4 / Letter

    def test_download_is_an_attachment_with_the_same_bytes(self):
        self.authenticate('admin', 'admin')
        inline = self.url_open(self._url('calibration'))
        saved = self.url_open(self._url('calibration', '&download=1'))
        self.assertEqual(saved.status_code, 200)
        self.assertEqual(saved.headers['Content-Type'], 'application/pdf')
        self.assertTrue(saved.headers['Content-Disposition'].startswith('attachment;'))
        self.assertEqual(saved.content, inline.content, "the download is the same PDF, not a different rendering")

    def test_a_download_manager_can_probe_and_resume(self):
        """HEAD and Range are what IDM-style tools send before and during a download."""
        self.authenticate('admin', 'admin')
        full = self.url_open(self._url('print')).content
        head = self.opener.head(self.base_url() + self._url('print'), timeout=30)
        self.assertEqual(head.status_code, 200)
        self.assertEqual(head.headers['Content-Type'], 'application/pdf')
        self.assertEqual(int(head.headers['Content-Length']), len(full))
        self.assertEqual(head.content, b'')
        part = self.url_open(self._url('print'), headers={'Range': 'bytes=0-99'})
        self.assertEqual(part.status_code, 206)
        self.assertEqual(part.content, full[:100])
        self.assertEqual(part.headers['Content-Range'], 'bytes 0-99/%d' % len(full))
        tail = self.url_open(self._url('print'), headers={'Range': 'bytes=%d-' % (len(full) - 50)})
        self.assertEqual(tail.status_code, 206)
        self.assertEqual(tail.content, full[-50:])
        open_ended = self.url_open(self._url('print'), headers={'Range': 'bytes=0-'})
        self.assertIn(open_ended.status_code, (200, 206))
        self.assertEqual(open_ended.content, full)

    def test_an_anonymous_request_never_receives_a_pdf(self):
        """Isolation: the same URL without the session is the login page, not the document."""
        self.authenticate(None, None)
        response = self.url_open(self._url('calibration'), allow_redirects=False)
        self.assertIn(response.status_code, (303, 401, 403))
        self.assertNotEqual(response.headers.get('Content-Type'), 'application/pdf')

    def test_preview_and_calibration_routes_return_exact_size_pdfs(self):
        self.authenticate('admin', 'admin')
        for mode in ('print', 'design', 'calibration'):
            response = self.url_open('/ncollection/cheque/preview?template=%d&mode=%s' % (self.template.id, mode))
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.headers['Content-Type'], 'application/pdf')
            width, height = _page_mm(response.content)
            self.assertAlmostEqual(width, self.template.width_mm, places=1)
            self.assertAlmostEqual(height, self.template.height_mm, places=1)

    def test_preview_follows_the_requested_print_language(self):
        self.env['res.lang']._activate_lang('ar_001')
        self.authenticate('admin', 'admin')
        admin_lang = 'ar' if (self.env.ref('base.user_admin').lang or '').startswith('ar') else 'en'
        for lang in ('ar', 'en', 'zz'):          # an unknown language falls back to the user's own
            response = self.url_open('/ncollection/cheque/preview?template=%d&mode=calibration&lang=%s'
                                     % (self.template.id, lang))
            self.assertEqual(response.status_code, 200, lang)
            expected = {'ar': 'ar', 'en': 'en', 'zz': admin_lang}[lang]
            self.assertEqual(b'DejaVu' in response.content, expected == 'ar', lang)   # Arabic labels need that font

    def test_preview_requires_login_and_valid_input(self):
        url = '/ncollection/cheque/preview?template=%d' % self.template.id
        response = self.url_open(url, allow_redirects=False)
        self.assertIn(response.status_code, (303, 401, 403))
        self.authenticate('admin', 'admin')
        evil = '/ncollection/cheque/preview?template=%d&mode=evil' % self.template.id
        self.assertEqual(self.url_open(evil).status_code, 400)
        self.assertEqual(self.url_open('/ncollection/cheque/preview?template=999999').status_code, 404)

    def test_download_belongs_to_the_printer_and_expires(self):
        aed = self.env.ref('base.AED')
        aed.active = True
        company = self.env.company
        bank = self.env['account.journal'].search([('type', '=', 'bank'), ('company_id', '=', company.id)], limit=1)
        bank = bank or self.env['account.journal'].create({'name': 'Smoke', 'type': 'bank', 'code': 'SMK9'})
        tpl = self.env.ref('ncollection_cheque_print.template_mashreq_neobiz')
        payment = self.env['account.payment'].create({
            'payment_type': 'outbound', 'partner_type': 'supplier', 'amount': 10.0, 'currency_id': aed.id,
            'partner_id': self.env['res.partner'].create({'name': 'Synthetic'}).id, 'journal_id': bank.id})
        admin = self.env.ref('base.user_admin')
        Job = self.env['ncollection.cheque.print.job'].with_user(admin)
        job = Job.create_for_payment(payment.with_user(admin), tpl)
        action = job.action_print()
        event = job.event_ids.filtered(lambda e: e.kind == 'print')
        self.assertEqual(action['url'], '/ncollection/cheque/download/%d?download=1' % event.id)
        self.assertEqual(action['target'], 'self')
        self.authenticate('admin', 'admin')
        response = self.url_open(action['url'])
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers['Content-Type'], 'application/pdf')
        # an old event has expired
        with mute_logger('odoo.sql_db'):
            self.env.cr.execute(
                """UPDATE ncollection_cheque_print_event SET "at" = "at" - interval '2 hours' WHERE id = %s""",
                (event.id,))
        event.invalidate_recordset()
        self.authenticate('admin', 'admin')
        self.assertEqual(self.url_open(action['url']).status_code, 403)
