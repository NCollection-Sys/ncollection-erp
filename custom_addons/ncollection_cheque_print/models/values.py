# -*- coding: utf-8 -*-
import datetime
import logging

from odoo import api, models
from odoo.exceptions import UserError

from .field_registry import OUTPUT_LANGS
from .renderer import LANG_LOCALES

_logger = logging.getLogger(__name__)

_SAMPLE_WORDS = {
    'en': 'Twelve Thousand Three Hundred And Forty-Five Dirhams And Sixty-Seven Fils Only',
    'ar': 'اثنا عشر ألفاً وثلاثمائة وخمسة وأربعون درهمًا وسبعة وستون فلسًا فقط لا غير',
}


class ChequeValues(models.AbstractModel):
    """The ONE place the facts printed on a cheque are read from an ``account.payment``.

    The result is plain typed data (strings, a date, a number); the renderer formats it per field. Nothing is copied
    into a model of its own except by the print job, which snapshots it at print time."""
    _name = 'ncollection.cheque.values'
    _description = 'Cheque values'

    @api.model
    def default_lang(self):
        """The print language to offer: Arabic for an Arabic user, English otherwise. Only a default."""
        return 'ar' if (self.env.lang or '').startswith('ar') else 'en'

    @api.model
    def clean_lang(self, lang):
        return lang if lang in dict(OUTPUT_LANGS) else self.default_lang()

    @api.model
    def labels(self, lang=None):
        """The words printed on the preview, design and calibration pages, translated into the PRINT language through
        Odoo's own translation (the catalogue of this module), so a page in Arabic reads Arabic."""
        lang = self.clean_lang(lang)
        code = LANG_LOCALES[lang]
        if code not in {installed for installed, _name in self.env['res.lang'].get_installed()}:
            # Arabic is not installed on this database: Odoo refuses to translate into a language it does not have.
            # Fall back to the person's own language rather than failing a preview.
            code = self.env.lang or 'en_US'
        env = self.with_context(lang=code).env
        return {
            'design': env._("DESIGN PREVIEW - the reference image and the boxes are NOT printed"),
            'watermark': env._("PREVIEW - NOT A PRINT"),
            'calibration_title': env._("Cheque calibration"),
            'calibration_page': env._(
                "%(name)s   |   page %(w).1f x %(h).1f mm   |   print at 100 percent (Actual size)"),
            'calibration_help': env._(
                "Each cross is exactly 10 mm from two edges. Measure from the paper edge to a cross: any difference "
                "is the printer shift. Enter it as the horizontal / vertical offset in the printer profile."),
            'calibration_profile': env._(
                "Profile in use: x %(x)+.1f mm   y %(y)+.1f mm   scale %(s).1f percent   "
                "(not applied to this sheet)"),
        }

    @api.model
    def from_payment(self, payment, cheque_number='', lang=None):
        payment.ensure_one()
        currency = payment.currency_id
        words_service = self.env['ncollection.amount.words']
        words = {}
        for words_lang in ('en', 'ar'):
            try:
                words[words_lang] = words_service.convert(payment.amount, currency, words_lang)
            except UserError:            # a currency with no Arabic wording: that language is simply blank
                words[words_lang] = ''
        partner = payment.partner_id
        bank_account = payment.journal_id.bank_account_id
        return {
            'lang': self.clean_lang(lang),
            'payee': ((partner.commercial_partner_id.name or partner.name) if partner else '') or '',
            'date': payment.date,
            'amount': payment.amount,
            'decimals': currency.decimal_places,
            'currency': currency.name or '',
            'amount_words': words,
            'reference': payment.memo or payment.name or '',
            'cheque_number': cheque_number or '',
            'account_number': bank_account.acc_number or '',
            'bank_name': bank_account.bank_id.name or payment.journal_id.bank_id.name or '',
        }

    @api.model
    def sample(self, lang=None):
        """Synthetic facts for the designer and the calibration of a template, so no real payment is needed to design
        a cheque. They are invented: nothing here is, or looks up, a real account."""
        lang = self.clean_lang(lang)
        words = dict(_SAMPLE_WORDS)
        aed = self.env.ref('base.AED', raise_if_not_found=False)
        if aed:
            for words_lang in ('en', 'ar'):
                try:
                    words[words_lang] = self.env['ncollection.amount.words'].convert(12345.67, aed, words_lang)
                except UserError:
                    _logger.info("no sample amount in words for %s: the built-in sample is used", words_lang)
        payee = 'شركة النموذج للتجارة ذ.م.م' if lang == 'ar' else 'Sample Trading LLC'
        return {
            'lang': lang, 'payee': payee, 'date': datetime.date(2026, 10, 14), 'amount': 12345.67, 'decimals': 2,
            'currency': 'AED', 'amount_words': words, 'reference': 'PAY/2026/0001', 'cheque_number': '000123',
            'account_number': 'SAMPLE-ACCOUNT', 'bank_name': 'Sample Bank',
        }
