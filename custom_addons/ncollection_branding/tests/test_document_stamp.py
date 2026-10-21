# -*- coding: utf-8 -*-
"""Document stamp templates: validation, scoping, precedence, company isolation, and the real preview.

Synthetic data only. Documents are partners (every database has them, with a company), printed by a synthetic report
is never rendered: only the selection rules and the overlay are under test, so nothing here depends on another module.
"""
import base64
import io

from PIL import Image
from reportlab.pdfgen import canvas

from odoo.exceptions import AccessError, ValidationError
from odoo.tests import HttpCase, TransactionCase, new_test_user, tagged

from ..models import stamp_overlay
from . import pdf_probe

MM = stamp_overlay.POINTS_PER_MM


def _png(size=(80, 40), color=(180, 20, 20, 150)):
    buffer = io.BytesIO()
    Image.new('RGBA', size, color).save(buffer, 'PNG')
    return buffer.getvalue()


def _b64(raw):
    return base64.b64encode(raw)


def _sheet(width_mm=210.0, height_mm=297.0, pages=1):
    buffer = io.BytesIO()
    page = canvas.Canvas(buffer, pagesize=(width_mm * MM, height_mm * MM))
    for _number in range(pages):
        page.drawString(20 * MM, 20 * MM, 'document')
        page.showPage()
    page.save()
    return buffer.getvalue()


class _StampCommon(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company_a = cls.env['res.company'].create({'name': 'Stamp Co A'})
        cls.company_b = cls.env['res.company'].create({'name': 'Stamp Co B'})
        cls.report = cls.env['ir.actions.report'].create({
            'name': 'Synthetic document', 'model': 'res.partner', 'report_type': 'qweb-pdf',
            'report_name': 'ncollection_branding.synthetic_document',
            'report_file': 'ncollection_branding.synthetic_document'})
        Type = cls.env['ncollection.document.type']
        cls.type_company = Type.create({
            'name': 'Synthetic company doc', 'code': 'syn_company', 'report_ids': [(6, 0, cls.report.ids)],
            'record_domain': "[('is_company', '=', True)]"})
        cls.type_person = Type.create({
            'name': 'Synthetic person doc', 'code': 'syn_person', 'report_ids': [(6, 0, cls.report.ids)],
            'record_domain': "[('is_company', '=', False)]"})
        cls.firm = cls.env['res.partner'].create({
            'name': 'A Firm', 'is_company': True, 'company_id': cls.company_a.id})
        cls.person = cls.env['res.partner'].create({'name': 'A Person', 'is_company': False,
                                                    'company_id': cls.company_a.id})

    def _stamp(self, types, company=None, **values):
        vals = {'name': 'Stamp', 'company_id': (company or self.company_a).id, 'image': _b64(_png()),
                'document_type_ids': [(6, 0, types.ids)]}
        vals.update(values)
        return self.env['ncollection.document.stamp'].create(vals)

    def _winner(self, record):
        return self.env['ncollection.document.stamp']._nc_winner(self.report, record)


@tagged('post_install', '-at_install')
class TestStampTemplate(_StampCommon):

    def test_a_stamp_needs_a_png_and_a_sane_geometry_and_at_least_one_document_type(self):
        jpeg = io.BytesIO()
        Image.new('RGB', (30, 30), (1, 2, 3)).save(jpeg, 'JPEG')
        with self.assertRaises(ValidationError):
            self._stamp(self.type_company, image=_b64(jpeg.getvalue()))
        for bad in ({'width_mm': 0.0}, {'height_mm': 900.0}, {'x_mm': -5.0}, {'y_mm': 5000.0}):
            with self.subTest(bad), self.assertRaises(ValidationError):
                self._stamp(self.type_company, **bad)
        with self.assertRaises(ValidationError):
            self._stamp(self.env['ncollection.document.type'])           # names no document: applies to nothing

    def test_the_image_is_kept_exactly_as_uploaded_and_transparency_is_kept(self):
        raw = _png(color=(10, 90, 160, 70))
        stamp = self._stamp(self.type_company, image=_b64(raw))
        self.assertEqual(base64.b64decode(stamp.image), raw)
        self.assertAlmostEqual(stamp.aspect_ratio, 2.0, places=3)

    def test_the_web_client_reading_the_image_as_its_size_text_does_not_break_the_stamp(self):
        """The web client reads every binary with ``bin_size``: the value is then text such as "2.1 Kb", not base64.
        Opening the form crashed on it once (found in a real browser, which the ORM tests never are)."""
        raw = _png(color=(10, 90, 160, 70))
        stamp = self._stamp(self.type_company, image=_b64(raw)).with_context(bin_size=True)
        stamp.invalidate_recordset()
        self.assertAlmostEqual(stamp.aspect_ratio, 2.0, places=3)
        self.assertEqual(stamp._nc_placement()['image'], raw)

    def test_the_sheet_is_the_paper_the_stamp_is_designed_on(self):
        a4 = self._stamp(self.type_company)
        self.assertEqual((a4.sheet_width_mm, a4.sheet_height_mm), (210.0, 297.0))
        landscape = self.env['report.paperformat'].create({'name': 'Synthetic A4 landscape', 'format': 'A4',
                                                           'orientation': 'Landscape'})
        wide = self._stamp(self.type_company, paper_format_ids=[(6, 0, landscape.ids)])
        self.assertEqual((wide.sheet_width_mm, wide.sheet_height_mm), (297.0, 210.0))
        custom = self.env['report.paperformat'].create({'name': 'Synthetic A6 custom', 'format': 'custom',
                                                        'page_width': 148, 'page_height': 105})
        small = self._stamp(self.type_company, paper_format_ids=[(6, 0, custom.ids)])
        self.assertEqual((small.sheet_width_mm, small.sheet_height_mm), (148.0, 105.0))

    def test_proportions_are_kept_while_keep_ratio_is_on_and_free_when_it_is_off(self):
        stamp = self._stamp(self.type_company, width_mm=40.0, height_mm=20.0)             # the image is 2:1
        stamp.width_mm = 60.0
        stamp._onchange_width()
        self.assertEqual(stamp.height_mm, 30.0)
        stamp.height_mm = 10.0
        stamp._onchange_height()
        self.assertEqual(stamp.width_mm, 20.0)
        stamp.keep_ratio = False
        stamp.width_mm = 50.0
        stamp._onchange_width()
        self.assertEqual(stamp.height_mm, 10.0, "free to stretch only when the administrator asks for it")

    def test_only_workspace_administrators_edit_stamps_and_everyone_who_prints_can_read_them(self):
        stamp = self._stamp(self.type_company)
        user = new_test_user(self.env, login='stamp_reader', groups='base.group_user',
                             company_id=self.company_a.id, company_ids=[(6, 0, self.company_a.ids)])
        self.assertEqual(stamp.with_user(user).name, 'Stamp')
        with self.assertRaises(AccessError):
            stamp.with_user(user).write({'name': 'Mine'})
        with self.assertRaises(AccessError):
            self.env['ncollection.document.stamp'].with_user(user).create({
                'name': 'x', 'company_id': self.company_a.id, 'image': _b64(_png()),
                'document_type_ids': [(6, 0, self.type_company.ids)]})
        admin = new_test_user(self.env, login='stamp_admin', groups='base.group_user,base.group_system',
                              company_id=self.company_a.id, company_ids=[(6, 0, self.company_a.ids)])
        stamp.with_user(admin).write({'name': 'Renamed'})
        self.assertEqual(stamp.name, 'Renamed')

    def test_a_user_never_sees_another_companys_stamp(self):
        stamp_b = self._stamp(self.type_company, company=self.company_b)
        user = new_test_user(self.env, login='stamp_only_a', groups='base.group_user',
                             company_id=self.company_a.id, company_ids=[(6, 0, self.company_a.ids)])
        self.assertFalse(self.env['ncollection.document.stamp'].with_user(user).search([('id', '=', stamp_b.id)]))
        with self.assertRaises(AccessError):
            stamp_b.with_user(user).read(['name'])


@tagged('post_install', '-at_install')
class TestStampContextKeysCannotBeForged(_StampCommon):
    """A report context can carry any key a client sends: the preview and the unstamped baseline are not for them."""

    def test_the_preview_context_is_honoured_only_for_someone_who_may_edit_the_stamp(self):
        stamp = self._stamp(self.type_company)
        plain = self.env['res.users'].create({
            'name': 'Plain Printer', 'login': 'plain_printer_stamp',
            'group_ids': [(6, 0, [self.env.ref('base.group_user').id])]})
        Stamp = self.env['ncollection.document.stamp']
        designer = Stamp.with_context(report_pdf_no_attachment=True)           # the designer's render
        self.assertTrue(designer._nc_may_preview({'stamp_id': stamp.id}))                  # the test runs elevated
        self.assertFalse(designer.with_user(plain)._nc_may_preview({'stamp_id': stamp.id}))
        self.assertFalse(Stamp._nc_may_preview({'stamp_id': stamp.id}),
                         'a download (which stores a copy of the document) never honours the preview context')
        for junk in ('1', None, True, {'stamp_id': 'x'}, [1]):
            self.assertFalse(Stamp._nc_may_preview(junk if isinstance(junk, dict) else {'stamp_id': junk}))


class TestStampSelection(_StampCommon):
    """WHICH stamp goes on a document: explicit, deterministic, one winner."""

    def test_a_stamp_applies_only_to_the_kinds_of_document_it_names(self):
        stamp = self._stamp(self.type_company)
        self.assertEqual(self._winner(self.firm), stamp)
        self.assertFalse(self._winner(self.person), "a person's document is another kind: not stamped")

    def test_a_document_no_report_registers_is_never_stamped(self):
        self._stamp(self.type_company)
        other = self.env['ir.actions.report'].create({
            'name': 'Unregistered', 'model': 'res.partner', 'report_type': 'qweb-pdf',
            'report_name': 'ncollection_branding.unregistered', 'report_file': 'ncollection_branding.unregistered'})
        self.assertFalse(self.env['ncollection.document.stamp']._nc_winner(other, self.firm))

    def test_the_higher_priority_wins_and_a_tie_goes_to_the_older_stamp(self):
        low = self._stamp(self.type_company, name='low', priority=5)
        high = self._stamp(self.type_company, name='high', priority=50)
        self.assertEqual(self._winner(self.firm), high)
        high.priority = 5
        self.assertEqual(self._winner(self.firm), low, "equal priority: the older template")

    def test_a_stamp_for_a_paper_format_beats_a_general_one_and_a_wrong_paper_is_excluded(self):
        paper = self.env['report.paperformat'].create({'name': 'Synthetic paper', 'format': 'A4'})
        other_paper = self.env['report.paperformat'].create({'name': 'Synthetic other paper', 'format': 'A5'})
        self.report.paperformat_id = paper
        general = self._stamp(self.type_company, name='general', priority=90)
        specific = self._stamp(self.type_company, name='specific', priority=1, paper_format_ids=[(6, 0, paper.ids)])
        self.assertEqual(self._winner(self.firm), specific, "the more specific rule beats priority")
        specific.paper_format_ids = other_paper
        self.assertEqual(self._winner(self.firm), general, "a stamp for another paper does not apply")

    def test_exactly_one_stamp_is_drawn_even_when_several_apply(self):
        self._stamp(self.type_company, name='one', priority=1)
        self._stamp(self.type_company, name='two', priority=2)
        out = self.env['ncollection.document.stamp']._nc_apply_to_pdf(self.report, self.firm, _sheet())
        self.assertEqual(len(pdf_probe.placements(out)[0]), 1, "no accidental duplicate stamps")

    def test_another_companys_stamp_never_stamps_this_companys_document(self):
        self._stamp(self.type_company, company=self.company_b)
        self.assertFalse(self._winner(self.firm))

    def test_an_archived_stamp_is_ignored(self):
        stamp = self._stamp(self.type_company)
        stamp.active = False
        self.assertFalse(self._winner(self.firm))

    def test_the_document_is_stamped_where_the_template_says(self):
        self._stamp(self.type_company, x_mm=120.0, y_mm=200.0, width_mm=40.0, height_mm=20.0, page_scope='last')
        out = self.env['ncollection.document.stamp']._nc_apply_to_pdf(self.report, self.firm, _sheet(pages=2))
        pages = pdf_probe.placements(out)
        self.assertEqual([len(found) for found in pages], [0, 1])
        for got, want in zip(pages[1][0], (120.0, 200.0, 40.0, 20.0)):
            self.assertAlmostEqual(got, want, places=1)

    def test_nothing_matching_leaves_the_document_byte_for_byte_untouched(self):
        doc = _sheet()
        self.assertIs(self.env['ncollection.document.stamp']._nc_apply_to_pdf(self.report, self.person, doc), doc)

    def test_the_companys_single_stamp_image_is_the_default_for_a_registered_document(self):
        self.company_a.nc_stamp = _b64(_png((90, 60)))
        out = self.env['ncollection.document.stamp']._nc_apply_to_pdf(self.report, self.firm, _sheet(pages=2))
        pages = pdf_probe.placements(out)
        self.assertEqual([len(found) for found in pages], [0, 1], "the last page, once")
        x, y, width, height = pages[1][0]
        self.assertAlmostEqual(width / height, 90 / 60, places=2, msg="proportions kept")
        self.assertLessEqual(width, 60.0 + 0.01)
        self.assertLessEqual(height, 32.0 + 0.01)
        self.assertAlmostEqual(x + width, 210.0 - 15.0, places=1)                 # inside the right margin
        self.assertAlmostEqual(y + height, 297.0 - 50.0, places=1)                # above the footer
        # a template, when one applies, replaces the default
        self._stamp(self.type_company, x_mm=10.0, y_mm=10.0, width_mm=20.0, height_mm=10.0, page_scope='first')
        again = pdf_probe.placements(self.env['ncollection.document.stamp']._nc_apply_to_pdf(
            self.report, self.firm, _sheet(pages=2)))
        self.assertEqual([len(found) for found in again], [1, 0])

    def test_the_designers_preview_uses_the_named_stamp_at_the_geometry_it_is_given(self):
        stamp = self._stamp(self.type_company, x_mm=10.0, y_mm=10.0, width_mm=20.0, height_mm=10.0)
        override = {'stamp_id': stamp.id, 'x_mm': 100.0, 'y_mm': 150.0, 'width_mm': 30.0, 'height_mm': None,
                    'pages': 'first'}
        out = self.env['ncollection.document.stamp'].with_context(
            nc_stamp_preview=override, report_pdf_no_attachment=True)._nc_apply_to_pdf(
            self.report, self.firm, _sheet())      # a firm IS the stamp's kind: the named stamp, as dragged
        (found,) = pdf_probe.placements(out)[0]
        for got, want in zip(found, (100.0, 150.0, 30.0, 10.0)):        # height not overridden: the saved one
            self.assertAlmostEqual(got, want, places=1)
        self.assertEqual(stamp.x_mm, 10.0, "previewing saves nothing")

    def test_the_preview_context_never_forces_a_stamp_onto_another_kind_of_document(self):
        """A report context is client input: naming a person's stamp for a firm's document draws nothing."""
        stamp = self._stamp(self.type_person, x_mm=10.0, y_mm=10.0, width_mm=20.0, height_mm=10.0)
        before = pdf_probe.placements(_sheet())
        out = self.env['ncollection.document.stamp'].with_context(
            nc_stamp_preview={'stamp_id': stamp.id})._nc_apply_to_pdf(self.report, self.firm, _sheet())
        self.assertEqual(pdf_probe.placements(out), before)

    def test_a_stamp_with_no_image_stamps_nothing(self):
        stamp = self._stamp(self.type_company)
        stamp.write({'image': False})
        self.assertFalse(self.env['ncollection.document.stamp']._nc_winner(self.report, self.firm))


@tagged('post_install', '-at_install')
class TestStampPreviewRoute(HttpCase):

    def setUp(self):
        super().setUp()
        self.company = self.env.company
        self.report = self.env['ir.actions.report'].create({
            'name': 'Synthetic document', 'model': 'res.partner', 'report_type': 'qweb-pdf',
            'report_name': 'ncollection_branding.synthetic_preview',
            'report_file': 'ncollection_branding.synthetic_preview'})
        # a kind with NO document in the workspace: the preview draws the stamp on the sample sheet
        self.kind = self.env['ncollection.document.type'].create({
            'name': 'Synthetic nothing', 'code': 'syn_nothing', 'report_ids': [(6, 0, self.report.ids)],
            'record_domain': "[('id', '=', 0)]"})
        self.stamp = self.env['ncollection.document.stamp'].create({
            'name': 'Preview me', 'company_id': self.company.id, 'image': _b64(_png()),
            'document_type_ids': [(6, 0, self.kind.ids)], 'x_mm': 10.0, 'y_mm': 20.0, 'width_mm': 30.0,
            'height_mm': 15.0})
        self.env.ref('base.user_admin').write({'company_ids': [(4, self.company.id)]})
        self.env.flush_all()
        self.authenticate('admin', 'admin')

    def test_the_preview_is_a_real_pdf_shown_inline_with_the_stamp_at_the_requested_place(self):
        response = self.url_open('/ncollection/stamp/preview/%d?x=55&y=66&w=33&h=22&pages=first' % self.stamp.id)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers['Content-Type'], 'application/pdf')
        self.assertTrue(response.headers['Content-Disposition'].startswith('inline;'))
        self.assertEqual(response.headers['Cache-Control'], 'no-store')
        self.assertTrue(response.content.startswith(b'%PDF'))
        (found,) = pdf_probe.placements(response.content)[0]
        for got, want in zip(found, (55.0, 66.0, 33.0, 22.0)):
            self.assertAlmostEqual(got, want, places=1)
        self.assertEqual(self.stamp.x_mm, 10.0, "the preview saved nothing")

    def test_numbers_that_are_not_sane_fall_back_to_the_saved_values_or_the_limits(self):
        response = self.url_open('/ncollection/stamp/preview/%d?x=abc&y=-9&w=99999&h=0.01&pages=bogus' % self.stamp.id)
        self.assertEqual(response.status_code, 200)
        (found,) = pdf_probe.placements(response.content)[0]
        x, y, width, height = found
        self.assertAlmostEqual(x, 10.0, places=1)                  # 'abc' -> the saved position
        self.assertAlmostEqual(y, 0.0, places=1)                   # clamped to the paper's edge
        self.assertAlmostEqual(width, 400.0, places=1)             # clamped to the largest allowed
        self.assertAlmostEqual(height, 1.0, places=1)              # clamped to the smallest

    def test_another_companys_stamp_and_a_missing_one_are_not_found(self):
        other = self.env['res.company'].create({'name': 'Somebody else'})
        theirs = self.env['ncollection.document.stamp'].create({
            'name': 'Theirs', 'company_id': other.id, 'image': _b64(_png()),
            'document_type_ids': [(6, 0, self.kind.ids)]})
        self.env.flush_all()
        self.assertEqual(self.url_open('/ncollection/stamp/preview/%d' % theirs.id).status_code, 404)
        self.assertEqual(self.url_open('/ncollection/stamp/preview/999999999').status_code, 404)
