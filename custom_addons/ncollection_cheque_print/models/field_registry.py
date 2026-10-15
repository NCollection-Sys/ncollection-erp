# -*- coding: utf-8 -*-
"""The controlled list of things a cheque template can print.

A template field is NOT free code: it names one entry of this registry, and the renderer knows how to turn that entry
into text from the typed payment facts (see ``renderer.field_text``). Adding a bank is therefore data only; adding a
genuinely new KIND of value is one line here plus one branch in the renderer.
"""
from .renderer import DATE_FORMATS, FONT_CHOICES  # noqa: F401  (re-exported for the models)

# key, label, the sample the designer shows, and the typical default box (mm: width, height) and size for a new field
FIELD_REGISTRY = [
    ('payee', 'Payee', 'Sample Trading LLC', (110.0, 8.0), 11.0),
    ('payment_date', 'Payment Date', '14/10/2026', (30.0, 7.0), 11.0),
    ('amount', 'Amount', '12,345.67', (35.0, 8.0), 12.0),
    ('currency', 'Currency', 'AED', (15.0, 7.0), 10.0),
    ('amount_words', 'Amount in Words', 'Twelve Thousand Three Hundred And Forty-Five Dirhams Only',
     (110.0, 14.0), 10.0),
    ('reference', 'Reference', 'PAY/2026/0001', (60.0, 7.0), 9.0),
    ('cheque_number', 'Cheque Number', '000123', (30.0, 7.0), 10.0),
    ('account_number', 'Account Number', '****1234', (45.0, 7.0), 10.0),
    ('bank_name', 'Bank Name', 'Sample Bank', (50.0, 7.0), 10.0),
    ('static_text', 'Static Text', 'Fixed text', (50.0, 7.0), 10.0),
]

FIELD_KEYS = [(key, label) for key, label, _sample, _box, _size in FIELD_REGISTRY]
SAMPLES = {key: sample for key, _label, sample, _box, _size in FIELD_REGISTRY}
DEFAULT_BOX = {key: (box, size) for key, _label, _sample, box, size in FIELD_REGISTRY}

ALIGN = [('auto', 'Automatic (follows the text)'), ('left', 'Left'), ('center', 'Center'), ('right', 'Right')]
DIRECTION = [('auto', 'Automatic'), ('ltr', 'Left to right'), ('rtl', 'Right to left')]
CASE = [('as_is', 'As typed'), ('upper', 'UPPER CASE'), ('lower', 'lower case'), ('title', 'Title Case')]
DIGITS = [('western', 'Western (0123)'), ('arabic', 'Arabic-Indic (٠١٢٣)')]
FIT = [('shrink', 'One line, shrink to fit'), ('wrap', 'Wrap onto several lines')]
# The language a cheque is PRINTED in: chosen when printing (it is not the language of the screen).
OUTPUT_LANGS = [('en', 'English'), ('ar', 'Arabic')]
WORDS_LANG = [('auto', 'Follow the print language'), ('en', 'English'), ('ar', 'Arabic')]
