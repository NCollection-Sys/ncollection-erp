# -*- coding: utf-8 -*-
"""What a document stamp can land on is exactly what the document types register, across the installed applications.

Runs after every module is installed (the kinds are registered by account_core, sale / purchase documents and this
module; the warehouse reports belong to stock), which is why it lives here and not in ncollection_branding's own tests.
"""
import base64
import io
from datetime import date

from PIL import Image

from odoo.addons.account.tests.common import AccountTestInvoicingCommon
from odoo.addons.ncollection_branding.tests import pdf_probe
from odoo.tests import HttpCase, TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestStampScope(TransactionCase):

    def test_warehouse_documents_and_unrelated_reports_are_never_stamped(self):
        warehouse = self.env['ir.actions.report'].search([
            ('model', 'in', ('stock.picking', 'stock.move', 'stock.quant'))])
        self.assertTrue(warehouse, "this database has warehouse reports (delivery slip, picking operations...)")
        Type = self.env['ncollection.document.type']
        self.assertFalse(warehouse & Type.search([]).report_ids)
        for report in warehouse:
            self.assertFalse(Type._nc_types_for(report, self.env[report.model].browse()))

    def test_every_stampable_kind_is_registered_with_a_report(self):
        codes = ('customer_invoice', 'customer_credit_note', 'vendor_bill', 'vendor_credit_note', 'payment_receipt',
                 'sales_quotation', 'sales_order', 'purchase_rfq', 'purchase_order', 'financial_report')
        for code in codes:
            with self.subTest(code):
                kind = self.env['ncollection.document.type'].search([('code', '=', code)])
                self.assertTrue(kind.report_ids, "%s names the report(s) that print it" % code)

    def test_an_unregistered_report_is_not_stamped_even_when_a_stamp_names_every_kind(self):
        Type = self.env['ncollection.document.type']
        picking_report = self.env['ir.actions.report'].search([('model', '=', 'stock.picking')], limit=1)
        every_kind = Type.search([])
        stamp = self.env['ncollection.document.stamp'].create({
            'name': 'Every kind', 'image': _png(), 'document_type_ids': [(6, 0, every_kind.ids)]})
        winner = self.env['ncollection.document.stamp']._nc_winner(picking_report, self.env['stock.picking'].browse())
        self.assertFalse(winner)
        self.assertTrue(stamp)


def _png():
    buffer = io.BytesIO()
    Image.new('RGBA', (20, 10), (10, 10, 10, 120)).save(buffer, 'PNG')
    return base64.b64encode(buffer.getvalue())


@tagged('post_install', '-at_install')
class TestFinancialReportStamp(HttpCase, AccountTestInvoicingCommon):
    """The real wkhtmltopdf render (HttpCase: the report engine needs a server to fetch its stylesheet from)."""

    def _wizard(self):
        return self.env['ncollection.account.report.trial.balance'].create(
            {'date_from': date(2026, 1, 1), 'date_to': date(2026, 12, 31)})

    def _pdf(self, wizard):
        action = self.env.ref(wizard._nc_report_action_ref()).with_context(force_report_rendering=True)
        return action._render_qweb_pdf(action.report_name, [wizard.id])[0]

    def test_a_financial_report_is_stamped_by_its_document_type_and_by_nothing_else(self):
        Stamp = self.env['ncollection.document.stamp']
        kind = self.env.ref('ncollection_account_reports.doc_type_financial_report')
        invoice_kind = self.env.ref('ncollection_account_core.doc_type_customer_invoice')
        geometry = {'x_mm': 120.0, 'y_mm': 230.0, 'width_mm': 40.0, 'height_mm': 20.0, 'page_scope': 'last'}
        wizard = self._wizard()
        self.assertIn(self.env.ref(wizard._nc_report_action_ref()), kind.report_ids)
        bare = self._pdf(wizard)                                            # no stamp exists yet
        Stamp.create(dict(geometry, name='Other kind', image=_png(), document_type_ids=[(6, 0, invoice_kind.ids)]))
        self.assertEqual(pdf_probe.placements(self._pdf(wizard)), pdf_probe.placements(bare),
                         "a stamp of another kind stays off a financial report")
        Stamp.create(dict(geometry, name='Reviewed', image=_png(), document_type_ids=[(6, 0, kind.ids)]))
        before, after = pdf_probe.placements(bare), pdf_probe.placements(self._pdf(wizard))
        added = [[found for found in page if found not in known] for page, known in zip(after, before)]
        self.assertEqual([len(page) for page in added], [0] * (len(added) - 1) + [1])
        for got, want in zip(added[-1][0], (120.0, 230.0, 40.0, 20.0)):
            self.assertAlmostEqual(got, want, places=1)
