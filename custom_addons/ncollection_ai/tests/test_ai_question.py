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
        # Free text is OFF by default (see ask()). These tests exercise the
        # filters BEHIND that switch, so they turn it on explicitly — which is
        # also a standing reminder that a default install does not have it.
        cls.env['ir.config_parameter'].sudo().set_param(
            'ncollection_ai.enable_free_text_questions', 'True')
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
            '.AiGatewayClient._complete',
            new=fake_complete,
        ):
            self.Question.ask(question)
        return captured['prompt']

    # ----------------------------------------------------------- opt-in switch
    def test_free_text_questions_are_refused_until_switched_on(self):
        """THE SCOPE DECISION, pinned.

        P5-T03 is the Context Injection Engine. Its acceptance criteria never
        included accepting arbitrary end-user prose — that was added here, and
        every CRITICAL across eight review rounds came from it. Round 8 showed
        why it cannot be resolved: "the wifi password is sunshine" must be
        refused and "the token is stored securely" must not, and those are the
        same sentence structurally.

        So the engine ships and the free-text surface ships OFF. Turning it on
        is a deliberate act, taken together with the provider terms it depends
        on (#375). The filters remain as defence in depth for whoever does.
        """
        self.env['ir.config_parameter'].sudo().set_param(
            'ncollection_ai.enable_free_text_questions', 'False')
        self.addCleanup(
            self.env['ir.config_parameter'].sudo().set_param,
            'ncollection_ai.enable_free_text_questions', 'True')

        with self.assertRaises(UserError) as caught:
            self.Question.ask("Which customer owes us the most?")
        self.assertIn('not enabled', str(caught.exception))

    def test_the_switch_is_off_when_the_parameter_was_never_set(self):
        """Absent must mean OFF. A default that arrives by installing a module
        is not a decision anyone made."""
        self.env['ir.config_parameter'].sudo().search([
            ('key', '=', 'ncollection_ai.enable_free_text_questions')]).unlink()
        self.addCleanup(
            self.env['ir.config_parameter'].sudo().set_param,
            'ncollection_ai.enable_free_text_questions', 'True')

        with self.assertRaises(UserError):
            self.Question.ask("Which customer owes us the most?")

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
            '.AiGatewayClient._complete',
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

    def test_the_known_residual_is_what_the_documentation_says_it_is(self):
        """THE GAP, PINNED. This test asserts that these still LEAK.

        That is deliberate. The boundary section in ai_question.py and the
        Known-limits section in README.rst both promise exactly this set, and a
        promise nobody checks drifts. If a future change closes one of these,
        THIS TEST FAILS — and the right response is to delete the case from
        here and from both documents, not to re-loosen the filter.

        A reviewer noted the previous round claimed a residual corpus that
        existed only in a scratchpad file, invisible to anyone reading the
        repo. This is that corpus, in the tree.
        """
        for question, value in (
            # a credential noun outside _CREDENTIAL_NOUN
            ("the doorcode is 4521", "4521"),
            # no noun at all — genuinely undecidable, five English words
            ("check correcthorsebatterystaple for me",
             "correcthorsebatterystaple"),
        ):
            prompt = self._prompt_for(question)   # must NOT raise
            # Assert the VALUE actually travels. A reviewer pointed out the
            # first version only checked that nothing raised, so it would have
            # stayed green if some unrelated pattern started redacting these —
            # the exact opposite of what the docstring promises.
            self.assertIn(value, prompt, question)

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
            '.AiGatewayClient._complete',
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
            # Round 5: ordinary English defeated the adjacency requirement, and
            # four everyday credential nouns were simply missing from the list.
            "I reset the wifi password to greenelephant purpletiger",
            "wifi password was greenelephant purpletiger before",
            "reset the password, greenelephant purpletiger blueocean",
            "the password, which was rotated last night, is p4ss123",
            "the vpn password (set by IT on Monday) is p4ss123",
            "the CVV is 123 on that card, can you check the charge",
            "my PIN is 4521 for the corporate card",
            "the OTP is 552211, please confirm the payout",
            "the passcode is 4521, does that look right",
            "the login is p4ss123",
            "the passkey is p4ss123",
            "my recovery phrase is apple grape ocean tiger delta ranch",
            # Round 6: `\b` does not fire between `_` and a letter, so every
            # env-var/config shape was invisible to BOTH the gate and pii.py —
            # proven live through the real ask() -> HTTP -> gateway path.
            "DB_PASSWORD=hunter2, can you check our receivables?",
            "wifi_password: greenelephant",
            "the smtp_password is Str0ngPass99 for the mailer",
            "userlogin: admin123",
            "just fyi jwt_token=abc.def.ghi is expiring soon",
            "root_password to p4ss123",
            # Round 6: juxtaposition, no connector at all — a reviewer called
            # this "arguably the single most common way people paste one".
            "wifi password p4ssW0rd2026",
            "root password Str0ngPass99 on the staging box",
            "ssh key AbCdEf123456 for the deploy user",
            "admin login Adm1nUser99 for the console",
            # Round 6: nouns the list was missing.
            "the security code is 4521 on that card",
            "the access code is 778899 for the gate",
            "the verification code is 445566, use it now",
            # Round 7: a trailing `s` is alphanumeric, so the noun lookahead
            # killed every PLURAL noun outright.
            "our passwords are hunter2 and greenelephant for the two accounts",
            "the api tokens are AbCdEf123456 and XyZ987654 for staging",
            "the logins are jdoe2026 and asmith2026",
            "the pins are 4521 and 7788 for the two terminals",
            # Round 7: ONE filler word ate the only connector, and the round-7
            # state-word additions (now/still/right/just/that) made it routine.
            "the password is now hunter2",
            "the pin is still 4521",
            "the cvv is same 552233",
            "my password is just hunter2",
            "the CVV is that 123",
            # Round 7: the two-clause decoy, previously documented as an
            # accepted residual. It is caught now.
            "the password is not accepted, it's actually p4ss123",
            # Round 7: pii.py's other eleven \b anchors had the same
            # underscore bug the password= pattern had.
            "STRIPE_SECRET_KEY_sk_live_51exampleK2FZabcdefghijkl please rotate",
            "prod_gh_token_ghp_1234567890abcdefghij was committed by mistake",
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
            # Round 5: the value side was a bare \S, so ordinary troubleshooting
            # was hard-refused. A control that blocks benign questions gets
            # switched off, which is its own security outcome.
            "The API key is invalid, can you check why the sync failed?",
            "Our access token is expired, is that expected behaviour?",
            "The credentials are wrong on the nightly sync job, any idea why?",
            "My password is not working today, can you check my account?",
            "The webhook secret is missing from the last deployment?",
            "The token is null in the response, is that a bug?",
            "Is the security key mandatory for this integration?",
            "summarize creditworthiness key metrics for Al Barari Trading "
            "LLC internationalisation project",
            # Round 6 false refusals. Bare `pass` was a credential noun, `key`
            # matched inside "monkey", and idioms were being read as
            # declarations. A control that refuses these gets switched off.
            "Can you pass this invoice to Sarah?",
            "I will pass the file to accounting.",
            "the key to success is teamwork",
            "the login page is down, can you check?",
            "The token expires in a day, is that normal?",
            "The api key rotation policy: how many keys are older than 90 days?",
            "did the monkey to zoo shipment clear customs?",
            "the password is not accepted, can you check my account?",
            # Round 7 false refusals. The state-word DENYLIST was replaced by a
            # value-shape ALLOWLIST precisely because these were being blocked
            # while "the password is now hunter2" sailed through.
            "credentials are stored in the vault, correct?",
            "the token is stored securely, right?",
            "the api key is embedded in our integration docs",
            "the pin is printed on the receipt",
            "the otp is sent by SMS",
            "the login is shared across our sales team, is that fine?",
            "the token count for this session is high",
            "the login history2026 export is ready",
            "confirm the login SO2026042 is linked correctly",
            "customer login AB123456 is locked out again",
            "the login - for testing purposes - is broken today",
            "the password, apparently, is unavailable right now",
            "the api key, honestly, is meaningless without rotation",
        ):
            prompt = self._prompt_for(question)   # must not raise
            self.assertIn('QUESTION:', prompt)
