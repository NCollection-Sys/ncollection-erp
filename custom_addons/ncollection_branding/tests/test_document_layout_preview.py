# -*- coding: utf-8 -*-
"""Settings > Configure Document Layout on the NCollection layout.

Odoo renders the wizard's live preview with the WIZARD (``base.document.layout``) as ``company``.
``external_layout_nc``
asked it for ``_nc_document_color()``, a ``res.company`` method, and the wizard failed with an RPC_ERROR
(AttributeError) before it opened. The preview must render, and follow the colour and logo being edited."""
import base64

from odoo.tests import TransactionCase, tagged

# a 1x1 PNG
PNG = base64.b64encode(bytes.fromhex(
    '89504e470d0a1a0a0000000d4948445200000001000000010806000000'
    '1f15c4890000000d4944415478da63f8ffff3f0005fe02fea7d6a0f60000000049454e44ae426082'))


@tagged('post_install', '-at_install')
class TestDocumentLayoutPreview(TransactionCase):

    def _wizard(self, **vals):
        layout = self.env.ref('ncollection_branding.report_layout_nc')
        return self.env['base.document.layout'].create(dict({
            'company_id': self.env.company.id, 'report_layout_id': layout.id,
            'external_report_layout_id': layout.view_id.id}, **vals))

    def test_the_preview_renders_on_the_ncollection_layout(self):
        wizard = self._wizard()
        self.assertTrue(wizard.preview, "the live preview rendered")
        self.assertIn('o_nc_doc', str(wizard.preview))

    def test_the_preview_follows_the_colour_being_edited(self):
        wizard = self._wizard(primary_color='#AA3311')
        self.assertIn('#AA3311', str(wizard.preview))

    def test_an_invalid_colour_never_reaches_the_document(self):
        # (Odoo's colour picker only produces hex; a free-text value is refused by Odoo's own preview stylesheet
        # compiler, so the NCollection options are checked directly, on an unsaved wizard)
        wizard = self.env['base.document.layout'].new({'company_id': self.env.company.id})
        wizard.primary_color = 'red;}body{x'
        opts = self.env['ncollection.document.type']._nc_print_options(False, wizard)
        self.assertEqual(opts['accent'], self.env.company._nc_document_color())

    def test_the_preview_renders_with_and_without_a_logo(self):
        for logo in (False, PNG):
            with self.subTest(logo=bool(logo)):
                self.assertTrue(self._wizard(logo=logo).preview)

    def test_printing_with_a_real_company_is_unchanged(self):
        opts = self.env['ncollection.document.type']._nc_print_options(False, self.env.company)
        self.assertEqual(opts['accent'], self.env.company._nc_document_color())
