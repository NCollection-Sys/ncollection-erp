# -*- coding: utf-8 -*-
"""View and Print are ONE document: same report, same context, same data, same stamp.

The View route (``/ncollection/document/view/account.move/<id>``, ncollection_core) and Print (the report action's
own ``/report/pdf/...`` route with the action's context) are requested over HTTP as the same user and compared as REAL
PDFs (the report engine answers with HTML while Odoo's tests run, so both routes get Odoo's own
``force_report_rendering`` switch for the length of the request, as the other real-PDF tests do). The comparison is on
what a reader gets -- the page count, each page's size and its text, and where the stamp is drawn -- never on raw bytes
or headers, which differ naturally between two renders (creation date, document id).

What this prevents, in the future: View showing another invoice than Print, View using another report or another
context, View adding or dropping the stamp, View rendering a pro-forma where Print does not.
"""
import io
import json
from unittest.mock import patch
from urllib.parse import quote

from odoo import Command
from odoo.addons.ncollection_branding.tests import pdf_probe
from odoo.tests import HttpCase, tagged
from odoo.tools import mute_logger
from odoo.tools.pdf import PdfFileReader

from .test_document_stamp import _Common


def _canonical(pdf):
    """What a reader gets: [(width mm, height mm, text)] per page, plus where the stamps are drawn."""
    reader = PdfFileReader(io.BytesIO(pdf))
    pages = []
    for number in range(reader.getNumPages()):
        page = reader.getPage(number)
        box = page.mediaBox
        text = page.extract_text() if hasattr(page, 'extract_text') else page.extractText()
        pages.append((round(float(box.getWidth()) * 25.4 / 72, 1), round(float(box.getHeight()) * 25.4 / 72, 1),
                      ' '.join((text or '').split())))
    return pages


@tagged('post_install', '-at_install')
class TestViewIsPrintOnTheRealPdf(HttpCase, _Common):

    # The FIRST PDF rendered in a given language can come out without its page footer ("Page 1 / 1"), whichever route
    # asked for it: measured, the first View lacked it, the first Print had it, and every later render of both had it.
    # That is the report engine's first-render behaviour, not a difference between View and Print, so one throwaway
    # render per language comes first and the comparisons are made between renders that are in the same state.
    _warmed_up = set()

    def _as_admin_in(self, company):
        self.env.ref('base.user_admin').write({'company_ids': [Command.link(company.id)]})
        self.env.flush_all()
        self.authenticate('admin', 'admin')

    def _both(self, move):
        """(View response, Print response) for ``move``: the very routes a user's two buttons reach, as real PDFs."""
        # the action is built the way the web client gets it: as the user, with the user's own context (language...)
        admin = self.env.ref('base.user_admin')
        as_user = move.with_user(admin).with_context(**admin.context_get())
        action = as_user.action_print_pdf()
        action = action.get('context', {}).get('report_action') or action
        print_url = '/report/pdf/%s/%d?context=%s' % (
            action['report_name'], move.id, quote(json.dumps(action['context'])))
        Report = type(self.env['ir.actions.report'])
        render = Report._render_qweb_pdf

        def render_for_real(report, report_ref, res_ids=None, data=None):
            return render(report.with_context(force_report_rendering=True), report_ref, res_ids=res_ids, data=data)

        # A real PDF is rendered by wkhtmltopdf, which fetches the report's stylesheets and fonts from this very
        # server; those are read-only routes and, in an HttpCase, they run on the TEST's own transaction and leave it
        # read-only. The ORM writes the render makes meanwhile then fail their cache-version bump (core logs it,
        # swallows it and carries on), and Odoo's read-only /report/pdf route logs its "retrying with a read/write
        # cursor" warning for the pending write_date that stock Odoo leaves too: test-transaction artefacts, not
        # something a deployed server does, and noise the CI log gate must not see. Only those loggers are muted,
        # only around the two requests; a render that really failed fails the assertions below.
        noise = ('odoo.addons.ncollection_core.models.aggregation.version', 'odoo.sql_db', 'odoo.http')
        with patch.object(Report, '_render_qweb_pdf', render_for_real), mute_logger(*noise):
            if admin.lang not in TestViewIsPrintOnTheRealPdf._warmed_up:
                self.env.flush_all()
                self.url_open(move._nc_view_url())
                TestViewIsPrintOnTheRealPdf._warmed_up.add(admin.lang)
            self.env.flush_all()
            viewed = self.url_open(move._nc_view_url())
            self.env.flush_all()
            printed = self.url_open(print_url)
        for response in (viewed, printed):
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.content.startswith(b'%PDF-'), 'a real PDF, not the HTML test mode answers with')
        return viewed, printed

    def _assert_same_document(self, move):
        viewed, printed = self._both(move)
        self.assertEqual(_canonical(viewed.content), _canonical(printed.content), 'same pages, same sizes, same text')
        view_stamps = [len(found) for found in pdf_probe.placements(viewed.content)]
        print_stamps = [len(found) for found in pdf_probe.placements(printed.content)]
        self.assertEqual(view_stamps, print_stamps, 'the stamp is drawn the same way on both')
        return viewed, printed, view_stamps

    def test_a_posted_invoice_reads_the_same_in_view_and_in_print(self):
        move = self.init_invoice('out_invoice', post=True, amounts=[100.0], invoice_date='2026-03-01')
        self._as_admin_in(move.company_id)
        viewed, _printed, _stamps = self._assert_same_document(move)
        self.assertIn(move.name, _canonical(viewed.content)[0][2])
        self.assertNotIn('Proforma', _canonical(viewed.content)[0][2])

    def test_a_draft_invoice_reads_the_same_in_view_and_in_print(self):
        move = self.init_invoice('out_invoice', post=False, amounts=[55.0], invoice_date='2026-03-01')
        self._as_admin_in(move.company_id)
        self._assert_same_document(move)

    def test_a_vendor_bill_reads_the_same_in_view_and_in_print(self):
        move = self.init_invoice('in_invoice', post=True, amounts=[80.0], invoice_date='2026-03-01')
        self._as_admin_in(move.company_id)
        self._assert_same_document(move)

    def test_the_stamp_is_drawn_identically_by_both_and_by_neither_without_one(self):
        move = self.init_invoice('out_invoice', post=True, amounts=[100.0], invoice_date='2026-03-01')
        self._as_admin_in(move.company_id)
        before = [len(found) for found in pdf_probe.placements(self._both(move)[0].content)]
        stamp = self._stamp(
            'customer_invoice', x_mm=120.0, y_mm=235.0, width_mm=40.0, height_mm=20.0, page_scope='last')
        self.env.flush_all()
        viewed, printed, stamped = self._assert_same_document(move)
        self.assertEqual(sum(stamped), sum(before) + 1, 'exactly one stamp more than the unstamped document')
        stamp.write({'active': False})
        self.env.flush_all()
        _viewed, _printed, again = self._assert_same_document(move)
        self.assertEqual(sum(again), sum(before), 'no stamp on either once the template is off')

    def test_the_arabic_document_reads_the_same_in_view_and_in_print(self):
        self.env['res.lang']._activate_lang('ar_001')
        move = self.init_invoice('out_invoice', post=True, amounts=[100.0], invoice_date='2026-03-01')
        admin = self.env.ref('base.user_admin')
        admin.write({'lang': 'ar_001'})
        self._as_admin_in(move.company_id)
        self._assert_same_document(move)

    def test_view_and_print_take_their_report_and_context_from_the_same_two_functions(self):
        """One source: change what the document is printed with, and BOTH change with it, together."""
        move = self.init_invoice('out_invoice', post=True, amounts=[100.0], invoice_date='2026-03-01')
        self._as_admin_in(move.company_id)
        action = move.action_print_pdf()
        action = action.get('context', {}).get('report_action') or action
        self.assertEqual(action['report_name'], move._nc_print_report().report_name)
        self.assertEqual(action['context']['proforma_invoice'], move._nc_print_context()['proforma_invoice'])
        original = type(move)._nc_print_context
        calls = []

        def pro_forma(record):
            calls.append(record.id)
            return dict(original(record), proforma_invoice=True)

        with patch.object(type(move), '_nc_print_context', pro_forma):
            viewed, printed, _stamps = self._assert_same_document(move)
        self.assertIn('Proforma', _canonical(viewed.content)[0][2], 'View followed the context')
        self.assertIn('Proforma', _canonical(printed.content)[0][2], 'Print followed the same context')
        self.assertGreaterEqual(len(calls), 2, 'both routes asked the one function')
