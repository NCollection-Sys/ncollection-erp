# -*- coding: utf-8 -*-
"""The pure PINT-AE logic: emirate mapping, payment means, identity/endpoints, allowances/charges, reconciliation,
readiness (HS/SAC and the rest) and the XML serialiser. Each test says which official rule or which decision it pins.
"""

import copy
from decimal import Decimal as D

from lxml import etree

from odoo.tests import TransactionCase, tagged

from ..lib import (
    adjustments,
    codelists,
    emirates,
    identity,
    issues as _issues,
    payment_means,
    readiness,
    reconcile,
    spec,
)
from ..lib.xml_builder import NS_CAC, NS_CBC, NS_CREDIT, NS_INVOICE, build
from . import payloads

NS = {'cac': NS_CAC, 'cbc': NS_CBC, 'inv': NS_INVOICE, 'cn': NS_CREDIT}


def rules(found):
    return {issue.rule for issue in found}


# ---------------------------------------------------------------------------------------------------------------------
@tagged('post_install', '-at_install')
class TestEmirateMapping(TransactionCase):

    def test_the_map_targets_are_exactly_the_seven_official_values(self):
        self.assertEqual(set(emirates.ODOO_STATE_TO_PINT.values()), set(spec.get_profile().emirate_codes))
        self.assertEqual(len(emirates.ODOO_STATE_TO_PINT), 7)

    def test_pint_codes_are_never_assumed_to_equal_odoo_state_codes(self):
        for odoo_code, pint_code in emirates.ODOO_STATE_TO_PINT.items():
            self.assertNotEqual(odoo_code, pint_code)

    def test_every_uae_state_that_odoo_ships_is_mapped(self):
        states = self.env['res.country.state'].search([('country_id.code', '=', 'AE')])
        self.assertEqual(len(states), 7)
        for state in states:
            self.assertIn(emirates.pint_subdivision(state.code), spec.get_profile().emirate_codes, state.code)

    def test_a_known_emirate_and_the_unknown_ones(self):
        self.assertEqual(emirates.pint_subdivision('DU'), 'DXB')
        self.assertEqual(emirates.pint_subdivision('az'), 'AUH')
        for unmapped in ('', None, 'DXB', 'XX', 'GB'):
            self.assertIsNone(emirates.pint_subdivision(unmapped), unmapped)


# ---------------------------------------------------------------------------------------------------------------------
@tagged('post_install', '-at_install')
class TestPaymentMeans(TransactionCase):

    def _resolve(self, **kw):
        base = {'type_code': '380', 'transaction_type_code': '00000000', 'has_payee_account': True}
        base.update(kw)
        return payment_means.resolve(**base)

    def test_a_configured_official_code_is_used(self):
        for code in codelists.codes('payment_means'):
            result = self._resolve(company_default=code, has_payee_account=True)
            self.assertEqual((result.code, result.omitted, result.issues), (code, False, []), code)

    def test_nothing_configured_is_an_error_never_a_guess(self):
        result = self._resolve()
        self.assertIsNone(result.code)
        self.assertFalse(result.omitted)
        self.assertEqual(rules(result.issues), {'OFFICIAL:ibr-191-ae'})

    def test_a_code_outside_the_official_list_is_refused(self):
        for bad in ('48', '99', 'cash', ''):
            if not bad:
                continue
            result = self._resolve(company_default=bad)
            self.assertIsNone(result.code)
            self.assertEqual(rules(result.issues), {'NCOLLECTION:payment-means-code-not-in-official-list'}, bad)

    def test_precedence_override_then_map_then_default(self):
        self.assertEqual(self._resolve(document_override='10', mapped_codes=['30'], company_default='54').code, '10')
        self.assertEqual(self._resolve(mapped_codes=['54'], company_default='10').code, '54')
        self.assertEqual(self._resolve(company_default='10').code, '10')

    def test_ambiguous_mapping_is_an_error(self):
        result = self._resolve(mapped_codes=['10', '30'], company_default='54')
        self.assertIsNone(result.code)
        self.assertEqual(rules(result.issues), {'NCOLLECTION:payment-means-ambiguous'})
        self.assertEqual(self._resolve(mapped_codes=['30', '30']).code, '30', "duplicates are not ambiguity")

    def test_credit_transfer_needs_the_payee_account(self):
        result = self._resolve(company_default='30', has_payee_account=False)
        self.assertEqual(result.code, '30')
        self.assertEqual(rules(result.issues), {'OFFICIAL:ibr-192-ae'})
        self.assertEqual(self._resolve(company_default='10', has_payee_account=False).issues, [])

    def test_the_official_exceptions_omit_the_payment_means(self):
        for type_code in ('381', '81', '261'):
            result = self._resolve(type_code=type_code)
            self.assertEqual((result.code, result.omitted, result.issues), (None, True, []), type_code)
        deemed = self._resolve(transaction_type_code=identity.transaction_type_code({'deemed_supply': True}))
        self.assertEqual((deemed.code, deemed.omitted, deemed.issues), (None, True, []))
        self.assertFalse(payment_means.exempt_from_payment_means('380', '10000000'))


# ---------------------------------------------------------------------------------------------------------------------
@tagged('post_install', '-at_install')
class TestIdentity(TransactionCase):

    def test_the_transaction_type_code_follows_the_official_flag_order(self):
        self.assertEqual(identity.transaction_type_code({}), '00000000')
        for position, name in enumerate(identity.FLAG_ORDER):
            code = identity.transaction_type_code({name: True})
            self.assertEqual(code, '0' * position + '1' + '0' * (7 - position), name)
        self.assertEqual(
            identity.parse_transaction_type('10000001'),
            {**{n: False for n in identity.FLAG_ORDER}, 'free_zone': True, 'export': True},
        )
        for bad in ('1', '0000000a', '000000000', ''):
            with self.assertRaises(ValueError):
                identity.parse_transaction_type(bad)
        with self.assertRaises(ValueError):
            identity.transaction_type_code({'nonsense': True})

    def test_the_flag_order_matches_the_shipped_code_list(self):
        names = {
            'free_zone': 'Free trade zone',
            'deemed_supply': 'Deemed supply',
            'margin_scheme': 'Profit Margin',
            'summary': 'Summary invoice',
            'continuous_supply': 'Continuous',
            'agent_billing': 'Agent billing',
            'ecommerce': 'E-commerce',
            'export': 'Exports',
        }
        rows = codelists.load('transaction_type').rows
        for position, name in enumerate(identity.FLAG_ORDER):
            pattern = 'X' * position + '1' + 'X' * (7 - position)
            self.assertIn(names[name].lower(), rows[pattern]['name'].lower(), name)

    def test_seller_endpoint_is_the_tin_with_scheme_0235(self):
        endpoint, found = identity.seller_endpoint('valid', '1987654321')
        self.assertEqual(
            (endpoint.scheme, endpoint.id, endpoint.source, endpoint.report_only),
            ('0235', '1987654321', 'seller_tin', False),
        )
        self.assertEqual(found, [])
        for state, rule in (
            ('missing', 'NCOLLECTION:seller-tin-missing'),
            ('invalid', 'NCOLLECTION:tin-must-be-10-digits'),
        ):
            endpoint, found = identity.seller_endpoint(state, None)
            self.assertIsNone(endpoint)
            self.assertEqual(rules(found), {rule})

    def test_buyer_endpoint_resolution_in_the_documented_order(self):
        def resolve(**kw):
            base = {'flags': {}, 'tin_state': 'missing', 'tin': None}
            base.update(kw)
            return identity.buyer_endpoint(**base)

        ep, _ = resolve(flags={'deemed_supply': True}, tin_state='valid', tin='1345678901')
        self.assertEqual((ep.id, ep.source, ep.report_only), ('9900000097', 'predefined_deemed', True))
        ep, _ = resolve(flags={'export': True})
        self.assertEqual((ep.id, ep.source, ep.report_only), ('9900000099', 'predefined_export', True))
        ep, _ = resolve(flags={'export': True}, tin_state='valid', tin='1345678901')
        self.assertEqual((ep.id, ep.source), ('1345678901', 'buyer_tin'), "an export buyer WITH an identity uses it")
        ep, _ = resolve(peppol_eas='0235', peppol_endpoint='1555555555', tin_state='valid', tin='1345678901')
        self.assertEqual((ep.scheme, ep.id, ep.source), ('0235', '1555555555', 'partner_peppol'))
        ep, _ = resolve(tin_state='valid', tin='1345678901')
        self.assertEqual((ep.scheme, ep.id, ep.source, ep.report_only), ('0235', '1345678901', 'buyer_tin', False))
        ep, _ = resolve(not_onboarded=True)
        self.assertEqual((ep.id, ep.source, ep.report_only), ('9900000098', 'predefined_not_onboarded', True))

    def test_a_missing_tin_never_becomes_not_onboarded_by_itself(self):
        ep, found = identity.buyer_endpoint(flags={}, tin_state='missing', tin=None)
        self.assertIsNone(ep)
        self.assertEqual(rules(found), {'NCOLLECTION:buyer-identity-missing'})

    def test_an_invalid_buyer_tin_is_an_error_even_if_not_onboarded_is_set(self):
        ep, found = identity.buyer_endpoint(flags={}, tin_state='invalid', tin='12', not_onboarded=True)
        self.assertIsNone(ep)
        self.assertEqual(rules(found), {'NCOLLECTION:tin-must-be-10-digits'})


# ---------------------------------------------------------------------------------------------------------------------
@tagged('post_install', '-at_install')
class TestAllowanceAndChargeMapping(TransactionCase):
    """D-13: a per-document-line toggle; the representation follows the official files."""

    def _line(self, **kw):
        line = {
            'id': '9',
            'name': 'Discount',
            'qty': D('1'),
            'price_unit': D('-100'),
            'net': D('-100'),
            'category': 'S',
            'rate': D('5'),
            'odoo_tax': D('-5'),
            'adjustment': None,
        }
        line.update(kw)
        return line

    def test_an_allowance_becomes_a_positive_document_level_allowance_with_its_category(self):
        items, adjs, found = adjustments.split_lines(
            [self._line(adjustment='allowance', reason_code='95', reason='Discount')]
        )
        self.assertEqual((items, found), ([], []))
        self.assertEqual(len(adjs), 1)
        self.assertEqual(
            (adjs[0]['charge'], adjs[0]['amount'], adjs[0]['category'], adjs[0]['rate'], adjs[0]['odoo_tax']),
            (False, D('100'), 'S', D('5'), D('-5')),
        )

    def test_a_charge_is_a_positive_document_level_charge(self):
        _items, adjs, found = adjustments.split_lines(
            [self._line(adjustment='charge', net=D('40'), price_unit=D('40'), odoo_tax=D('2'), reason_code='AAC')]
        )
        self.assertEqual(found, [])
        self.assertEqual((adjs[0]['charge'], adjs[0]['amount']), (True, D('40')))

    def test_unflagged_lines_stay_items_and_a_negative_unflagged_line_is_refused(self):
        positive = self._line(net=D('100'), price_unit=D('100'), odoo_tax=D('5'))
        items, adjs, found = adjustments.split_lines([positive])
        self.assertEqual((len(items), adjs, found), (1, [], []))
        items, adjs, found = adjustments.split_lines([self._line()])
        self.assertEqual((items, adjs), ([], []))
        self.assertEqual(rules(found), {'NCOLLECTION:unclassified-negative-line'})

    def test_the_direction_must_match_the_kind(self):
        _i, _a, found = adjustments.split_lines([self._line(adjustment='charge')])
        self.assertEqual(rules(found), {'NCOLLECTION:adjustment-direction'})
        _i, _a, found = adjustments.split_lines([self._line(adjustment='allowance', net=D('10'))])
        self.assertEqual(rules(found), {'NCOLLECTION:adjustment-direction'})

    def test_a_category_is_mandatory_and_the_margin_category_is_forbidden(self):
        _i, _a, found = adjustments.split_lines([self._line(adjustment='allowance', category=None)])
        self.assertEqual(rules(found), {'OFFICIAL:aligned-ibrp-032'})
        _i, _a, found = adjustments.split_lines([self._line(adjustment='allowance', category='N')])
        self.assertEqual(rules(found), {'OFFICIAL:ibr-115-ae'})
        _i, _a, found = adjustments.split_lines([self._line(adjustment='charge', net=D('5'), category='N')])
        self.assertEqual(rules(found), {'OFFICIAL:ibr-114-ae'})

    def test_reason_codes_come_only_from_the_official_lists(self):
        allowance_code = sorted(codelists.codes('allowance_reason'))[0]
        _i, adjs, found = adjustments.split_lines([self._line(adjustment='allowance', reason_code=allowance_code)])
        self.assertEqual((len(adjs), found), (1, []))
        _i, adjs, found = adjustments.split_lines([self._line(adjustment='allowance', reason_code='NOT-A-CODE')])
        self.assertEqual((adjs, rules(found)), ([], {'NCOLLECTION:reason-code-not-in-official-list'}))
        _i, adjs, found = adjustments.split_lines([self._line(adjustment='allowance')])
        self.assertEqual((len(adjs), found), (1, []), "a reason code is optional")

    def test_base_amount_and_percentage_go_together_and_must_agree_with_the_amount(self):
        _i, _a, found = adjustments.split_lines([self._line(adjustment='allowance', percent=D('10'))])
        self.assertEqual(rules(found), {'OFFICIAL:aligned-ibrp-057'})
        _i, adjs, found = adjustments.split_lines(
            [self._line(adjustment='allowance', percent=D('10'), base_amount=D('1000'))]
        )
        self.assertEqual((len(adjs), found), (1, []))
        _i, _a, found = adjustments.split_lines(
            [self._line(adjustment='allowance', percent=D('10'), base_amount=D('999'))]
        )
        self.assertEqual(rules(found), {'OFFICIAL:ibr-131-ae'})


# ---------------------------------------------------------------------------------------------------------------------
@tagged('post_install', '-at_install')
class TestReconciliation(TransactionCase):
    """Odoo's figures go into the XML unchanged; the official tolerances are applied exactly; otherwise BLOCKED."""

    def _many(self, count, price='0.30'):
        lines = [payloads._line(i + 1, 1, price) for i in range(count)]
        return {'lines': lines, 'adjustments': [], 'odoo_totals': payloads.totals_for(lines)}

    def test_a_clean_invoice_is_exact(self):
        result = reconcile.reconcile(payloads.standard_payload())
        self.assertEqual((result.state, result.issues), (reconcile.EXACT, []))
        self.assertTrue(all(check.ok for check in result.checks))

    def test_three_lines_of_0_30_differ_by_one_cent_and_are_within_the_official_tolerance(self):
        result = reconcile.reconcile(self._many(3))
        self.assertEqual(result.state, reconcile.WITHIN_TOLERANCE)
        self.assertEqual(result.issues, [])
        slack = [c for c in result.checks if c.rule == 'OFFICIAL:aligned-ibrp-s-09'][0]
        self.assertEqual((slack.odoo, slack.pint, slack.delta, slack.kind), (D('0.06'), D('0.05'), D('0.01'), 'slack'))
        # THE XML CARRIES WHAT ODOO BOOKED, not the recomputed 0.05
        self.assertEqual(result.figures['tax_total'], D('0.06'))
        self.assertEqual(result.figures['breakdown'][0]['tax'], D('0.06'))

    def test_the_tolerance_is_exactly_two_fils_and_no_more(self):
        """aligned-ibrp-s-09 uses u:slack(.., 0.02): a difference of 0.02 passes, 0.03 blocks."""

        def with_tax(delta):
            lines = [payloads._line(1, 1, '100.00', odoo_tax=D('5.00') + delta)]
            return {
                'lines': lines,
                'adjustments': [],
                'odoo_totals': {'untaxed': D('100.00'), 'tax': D('5.00') + delta, 'total': D('105.00') + delta},
            }

        self.assertEqual(reconcile.reconcile(with_tax(D('0.02'))).state, reconcile.WITHIN_TOLERANCE)
        self.assertEqual(reconcile.reconcile(with_tax(D('-0.02'))).state, reconcile.WITHIN_TOLERANCE)
        blocked = reconcile.reconcile(with_tax(D('0.03')))
        self.assertEqual(blocked.state, reconcile.BLOCKED)
        self.assertEqual(rules(blocked.issues), {'OFFICIAL:aligned-ibrp-s-09'})

    def test_forty_lines_of_0_30_cannot_satisfy_the_official_rule_and_are_blocked_without_changing_anything(self):
        payload = self._many(40)
        before = copy.deepcopy(payload)
        result = reconcile.reconcile(payload)
        self.assertEqual(result.state, reconcile.BLOCKED)
        self.assertEqual(rules(result.issues), {'OFFICIAL:aligned-ibrp-s-09'})
        self.assertEqual(payload, before, "reconciliation must not mutate the document")
        self.assertEqual(result.figures['tax_total'], D('0.80'), "the figures stay Odoo's, not the recomputed 0.60")
        self.assertIn('nothing was changed', result.issues[0].message.lower())

    def test_exact_checks_have_no_tolerance(self):
        for field, rule in (
            ('untaxed', 'OFFICIAL:ibr-co-13'),
            ('tax', 'OFFICIAL:ibr-co-14'),
            ('total', 'OFFICIAL:ibr-co-15'),
        ):
            payload = payloads.standard_payload()
            payload['odoo_totals'] = dict(payload['odoo_totals'])
            payload['odoo_totals'][field] += D('0.01')
            result = reconcile.reconcile(payload)
            self.assertEqual(result.state, reconcile.BLOCKED, field)
            self.assertIn(rule, rules(result.issues), field)

    def test_a_line_amount_that_is_not_quantity_times_net_price_is_blocked(self):
        payload = payloads.standard_payload()
        payload['lines'][0]['net'] += D('0.01')
        self.assertIn('OFFICIAL:ibr-147-ae', rules(reconcile.reconcile(payload).issues))

    def test_zero_tax_categories_must_carry_exactly_zero_tax(self):
        for category, rule in (
            ('Z', 'aligned-ibrp-z-09'),
            ('E', 'aligned-ibrp-e-09'),
            ('O', 'aligned-ibrp-o-09'),
            ('AE', 'aligned-ibrp-ae-09-ae'),
            ('N', 'ibr-108-ae'),
        ):
            line = payloads._line(
                1, 1, '100.00', rate='5' if category in ('AE', 'N') else '0', category=category, odoo_tax=D('0.01')
            )
            line['rate'] = None if category in ('E', 'O') else D('5') if category in ('AE', 'N') else D('0')
            payload = {
                'lines': [line],
                'adjustments': [],
                'odoo_totals': {'untaxed': D('100.00'), 'tax': D('0.01'), 'total': D('100.01')},
            }
            result = reconcile.reconcile(payload)
            self.assertEqual(result.state, reconcile.BLOCKED, category)
            self.assertIn('OFFICIAL:' + rule, rules(result.issues), category)
            line['odoo_tax'] = D('0.00')
            payload['odoo_totals'] = {'untaxed': D('100.00'), 'tax': D('0.00'), 'total': D('100.00')}
            self.assertEqual(reconcile.reconcile(payload).state, reconcile.EXACT, category)

    def test_a_standard_rate_other_than_the_profiles_is_blocked_not_coerced(self):
        line = payloads._line(1, 1, '100.00', rate='6', odoo_tax=D('6.00'))
        payload = {
            'lines': [line],
            'adjustments': [],
            'odoo_totals': {'untaxed': D('100.00'), 'tax': D('6.00'), 'total': D('106.00')},
        }
        result = reconcile.reconcile(payload)
        self.assertEqual(result.state, reconcile.BLOCKED)
        self.assertIn('OFFICIAL:ibr-190-ae', rules(result.issues))

    def test_allowances_and_charges_join_the_taxable_base_of_their_category(self):
        lines = [payloads._line(1, 1, '1000.00')]
        _items, adjs, _found = adjustments.split_lines(
            [
                {
                    'id': '2',
                    'name': 'Rebate',
                    'qty': D('1'),
                    'price_unit': D('-100'),
                    'net': D('-100'),
                    'category': 'S',
                    'rate': D('5'),
                    'odoo_tax': D('-5'),
                    'adjustment': 'allowance',
                },
                {
                    'id': '3',
                    'name': 'Rush',
                    'qty': D('1'),
                    'price_unit': D('40'),
                    'net': D('40'),
                    'category': 'S',
                    'rate': D('5'),
                    'odoo_tax': D('2'),
                    'adjustment': 'charge',
                },
            ]
        )
        totals = {'untaxed': D('940.00'), 'tax': D('47.00'), 'total': D('987.00')}
        result = reconcile.reconcile({'lines': lines, 'adjustments': adjs, 'odoo_totals': totals})
        self.assertEqual(result.state, reconcile.EXACT)
        self.assertEqual(result.figures['breakdown'][0]['taxable'], D('940.00'))
        self.assertEqual(
            (result.figures['allowance_total'], result.figures['charge_total']), (D('100.00'), D('40.00'))
        )

    def test_a_payable_rounding_amount_is_carried_when_present(self):
        payload = payloads.standard_payload()
        payload['odoo_totals'] = dict(payload['odoo_totals'], rounding=D('-0.03'))
        result = reconcile.reconcile(payload)
        self.assertEqual(result.figures['rounding'], D('-0.03'))
        self.assertEqual(result.figures['payable'], D('263.00'))


# ---------------------------------------------------------------------------------------------------------------------
def snapshot(**overrides):
    side = {
        'name': 'Party LLC',
        'tin_state': 'valid',
        'tin': '1987654321',
        'trn_state': 'valid',
        'trn': '198765432102003',
        'trn_advisory': False,
        'legal_id': '112345678900003',
        'legal_type': 'TL',
        'legal_authority': 'DED',
        'street': 'Road 1',
        'city': 'Dubai',
        'state_code': 'DU',
        'country': 'AE',
    }
    line = {
        'id': '1',
        'name': 'Widget',
        'description': 'A widget',
        'category': 'S',
        'rate': D('5'),
        'unit_code': 'H87',
        'item_type': 'G',
        'hs': '12345678',
        'sac': None,
    }
    snap = {
        'doc': {
            'kind': 'invoice',
            'type_code': '380',
            'flags': {},
            'transaction_type_code': '00000000',
            'currency': 'AED',
            'issue_date': '2026-10-01',
            'due_date': '2026-10-31',
            'payable': D('105'),
        },
        'seller': dict(side),
        'buyer': dict(side, tin='1345678901', trn='134567890123003'),
        'lines': [line],
        'adjustments': [],
    }
    for key, value in overrides.items():
        snap[key] = value
    return snap


@tagged('post_install', '-at_install')
class TestReadiness(TransactionCase):

    def test_a_complete_snapshot_is_clean(self):
        self.assertEqual(readiness.check(snapshot()), [])

    def test_every_finding_names_whose_rule_it_is(self):
        snap = snapshot()
        snap['seller'].update(tin_state='missing', tin=None, street=None)
        snap['lines'][0].update(hs=None, unit_code='ZZZ')
        found = readiness.check(snap)
        self.assertTrue(found)
        for issue in found:
            self.assertTrue(issue.rule.startswith(('OFFICIAL:', 'NCOLLECTION:')), issue.rule)
        self.assertIn('NCOLLECTION:seller-tin-missing', rules(found))
        self.assertIn('OFFICIAL:ibr-184-ae', rules(found))

    def test_not_applicable_is_an_engine_state(self):
        self.assertEqual(readiness.NOT_APPLICABLE, 'not_applicable')
        snap = snapshot()
        snap['doc'].update(
            flags={'export': True}, transaction_type_code=identity.transaction_type_code({'export': True})
        )
        snap['buyer'].update(tin_state='missing', tin=None, trn_state='missing', trn=None, legal_id=None, country='AE')
        self.assertNotIn(
            'NCOLLECTION:buyer-identity-missing',
            rules(readiness.check(snap)),
            "an export buyer with no identity is resolved by the predefined endpoint, not an error",
        )

    # ---- HS / SAC conditional on the declared item type
    def test_goods_need_hs_services_need_sac_both_need_both(self):
        def run(item_type, hs, sac):
            snap = snapshot()
            snap['lines'][0].update(item_type=item_type, hs=hs, sac=sac)
            return rules(readiness.check(snap))

        self.assertEqual(run('G', '123', None), set())
        self.assertEqual(run('G', None, None), {'OFFICIAL:ibr-184-ae'})
        self.assertEqual(run('G', None, '999'), {'OFFICIAL:ibr-184-ae'})
        self.assertEqual(run('S', None, '999'), set())
        self.assertEqual(run('S', '123', None), {'OFFICIAL:ibr-185-ae'})
        self.assertEqual(run('B', '123', '999'), set())
        self.assertEqual(run('B', '123', None), {'OFFICIAL:ibr-186-ae'})
        self.assertEqual(run('B', None, '999'), {'OFFICIAL:ibr-186-ae'})
        self.assertEqual(run('B', None, None), {'OFFICIAL:ibr-186-ae'})

    def test_an_undeclared_item_type_forces_nothing_but_a_warning(self):
        snap = snapshot()
        snap['lines'][0].update(item_type=None, hs=None, sac=None)
        found = readiness.check(snap)
        self.assertEqual(rules(found), {'NCOLLECTION:item-type-not-declared'})
        self.assertEqual({issue.severity for issue in found}, {_issues.WARNING})
        self.assertEqual(_issues.errors(found), [])

    def test_hs_sac_format_is_only_a_warning_because_the_official_length_rule_is_a_placeholder(self):
        snap = snapshot()
        snap['lines'][0].update(hs='6204.43')
        found = readiness.check(snap)
        self.assertEqual(rules(found), {'NCOLLECTION:classification-format'})
        self.assertEqual(_issues.errors(found), [])

    # ---- addresses and the emirate map
    def test_an_unmapped_or_missing_emirate_is_an_error(self):
        snap = snapshot()
        snap['buyer'].update(state_code='XX')
        self.assertEqual(rules(readiness.check(snap)), {'NCOLLECTION:subdivision-unmapped'})
        snap['buyer'].update(state_code=None)
        self.assertEqual(rules(readiness.check(snap)), {'OFFICIAL:ibr-144-ae'})
        snap = snapshot()
        snap['seller'].update(state_code=None)
        self.assertEqual(rules(readiness.check(snap)), {'OFFICIAL:ibr-143-ae'})

    # ---- identity
    def test_missing_invalid_and_valid_identifiers_report_differently(self):
        snap = snapshot()
        snap['seller'].update(trn_state='missing', trn=None)
        self.assertEqual(rules(readiness.check(snap)), {'OFFICIAL:ibr-134-ae'})
        snap['seller'].update(trn_state='invalid', trn='12')
        self.assertEqual(rules(readiness.check(snap)), {'NCOLLECTION:trn-must-be-15-digits'})
        snap = snapshot()
        snap['seller'].update(tin_state='invalid', tin='12')
        self.assertEqual(rules(readiness.check(snap)), {'NCOLLECTION:tin-must-be-10-digits'})

    def test_the_seller_trn_is_not_required_on_an_out_of_scope_document(self):
        snap = snapshot()
        snap['doc'].update(type_code='480')
        snap['seller'].update(trn_state='missing', trn=None)
        snap['lines'][0].update(category='O', rate=None)
        self.assertNotIn('OFFICIAL:ibr-134-ae', rules(readiness.check(snap)))

    def test_legal_registration_rules(self):
        snap = snapshot()
        snap['seller'].update(legal_id=None)
        self.assertEqual(rules(readiness.check(snap)), {'OFFICIAL:ibr-150-ae'})
        snap = snapshot()
        snap['seller'].update(legal_type=None)
        self.assertEqual(rules(readiness.check(snap)), {'OFFICIAL:ibr-181-ae'})
        snap = snapshot()
        snap['seller'].update(legal_authority=None)
        self.assertEqual(rules(readiness.check(snap)), {'OFFICIAL:ibr-172-ae'})
        snap = snapshot()
        snap['buyer'].update(legal_type='PAS', legal_country=None)
        self.assertEqual(rules(readiness.check(snap)), {'OFFICIAL:ibr-010-ae'})

    def test_advisory_trn_shape_is_a_warning_not_an_error(self):
        snap = snapshot()
        snap['seller'].update(trn='200000000000004', trn_advisory=True)
        found = readiness.check(snap)
        self.assertEqual(rules(found), {'OFFICIAL:ibr-132-ae'})
        self.assertEqual(_issues.errors(found), [])

    # ---- tax categories
    def test_category_and_rate_rules(self):
        def run(**line):
            snap = snapshot()
            snap['lines'][0].update(line)
            if line.get('category') in ('E', 'O'):
                snap['doc'].update(type_code='480')  # ibr-151-ae: only-E/O is an out-of-scope document
            return rules(readiness.check(snap))

        self.assertEqual(run(category='S', rate=D('6')), {'OFFICIAL:ibr-190-ae'})
        self.assertEqual(run(category='Z', rate=D('0')), set())
        self.assertEqual(run(category='Z', rate=D('5')), {'OFFICIAL:aligned-ibrp-z-05'})
        self.assertEqual(run(category='E', rate=None, exemption_code='DL8.46.1'), set())
        self.assertEqual(run(category='E', rate=D('5'), exemption_code='DL8.46.1'), {'OFFICIAL:aligned-ibrp-e-05'})
        self.assertEqual(run(category='E', rate=None, exemption_code=None), {'OFFICIAL:ibr-167-ae'})
        self.assertEqual(run(category='bogus'), {'OFFICIAL:ibr-139-ae'})

    def test_an_exemption_code_outside_the_shipped_list_is_only_a_warning(self):
        snap = snapshot()
        snap['doc'].update(type_code='480')
        snap['lines'][0].update(category='E', rate=None, exemption_code='VATEX-AE-EDU')
        found = readiness.check(snap)
        self.assertEqual(rules(found), {'NCOLLECTION:exemption-code-outside-shipped-list'})
        self.assertEqual(_issues.errors(found), [])

    def test_reverse_charge_needs_goods_type_gtin_and_the_buyer_trn(self):
        snap = snapshot()
        snap['lines'][0].update(category='AE', rate=D('5'), goods_type=None, gtin=None)
        snap['buyer'].update(trn_state='missing', trn=None)
        self.assertTrue(
            {'OFFICIAL:ibr-166-ae', 'OFFICIAL:ibr-174-ae', 'OFFICIAL:ibr-103-ae'} <= rules(readiness.check(snap))
        )
        snap = snapshot()
        snap['lines'][0].update(category='AE', rate=D('5'), goods_type='DL8.48.3.1', gtin='324243424')
        self.assertEqual(rules(readiness.check(snap)), set())

    # ---- document rules
    def test_credit_note_rules(self):
        snap = snapshot()
        snap['doc'].update(kind='credit_note', type_code='381', due_date=None, credit_reason=None, preceding=[])
        self.assertTrue({'OFFICIAL:ibr-158-ae', 'OFFICIAL:ibr-055-ae'} <= rules(readiness.check(snap)))
        snap['doc'].update(credit_reason='VD')
        self.assertEqual(rules(readiness.check(snap)), set(), "a volume discount needs no preceding invoice")
        snap['doc'].update(credit_reason='DL8.61.1.A', preceding=[{'number': 'X'}], vat_point_date='2026-09-30')
        self.assertEqual(rules(readiness.check(snap)), {'OFFICIAL:ibr-124-ae'})
        snap['doc'].update(vat_point_date=None, credit_reason='WRONG')
        self.assertEqual(rules(readiness.check(snap)), {'OFFICIAL:ibr-001-ae'})

    def test_a_tax_invoice_cannot_contain_only_exempt_or_not_subject_lines(self):
        """ibr-151-ae (found by the official end-to-end run): 380/381 need at least one taxable category; the converse
        of
        ibr-122-ae, which confines 480/81 to E/O. The document type follows from the categories."""
        for category in ('E', 'O'):
            snap = snapshot()
            snap['lines'][0].update(category=category, rate=None, exemption_code='DL8.46.1')
            self.assertIn('OFFICIAL:ibr-151-ae', rules(readiness.check(snap)), category)
            snap['doc'].update(type_code='480')
            self.assertNotIn('OFFICIAL:ibr-151-ae', rules(readiness.check(snap)), category)
        snap = snapshot()
        snap['lines'].append(dict(snap['lines'][0], id='2', category='E', rate=None, exemption_code='DL8.46.1'))
        self.assertNotIn('OFFICIAL:ibr-151-ae', rules(readiness.check(snap)), 'mixed S + E is a normal tax invoice')

    def test_a_predefined_endpoint_buyer_still_needs_an_identifier_unless_it_is_an_export(self):
        """ibr-135-ae has no exception for the predefined endpoints (found by the official end-to-end run)."""
        snap = snapshot()
        snap['doc'].update(flags={'deemed_supply': True}, transaction_type_code='01000000')
        snap['buyer'].update(tin_state='missing', tin=None, trn_state='missing', trn=None)
        self.assertIn('OFFICIAL:ibr-135-ae', rules(readiness.check(snap)))
        snap['buyer'].update(trn_state='valid', trn='134567890123003')
        self.assertNotIn('OFFICIAL:ibr-135-ae', rules(readiness.check(snap)))
        snap = snapshot()
        snap['doc'].update(flags={'export': True}, transaction_type_code='00000001')
        snap['buyer'].update(tin_state='missing', tin=None, trn_state='missing', trn=None)
        self.assertNotIn('OFFICIAL:ibr-135-ae', rules(readiness.check(snap)), 'exports are excepted by the rule')

    def test_the_vat_point_date_must_be_strictly_before_issue(self):
        snap = snapshot()
        snap['doc'].update(vat_point_date='2026-10-01')
        self.assertEqual(rules(readiness.check(snap)), {'OFFICIAL:ibr-141-ae'})
        snap['doc'].update(vat_point_date='2026-09-30')
        self.assertEqual(rules(readiness.check(snap)), set())

    def test_flag_driven_requirements(self):
        def run(flag, **doc):
            snap = snapshot()
            snap['doc'].update(
                flags={flag: True}, transaction_type_code=identity.transaction_type_code({flag: True}), **doc
            )
            return rules(readiness.check(snap))

        self.assertIn('OFFICIAL:ibr-007-ae', run('free_zone'))
        self.assertNotIn('OFFICIAL:ibr-007-ae', run('free_zone', beneficiary_id='B1'))
        self.assertIn('OFFICIAL:ibr-137-ae', run('agent_billing'))
        self.assertIn('OFFICIAL:ibr-138-ae', run('summary'))
        self.assertNotIn('OFFICIAL:ibr-138-ae', run('summary', period_start='2026-09-01', period_end='2026-09-30'))
        self.assertIn('OFFICIAL:ibr-116-ae', run('margin_scheme'))

    def test_foreign_currency_needs_a_rate_with_at_most_six_decimals(self):
        snap = snapshot()
        snap['doc'].update(currency='USD', exchange_rate=None)
        self.assertEqual(rules(readiness.check(snap)), {'OFFICIAL:ibr-159-ae'})
        snap['doc'].update(exchange_rate=D('3.6725'))
        self.assertEqual(rules(readiness.check(snap)), set())
        snap['doc'].update(exchange_rate=D('3.6725123'))
        self.assertEqual(rules(readiness.check(snap)), {'OFFICIAL:ibr-002-ae'})

    def test_a_due_date_is_required_unless_the_official_exceptions_apply(self):
        snap = snapshot()
        snap['doc'].update(due_date=None)
        self.assertEqual(rules(readiness.check(snap)), {'OFFICIAL:ibr-127-ae'})
        snap['doc'].update(payable=D('0'))
        self.assertEqual(rules(readiness.check(snap)), set())


# ---------------------------------------------------------------------------------------------------------------------
@tagged('post_install', '-at_install')
class TestXmlSerialiser(TransactionCase):

    def _tree(self, payload):
        return etree.fromstring(build(payload))

    def test_an_invoice_has_the_official_header_and_structure(self):
        root = self._tree(payloads.standard_payload())
        profile = spec.get_profile()
        self.assertEqual(root.tag, '{%s}Invoice' % NS_INVOICE)
        order = [etree.QName(child).localname for child in root]
        self.assertEqual(
            order[:9],
            [
                'CustomizationID',
                'ProfileID',
                'ProfileExecutionID',
                'ID',
                'UUID',
                'IssueDate',
                'IssueTime',
                'DueDate',
                'InvoiceTypeCode',
            ],
        )
        self.assertEqual(root.findtext('cbc:CustomizationID', namespaces=NS), profile.customization_id)
        self.assertEqual(root.findtext('cbc:ProfileID', namespaces=NS), profile.profile_id)
        self.assertEqual(root.findtext('cbc:ProfileExecutionID', namespaces=NS), '00000000')
        self.assertEqual(root.findtext('cbc:InvoiceTypeCode', namespaces=NS), '380')
        self.assertEqual(root.find('cac:AccountingSupplierParty/cac:Party/cbc:EndpointID', NS).get('schemeID'), '0235')
        self.assertEqual(
            root.findtext(
                'cac:AccountingSupplierParty/cac:Party/cac:PostalAddress/cbc:CountrySubentity', namespaces=NS
            ),
            'DXB',
        )
        self.assertEqual(len(root.findall('cac:InvoiceLine', NS)), 2)
        self.assertEqual(order[-3:], ['LegalMonetaryTotal', 'InvoiceLine', 'InvoiceLine'])

    def test_amounts_are_exactly_the_reconciled_figures_with_two_decimals(self):
        payload = payloads.standard_payload()
        root = self._tree(payload)
        totals = root.find('cac:LegalMonetaryTotal', NS)
        self.assertEqual(totals.findtext('cbc:LineExtensionAmount', namespaces=NS), '250.50')
        self.assertEqual(totals.findtext('cbc:TaxExclusiveAmount', namespaces=NS), '250.50')
        self.assertEqual(totals.findtext('cbc:TaxInclusiveAmount', namespaces=NS), '263.03')
        self.assertEqual(totals.findtext('cbc:PayableAmount', namespaces=NS), '263.03')
        self.assertEqual(root.findtext('cac:TaxTotal/cbc:TaxAmount', namespaces=NS), '12.53')
        sub = root.find('cac:TaxTotal/cac:TaxSubtotal', NS)
        self.assertEqual(
            (
                sub.findtext('cbc:TaxableAmount', namespaces=NS),
                sub.findtext('cbc:TaxAmount', namespaces=NS),
                sub.findtext('cac:TaxCategory/cbc:Percent', namespaces=NS),
            ),
            ('250.50', '12.53', '5'),
        )
        self.assertEqual(totals.find('cbc:PayableAmount', NS).get('currencyID'), 'AED')

    def test_the_xml_carries_odoos_booked_tax_not_the_recomputed_one(self):
        lines = [payloads._line(i + 1, 1, '0.30') for i in range(3)]
        payload = payloads.standard_payload(lines=lines, odoo_totals=payloads.totals_for(lines))
        self.assertEqual(self._tree(payload).findtext('cac:TaxTotal/cbc:TaxAmount', namespaces=NS), '0.06')

    def test_a_credit_note_uses_the_credit_note_structure(self):
        root = self._tree(payloads.credit_note_payload())
        self.assertEqual(root.tag, '{%s}CreditNote' % NS_CREDIT)
        self.assertEqual(root.findtext('cbc:CreditNoteTypeCode', namespaces=NS), '381')
        self.assertEqual(root.findtext('cac:DiscrepancyResponse/cbc:ResponseCode', namespaces=NS), 'DL8.61.1.E')
        self.assertEqual(
            root.findtext('cac:BillingReference/cac:InvoiceDocumentReference/cbc:ID', namespaces=NS), 'INV/2026/00001'
        )
        self.assertIsNone(root.find('cbc:DueDate', NS))
        self.assertEqual(len(root.findall('cac:CreditNoteLine', NS)), 1)
        self.assertIsNotNone(root.find('cac:CreditNoteLine/cbc:CreditedQuantity', NS))
        self.assertIsNone(root.find('cac:PaymentMeans', NS), "a credit note carries no payment means")

    def test_allowances_and_charges_are_emitted_with_their_indicator_and_category(self):
        _items, adjs, _found = adjustments.split_lines(
            [
                {
                    'id': '2',
                    'name': 'Rebate',
                    'qty': D('1'),
                    'price_unit': D('-100'),
                    'net': D('-100'),
                    'category': 'S',
                    'rate': D('5'),
                    'odoo_tax': D('-5'),
                    'adjustment': 'allowance',
                    'reason_code': '95',
                    'reason': 'Discount',
                }
            ]
        )
        lines = [payloads._line(1, 1, '1000.00')]
        payload = payloads.standard_payload(lines=lines, adjustments=adjs, odoo_totals=None)
        node = self._tree(payload).find('cac:AllowanceCharge', NS)
        self.assertEqual(node.findtext('cbc:ChargeIndicator', namespaces=NS), 'false')
        self.assertEqual(node.findtext('cbc:AllowanceChargeReasonCode', namespaces=NS), '95')
        self.assertEqual(node.findtext('cbc:Amount', namespaces=NS), '100.00')
        self.assertEqual(node.findtext('cac:TaxCategory/cbc:ID', namespaces=NS), 'S')

    def test_a_foreign_currency_document_carries_the_aed_elements(self):
        payload = payloads.standard_payload(
            currency='USD', exchange_rate=D('3.6725'), aed={'tax_total': D('46.02'), 'total_incl': D('965.98')}
        )
        root = self._tree(payload)
        self.assertEqual(root.findtext('cbc:TaxCurrencyCode', namespaces=NS), 'AED')
        self.assertEqual(root.findtext('cac:TaxExchangeRate/cbc:CalculationRate', namespaces=NS), '3.6725')
        self.assertEqual([e.get('currencyID') for e in root.findall('cac:TaxTotal/cbc:TaxAmount', NS)], ['USD', 'AED'])
        self.assertEqual(
            root.findtext('cac:AdditionalDocumentReference/cbc:DocumentTypeCode', namespaces=NS), 'aedtotal-incl-vat'
        )

    def test_a_seller_without_a_trn_carries_its_tin_as_the_tax_registration_id(self):
        payload = payloads.standard_payload()
        payload['seller']['trn'] = None
        schemes = self._tree(payload).findall('cac:AccountingSupplierParty/cac:Party/cac:PartyTaxScheme', NS)
        self.assertEqual(
            [
                (s.findtext('cbc:CompanyID', namespaces=NS), s.findtext('cac:TaxScheme/cbc:ID', namespaces=NS))
                for s in schemes
            ],
            [('1987654321', 'TIN')],
        )

    def test_an_exempt_line_has_no_percent_and_no_line_vat_amount(self):
        line = payloads._line(1, 1, '100.00', category='E', exemption_code='DL8.46.1', odoo_tax=D('0.00'))
        payload = payloads.standard_payload(lines=[line], odoo_totals=None)
        root = self._tree(payload)
        category = root.find('cac:InvoiceLine/cac:Item/cac:ClassifiedTaxCategory', NS)
        self.assertEqual(category.findtext('cbc:ID', namespaces=NS), 'E')
        self.assertIsNone(category.find('cbc:Percent', NS))
        self.assertEqual(category.findtext('cbc:TaxExemptionReasonCode', namespaces=NS), 'DL8.46.1')
        self.assertIsNone(root.find('cac:InvoiceLine/cac:ItemPriceExtension/cac:TaxTotal', NS))

    def test_every_line_carries_the_gross_price_and_base_quantity(self):
        """ibr-126-ae (found by the official end-to-end run): mandatory even when there is no discount."""
        root = self._tree(payloads.standard_payload())
        for price in root.findall('cac:InvoiceLine/cac:Price', NS):
            self.assertIsNotNone(price.find('cbc:BaseQuantity', NS))
            self.assertIsNotNone(price.find('cac:AllowanceCharge/cbc:BaseAmount', NS))
            self.assertEqual(price.findtext('cac:AllowanceCharge/cbc:Amount', namespaces=NS), '0.00')

    def test_a_discounted_price_is_gross_minus_discount(self):
        line = payloads._line(1, 1, '9.50', price_gross=D('10.00'), price_discount=D('0.50'), price_net=D('9.50'))
        root = self._tree(payloads.standard_payload(lines=[line], odoo_totals=None))
        price = root.find('cac:InvoiceLine/cac:Price', NS)
        self.assertEqual(price.findtext('cbc:PriceAmount', namespaces=NS), '9.50')
        self.assertEqual(price.findtext('cac:AllowanceCharge/cbc:Amount', namespaces=NS), '0.50')
        self.assertEqual(price.findtext('cac:AllowanceCharge/cbc:BaseAmount', namespaces=NS), '10.00')

    def test_classification_elements_follow_the_declared_item(self):
        goods = payloads._line(1, 1, '10.00', item_type='G', hs='12345')
        services = payloads._line(2, 1, '10.00', item_type='S', hs=None, sac='998311')
        root = self._tree(payloads.standard_payload(lines=[goods, services], odoo_totals=None))
        first, second = root.findall('cac:InvoiceLine/cac:Item', NS)
        self.assertEqual(first.find('cac:CommodityClassification/cbc:ItemClassificationCode', NS).get('listID'), 'HS')
        self.assertIsNone(first.find('cac:AdditionalItemIdentification', NS))
        self.assertEqual(second.find('cac:AdditionalItemIdentification/cbc:ID', NS).get('schemeID'), 'SAC')
        self.assertIsNone(second.find('cac:CommodityClassification/cbc:ItemClassificationCode', NS))

    def test_unicode_and_markup_are_escaped_not_interpreted(self):
        payload = payloads.standard_payload()
        payload['buyer']['name'] = 'شركة النور <script>&"\'</script> 🚀'
        root = self._tree(payload)
        self.assertEqual(
            root.findtext(
                'cac:AccountingCustomerParty/cac:Party/cac:PartyLegalEntity/cbc:RegistrationName', namespaces=NS
            ),
            payload['buyer']['name'],
        )
