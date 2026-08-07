# -*- coding: utf-8 -*-
"""Tests for the orchestrator entry point (P5-T03 / #60).

THIS FILE EXISTS BECAUSE ITS ABSENCE WAS THE BUG.

`ask()` is the module's only public entry point and the one place user-typed
free text enters the pipeline. It shipped with **no test at all** — and that is
precisely where four reviewers independently found leaks: a customer name, an
API key, a JWT and a passport number all reached the provider verbatim, because
the sanitiser recognises identities and secrets BY FIELD NAME and `question` is
not a field name.

The prompt is intercepted rather than sent: these assert on exactly what WOULD
cross the boundary, which is the only thing that matters here. No gateway, no
network.
"""
from unittest.mock import patch

from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestAskSanitisation(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Question = cls.env['ncollection.ai.question']
        cls.partner = cls.env['res.partner'].create({
            'name': 'Al Barari Trading LLC',
            'email': 'ahmed@albarari.example',
        })

    def _prompt_for(self, question):
        """Run ask() and return the prompt that would have been transmitted."""
        captured = {}

        def fake_complete(_self, prompt, max_tokens=1024):
            captured['prompt'] = prompt
            return {'text': 'ok', 'usage': {}}

        with patch(
            'odoo.addons.ncollection_ai.models.gateway_client'
            '.AiGatewayClient.complete',
            new=fake_complete,
        ):
            self.Question.ask(question)
        return captured['prompt']

    # ------------------------------------------------------- identity in text
    def test_a_customer_name_typed_in_the_question_never_reaches_the_prompt(self):
        """CRITICAL, reproduced by three reviewers independently.

        The old code sanitised the question but only by field name, so a typed
        customer name travelled verbatim to the third-party provider.
        """
        prompt = self._prompt_for(
            "How much does Al Barari Trading LLC owe us right now?")
        self.assertNotIn('Al Barari Trading LLC', prompt)
        self.assertIn('PARTNER_', prompt)

    def test_the_company_name_typed_in_the_question_is_pseudonymised(self):
        prompt = self._prompt_for(
            "Compare %s against our receivables" % self.env.company.name)
        self.assertNotIn(self.env.company.name, prompt)

    # --------------------------------------------------------- secrets in text
    def test_an_api_key_pasted_into_the_question_is_redacted(self):
        """CRITICAL. The realistic case is a user asking 'is this the right
        key?' — §5's 'never send' tier must reach free text, not only fields."""
        prompt = self._prompt_for(
            "Is this the right webhook secret: sk_live_51H8gk3K2FZabcdefghijkl ?")
        self.assertNotIn('sk_live_51H8gk3K2FZabcdefghijkl', prompt)
        self.assertIn('[REDACTED]', prompt)

    def test_a_jwt_pasted_into_the_question_is_redacted(self):
        jwt = ('eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.'
               'dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U')
        prompt = self._prompt_for("Decode this token for me: %s" % jwt)
        self.assertNotIn(jwt, prompt)

    def test_an_aws_key_is_redacted(self):
        prompt = self._prompt_for("why is AKIAIOSFODNN7EXAMPLE failing?")
        self.assertNotIn('AKIAIOSFODNN7EXAMPLE', prompt)

    def test_a_passport_number_is_redacted(self):
        """HIGH. One embedded letter breaks the phone pattern's character
        class, so alphanumeric government IDs were invisible to every check."""
        prompt = self._prompt_for("Check passport A1234567 against the list")
        self.assertNotIn('A1234567', prompt)

    def test_an_email_typed_in_the_question_is_pseudonymised(self):
        prompt = self._prompt_for("email ahmed@albarari.example about this")
        self.assertNotIn('ahmed@albarari.example', prompt)

    # ------------------------------------------------------- must NOT scrub
    def test_a_document_reference_survives(self):
        """§5 lists document references as substance. An over-eager ID pattern
        would make every question about a specific invoice unanswerable."""
        prompt = self._prompt_for("What is the status of INV/2026/00042?")
        self.assertIn('INV/2026/00042', prompt)

    def test_amounts_and_dates_survive(self):
        prompt = self._prompt_for("Anything over 15250.75 AED since 2026-08-01?")
        self.assertIn('15250.75', prompt)
        self.assertIn('2026-08-01', prompt)

    # ---------------------------------------------------------------- bounds
    def test_an_overlong_question_is_capped(self):
        """A question is a sentence. Anything longer is a paste, a bug, or an
        attempt to push the real instructions out of the context window — and it
        spends the tenant's token budget on it."""
        prompt = self._prompt_for('x' * 10000)
        self.assertLess(prompt.count('x'), 5000)

    # ------------------------------------------------------------- rehydration
    def test_the_answer_is_rehydrated_with_real_identities(self):
        """The mapping never left this database, so only this database can
        restore the names — which is what makes the answer useful to a human."""
        def fake_complete(_self, prompt, max_tokens=1024):
            return {'text': 'PARTNER_1 owes the most.', 'usage': {}}

        with patch(
            'odoo.addons.ncollection_ai.models.gateway_client'
            '.AiGatewayClient.complete',
            new=fake_complete,
        ):
            result = self.Question.ask(
                "who owes most? Al Barari Trading LLC maybe?")
        # PARTNER_1 is whichever identity was tokenised first; the point is that
        # a token does not survive into the user-visible answer.
        self.assertNotIn('PARTNER_1', result['answer'])
