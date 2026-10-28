# -*- coding: utf-8 -*-
"""UAE e-invoicing master data: ``nc_trn``, ``nc_tin``, legal registration, product classification.

What these prove, in the order the requirements were given:
  * TRN and TIN are two independent, optional, explicitly maintained fields; nothing derives one from the other or from
    ``vat`` and nothing falls back to ``vat``;
  * ``vat`` (the existing Tax ID) is untouched: same validation, no new coupling;
  * only a NON-EMPTY value in the wrong shape is refused;
  * no synchronisation to child contacts, no copy on duplicate;
  * readiness distinguishes missing / invalid / valid (never "not applicable": that is the engine's call);
  * HS / SAC / item type are optional on every product (the conditional rules belong to the e-invoice, not the form);
  * nothing is printed on the invoice PDF.
"""

from odoo.exceptions import ValidationError
from odoo.tests import TransactionCase, tagged
from odoo.tools import mute_logger

from ..models import uae_identifiers as ids


@tagged('post_install', '-at_install')
class TestTrnTinSeparation(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Partner = cls.env['res.partner']

    def test_both_fields_exist_and_are_optional(self):
        partner = self.Partner.create({'name': 'No identifiers'})
        self.assertFalse(partner.nc_trn)
        self.assertFalse(partner.nc_tin)
        for name in ('nc_trn', 'nc_tin'):
            field = self.Partner._fields[name]
            self.assertFalse(field.required, "%s must never be required to save a customer/vendor" % name)
            self.assertEqual(field.type, 'char')
        self.assertEqual(self.Partner._fields['nc_trn'].string, 'TRN / Tax Registration Number')
        self.assertEqual(self.Partner._fields['nc_tin'].string, 'TIN / Tax Identification Number')

    def test_neither_field_is_computed_related_defaulted_or_inverse(self):
        """The no-derivation rule at the field level: a derivation would have to live in one of these."""
        for name in ('nc_trn', 'nc_tin'):
            field = self.Partner._fields[name]
            self.assertFalse(field.compute, "%s must not be computed" % name)
            self.assertFalse(field.related, "%s must not be related" % name)
            self.assertFalse(field.inverse, "%s must not have an inverse" % name)
            self.assertIsNone(field.default, "%s must have no default" % name)
        onchange_fields = set(self.Partner._onchange_methods)
        self.assertFalse({'nc_trn', 'nc_tin'} & onchange_fields, "no onchange may write TRN/TIN")

    def test_setting_one_never_sets_the_other_or_vat(self):
        partner = self.Partner.create({'name': 'A', 'nc_trn': '100123456789003'})
        self.assertEqual(partner.nc_trn, '100123456789003')
        self.assertFalse(partner.nc_tin, "TIN must not be derived from the TRN")
        self.assertFalse(partner.vat, "vat must not be written from nc_trn")
        partner.nc_tin = '1001234567'
        self.assertEqual(partner.nc_trn, '100123456789003', "TRN must not be touched when the TIN is set")
        self.assertFalse(partner.vat)

    def test_vat_never_feeds_nc_trn_or_nc_tin(self):
        partner = self.Partner.create(
            {'name': 'B', 'country_id': self.env.ref('base.ae').id, 'vat': '100123456789003'}
        )
        self.assertEqual(partner.vat, '100123456789003')
        self.assertFalse(partner.nc_trn, "no fallback/copy from vat to nc_trn")
        self.assertFalse(partner.nc_tin, "no derivation from vat to nc_tin")
        # and the readiness view reports the truth: the TRN is missing even though vat holds a valid-looking number
        self.assertEqual(partner._nc_uae_identity_states()['trn'].state, ids.MISSING)

    def test_vat_validation_is_unchanged(self):
        """The existing Tax ID keeps its own validation, independent of the new fields."""
        self.assertTrue(self.Partner.check_vat_ae('100123456789003'))
        self.assertFalse(self.Partner.check_vat_ae('12345'))
        self.assertFalse(self.Partner.check_vat_ae('100123456789003\n'))

    def test_only_non_empty_values_in_the_wrong_shape_are_refused(self):
        for bad in (
            '12345678901234',
            '1234567890123456',
            '10012345678900A',
            '100 123 456 789 00',
            '100123456789003\n',
        ):
            with self.assertRaises(ValidationError, msg=repr(bad)):
                self.Partner.create({'name': 'X', 'nc_trn': bad})
        for bad in ('123456789', '12345678901', '12345678A0', '1234 56789', '1234567890\n', '١٢٣٤٥٦٧٨٩٠'):
            with self.assertRaises(ValidationError, msg=repr(bad)):
                self.Partner.create({'name': 'X', 'nc_tin': bad})
        for empty in (False, ''):
            partner = self.Partner.create({'name': 'Empty ok', 'nc_trn': empty, 'nc_tin': empty})
            self.assertFalse(partner.nc_trn or partner.nc_tin)

    def test_no_synchronisation_between_a_company_and_its_contacts(self):
        parent = self.Partner.create(
            {'name': 'Parent Co', 'is_company': True, 'nc_trn': '100123456789003', 'nc_tin': '1001234567'}
        )
        child = self.Partner.create({'name': 'Contact', 'parent_id': parent.id, 'type': 'contact'})
        self.assertFalse(child.nc_trn, "a contact must not silently inherit the company's TRN")
        self.assertFalse(child.nc_tin, "a contact must not silently inherit the company's TIN")
        child.write({'nc_tin': '1009999999'})
        self.assertEqual(parent.nc_tin, '1001234567', "a contact's TIN must never overwrite the company's")
        parent.write({'nc_tin': '1008888888', 'nc_trn': '100888888888003'})
        self.assertEqual(child.nc_tin, '1009999999')
        self.assertFalse(child.nc_trn)
        for name in ('nc_trn', 'nc_tin', 'nc_legal_reg_type', 'nc_legal_reg_authority', 'nc_legal_reg_country_id'):
            self.assertNotIn(name, parent._commercial_fields(), "%s must not be a commercial (synced) field" % name)
            self.assertNotIn(name, parent._synced_commercial_fields())

    def test_duplicating_a_partner_does_not_copy_regulated_identifiers(self):
        partner = self.Partner.create(
            {
                'name': 'Dup',
                'nc_trn': '100123456789003',
                'nc_tin': '1001234567',
                'nc_legal_reg_type': 'TL',
                'nc_legal_reg_authority': 'DED Dubai',
            }
        )
        copy = partner.copy()
        self.assertFalse(copy.nc_trn or copy.nc_tin or copy.nc_legal_reg_type or copy.nc_legal_reg_authority)

    def test_the_fields_are_tracked(self):
        for name in ('nc_trn', 'nc_tin', 'nc_legal_reg_type'):
            self.assertTrue(self.Partner._fields[name].tracking, "%s changes must leave a trail" % name)


@tagged('post_install', '-at_install')
class TestReadinessStates(TransactionCase):
    """missing / invalid / valid, with authority-labelled notes; 'not applicable' is never returned from here."""

    def test_trn_states(self):
        self.assertEqual(ids.trn_state(False).state, ids.MISSING)
        self.assertEqual(ids.trn_state('').state, ids.MISSING)
        self.assertEqual(ids.trn_state(None).state, ids.MISSING)
        invalid = ids.trn_state('12345')
        self.assertEqual(invalid.state, ids.INVALID)
        self.assertEqual(invalid.notes, ('NCOLLECTION:trn-must-be-15-digits',))
        self.assertEqual(ids.trn_state('100123456789003'), ids.IdentifierState(ids.VALID, ()))

    def test_trn_official_should_rule_is_advisory_only(self):
        """ibr-132-ae ("starts with 1, ends 03") is a *should*: valid, with an OFFICIAL note, never a refusal."""
        state = ids.trn_state('200123456789004')
        self.assertEqual(state.state, ids.VALID)
        self.assertEqual(state.notes, ('OFFICIAL:ibr-132-ae',))

    def test_tin_states(self):
        self.assertEqual(ids.tin_state(None).state, ids.MISSING)
        invalid = ids.tin_state('123')
        self.assertEqual(invalid.state, ids.INVALID)
        self.assertEqual(
            invalid.notes,
            ('NCOLLECTION:tin-must-be-10-digits',),
            "the endpoint format is OUR rule: the official Schematron has none",
        )
        self.assertEqual(ids.tin_state('1001234567'), ids.IdentifierState(ids.VALID, ()))
        self.assertEqual(ids.tin_state('2001234567').notes, ('OFFICIAL:ibr-148-ae',))

    def test_not_applicable_is_not_a_master_data_state(self):
        returned = {ids.trn_state(v).state for v in (None, 'x', '100123456789003')} | {
            ids.tin_state(v).state for v in (None, 'x', '1001234567')
        }
        self.assertEqual(returned, {ids.MISSING, ids.INVALID, ids.VALID})

    def test_partner_states_read_only_the_new_fields(self):
        partner = self.env['res.partner'].create({'name': 'S', 'nc_tin': '1001234567'})
        states = partner._nc_uae_identity_states()
        self.assertEqual(states['tin'].state, ids.VALID)
        self.assertEqual(states['trn'].state, ids.MISSING)


@tagged('post_install', '-at_install')
class TestLegalRegistrationAndClassification(TransactionCase):

    def test_registration_type_values_are_the_official_four(self):
        selection = dict(self.env['res.partner']._fields['nc_legal_reg_type'].selection)
        self.assertEqual(set(selection), {'TL', 'EID', 'PAS', 'CD'})

    def test_registration_details_are_optional_and_unenforced_on_the_form(self):
        """Conditional requirements (TL needs an authority, PAS a country) are the e-invoice's readiness rules, not
        something that stops a partner being saved."""
        partner = self.env['res.partner'].create({'name': 'Reg', 'nc_legal_reg_type': 'TL'})
        self.assertFalse(partner.nc_legal_reg_authority)
        partner = self.env['res.partner'].create({'name': 'Pass', 'nc_legal_reg_type': 'PAS'})
        self.assertFalse(partner.nc_legal_reg_country_id)

    def test_classification_is_optional_on_every_product(self):
        for item_type in (False, 'G', 'S', 'B'):
            product = self.env['product.template'].create({'name': 'P-%s' % item_type, 'nc_item_type': item_type})
            self.assertFalse(product.nc_hs_code, "no HS is forced for type %s" % item_type)
            self.assertFalse(product.nc_sac_code, "no SAC is forced for type %s" % item_type)
        for name in ('nc_item_type', 'nc_hs_code', 'nc_sac_code'):
            self.assertFalse(self.env['product.template']._fields[name].required)

    def test_item_type_values_are_the_official_three(self):
        selection = dict(self.env['product.template']._fields['nc_item_type'].selection)
        self.assertEqual(selection, {'G': 'Goods', 'S': 'Services', 'B': 'Both'})


@tagged('post_install', '-at_install')
class TestNothingIsPrinted(TransactionCase):
    """Existing PDFs must not start showing the new identifiers (a separate business requirement would be needed)."""

    @mute_logger('odoo.addons.base.models.ir_attachment')
    def test_invoice_report_does_not_show_nc_trn_or_nc_tin(self):
        company = self.env['res.company'].create({'name': 'Print Co'})
        company._nc_apply_uae_localization()
        company.partner_id.write({'nc_trn': '100999999999003', 'nc_tin': '1009999999'})
        env = self.env(context=dict(self.env.context, allowed_company_ids=company.ids))
        product = env['product.product'].create({'name': 'Widget', 'list_price': 10.0})
        partner = env['res.partner'].create(
            {
                'name': 'Cust',
                'company_type': 'company',
                'country_id': self.env.ref('base.ae').id,
                'nc_trn': '100888888888003',
                'nc_tin': '1008888888',
            }
        )
        invoice = env['account.move'].create(
            {
                'move_type': 'out_invoice',
                'company_id': company.id,
                'partner_id': partner.id,
                'invoice_line_ids': [(0, 0, {'product_id': product.id, 'quantity': 1, 'price_unit': 10.0})],
            }
        )
        invoice.action_post()
        html, _dtype = self.env['ir.actions.report']._render_qweb_html('account.account_invoices', invoice.ids)
        html = html.decode() if isinstance(html, bytes) else html
        for value in ('100999999999003', '1009999999', '100888888888003', '1008888888'):
            self.assertNotIn(value, html, "%s must not appear on the printed invoice" % value)
