# -*- coding: utf-8 -*-
"""UAE tax-invoice PDF (P3-T09) — renders a compliant bilingual tax invoice.

The bilingual Arabic/English layout, TRN and per-line VAT come from Odoo's
``l10n_ae`` + ``l10n_gcc_invoice`` (mechanism Odoo-owned, active once the 'ae'
localization is applied — P3-T04). This ticket adds the tenant brand accent and
PROVES the rendered document carries the UAE FTA tax-invoice elements. The
human-readable requirements mapping lives in ``UAE_TAX_INVOICE_CHECKLIST.md``.
"""
from odoo.tests import TransactionCase, tagged
from odoo.tools import mute_logger


@tagged('post_install', '-at_install')
class TestUaeInvoicePdf(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env['res.company'].create({'name': 'UAE Invoice Co'})
        # applying UAE localization also enables the bilingual invoice layout
        # (dual-language flag + Arabic active) — we assert the end-to-end result.
        cls.company._nc_apply_uae_localization()
        # a valid 15-digit TRN + a brand colour, so both render on the invoice.
        cls.company.vat = '100000000000003'
        cls.company.nc_primary_color = '#1F5F8F'

    def _posted_invoice(self):
        env = self.env(context=dict(
            self.env.context, allowed_company_ids=self.company.ids))
        product = env['product.product'].create({
            'name': 'UAE Widget', 'list_price': 100.0})
        # a VAT-REGISTERED (company) recipient — l10n_ae renders the FULL tax
        # invoice for these; a person/consumer would get the SIMPLIFIED one
        # (l10n_ae._l10n_ae_is_simplified = not commercial_partner.is_company).
        partner = env['res.partner'].create({
            'name': 'UAE Customer', 'company_type': 'company',
            'vat': '100000000000011',
            'country_id': self.env.ref('base.ae').id})
        invoice = env['account.move'].create({
            'move_type': 'out_invoice', 'company_id': self.company.id,
            'partner_id': partner.id,
            'invoice_line_ids': [(0, 0, {
                'product_id': product.id, 'quantity': 1, 'price_unit': 100.0})],
        })
        invoice.action_post()
        return invoice

    @mute_logger('odoo.addons.base.models.ir_attachment')
    def _render(self, invoice):
        # mute_logger: rendering creates + garbage-collects report attachments;
        # in the test filestore that GC logs benign "_file_gc could not unlink"
        # tracebacks which would otherwise trip CI's traceback gate.
        html, dtype = self.env['ir.actions.report']._render_qweb_html(
            'account.account_invoices', invoice.ids)
        self.assertEqual(dtype, 'html')
        return html.decode() if isinstance(html, bytes) else html

    def test_invoice_pdf_has_uae_fta_elements(self):
        invoice = self._posted_invoice()
        html = self._render(invoice)
        self.assertIn('Tax Invoice', html, "the UAE 'Tax Invoice' title must render")
        self.assertNotIn('Simplified', html,
                         "a registered (company) recipient gets the FULL tax "
                         "invoice, not the simplified one")
        self.assertIn(self.company.vat, html, "the supplier TRN must appear")
        self.assertIn(invoice.partner_id.vat, html,
                      "the registered recipient's TRN must appear (full tax invoice)")
        self.assertIn('VAT', html, "the VAT summary must appear")
        self.assertIn(invoice.name, html, "the sequential invoice number must appear")

    def test_invoice_pdf_is_bilingual(self):
        html = self._render(self._posted_invoice())
        # assert SPECIFIC Arabic labels from the l10n_gcc/l10n_ae bilingual
        # layout, not merely "any Arabic codepoint anywhere": the Arabic
        # 'Description' and 'VAT amount' column headers prove the bilingual
        # tax-invoice table actually rendered.
        self.assertIn('الوصف', html,
                      "the Arabic 'Description' column header must render")
        self.assertIn('مبلغ الضريبة', html,
                      "the Arabic 'VAT amount' column header must render "
                      "(bilingual tax-invoice layout)")

    def test_invoice_pdf_carries_tenant_brand_colour(self):
        html = self._render(self._posted_invoice())
        self.assertIn(self.company.nc_primary_color, html,
                      "the tenant brand accent colour must appear on the invoice")
