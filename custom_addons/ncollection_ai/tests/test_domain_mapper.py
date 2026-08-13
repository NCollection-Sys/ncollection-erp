# -*- coding: utf-8 -*-
"""Conformance and safety tests for the NL->domain mapper (P5-T05 / #62).

No provider is ever contacted. ``AiGatewayClient._complete`` is patched with a
deterministic response per case (the pattern established by
``test_ai_question.py``), and the ``/healthz`` probe is patched to report the
mock provider, so these assert on what the VALIDATOR does with a payload rather
than on what a model happens to generate.

The cases live in ``data/domain_test_set.json`` so they can be reviewed as data.
"""
import ast
import json
import os
from unittest.mock import patch

from odoo.exceptions import AccessError, UserError
from odoo.tests import TransactionCase, tagged

from odoo.addons.ncollection_ai.models import domain_schema

_GATEWAY = ('odoo.addons.ncollection_ai.models.gateway_client'
            '.AiGatewayClient._complete')
_PROBE = ('odoo.addons.ncollection_ai.models.domain_mapper'
          '.AiDomainMapper._gateway_provider')

_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_MAPPER_SRC = os.path.join(_HERE, 'models', 'domain_mapper.py')
_SCHEMA_SRC = os.path.join(_HERE, 'models', 'domain_schema.py')


def _load_cases():
    path = os.path.join(_HERE, 'data', 'domain_test_set.json')
    with open(path, encoding='utf-8') as handle:
        return json.load(handle)['cases']


CASES = _load_cases()
VALID = [c for c in CASES if c['category'] == 'valid']
REFUSING = [c for c in CASES if c.get('expect') == 'refusal']


@tagged('post_install', '-at_install')
class TestDomainMapperConformance(TransactionCase):
    """The 50-question set and its adversarial companions."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Mapper = cls.env['ncollection.ai.domain.mapper']
        # The mapper is OFF by default (pinned in TestDomainMapperGates). These
        # tests exercise what is BEHIND the switch, so they turn it on here —
        # which doubles as a standing reminder that an install does not have it.
        cls.env['ir.config_parameter'].sudo().set_param(
            'ncollection_ai.enable_nl_domain_mapper', 'True')

    def _map(self, case):
        """Run one case with its canned provider response."""
        def fake_complete(_self, prompt, max_tokens=1024):
            return {'text': case['provider_response'], 'usage': {}}

        with patch(_PROBE, return_value='mock'), \
                patch(_GATEWAY, new=fake_complete):
            return self.Mapper.map_question(case['question'], case['model'])

    # ------------------------------------------------------------ 1. valid
    def test_the_valid_set_maps_to_the_expected_domains(self):
        """PROOF 1 — every valid question yields exactly its expected domain."""
        self.assertGreaterEqual(
            len(VALID), 50,
            "the acceptance criterion is a 50-question set; found %s"
            % len(VALID))
        for case in VALID:
            with self.subTest(case=case['id']):
                result = self._map(case)
                self.assertEqual(result['model'], case['model'])
                self.assertEqual(result['domain'], case['expected_domain'])

    def test_every_whitelisted_model_is_covered_by_the_valid_set(self):
        covered = {c['model'] for c in VALID}
        self.assertEqual(covered, set(domain_schema.allowed_models()))

    # -------------------------------------------------- 2-9. every refusal
    def test_every_adversarial_case_is_refused(self):
        """PROOFS 2-9 — ambiguous, unsupported, injected, malformed, unsafe.

        One loop rather than nine near-identical tests: each case carries its
        own category, and subTest names the id, so a failure still points at
        exactly which payload got through.
        """
        self.assertTrue(REFUSING)
        for case in REFUSING:
            with self.subTest(case=case['id'], category=case['category']):
                with self.assertRaises(UserError):
                    self._map(case)

    def test_the_adversarial_set_covers_every_category(self):
        """A refusal suite that lost a category would still pass; this notices."""
        self.assertEqual(
            {c['category'] for c in REFUSING},
            {'injection', 'unsupported_field', 'unsupported_operator',
             'malformed_output', 'ambiguous', 'invalid_value',
             'unsafe_structure'})

    # ---------------------------------------------------- 3. unsupported model
    def test_an_unsupported_model_is_refused_before_anything_is_sent(self):
        """PROOF 3 — and the provider is never reached to find out."""
        def explode(_self, prompt, max_tokens=1024):  # pragma: no cover
            raise AssertionError("the provider must not be contacted")

        with patch(_PROBE, return_value='mock'), patch(_GATEWAY, new=explode):
            with self.assertRaises(UserError):
                self.Mapper.map_question('list the users', 'res.users')


@tagged('post_install', '-at_install')
class TestDomainMapperGates(TransactionCase):
    """The switches, and what they are worth when they are off."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Mapper = cls.env['ncollection.ai.domain.mapper']

    def test_the_mapper_is_off_when_the_parameter_was_never_set(self):
        """PROOF 11 — default OFF, and not by accident of a stored value."""
        self.env['ir.config_parameter'].sudo().search([
            ('key', '=', 'ncollection_ai.enable_nl_domain_mapper')]).unlink()
        with self.assertRaises(UserError):
            self.Mapper.map_question('confirmed orders', 'sale.order')

    def test_nothing_is_sent_while_the_mapper_is_off(self):
        self.env['ir.config_parameter'].sudo().set_param(
            'ncollection_ai.enable_nl_domain_mapper', 'False')

        def explode(_self, prompt, max_tokens=1024):  # pragma: no cover
            raise AssertionError("the provider must not be contacted")

        with patch(_GATEWAY, new=explode):
            with self.assertRaises(UserError):
                self.Mapper.map_question('confirmed orders', 'sale.order')

    def test_a_non_mock_provider_is_refused(self):
        """The #375 gate: the flag alone must not be enough."""
        self.env['ir.config_parameter'].sudo().set_param(
            'ncollection_ai.enable_nl_domain_mapper', 'True')

        def explode(_self, prompt, max_tokens=1024):  # pragma: no cover
            raise AssertionError("nothing may be sent to a live provider")

        with patch(_PROBE, return_value='anthropic'), \
                patch(_GATEWAY, new=explode):
            with self.assertRaises(UserError):
                self.Mapper.map_question('confirmed orders', 'sale.order')

    def test_an_unreachable_gateway_refuses_rather_than_assuming_mock(self):
        """A gate that fails open is not a gate."""
        self.env['ir.config_parameter'].sudo().set_param(
            'ncollection_ai.enable_nl_domain_mapper', 'True')
        with patch(_PROBE, return_value=None):
            with self.assertRaises(UserError):
                self.Mapper.map_question('confirmed orders', 'sale.order')

    def test_a_sales_user_may_not_map(self):
        """Role scope is unchanged from #375: accountant / CEO / owner."""
        user = self.env['res.users'].create({
            'name': 'Mapper Sales', 'login': 'mapper_sales_user',
            'group_ids': [(6, 0, [
                self.env.ref('base.group_user').id,
                self.env.ref('ncollection_core.group_role_sales').id])],
        })
        self.env['ir.config_parameter'].sudo().set_param(
            'ncollection_ai.enable_nl_domain_mapper', 'True')
        with self.assertRaises(AccessError):
            self.Mapper.with_user(user).map_question(
                'confirmed orders', 'sale.order')

    def test_an_overlong_question_is_refused(self):
        self.env['ir.config_parameter'].sudo().set_param(
            'ncollection_ai.enable_nl_domain_mapper', 'True')
        with patch(_PROBE, return_value='mock'):
            with self.assertRaises(UserError):
                self.Mapper.map_question('x' * 5000, 'sale.order')


@tagged('post_install', '-at_install')
class TestDomainMapperBoundary(TransactionCase):
    """PROOFS 10, 12, 13 — what the mapper must NOT do, and must not touch."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Mapper = cls.env['ncollection.ai.domain.mapper']
        cls.env['ir.config_parameter'].sudo().set_param(
            'ncollection_ai.enable_nl_domain_mapper', 'True')

    def test_no_raw_execution_path_exists(self):
        """PROOF 10 — statically, in both files that touch provider output."""
        for path in (_MAPPER_SRC, _SCHEMA_SRC):
            source = open(path, encoding='utf-8').read()
            tree = ast.parse(source)
            called = {
                node.func.id
                for node in ast.walk(tree)
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            }
            for forbidden in ('eval', 'exec', 'compile', 'safe_eval',
                              '__import__'):
                self.assertNotIn(forbidden, called, '%s in %s' % (forbidden, path))

    def test_the_mapper_never_calls_the_context_builder(self):
        """No tenant record can reach this prompt, by construction."""
        def explode(_self, *args, **kwargs):  # pragma: no cover
            raise AssertionError("the mapper must not build tenant context")

        captured = {}

        def fake_complete(_self, prompt, max_tokens=1024):
            captured['prompt'] = prompt
            return {'text': '{"domain": [["state", "=", "sale"]]}'}

        with patch(_PROBE, return_value='mock'), \
                patch('odoo.addons.ncollection_ai.models.context_builder'
                      '.AiContextBuilder._build', new=explode), \
                patch(_GATEWAY, new=fake_complete):
            self.Mapper.map_question('confirmed orders', 'sale.order')
        self.assertIn('sale.order', captured['prompt'])

    def test_the_prompt_carries_schema_only_and_no_tenant_data(self):
        """The prompt names fields and options; it must not carry records."""
        partner = self.env['res.partner'].create({'name': 'Zylo Trading LLC'})
        captured = {}

        def fake_complete(_self, prompt, max_tokens=1024):
            captured['prompt'] = prompt
            return {'text': '{"domain": [["state", "=", "sale"]]}'}

        with patch(_PROBE, return_value='mock'), patch(_GATEWAY, new=fake_complete):
            self.Mapper.map_question('confirmed orders', 'sale.order')

        prompt = captured['prompt']
        self.assertIn('amount_total', prompt)          # schema is disclosed
        self.assertNotIn(partner.name, prompt)         # records are not
        self.assertNotIn(self.env.cr.dbname, prompt)

    def test_the_result_is_inert_data(self):
        """PROOF 12 — a plain nested list of primitives, nothing else."""
        def fake_complete(_self, prompt, max_tokens=1024):
            return {'text': '{"domain": ["&", ["state", "=", "sale"],'
                            ' ["amount_total", ">", 1000]]}'}

        with patch(_PROBE, return_value='mock'), patch(_GATEWAY, new=fake_complete):
            result = self.Mapper.map_question('big confirmed orders',
                                              'sale.order')
        for token in result['domain']:
            if isinstance(token, str):
                self.assertIn(token, ('&', '|', '!'))
                continue
            self.assertIsInstance(token, list)
            field, operator, value = token
            self.assertIsInstance(field, str)
            self.assertIsInstance(operator, str)
            self.assertIsInstance(value, (str, int, float, bool, list))

    def test_the_module_adds_no_user_facing_surface(self):
        """PROOF 13 — no view, menu, action or controller ships with it."""
        data = self.env['ir.model.data'].search([('module', '=', 'ncollection_ai')])
        self.assertFalse(
            data.filtered(lambda d: d.model in (
                'ir.ui.view', 'ir.ui.menu', 'ir.actions.act_window',
                'ir.actions.client', 'ir.actions.server')),
            "ncollection_ai must not ship a user-facing surface (#375)")

    def test_free_text_questions_remain_off_and_unchanged(self):
        """PROOF 12 — enabling the mapper does not enable ask()."""
        self.env['ir.config_parameter'].sudo().search([
            ('key', '=', 'ncollection_ai.enable_free_text_questions')]).unlink()
        with self.assertRaises(UserError):
            self.env['ncollection.ai.question'].ask('what are my sales?')


@tagged('post_install', '-at_install')
class TestDomainSchemaUnit(TransactionCase):
    """The validator on its own — no Odoo, no gateway, no mapper."""

    def test_dotted_traversal_is_rejected(self):
        with self.assertRaises(domain_schema.DomainRejected):
            domain_schema.validate(
                'sale.order', [['partner_id.name', '=', 'x']])

    def test_depth_beyond_the_limit_is_rejected(self):
        deep = ['!', '!', '!', '!', ['state', '=', 'sale']]
        with self.assertRaises(domain_schema.DomainRejected):
            domain_schema.validate('sale.order', deep)

    def test_too_many_leaves_are_rejected(self):
        leaves = [['state', '=', 'sale']] * 21
        oversized = ['&'] * 20 + leaves
        with self.assertRaises(domain_schema.DomainRejected):
            domain_schema.validate('sale.order', oversized)

    def test_a_trailing_operand_is_rejected(self):
        with self.assertRaises(domain_schema.DomainRejected):
            domain_schema.validate('sale.order', [
                ['state', '=', 'sale'], ['state', '=', 'draft']])

    def test_the_returned_domain_shares_nothing_with_the_input(self):
        raw = [['state', '=', 'sale']]
        out = domain_schema.validate('sale.order', raw)
        self.assertEqual(out, raw)
        self.assertIsNot(out[0], raw[0])

    def test_tuples_are_accepted_and_normalised_to_lists(self):
        out = domain_schema.validate('sale.order', [('state', '=', 'sale')])
        self.assertEqual(out, [['state', '=', 'sale']])

    def test_a_date_with_a_trailing_newline_is_rejected(self):
        """Regression: `$` matches before a trailing newline, so `.match()`
        accepted "2026-01-01\\n". Caught by invariants R8 (#377) on this file's
        first run; pinned here so a revert to `.match()` fails a test and not
        only a lint rule."""
        with self.assertRaises(domain_schema.DomainRejected):
            domain_schema.validate(
                'sale.order', [['date_order', '>=', '2026-01-01\n']])
        with self.assertRaises(domain_schema.DomainRejected):
            domain_schema.validate(
                'account.move', [['invoice_date', '>=', '2026-01-01\n']])
