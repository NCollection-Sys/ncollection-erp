# -*- coding: utf-8 -*-
"""ncollection_account_localization_uae (F5-T01) — scaffold unit tests.

Cover the two net-new deliverables: UAE TRN validation (plugged into base_vat's
dispatch, on partner AND company) and the per-company FTA compliance checklist
(seeded, idempotent, with a readiness rollup). No accounting-engine behaviour is
exercised — Odoo owns that.
"""
from odoo.exceptions import ValidationError
from odoo.tests import TransactionCase, tagged

from ..models.fta_compliance import STANDARD_FTA_ITEMS


@tagged('post_install', '-at_install')
class TestUaeLocalization(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.ae = cls.env.ref('base.ae')  # United Arab Emirates
        cls.Partner = cls.env['res.partner']
        cls.Company = cls.env['res.company']
        cls.Item = cls.env['ncollection.fta.compliance.item']

    def test_module_installed(self):
        m = self.env['ir.module.module'].search(
            [('name', '=', 'ncollection_account_localization_uae')], limit=1)
        self.assertEqual(m.state, 'installed')

    # ---- TRN validation (base_vat dispatch: check_vat_ae) ----------------

    def test_valid_trn_accepted(self):
        p = self.Partner.create({
            'name': 'Emirates Co', 'country_id': self.ae.id,
            'vat': '123456789012345'})  # exactly 15 digits
        self.assertEqual(p.vat, '123456789012345')

    def test_invalid_trn_rejected(self):
        for bad in ('12345678901234',       # 14 digits
                    '1234567890123456',      # 16 digits
                    'ABCDEFGHIJKLMNO',       # non-numeric
                    '123456789012345\n'):    # trailing newline — fullmatch rejects
            with self.assertRaises(ValidationError):
                self.Partner.create({
                    'name': 'Bad TRN', 'country_id': self.ae.id, 'vat': bad})

    def test_company_partner_trn_validated(self):
        """A company is a partner (company.vat is related to partner_id.vat), so
        the same check_vat_ae path guards a company's TRN."""
        with self.assertRaises(ValidationError):
            self.Partner.create({
                'name': 'Bad Co', 'is_company': True,
                'country_id': self.ae.id, 'vat': '999'})

    # ---- FTA compliance checklist ----------------------------------------

    def test_fta_items_seeded_for_existing_company(self):
        keys = set(self.Item.search(
            [('company_id', '=', self.env.company.id)]).mapped('key'))
        for key, _name, _seq in STANDARD_FTA_ITEMS:
            self.assertIn(key, keys, "post_init_hook seeds every standard item")

    def test_new_company_gets_checklist(self):
        company = self.Company.create({'name': 'Fresh FTA Co'})
        items = self.Item.search([('company_id', '=', company.id)])
        self.assertEqual(len(items), len(STANDARD_FTA_ITEMS),
                         "res.company.create seeds the checklist")

    def test_readiness_rollup(self):
        company = self.Company.create({'name': 'Readiness Co'})
        items = self.Item.search([('company_id', '=', company.id)])
        self.assertEqual(company.fta_readiness, 0)
        items[0].state = 'done'
        self.assertEqual(company.fta_readiness, round(100 / len(items)))
        # 'not applicable' rows are excluded from the base.
        items[1].state = 'not_applicable'
        applicable = len(items) - 1
        self.assertEqual(company.fta_readiness, round(100 / applicable))

    def test_readiness_zero_when_all_not_applicable(self):
        """Every item not-applicable -> empty base -> 0, not a ZeroDivisionError."""
        company = self.Company.create({'name': 'All-NA Co'})
        self.Item.search([('company_id', '=', company.id)]).write(
            {'state': 'not_applicable'})
        self.assertEqual(company.fta_readiness, 0)

    def test_seeding_is_idempotent(self):
        company = self.env.company
        before = self.Item.search_count([('company_id', '=', company.id)])
        self.Item._ensure_for_company(company)  # re-run
        after = self.Item.search_count([('company_id', '=', company.id)])
        self.assertEqual(before, after, "re-seeding creates no duplicates")

    def test_duplicate_key_rejected(self):
        from psycopg2 import IntegrityError
        from odoo.tools import mute_logger
        company = self.env.company
        with mute_logger('odoo.sql_db'), self.assertRaises(IntegrityError):
            with self.env.cr.savepoint():
                self.Item.create({
                    'key': 'trn_registered', 'name': 'dup',
                    'company_id': company.id})  # already seeded -> unique breach
                self.env.flush_all()
