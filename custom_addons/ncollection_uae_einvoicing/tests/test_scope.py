# -*- coding: utf-8 -*-
"""Scope and exclusions (lib/scope.py): is an e-invoice required at all?

Each test names the official text it proves. Pure tests pin the rules and every date boundary; model tests pin that
the decision is made on a real posted ``account.move``, that an excluded document never produces XML, and that only an
e-invoicing manager can declare an exclusion (at the ORM, not in a view)."""

from datetime import date

from odoo import Command
from odoo.exceptions import AccessError, UserError
from odoo.tests import TransactionCase, tagged

from ..lib import scope
from .common import GROUP, EInvoiceCase

GROUP_TRN = '100000000000003'
OTHER_GROUP_TRN = '100000000000103'


def rules(found):
    return {issue.rule for issue in found}


@tagged('post_install', '-at_install')
class TestScopeRules(TransactionCase):

    def test_a_business_buyer_is_in_scope(self):
        """Guidelines V2.0 §6.1-6.2: every Business Transaction is in scope."""
        decided, found = scope.classify(date(2027, 3, 1))
        self.assertEqual((decided.state, found), (scope.IN_SCOPE, []))

    def test_a_government_buyer_is_in_scope(self):
        """Guidelines V2.0 §6.2: B2G is in scope."""
        decided, _found = scope.classify(date(2027, 3, 1), buyer_party_type='government')
        self.assertEqual(decided.state, scope.IN_SCOPE)

    def test_a_natural_person_not_in_business_is_not_required(self):
        """Guidelines V2.0 §6.2: supplies to natural persons who are not in Business are outside the scope."""
        decided, found = scope.classify(date(2027, 3, 1), buyer_party_type='consumer')
        self.assertEqual((decided.state, decided.reason_code), (scope.NOT_REQUIRED, 'b2c-natural-person'))
        self.assertIn('V2.0, §6.2', decided.source)
        self.assertEqual(found, [])

    def test_an_unset_party_type_is_an_error_never_a_guess(self):
        decided, found = scope.classify(date(2027, 3, 1), buyer_party_type=None)
        self.assertEqual(decided.state, scope.IN_SCOPE)
        self.assertEqual(rules(found), {'NCOLLECTION:scope-party-type-invalid'})

    def test_a_missing_transaction_date_is_an_error(self):
        decided, found = scope.classify(None)
        self.assertEqual(decided.state, scope.IN_SCOPE)
        self.assertEqual(rules(found), {'NCOLLECTION:scope-transaction-date-missing'})

    # ---- MD 243 Art 4(1)(f) as inserted by MD 197 Art 2: Tax Group, 24 months from 2027-01-01
    def _group(self, day, seller=GROUP_TRN, buyer=GROUP_TRN):
        return scope.classify(day, seller_vat_group=seller, buyer_vat_group=buyer)[0]

    def test_tax_group_window_first_day_is_excluded(self):
        decided = self._group(date(2027, 1, 1))
        self.assertEqual((decided.state, decided.reason_code), (scope.NOT_REQUIRED, 'tax-group-member'))
        self.assertEqual(decided.window, '2027-01-01..2028-12-31')
        self.assertIn('Art 4(1)(f)', decided.source)
        self.assertIn('MD 197/2026', decided.source)

    def test_tax_group_window_last_day_is_excluded(self):
        self.assertEqual(self._group(date(2028, 12, 31)).state, scope.NOT_REQUIRED)

    def test_the_day_before_the_tax_group_window_is_in_scope(self):
        self.assertEqual(self._group(date(2026, 12, 31)).state, scope.IN_SCOPE)

    def test_the_day_after_the_tax_group_window_is_in_scope(self):
        self.assertEqual(self._group(date(2029, 1, 1)).state, scope.IN_SCOPE)

    def test_different_tax_groups_are_in_scope(self):
        self.assertEqual(self._group(date(2027, 6, 1), buyer=OTHER_GROUP_TRN).state, scope.IN_SCOPE)

    def test_one_party_without_a_tax_group_is_in_scope(self):
        self.assertEqual(self._group(date(2027, 6, 1), buyer=None).state, scope.IN_SCOPE)

    # ---- MD 243 Art 4(1)(a)-(e): declared exclusions, verified as far as data allows
    def test_sovereign_activity_needs_a_government_seller(self):
        """MD 243 Art 4(1)(a): only a Government Entity's sovereign activity is excluded."""
        decided, found = scope.classify(date(2027, 3, 1), declared_exclusion='sovereign')
        self.assertEqual(decided.state, scope.IN_SCOPE)
        self.assertEqual(rules(found), {'NCOLLECTION:scope-sovereign-needs-government-seller'})
        decided, found = scope.classify(
            date(2027, 3, 1), seller_party_type='government', declared_exclusion='sovereign'
        )
        self.assertEqual((decided.state, decided.reason_code), (scope.NOT_REQUIRED, 'sovereign-government-activity'))
        self.assertIn('Art 4(1)(a)', decided.source)

    def test_airline_passenger_and_ancillary_are_accepted_as_declared(self):
        """MD 243 Art 4(1)(b) and (c)."""
        for declared, code, article in (
            ('airline_passenger', 'airline-passenger-e-ticket', 'Art 4(1)(b)'),
            ('airline_ancillary', 'airline-ancillary-emd', 'Art 4(1)(c)'),
        ):
            decided, found = scope.classify(date(2027, 3, 1), declared_exclusion=declared)
            self.assertEqual((decided.state, decided.reason_code, found), (scope.NOT_REQUIRED, code, []))
            self.assertIn(article, decided.source)

    def test_airline_cargo_needs_the_effective_date(self):
        """MD 243 Art 4(1)(d): 24 months from the date e-invoicing became effective, never assumed (OCQ-21)."""
        decided, found = scope.classify(date(2027, 3, 1), declared_exclusion='airline_cargo')
        self.assertEqual(decided.state, scope.IN_SCOPE)
        self.assertEqual(rules(found), {'NCOLLECTION:scope-airline-cargo-needs-effective-date'})

    def test_airline_cargo_window_boundaries(self):
        start = date(2027, 1, 1)
        inside_first = scope.classify(start, declared_exclusion='airline_cargo', effective_date=start)[0]
        inside_last = scope.classify(date(2028, 12, 31), declared_exclusion='airline_cargo', effective_date=start)[0]
        after, found = scope.classify(date(2029, 1, 1), declared_exclusion='airline_cargo', effective_date=start)
        before = scope.classify(date(2026, 12, 31), declared_exclusion='airline_cargo', effective_date=start)[0]
        self.assertEqual(inside_first.state, scope.NOT_REQUIRED)
        self.assertEqual(inside_first.window, '2027-01-01..2029-01-01')
        self.assertEqual(inside_last.state, scope.NOT_REQUIRED)
        self.assertEqual(after.state, scope.IN_SCOPE)
        self.assertEqual(rules(found), {'NCOLLECTION:scope-airline-cargo-outside-window'})
        self.assertEqual(before.state, scope.IN_SCOPE)

    def test_airline_cargo_window_from_a_month_end_is_clamped(self):
        decided = scope.classify(
            date(2029, 2, 27), declared_exclusion='airline_cargo', effective_date=date(2027, 2, 28)
        )[0]
        self.assertEqual(decided.window, '2027-02-28..2029-02-28')

    def test_financial_services_must_be_exempt_or_zero_rated(self):
        """MD 243 Art 4(1)(e): exempt or zero-rated financial services (VAT Executive Regulation Art 42)."""
        ok, found = scope.classify(date(2027, 3, 1), declared_exclusion='financial_services', categories={'E', 'Z'})
        self.assertEqual((ok.state, found), (scope.NOT_REQUIRED, []))
        self.assertIn('Art 4(1)(e)', ok.source)
        refused, found = scope.classify(
            date(2027, 3, 1), declared_exclusion='financial_services', categories={'E', 'S'}
        )
        self.assertEqual(refused.state, scope.IN_SCOPE)
        self.assertEqual(rules(found), {'NCOLLECTION:scope-financial-services-must-be-exempt-or-zero'})

    def test_an_unknown_declaration_is_refused(self):
        decided, found = scope.classify(date(2027, 3, 1), declared_exclusion='anything')
        self.assertEqual(decided.state, scope.IN_SCOPE)
        self.assertEqual(rules(found), {'NCOLLECTION:scope-exclusion-unknown'})

    def test_the_decision_is_deterministic(self):
        args = dict(seller_vat_group=GROUP_TRN, buyer_vat_group=GROUP_TRN)
        self.assertEqual(scope.classify(date(2027, 5, 5), **args), scope.classify(date(2027, 5, 5), **args))


@tagged('post_install', '-at_install')
class TestScopeOnRealDocuments(EInvoiceCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.manager = cls._user('scope.manager', [GROUP + 'group_einvoice_manager', 'account.group_account_invoice'])
        cls.clerk = cls._user('scope.clerk', [GROUP + 'group_einvoice_user', 'account.group_account_invoice'])

    def _dated_invoice(self, day, partner=None):
        return self._invoice(
            partner=partner, invoice_date=day, invoice_date_due=day.replace(day=28)
        )

    def test_a_consumer_buyer_ends_not_required_and_no_xml_is_ever_generated(self):
        consumer = self._partner('A Private Person', state='AZ')
        consumer.write({'company_type': 'person', 'nc_einvoice_party_type': 'consumer'})
        record = self._einvoice(self._invoice(partner=consumer))
        record.action_prepare()
        self.assertEqual(record.local_state, 'not_required')
        self.assertEqual((record.scope_state, record.scope_reason_code), ('not_required', 'b2c-natural-person'))
        self.assertIn('§6.2', record.scope_source)
        self.assertFalse(record.payload_json)
        with self.assertRaisesRegex(UserError, 'No e-invoice is required'):
            record.action_generate_xml()
        self.assertFalse(record.xml_attachment_id)
        self.assertIn('not_required', record.history_ids.mapped('event'))

    def test_a_person_who_is_not_a_company_is_not_assumed_to_be_a_consumer(self):
        trader = self._partner('Sole Trader', tin='1345678902', trn='134567890223003', registry='112345679000002')
        trader.company_type = 'person'
        record = self._einvoice(self._invoice(partner=trader))
        record.action_prepare()
        self.assertEqual(record.scope_state, 'in_scope')
        self.assertNotEqual(record.local_state, 'not_required')

    def test_tax_group_members_inside_the_window_are_not_required(self):
        self.company.partner_id.nc_vat_group_trn = GROUP_TRN
        self.buyer.nc_vat_group_trn = GROUP_TRN
        record = self._einvoice(self._dated_invoice(date(2027, 2, 1)))
        record.action_prepare()
        self.assertEqual((record.local_state, record.scope_reason_code), ('not_required', 'tax-group-member'))
        self.assertEqual(record.transaction_date, date(2027, 2, 1))
        self.assertEqual(record.scope_window, '2027-01-01..2028-12-31')

    def test_tax_group_members_before_the_window_are_in_scope(self):
        self.company.partner_id.nc_vat_group_trn = GROUP_TRN
        self.buyer.nc_vat_group_trn = GROUP_TRN
        record = self._einvoice(self._dated_invoice(date(2026, 12, 31)))
        record.action_prepare()
        self.assertEqual(record.scope_state, 'in_scope')
        self.assertEqual(record.local_state, 'ready', self.rules(record))

    def test_only_a_manager_can_declare_an_exclusion_at_the_orm(self):
        move = self._invoice()
        with self.assertRaises(AccessError):
            self.env['ncollection.einvoice'].with_user(self.clerk).create(
                {'move_id': move.id, 'excluded_transaction': 'airline_passenger'}
            )
        record = self.env['ncollection.einvoice'].with_user(self.clerk).create({'move_id': move.id})
        with self.assertRaises(AccessError):
            record.with_user(self.clerk).write({'excluded_transaction': 'airline_passenger'})
        record.with_user(self.manager).write({'excluded_transaction': 'airline_passenger'})
        self.assertIn('exclusion_declared', record.history_ids.mapped('event'))

    def test_declaring_an_exclusion_after_preparation_forces_a_new_decision(self):
        record = self._einvoice()
        record.action_prepare()
        self.assertEqual(record.local_state, 'ready')
        record.with_user(self.manager).write({'excluded_transaction': 'airline_passenger'})
        self.assertEqual(record.local_state, 'draft')
        record.action_prepare()
        self.assertEqual(
            (record.local_state, record.scope_reason_code), ('not_required', 'airline-passenger-e-ticket')
        )

    def test_a_refused_declaration_blocks_the_document(self):
        record = self._einvoice()
        record.with_user(self.manager).write({'excluded_transaction': 'sovereign'})
        record.action_prepare()
        self.assertEqual(record.local_state, 'draft')
        self.assertIn('NCOLLECTION:scope-sovereign-needs-government-seller', self.rules(record))
        self.assertEqual(
            record.validation_ids.filtered(lambda v: v.layer == 'scope').mapped('rule'),
            ['NCOLLECTION:scope-sovereign-needs-government-seller'],
        )

    def test_a_scope_change_after_preparation_refuses_the_xml(self):
        record = self._einvoice()
        record.action_prepare()
        self.assertEqual(record.local_state, 'ready')
        self.buyer.nc_einvoice_party_type = 'consumer'
        with self.assertRaisesRegex(UserError, 'Prepare it again'):
            record.action_generate_xml()
        self.assertFalse(record.xml_attachment_id)

    def test_financial_services_declaration_on_an_exempt_document(self):
        move = self._invoice(
            lines=[{'product_id': self.product.id, 'quantity': 1, 'price_unit': 100.0,
                    'tax_ids': [Command.set(self.tax_ext.ids)]}]
        )
        record = self._einvoice(move)
        record.with_user(self.manager).write({'excluded_transaction': 'financial_services'})
        record.action_prepare()
        self.assertEqual(
            (record.local_state, record.scope_reason_code), ('not_required', 'exempt-or-zero-rated-financial-services')
        )
