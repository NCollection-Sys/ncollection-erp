# -*- coding: utf-8 -*-
"""The cheque overlay renderer: ONE function from a layout and the payment facts to an exact-size PDF.

PHYSICAL, NOT PIXELS. The page is the cheque: ``width_mm`` x ``height_mm`` converted once to PDF points, no A4 or
Letter wrapper around it, and every field is placed by millimetre from the top-left corner. Nothing here knows about
a browser, a screenshot or a CSS pixel, so a PDF printed at 100% / Actual size lands where the template says it does.

OVERLAY ONLY. The bank's logo, its security pattern, the MICR line and any QR code are pre-printed on the paper. This
module draws the variable payment text and nothing else. The reference image of a template is for the designer and is
drawn only in the design preview, or in the printed output if the template explicitly asks for it.

PURE. No ORM, no environment (only Odoo's babel locale helper): ``render_pdf(spec, raw, ...)`` takes plain data
and returns bytes plus a list of
warnings, so the same function serves a print, a reprint, a preview, the designer and the tests, and the same inputs
always give the same bytes.
"""
import io
import logging
import os
from datetime import date as _date

from babel.dates import format_date as babel_format_date
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas as rl_canvas

from odoo.tools.misc import babel_locale_parse

from . import text_shaping

_logger = logging.getLogger(__name__)

MM = 72.0 / 25.4                       # PDF points per millimetre
BRAND = (0.12, 0.37, 0.56)             # the NCollection primary, as RGB 0..1, for the design overlay only

# font key -> (regular, bold) as (built-in name) or (TrueType candidates). Only the TrueType ones carry Arabic.
_BUILTIN = {'helvetica': ('Helvetica', 'Helvetica-Bold'), 'times': ('Times-Roman', 'Times-Bold'),
            'courier': ('Courier', 'Courier-Bold')}
_TTF = {
    'dejavu': (('DejaVuSans.ttf',), ('DejaVuSans-Bold.ttf',)),
    'dejavu_mono': (('DejaVuSansMono.ttf',), ('DejaVuSansMono-Bold.ttf',)),
}
_FONT_DIRS = ('/usr/share/fonts/truetype/dejavu', '/usr/share/fonts/dejavu', '/usr/share/fonts/TTF',
              '/usr/local/share/fonts', os.path.join(os.path.dirname(__file__), '..', 'static', 'fonts'))

FONT_CHOICES = [('dejavu', 'DejaVu Sans (Arabic and Latin)'), ('dejavu_mono', 'DejaVu Sans Mono (Arabic and Latin)'),
                ('helvetica', 'Helvetica (Latin only)'), ('times', 'Times (Latin only)'),
                ('courier', 'Courier (Latin only)')]

DATE_FORMATS = [
    ('dd/mm/yyyy', 'Day/Month/Year (05/01/2026)'), ('dd-mm-yyyy', 'Day-Month-Year (05-01-2026)'),
    ('dd.mm.yyyy', 'Day.Month.Year (05.01.2026)'), ('yyyy-mm-dd', 'Year-Month-Day (2026-01-05)'),
    ('dd mmm yyyy', 'Day Mon Year (05 Jan 2026)'), ('ddmmyyyy', 'No separators (05012026)'),
    ('d/m/yy', 'Short (5/1/26)')]
# The PRINT language of a cheque (what the person chose when printing, not the language of the screen): it decides the
# month name of a date and the language of an "automatic" amount in words. Localisation is Odoo's own: babel, through
# the same locale parser Odoo's ``format_date`` uses.
LANG_LOCALES = {'en': 'en_US', 'ar': 'ar_001'}
# The date formats as babel (LDML) patterns. Digits stay western: the field's own "Digits" option decides the rest.
_DATE_PATTERNS = {
    'dd/mm/yyyy': 'dd/MM/yyyy', 'dd-mm-yyyy': 'dd-MM-yyyy', 'dd.mm.yyyy': 'dd.MM.yyyy', 'yyyy-mm-dd': 'yyyy-MM-dd',
    'dd mmm yyyy': 'dd MMM yyyy', 'ddmmyyyy': 'ddMMyyyy', 'd/m/yy': 'd/M/yy',
}

# The words printed on the NON-cheque pages (design preview, print-preview watermark, calibration sheet). Callers pass
# them translated (``ncollection.cheque.values.labels``); this is only the English fallback for pure use and tests.
DEFAULT_LABELS = {
    'design': 'DESIGN PREVIEW - the reference image and the boxes are NOT printed',
    'watermark': 'PREVIEW - NOT A PRINT',
    'calibration_title': 'NCollection cheque calibration',
    'calibration_page': '%(name)s   |   page %(w).1f x %(h).1f mm   |   print at 100 percent (Actual size)',
    'calibration_help': 'Each cross is exactly 10 mm from two edges. Measure from the paper edge to a cross: any '
                        'difference is the printer shift. Enter it as the horizontal / vertical offset in the '
                        'printer profile.',
    'calibration_profile': 'Profile in use: x %(x)+.1f mm   y %(y)+.1f mm   scale %(s).1f percent   '
                           '(not applied to this sheet)',
}

_registered = {}


def _font_name(font, bold):
    """The reportlab font name for ``font`` + ``bold``; a TrueType family is registered on first use. Returns
    (name, arabic_capable)."""
    if font in _BUILTIN:
        return _BUILTIN[font][1 if bold else 0], False
    family = _TTF.get(font) or _TTF['dejavu']
    candidates = family[1 if bold else 0]
    key = (font if font in _TTF else 'dejavu', bold)
    if key not in _registered:
        path = next((os.path.normpath(os.path.join(d, f)) for f in candidates for d in _FONT_DIRS
                     if os.path.exists(os.path.join(d, f))), None)
        if path:
            name = 'NC-%s-%s' % (key[0], 'B' if bold else 'R')
            pdfmetrics.registerFont(TTFont(name, path))
            _registered[key] = name
        else:
            _logger.warning("cheque print: no TrueType font found for %s; using Helvetica (no Arabic)", key[0])
            _registered[key] = None
    name = _registered[key]
    if name:
        return name, True
    return ('Helvetica-Bold' if bold else 'Helvetica'), False


def _needs_unicode(text):
    return any(ord(c) > 255 for c in text)


# ------------------------------------------------------------------------------------------------ text values
def format_date(value, fmt, lang='en'):
    """``value`` in the template's date format, with the month name in the PRINT language ``lang`` ('en' / 'ar').

    Odoo's own localisation does the work (babel through ``odoo.tools.misc.babel_locale_parse``, as ``format_date``
    there does); there is no second month table. Numeric formats come out in western digits: the field's own
    "Digits" option turns them into Arabic-Indic."""
    if not value:
        return ''
    if not isinstance(value, _date):
        return str(value)
    pattern = _DATE_PATTERNS.get(fmt or 'dd/mm/yyyy', _DATE_PATTERNS['dd/mm/yyyy'])
    locale = babel_locale_parse(LANG_LOCALES.get(lang) or LANG_LOCALES['en'])
    return babel_format_date(value, format=pattern, locale=locale)


def format_amount(amount, decimals):
    if amount is None:
        return ''
    return '{:,.{d}f}'.format(float(amount), d=int(decimals if decimals is not None else 2))


def field_text(field, raw):
    """The text one field prints, from the typed facts in ``raw`` and the field's own formatting options. Missing
    facts give an empty string: a blank is printed, never the word 'False' or an exception."""
    key = field.get('key')
    if key == 'static_text':
        text = field.get('static_text') or ''
    elif key == 'payment_date':
        text = format_date(raw.get('date'), field.get('date_format'), raw.get('lang') or 'en')
    elif key == 'amount':
        text = format_amount(raw.get('amount'), raw.get('decimals', 2)) if raw.get('amount') is not None else ''
    elif key == 'currency':
        text = raw.get('currency') or ''
    elif key == 'amount_words':
        words = raw.get('amount_words') or {}
        lang = field.get('words_lang') or 'auto'
        if lang == 'auto':                 # follow the language the cheque is printed in
            lang = raw.get('lang') or 'en'
        text = words.get(lang) or words.get('en') or ''
    else:
        text = raw.get(key) or ''
    text = '' if text in (None, False) else str(text)
    if not text:
        return ''
    case = field.get('case') or 'as_is'
    text = {'upper': text.upper, 'lower': text.lower, 'title': text.title}.get(case, lambda: text)()
    if field.get('digits') == 'arabic':
        text = text_shaping.to_arabic_indic(text)
    return '%s%s%s' % (field.get('prefix') or '', text, field.get('suffix') or '')


# ------------------------------------------------------------------------------------------------ layout helpers
def _width(text, direction, font_name, size):
    return pdfmetrics.stringWidth(text_shaping.prepare(text, direction), font_name, size)


def _wrap(text, direction, font_name, size, max_width):
    """Break ``text`` (logical order) into lines that fit ``max_width`` points when drawn."""
    words, lines, current = text.split(), [], ''
    for word in words:
        trial = '%s %s' % (current, word) if current else word
        if current and _width(trial, direction, font_name, size) > max_width:
            lines.append(current)
            current = word
        else:
            current = trial
    if current:
        lines.append(current)
    return lines


def _truncate(text, direction, font_name, size, max_width):
    if _width(text, direction, font_name, size) <= max_width:
        return text
    while text and _width(text + '…', direction, font_name, size) > max_width:
        text = text[:-1]
    return text + '…' if text else ''


def _layout(field, text, size_scale, warnings):
    """(font_name, size_pt, [logical lines]) that fit the field's box, shrinking before wrapping before clipping."""
    direction = field.get('direction') or 'auto'
    font = field.get('font') or 'dejavu'
    name, unicode_ok = _font_name(font, bool(field.get('bold')))
    if not unicode_ok and _needs_unicode(text):
        name, unicode_ok = _font_name('dejavu', bool(field.get('bold')))
        if not unicode_ok:
            warnings.append("%s: no font with Arabic glyphs is installed" % field.get('key'))
    size = float(field.get('size_pt') or 11.0) * size_scale
    floor = max(5.0, size * 0.55)
    box_w = float(field['width_mm']) * MM * size_scale
    box_h = float(field['height_mm']) * MM * size_scale
    max_lines = max(1, int(field.get('max_lines') or 1))
    fit = field.get('fit') or 'shrink'
    if fit == 'wrap' and max_lines > 1:
        trial = size
        while trial >= floor - 1e-6:
            lines = _wrap(text, direction, name, trial, box_w)
            if len(lines) <= max_lines and len(lines) * trial * 1.2 <= box_h * 1.05:
                return name, trial, lines
            trial -= 0.5
        lines = _wrap(text, direction, name, floor, box_w)
        if len(lines) > max_lines:
            lines = lines[:max_lines]
        lines[-1] = _truncate(lines[-1], direction, name, floor, box_w)
        warnings.append("%s: text does not fit its box even at the smallest size and was cut" % field.get('key'))
        return name, floor, lines
    trial = size
    while trial > floor and _width(text, direction, name, trial) > box_w:
        trial -= 0.5
    if _width(text, direction, name, trial) > box_w:
        warnings.append("%s: text is longer than its box at the smallest size and was cut" % field.get('key'))
        text = _truncate(text, direction, name, trial, box_w)
    return name, trial, [text]


# ------------------------------------------------------------------------------------------------ the renderer
def page_size_pt(spec):
    return float(spec['width_mm']) * MM, float(spec['height_mm']) * MM


def render_pdf(spec, raw, mode='print', profile=None, background=None, preview=False, labels=None):
    """Render one cheque page. Returns ``(pdf_bytes, warnings)``.

    ``spec``   {'name', 'width_mm', 'height_mm', 'include_background', 'fields': [...]}
    ``raw``    the typed payment facts (see ``ncollection.cheque.values``)
    ``mode``   'print' (overlay only), 'design' (reference image + field boxes) or 'calibration' (a measuring sheet)
    ``profile`` {'offset_x_mm', 'offset_y_mm', 'scale_pct'}; applied to the printed overlay only
    ``preview`` marks an on-screen check of the print: a faint watermark so a preview is never mistaken for a print
    ``labels`` the (translated) words of the design banner, watermark and calibration sheet; see DEFAULT_LABELS
    """
    warnings = []
    page_w, page_h = page_size_pt(spec)
    buffer = io.BytesIO()
    # invariant=1: no creation date and a fixed document id, so identical input gives byte-identical output
    pdf = rl_canvas.Canvas(buffer, pagesize=(page_w, page_h), pageCompression=0, invariant=1)
    pdf.setTitle('Cheque - %s' % (spec.get('name') or ''))
    pdf.setAuthor('NCollection')
    pdf.setCreator('NCollection cheque print')
    profile = profile or {}
    labels = dict(DEFAULT_LABELS, **(labels or {}))
    if mode == 'calibration':
        _calibration(pdf, spec, profile, labels)
    else:
        if background is not None and (mode == 'design' or spec.get('include_background')):
            _background(pdf, background, page_w, page_h)
        apply_profile = mode == 'print'
        off_x = float(profile.get('offset_x_mm') or 0.0) if apply_profile else 0.0
        off_y = float(profile.get('offset_y_mm') or 0.0) if apply_profile else 0.0
        scale = (float(profile.get('scale_pct') or 100.0) / 100.0) if apply_profile else 1.0
        for field in spec.get('fields') or []:
            if not field.get('visible', True) and mode != 'design':
                continue
            _draw_field(pdf, field, raw, page_h, off_x, off_y, scale, mode, warnings)
        if mode == 'design':
            _design_banner(pdf, page_w, page_h, labels)
        if preview and mode == 'print':
            _watermark(pdf, page_w, page_h, labels)
    pdf.showPage()
    pdf.save()
    return buffer.getvalue(), warnings


def _background(pdf, data, page_w, page_h):
    try:
        from PIL import Image
        image = Image.open(io.BytesIO(data)).convert('RGB')
        pdf.drawImage(ImageReader(image), 0, 0, width=page_w, height=page_h, preserveAspectRatio=False)
    except Exception:       # noqa: BLE001 - an unreadable reference image must not stop a design preview
        _logger.warning("cheque print: the reference image could not be drawn", exc_info=True)


def _draw_field(pdf, field, raw, page_h, off_x, off_y, scale, mode, warnings):
    text = field_text(field, raw)
    x = (float(field['x_mm']) * scale + off_x) * MM
    y_top = (float(field['y_mm']) * scale + off_y) * MM
    box_w = float(field['width_mm']) * MM * scale
    box_h = float(field['height_mm']) * MM * scale
    if mode == 'design':
        pdf.saveState()
        pdf.setStrokeColorRGB(*BRAND)
        pdf.setLineWidth(0.6)
        pdf.setDash(2, 2)
        pdf.rect(x, page_h - y_top - box_h, box_w, box_h)
        pdf.setFillColorRGB(*BRAND)
        pdf.setFont('Helvetica', 5)
        pdf.drawString(x, page_h - y_top + 1.5, '%s  %.1f,%.1f  %.1fx%.1f mm' % (
            field.get('key'), float(field['x_mm']), float(field['y_mm']), float(field['width_mm']),
            float(field['height_mm'])))
        pdf.restoreState()
        if not text:
            return
    if not text:
        return
    name, size, lines = _layout(field, text, scale, warnings)
    direction = field.get('direction') or 'auto'
    line_h = size * 1.2
    pdf.saveState()
    pdf.setFont(name, size)
    pdf.setFillColorRGB(0, 0, 0)
    if len(lines) == 1:
        baseline = page_h - (y_top + (box_h + size * 0.72) / 2.0)
    else:
        baseline = page_h - (y_top + size * 0.85)
    for index, line in enumerate(lines):
        visual = text_shaping.prepare(line, direction)
        width = pdfmetrics.stringWidth(visual, name, size)
        align = field.get('align') or 'auto'
        if align == 'auto':       # an Arabic line sits against the right edge of its box, an English one the left
            align = 'right' if text_shaping.base_direction(line, direction) == 'rtl' else 'left'
        if align == 'right':
            left = x + box_w - width
        elif align == 'center':
            left = x + (box_w - width) / 2.0
        else:
            left = x
        pdf.drawString(left, baseline - index * line_h, visual)
    pdf.restoreState()


def _draw_label(pdf, text, x, y, size, bold=False, max_width=None):
    """One label, wrapped to ``max_width`` points. Arabic text takes the Arabic-capable font, is shaped, and is aligned
    to the right edge of its width (x + max_width); anything else starts at ``x``. Returns the number of lines
    drawn."""
    rtl = text_shaping.has_rtl(text)
    if rtl or _needs_unicode(text):
        name, _ok = _font_name('dejavu', bold)
    else:
        name = 'Helvetica-Bold' if bold else 'Helvetica'
    lines = _wrap(text, 'auto', name, size, max_width) if max_width else [text]
    pdf.setFont(name, size)
    for index, line in enumerate(lines):
        visual = text_shaping.prepare(line, 'auto')
        left = x
        if rtl and max_width:
            left = x + max_width - pdfmetrics.stringWidth(visual, name, size)
        pdf.drawString(left, y - index * size * 1.3, visual)
    return len(lines)


def _design_banner(pdf, page_w, page_h, labels):
    pdf.saveState()
    pdf.setFillColorRGB(*BRAND)
    _draw_label(pdf, labels['design'], 3 * MM, page_h - 3 * MM, 6, bold=True, max_width=page_w - 6 * MM)
    pdf.restoreState()


def _watermark(pdf, page_w, page_h, labels):
    text = labels['watermark']
    name, _ok = _font_name('dejavu', True) if text_shaping.has_rtl(text) else ('Helvetica-Bold', False)
    pdf.saveState()
    pdf.setFillColorRGB(0.82, 0.82, 0.82)
    pdf.setFont(name, 26)
    pdf.translate(page_w / 2.0, page_h / 2.0)
    pdf.rotate(14)
    pdf.drawCentredString(0, 0, text_shaping.prepare(text, 'auto'))
    pdf.restoreState()


def _calibration(pdf, spec, profile, labels):
    """A measuring sheet: a 10 mm grid, labelled every 20 mm, corner crosses and the border, to be printed at
    100% on plain paper so the real shift of this printer can be read off with a ruler."""
    width_mm, height_mm = float(spec['width_mm']), float(spec['height_mm'])
    page_h = height_mm * MM
    pdf.saveState()
    pdf.setStrokeColorRGB(0.75, 0.75, 0.75)
    pdf.setLineWidth(0.2)
    x = 0.0
    while x <= width_mm + 1e-6:
        pdf.line(x * MM, 0, x * MM, page_h)
        x += 10.0
    y = 0.0
    while y <= height_mm + 1e-6:
        pdf.line(0, page_h - y * MM, width_mm * MM, page_h - y * MM)
        y += 10.0
    pdf.setFillColorRGB(0.35, 0.35, 0.35)
    pdf.setFont('Helvetica', 5)
    x = 20.0
    while x < width_mm:
        pdf.drawString(x * MM + 1, page_h - 3.2 * MM, '%d' % x)
        x += 20.0
    y = 20.0
    while y < height_mm:
        pdf.drawString(1.2 * MM, page_h - y * MM + 1, '%d' % y)
        y += 20.0
    pdf.setStrokeColorRGB(0, 0, 0)
    pdf.setLineWidth(0.3)
    pdf.rect(0.25 * MM, 0.25 * MM, (width_mm - 0.5) * MM, (height_mm - 0.5) * MM)
    arm = 4.0
    far_x, far_y = width_mm - 10.0, height_mm - 10.0
    for cx, cy in ((10.0, 10.0), (far_x, 10.0), (10.0, far_y), (far_x, far_y)):
        pdf.line((cx - arm) * MM, page_h - cy * MM, (cx + arm) * MM, page_h - cy * MM)
        pdf.line(cx * MM, page_h - (cy - arm) * MM, cx * MM, page_h - (cy + arm) * MM)
    text_w = (width_mm - 28.0) * MM
    pdf.setFillColorRGB(0, 0, 0)
    _draw_label(pdf, labels['calibration_title'], 14 * MM, page_h - 17 * MM, 7, bold=True, max_width=text_w)
    _draw_label(pdf, labels['calibration_page'] % {'name': spec.get('name') or '', 'w': width_mm, 'h': height_mm},
                14 * MM, page_h - 21 * MM, 6, max_width=text_w)
    help_lines = _draw_label(pdf, labels['calibration_help'], 14 * MM, page_h - 25 * MM, 6, max_width=text_w)
    _draw_label(pdf, labels['calibration_profile'] % {
        'x': float(profile.get('offset_x_mm') or 0.0), 'y': float(profile.get('offset_y_mm') or 0.0),
        's': float(profile.get('scale_pct') or 100.0)},
        14 * MM, page_h - 25 * MM - help_lines * 6 * 1.3 - 2, 5, max_width=text_w)
    pdf.restoreState()
