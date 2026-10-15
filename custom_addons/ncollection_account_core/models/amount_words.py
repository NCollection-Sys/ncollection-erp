# -*- coding: utf-8 -*-
"""Amount in words: ONE shared service for every financial document that spells an amount out.

WHY NOT ``res.currency.amount_to_text``. Odoo's own method is built for invoices, not for a cheque: it prints the
unit without a plural ("Three Thousand, One Hundred Dirham"), leaves the currency NAME in English inside Arabic text,
puts a comma inside the number and a space after the Arabic conjunction ("ثلاثة آلاف و مائة"). A cheque is a legal
instrument, so the wording has to be exact. This service uses ``num2words`` (already in the Odoo image) for the NUMBER
and owns only what num2words does not: the currency unit and subunit names, their plural/dual/accusative forms, the
"only" closing word, and the punctuation clean-up.

The service converts a number it is GIVEN. It never reads a payment, never positions anything on a page, and nothing in
a template calls it: a renderer consumes the finished string.

Languages: ``en`` and ``ar``. A currency this table does not know falls back to the unit labels Odoo already stores on
the currency (``currency_unit_label`` / ``currency_subunit_label``), so an unlisted currency still reads correctly in
English and never crashes.
"""
import re
from decimal import ROUND_HALF_UP, Decimal

from num2words import num2words

from odoo import api, models
from odoo.exceptions import UserError

# code -> (decimals, english (unit, units, subunit, subunits), arabic forms)
# Arabic forms are (singular, dual, plural 3-10, accusative 11-99) for the unit, then the same four for the subunit.
_CURRENCIES = {
    'AED': (2, ('Dirham', 'Dirhams', 'Fils', 'Fils'),
            (('درهم', 'درهمان', 'دراهم', 'درهمًا'), ('فلس', 'فلسان', 'فلوس', 'فلسًا'))),
    'SAR': (2, ('Riyal', 'Riyals', 'Halala', 'Halalas'),
            (('ريال', 'ريالان', 'ريالات', 'ريالًا'), ('هللة', 'هللتان', 'هللات', 'هللة'))),
    'QAR': (2, ('Riyal', 'Riyals', 'Dirham', 'Dirhams'),
            (('ريال', 'ريالان', 'ريالات', 'ريالًا'), ('درهم', 'درهمان', 'دراهم', 'درهمًا'))),
    'KWD': (3, ('Dinar', 'Dinars', 'Fils', 'Fils'),
            (('دينار', 'ديناران', 'دنانير', 'دينارًا'), ('فلس', 'فلسان', 'فلوس', 'فلسًا'))),
    'BHD': (3, ('Dinar', 'Dinars', 'Fils', 'Fils'),
            (('دينار', 'ديناران', 'دنانير', 'دينارًا'), ('فلس', 'فلسان', 'فلوس', 'فلسًا'))),
    'OMR': (3, ('Rial', 'Rials', 'Baisa', 'Baisas'),
            (('ريال', 'ريالان', 'ريالات', 'ريالًا'), ('بيسة', 'بيستان', 'بيسات', 'بيسة'))),
    'USD': (2, ('Dollar', 'Dollars', 'Cent', 'Cents'),
            (('دولار', 'دولاران', 'دولارات', 'دولارًا'), ('سنت', 'سنتان', 'سنتات', 'سنتًا'))),
    'EUR': (2, ('Euro', 'Euros', 'Cent', 'Cents'),
            (('يورو', 'يورو', 'يورو', 'يورو'), ('سنت', 'سنتان', 'سنتات', 'سنتًا'))),
    'GBP': (2, ('Pound', 'Pounds', 'Penny', 'Pence'),
            (('جنيه إسترليني', 'جنيهان إسترلينيان', 'جنيهات إسترلينية', 'جنيهًا إسترلينيًا'),
             ('بنس', 'بنسان', 'بنسات', 'بنسًا'))),
    'EGP': (2, ('Pound', 'Pounds', 'Piastre', 'Piastres'),
            (('جنيه', 'جنيهان', 'جنيهات', 'جنيهًا'), ('قرش', 'قرشان', 'قروش', 'قرشًا'))),
    'JOD': (3, ('Dinar', 'Dinars', 'Fils', 'Fils'),
            (('دينار', 'ديناران', 'دنانير', 'دينارًا'), ('فلس', 'فلسان', 'فلوس', 'فلسًا'))),
    'INR': (2, ('Rupee', 'Rupees', 'Paisa', 'Paise'),
            (('روبية', 'روبيتان', 'روبيات', 'روبيةً'), ('بيسة', 'بيستان', 'بيسات', 'بيسةً'))),
}

_ONLY = {'en': 'Only', 'ar': 'فقط لا غير'}
_AND = {'en': 'And', 'ar': 'و'}


def _arabic_form(forms, count):
    """The noun form Arabic grammar asks for after ``count``: 1 singular, 2 dual, 3-10 plural, 11-99 accusative
    singular, and the singular again for 100 and up (hundreds and thousands take the genitive singular)."""
    singular, dual, plural, accusative = forms
    if count == 1:
        return singular
    if count == 2:
        return dual
    tail = count % 100
    if 3 <= tail <= 10:
        return plural
    if 11 <= tail <= 99:
        return accusative
    return singular


class AmountWords(models.AbstractModel):
    _name = 'ncollection.amount.words'
    _description = 'Amount in words'

    @api.model
    def convert(self, amount, currency, lang='en', only=True):
        """``amount`` spelled out in ``lang`` ('en' or 'ar') for ``currency``.

        ``only`` appends the closing word that stops a cheque amount being extended ("Only" / "فقط لا غير").
        A negative amount is refused: nothing is ever written on a cheque for less than nothing."""
        lang = (lang or 'en').split('_')[0].lower()
        if lang not in ('en', 'ar'):
            raise UserError(self.env._("Amount in words is available in English and Arabic, not '%s'.", lang))
        value = Decimal(str(amount if amount is not None else 0))
        if value < 0:
            raise UserError(self.env._("A negative amount cannot be written in words."))
        code = (currency.name or '').upper() if currency else ''
        decimals, en_names, ar_names = self._currency_data(currency, code)
        units = 10 ** decimals
        total_minor = int((value * units).quantize(Decimal(1), rounding=ROUND_HALF_UP))
        major, minor = divmod(total_minor, units)
        words = self._phrase(major, minor, lang, en_names, ar_names)
        if only:
            words = '%s %s' % (words, _ONLY[lang])
        return words

    # ------------------------------------------------------------------ internals
    def _currency_data(self, currency, code):
        if code in _CURRENCIES:
            return _CURRENCIES[code]
        # An unlisted currency: the labels Odoo stores on it (English), the stored decimals, and no Arabic table.
        unit = (currency.currency_unit_label if currency else '') or code or 'Unit'
        sub = (currency.currency_subunit_label if currency else '') or 'Cent'
        decimals = currency.decimal_places if currency else 2
        return decimals, (unit, unit, sub, sub), None

    def _phrase(self, major, minor, lang, en_names, ar_names):
        if lang == 'en':
            return self._phrase_en(major, minor, en_names)
        if ar_names is None:
            # no Arabic noun table for this currency: the number in Arabic, the currency code kept as written
            raise UserError(self.env._(
                "Arabic wording is not available for this currency. Use English, or add it to the currency table."))
        return self._phrase_ar(major, minor, ar_names)

    @staticmethod
    def _title(text):
        # num2words keeps "-" ("ninety-two") and writes "and"; a cheque is title-cased and has no commas.
        text = text.replace(',', '')
        return ' '.join(word.capitalize() if '-' not in word else '-'.join(p.capitalize() for p in word.split('-'))
                        for word in text.split())

    def _phrase_en(self, major, minor, names):
        unit, units, sub, subs = names
        parts = []
        if major or not minor:
            parts.append('%s %s' % (self._title(num2words(major, lang='en')), unit if major == 1 else units))
        if minor:
            parts.append('%s %s' % (self._title(num2words(minor, lang='en')), sub if minor == 1 else subs))
        return (' %s ' % _AND['en']).join(parts)

    def _arabic_counted(self, count, forms):
        """'درهم واحد' for 1, 'درهمان' for 2 (the dual carries its own number), otherwise 'number noun'."""
        noun = _arabic_form(forms, count)
        if count == 1:
            return '%s واحد' % noun
        if count == 2:
            return noun
        return '%s %s' % (self._arabic_number(count), noun)

    def _phrase_ar(self, major, minor, names):
        unit_forms, sub_forms = names
        parts = []
        if major or not minor:
            parts.append(self._arabic_counted(major, unit_forms) if major else 'صفر %s' % unit_forms[0])
        if minor:
            parts.append(self._arabic_counted(minor, sub_forms))
        if len(parts) == 1:
            return parts[0]
        return '%s %s%s' % (parts[0], _AND['ar'], parts[1])    # "…درهم وخمسون فلسًا": the conjunction attaches

    @staticmethod
    def _arabic_number(number):
        text = num2words(number, lang='ar')
        # num2words writes the conjunction as a separate word ("ثلاثة آلاف و مائة"); written Arabic attaches it
        text = re.sub(r'\s+و\s+', ' و', text.strip())
        return re.sub(r'\s+', ' ', text)
