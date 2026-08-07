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

from odoo.exceptions import AccessError, UserError
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

    # ------------------------------------------------------------ authorization
    def test_a_sales_user_cannot_ask_and_therefore_cannot_read_receivables(self):
        """CRITICAL, round 4. Standing Rule 4: mirror every UI restriction at
        the ORM/RPC layer.

        This module ships no menu, which is exactly why the gate must be in the
        method — ask() is a public @api.model that any authenticated tenant user
        can reach by RPC today, before P5-T06 adds a widget.

        The bug was believing the aggregation engine's per-model readability
        check was enough. It is not, and this is not a hypothetical: core grants
        sales_team.group_sale_salesman read on account.move, and
        ncollection_core/hooks.py links group_role_sales to it at install — so a
        Sales user passed _model_readable('account.move') and the default
        context would have handed them company receivables and invoice history.

        9bb86e7 (#358) disproved the identical 'the ACL alone mirrors this'
        claim for the financial dashboards ONE DAY before this module was
        written, which is the real reason this test exists.
        """
        sales = self.env['res.users'].create({
            'name': 'Sales Only', 'login': 'ai_sales_probe',
            'group_ids': [(6, 0, [
                self.env.ref('base.group_user').id,
                self.env.ref('ncollection_core.group_role_sales').id,
            ])],
        })
        with self.assertRaises(AccessError):
            self.Question.with_user(sales).ask("What are our receivables?")

    def test_an_accountant_may_ask(self):
        """The gate has to let the intended roles through, or it is just an
        outage. Pairs with the test above: refusing everyone would pass that
        one on its own."""
        accountant = self.env['res.users'].create({
            'name': 'Accountant', 'login': 'ai_acct_probe',
            'group_ids': [(6, 0, [
                self.env.ref('base.group_user').id,
                self.env.ref('ncollection_core.group_role_accountant').id,
            ])],
        })

        def fake_complete(_self, prompt, max_tokens=1024):
            return {'text': 'ok', 'usage': {}}

        with patch(
            'odoo.addons.ncollection_ai.models.gateway_client'
            '.AiGatewayClient.complete',
            new=fake_complete,
        ):
            result = self.Question.with_user(accountant).ask("Any overdue?")
        self.assertEqual(result['answer'], 'ok')

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
    def test_a_pasted_credential_is_REFUSED_not_merely_scrubbed(self):
        """The scope change after three review rounds: fail closed.

        These used to assert the secret was scrubbed out of the prompt. Three
        rounds showed that denylist scrubbing of arbitrary text cannot be made
        complete — each set of patterns closed one exploit and opened another.
        So a question carrying anything shaped like a credential is now REFUSED
        and nothing is transmitted at all, which bounds the class instead of
        chasing instances.
        """
        for secret in (
            "Is this the right webhook secret: sk-live-51H8gk3K2FZabcdefghijkl ?",
            "Decode this: eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0."
            "dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U",
            "why is AKIAIOSFODNN7EXAMPLEXXXXXXXXXXXXXXX failing?",
        ):
            with self.assertRaises(UserError) as caught:
                self._prompt_for(secret)
            self.assertIn('Nothing was sent', str(caught.exception))

    def test_the_refusal_tells_the_user_what_to_do(self):
        """Refusing is only useful if the person can act on it."""
        with self.assertRaises(UserError) as caught:
            self._prompt_for("key sk-live-51H8gk3K2FZabcdefghijklmnop please")
        message = str(caught.exception)
        self.assertIn('remove it', message)
        self.assertIn('Nothing was sent', message)

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

    # --------------------------------------------------- the round-4 corpus
    #
    # Two reviewers independently proved the round-3 gate was simultaneously too
    # loose (missed short/lowercase/multi-word secrets) and too tight (refused
    # real emails and URLs). Both directions are pinned here, because testing
    # only the first is how the second shipped.

    def test_credentials_that_are_not_long_mixed_alphanumerics_are_refused(self):
        """The round-4 CRITICAL, found twice independently.

        The old gate needed len>24 AND a digit AND a letter; the scrubber's
        generic shapes needed 32 hex / 40 base64. Anything between — an ordinary
        password, an all-lowercase passphrase, a multi-word one — fell in the
        trench between the two layers and reached the provider verbatim.
        """
        for question in (
            "My database password is Str0ngPass99, check connections?",
            "root password is hunter2 for the staging box",
            "here is the key: correcthorsebatterystaple please store it",
            "ssh key fingerprint check: myapikeyisabcdefghijklmnop works",
            "wifi password is greenelephant purpletiger blueocean today",
            "boundary token AbCdEfGh12345678ABCDefgh done",
            "the database password is Tr0ub4dor3xKcvQmZpL9 check syncs?",
            "the api secret is 8xK2mN9pQr4sT6vW1yB3cD5e for that vendor",
            "service account key: aZ9mK3pQ7rL1vB5nC8xD2eF6 check webhooks",
        ):
            with self.assertRaises(UserError, msg=question):
                self._prompt_for(question)

    def test_ordinary_business_questions_are_not_refused(self):
        """The other half, and the half that was missing.

        A control that blocks a customer's email address while passing
        'hunter2' is not reading the right signal. The first three of these
        were refused outright by the round-3 gate; the last two are the reason
        the credential-noun triggers require a VALUE rather than firing on the
        noun alone.
        """
        for question in (
            "email ahmed.al-mansoori2026@albarari-trading-solutions.ae about this",
            "check https://albarari-trading.ae/portal/invoices/00042 please",
            "Explain the internationalisation of our receivables report",
            "What is the status of the secret santa invoice?",
            "Does the password reset email actually work for tenants?",
        ):
            prompt = self._prompt_for(question)   # must not raise
            self.assertIn('QUESTION:', prompt)
