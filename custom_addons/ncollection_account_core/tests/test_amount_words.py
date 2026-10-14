# -*- coding: utf-8 -*-
"""The shared amount-in-words service: English and Arabic, currency forms and fractions, outside any template."""
from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestAmountWords(TransactionCase):

    def _words(self, amount, code='AED', lang='en', **kw):
        return self.env['ncollection.amount.words'].convert(amount, self.env.ref('base.' + code), lang, **kw)

    def test_english(self):
        self.assertEqual(self._words(1.0), 'One Dirham Only')
        self.assertEqual(self._words(2.5), 'Two Dirhams And Fifty Fils Only')
        self.assertEqual(self._words(12345.67),
                         'Twelve Thousand Three Hundred And Forty-Five Dirhams And Sixty-Seven Fils Only')
        self.assertEqual(self._words(0.5), 'Fifty Fils Only')
        self.assertEqual(self._words(0.0), 'Zero Dirhams Only')
        self.assertEqual(self._words(21.01, 'USD'), 'Twenty-One Dollars And One Cent Only')
        self.assertEqual(self._words(1000000.0, 'SAR'), 'One Million Riyals Only')

    def test_arabic_uses_grammatical_forms(self):
        self.assertEqual(self._words(1.0, lang='ar'), 'درهم واحد فقط لا غير')
        self.assertEqual(self._words(2.5, lang='ar'), 'درهمان وخمسون فلسًا فقط لا غير')      # dual, then tamyiz
        self.assertEqual(self._words(100000.0, lang='ar'), 'مائة ألف درهم فقط لا غير')
        self.assertEqual(self._words(12345.67, lang='ar'),
                         'اثنا عشر ألفاً وثلاثمائة وخمسة وأربعون درهمًا وسبعة وستون فلسًا فقط لا غير')

    def test_only_suffix_is_optional(self):
        self.assertEqual(self._words(1.0, only=False), 'One Dirham')

    def test_unsupported_input_is_refused_not_guessed(self):
        with self.assertRaises(UserError):
            self._words(1.0, lang='fr')
        with self.assertRaises(UserError):
            self._words(-5.0)
