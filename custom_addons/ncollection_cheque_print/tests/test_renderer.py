# -*- coding: utf-8 -*-
"""The renderer and the Arabic text shaping: pure functions, no database rows."""
import datetime
import io

from PyPDF2 import PdfReader
from reportlab.pdfbase import pdfmetrics

from odoo.tests import TransactionCase, tagged

from ..models import renderer, text_shaping

MM = 72.0 / 25.4


def _field(key, **kw):
    base = {'key': key, 'visible': True, 'x_mm': 10.0, 'y_mm': 10.0, 'width_mm': 80.0, 'height_mm': 8.0,
            'font': 'dejavu', 'size_pt': 11.0, 'bold': False, 'align': 'left', 'direction': 'auto', 'case': 'as_is',
            'digits': 'western', 'date_format': 'dd/mm/yyyy', 'words_lang': 'en', 'prefix': '', 'suffix': '',
            'fit': 'shrink', 'max_lines': 1, 'static_text': ''}
    base.update(kw)
    return base


def _spec(fields, width=203.0, height=94.0):
    return {'name': 'Synthetic', 'width_mm': width, 'height_mm': height, 'include_background': False, 'fields': fields}


RAW = {
    'payee': 'Synthetic Payee LLC', 'date': datetime.date(2026, 10, 14), 'amount': 1234.5, 'decimals': 2,
    'currency': 'AED', 'amount_words': {'en': 'One Thousand Dirhams Only', 'ar': 'ألف درهم فقط لا غير'},
    'reference': 'REF-1', 'cheque_number': '000001', 'account_number': '000', 'bank_name': 'Sample Bank',
}


def _page(pdf):
    box = PdfReader(io.BytesIO(pdf)).pages[0].mediabox
    return float(box.width) / MM, float(box.height) / MM


@tagged('post_install', '-at_install')
class TestTextShaping(TransactionCase):

    def test_arabic_letters_become_presentation_forms(self):
        shaped = text_shaping.shape('درهم')
        self.assertNotEqual(shaped, 'درهم')
        self.assertTrue(all(0xFB50 <= ord(c) <= 0xFEFF for c in shaped))

    def test_rtl_run_is_reordered_and_latin_is_not(self):
        self.assertEqual(text_shaping.prepare('ABC 123', 'ltr'), 'ABC 123')
        self.assertTrue(text_shaping.has_rtl('ألف'))
        self.assertFalse(text_shaping.has_rtl('One'))

    def test_arabic_indic_digits(self):
        self.assertEqual(text_shaping.to_arabic_indic('12,345.67'), '١٢,٣٤٥.٦٧')

    def test_empty_text_is_safe(self):
        self.assertEqual(text_shaping.prepare('', 'auto'), '')

    def test_lam_alef_is_the_only_ligature(self):
        """A regression: lam + meem used to be shaped into a Forms-A ligature the font has no glyph for, which printed
        an empty box where two letters belonged."""
        self.assertEqual(len(text_shaping.shape('لم')), 2)
        self.assertEqual(len(text_shaping.shape('الم')), 3)             # alef, lam, meem: no ligature
        self.assertEqual(len(text_shaping.shape('لا')), 1)              # the real ligature
        self.assertEqual(len(text_shaping.shape('لأ')), 1)

    def test_every_shaped_form_exists_in_the_font(self):
        """No glyph corruption: whatever the shaper emits must be a character the Arabic font can draw. Every pair of
        Arabic letters in every position, plus real names and amounts."""
        name, arabic_capable = renderer._font_name('dejavu', False)
        self.assertTrue(arabic_capable, "the Arabic-capable font is not installed")
        glyphs = pdfmetrics.getFont(name).face.charToGlyph
        letters = [chr(c) for c in list(range(0x0621, 0x063B)) + list(range(0x0641, 0x064B))]
        samples = ['شركة الأفق للتجارة والمقاولات ذ.م.م', 'مؤسسة النور Al Noor للخدمات اللوجستية',
                   'اثنا عشر ألفاً وثلاثمائة وخمسة وأربعون درهمًا وسبعة وستون فلسًا فقط لا غير',
                   'مائتان وخمسون ألفاً وخمسمائة درهم وخمسون فلساً فقط لا غير']
        samples += [first + second + first for first in letters for second in letters]
        missing = {ch for text in samples for ch in text_shaping.shape(text)
                   if ord(ch) > 127 and ord(ch) not in glyphs}
        self.assertEqual(missing, set(), "shaped characters with no glyph: %s" % sorted(hex(ord(c)) for c in missing))

    def test_visual_order_of_mixed_text(self):
        """What the canvas is given, left to right, for the mixed lines a cheque really has."""
        arabic = text_shaping.shape('فاتورة')
        # a Latin reference then an Arabic word: the space between them must stay between them (it used to jump to the
        # far end, printing "INV-2026-0042فاتورة")
        self.assertEqual(text_shaping.prepare('INV-2026-0042 فاتورة', 'auto'), 'INV-2026-0042 ' + arabic[::-1])
        # an Arabic word then a number inside a left-to-right line: the number sits to the LEFT of the word
        self.assertEqual(text_shaping.prepare('Ref فاتورة 123', 'auto'), 'Ref 123 ' + arabic[::-1])
        # a right-to-left line: the first thing read (the day) ends up at the right-hand end, digits keep their order
        visual = text_shaping.prepare('05 أكتوبر 2026', 'auto')
        self.assertTrue(visual.startswith('2026 ') and visual.endswith(' 05'), visual)
        # Arabic first, Latin name inside: the Latin words stay readable left to right
        visual = text_shaping.prepare('مؤسسة النور Al Noor للخدمات', 'auto')
        self.assertIn('Al Noor', visual)
        # plain Latin and plain numbers are untouched
        latin = 'Horizon Trading & Contracting LLC'
        self.assertEqual(text_shaping.prepare(latin, 'auto'), latin)
        self.assertEqual(text_shaping.prepare('12,345.67', 'auto'), '12,345.67')

    def test_brackets_around_arabic_stay_together_in_a_latin_led_line(self):
        """UBA N0: the closing bracket of "(<arabic>)" used to drift to the far end of an English-led line."""
        inner = text_shaping.shape('الحجم الفعلي')
        for line in ('ADCB | الصفحة (الحجم الفعلي)', 'Page (الحجم الفعلي) 99', '(الحجم الفعلي) ADCB'):
            self.assertIn('(' + inner[::-1] + ')', text_shaping.prepare(line, 'auto'), line)
        # brackets around Latin text in an Arabic line stay readable as they are written
        self.assertIn('(ABC)', text_shaping.prepare('الدفعة (ABC) اليوم', 'auto'))
        self.assertEqual(text_shaping.prepare('Total (AED) 100', 'auto'), 'Total (AED) 100')

    def test_base_direction(self):
        self.assertEqual(text_shaping.base_direction('ألف', 'auto'), 'rtl')
        self.assertEqual(text_shaping.base_direction('One', 'auto'), 'ltr')
        self.assertEqual(text_shaping.base_direction('123 ألف', 'auto'), 'rtl')    # numbers are not strong
        self.assertEqual(text_shaping.base_direction('ألف', 'ltr'), 'ltr')           # an explicit direction wins
        self.assertEqual(text_shaping.base_direction('', 'auto'), 'ltr')


@tagged('post_install', '-at_install')
class TestRenderer(TransactionCase):

    def test_page_is_exactly_the_physical_size(self):
        for width, height in ((203.0, 94.0), (203.0, 99.0), (203.0, 106.0), (175.5, 82.25)):
            pdf, _warn = renderer.render_pdf(_spec([_field('payee')], width, height), RAW)
            w, h = _page(pdf)
            self.assertAlmostEqual(w, width, places=1)
            self.assertAlmostEqual(h, height, places=1)

    def test_print_mode_is_deterministic(self):
        spec = _spec([_field('payee'), _field('amount', y_mm=30)])
        self.assertEqual(renderer.render_pdf(spec, RAW)[0], renderer.render_pdf(spec, RAW)[0])

    def test_formatting(self):
        self.assertEqual(renderer.format_amount(1234.5, 2), '1,234.50')
        self.assertEqual(renderer.format_amount(1234.567, 3), '1,234.567')
        self.assertEqual(renderer.format_date(datetime.date(2026, 1, 5), 'dd/mm/yyyy'), '05/01/2026')
        self.assertEqual(renderer.format_date(None, 'dd/mm/yyyy'), '')

    def test_date_month_name_follows_the_print_language_not_the_screen(self):
        day = datetime.date(2026, 10, 5)
        self.assertEqual(renderer.format_date(day, 'dd mmm yyyy', 'en'), '05 Oct 2026')
        self.assertEqual(renderer.format_date(day, 'dd mmm yyyy', 'ar'), '05 أكتوبر 2026')
        self.assertEqual(renderer.format_date(day, 'dd mmm yyyy'), '05 Oct 2026')          # English is the default
        self.assertEqual(renderer.format_date(day, 'dd mmm yyyy', 'xx'), '05 Oct 2026')    # unknown: never an error
        for fmt, expected in (('dd/mm/yyyy', '05/10/2026'), ('dd-mm-yyyy', '05-10-2026'), ('dd.mm.yyyy', '05.10.2026'),
                              ('yyyy-mm-dd', '2026-10-05'), ('ddmmyyyy', '05102026'), ('d/m/yy', '5/10/26')):
            for lang in ('en', 'ar'):
                self.assertEqual(renderer.format_date(day, fmt, lang), expected, (fmt, lang))
        january = datetime.date(2026, 1, 5)
        self.assertNotEqual(renderer.format_date(january, 'dd mmm yyyy', 'ar'),
                            renderer.format_date(january, 'dd mmm yyyy', 'en'))

    def test_date_field_uses_the_language_of_the_raw_facts(self):
        field = _field('payment_date', date_format='dd mmm yyyy')
        self.assertEqual(renderer.field_text(field, dict(RAW, lang='en')), '14 Oct 2026')
        self.assertEqual(renderer.field_text(field, dict(RAW, lang='ar')), '14 أكتوبر 2026')
        arabic_digits = _field('payment_date', date_format='dd mmm yyyy', digits='arabic')
        self.assertEqual(renderer.field_text(arabic_digits, dict(RAW, lang='ar')), '١٤ أكتوبر ٢٠٢٦')

    def test_words_language_auto_follows_the_print_language(self):
        auto = _field('amount_words', words_lang='auto')
        self.assertEqual(renderer.field_text(auto, dict(RAW, lang='ar')), RAW['amount_words']['ar'])
        self.assertEqual(renderer.field_text(auto, dict(RAW, lang='en')), RAW['amount_words']['en'])
        self.assertEqual(renderer.field_text(auto, RAW), RAW['amount_words']['en'])
        fixed = _field('amount_words', words_lang='en')                 # a bank that wants English only keeps it
        self.assertEqual(renderer.field_text(fixed, dict(RAW, lang='ar')), RAW['amount_words']['en'])

    def test_automatic_alignment_follows_the_text(self):
        """Arabic sits against the right edge of its box, English against the left; an explicit alignment wins."""
        def render(field, raw):
            return renderer.render_pdf(_spec([field]), raw)[0]
        arabic = dict(RAW, payee='شركة النموذج')
        self.assertNotEqual(render(_field('payee', align='auto'), arabic),
                            render(_field('payee', align='left'), arabic))
        self.assertEqual(render(_field('payee', align='auto'), arabic), render(_field('payee', align='right'), arabic))
        self.assertEqual(render(_field('payee', align='auto'), RAW), render(_field('payee', align='left'), RAW))

    def test_preview_pages_render_in_arabic_and_stay_page_sized(self):
        labels = {
            'design': 'معاينة التصميم - الصورة المرجعية والمربعات لا تُطبع', 'watermark': 'معاينة - ليست طباعة',
            'calibration_title': 'معايرة الشيك',
            'calibration_page': '%(name)s | الصفحة %(w).1f × %(h).1f مم',
            'calibration_help': 'كل علامة تبعد عشرة مليمترات تمامًا عن حافتين. قِس المسافة من الحافة إلى العلامة.',
            'calibration_profile': 'الإزاحة x %(x)+.1f مم y %(y)+.1f مم المقياس %(s).1f بالمائة'}
        spec = _spec([_field('payee')], 203.0, 99.0)
        for mode, preview in (('calibration', False), ('design', False), ('print', True)):
            pdf, _warn = renderer.render_pdf(spec, RAW, mode=mode, preview=preview, labels=labels)
            width, height = _page(pdf)
            self.assertEqual((round(width, 1), round(height, 1)), (203.0, 99.0))
            self.assertIn(b'DejaVu', pdf, mode)                       # an Arabic label needs the Arabic-capable font
        english = renderer.render_pdf(spec, RAW, mode='calibration')[0]
        self.assertNotIn(b'DejaVu', english)

    def test_missing_values_print_blank_not_false(self):
        raw = dict(RAW, payee=False, reference=None, amount=None, date=None)
        for key in ('payee', 'reference', 'amount', 'payment_date', 'cheque_number'):
            text = renderer.field_text(_field(key), dict(raw, cheque_number=''))
            self.assertEqual(text, '')
        pdf, _warn = renderer.render_pdf(_spec([_field('payee'), _field('amount')]), raw)
        self.assertTrue(pdf.startswith(b'%PDF'))

    def test_field_options_apply(self):
        self.assertEqual(renderer.field_text(_field('payee', case='upper'), RAW), 'SYNTHETIC PAYEE LLC')
        self.assertEqual(renderer.field_text(_field('amount', prefix='**', suffix='**'), RAW), '**1,234.50**')
        self.assertEqual(renderer.field_text(_field('amount', digits='arabic'), RAW), '١,٢٣٤.٥٠')
        self.assertEqual(renderer.field_text(_field('amount_words', words_lang='ar'), RAW), RAW['amount_words']['ar'])
        self.assertEqual(renderer.field_text(_field('static_text', static_text='PAY'), RAW), 'PAY')

    def test_long_text_is_fitted_and_flagged(self):
        raw = dict(RAW, payee='A very long payee name ' * 12)
        for fit in ('shrink', 'wrap'):
            pdf, warnings = renderer.render_pdf(
                _spec([_field('payee', width_mm=40, height_mm=8, fit=fit, max_lines=2)]), raw)
            self.assertTrue(pdf.startswith(b'%PDF'))
            self.assertTrue(warnings, "A long name must warn (%s)" % fit)

    def test_arabic_and_rtl_render(self):
        raw = dict(RAW, payee='شركة النموذج التجارية ذ.م.م')
        pdf, _warn = renderer.render_pdf(
            _spec([_field('payee', direction='rtl', align='right'),
                   _field('amount_words', words_lang='ar', direction='rtl', y_mm=30)]), raw)
        self.assertTrue(pdf.startswith(b'%PDF'))
        self.assertIn(b'DejaVu', pdf)

    def test_print_mode_has_no_background_or_watermark(self):
        spec = _spec([_field('payee')])
        printed = renderer.render_pdf(spec, RAW, mode='print')[0]
        previewed = renderer.render_pdf(spec, RAW, mode='print', preview=True)[0]
        design = renderer.render_pdf(spec, RAW, mode='design')[0]
        self.assertNotEqual(printed, previewed)
        self.assertNotEqual(printed, design)
        self.assertNotIn(b'PREVIEW', printed)

    def test_calibration_sheet_is_page_sized(self):
        pdf, _warn = renderer.render_pdf(_spec([_field('payee')], 203.0, 99.0), RAW, mode='calibration')
        w, h = _page(pdf)
        self.assertAlmostEqual(w, 203.0, places=1)
        self.assertAlmostEqual(h, 99.0, places=1)

    def test_printer_offset_moves_the_page_content_not_the_page(self):
        spec = _spec([_field('payee')])
        plain = renderer.render_pdf(spec, RAW)[0]
        profile = {'offset_x_mm': 2.0, 'offset_y_mm': -1.5, 'scale_pct': 99.0}
        shifted = renderer.render_pdf(spec, RAW, profile=profile)[0]
        self.assertNotEqual(plain, shifted)
        self.assertAlmostEqual(_page(shifted)[0], 203.0, places=1)
