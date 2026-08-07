# -*- coding: utf-8 -*-
"""The orchestrator: question in, answer out (P5-T03 / #60).

Four steps, in this order, and the order is the security property:

    build  ->  sanitise  ->  send  ->  re-hydrate
      |           |            |           |
   tenant DB   tenant DB    satellite   tenant DB

Sanitisation sits between the database and the wire because that is the last
point at which the real values still exist inside the boundary
(AI_PLATFORM_DESIGN §5). Re-hydration sits after the response for the same
reason in reverse: the mapping never left, so only this database can restore the
identities.

This module is deliberately thin. Every piece of policy lives in the component
that owns it — context shape in the builder, PII rules in the sanitiser, budgets
and provider choice in the satellite. An orchestrator that starts making
decisions is an orchestrator that starts duplicating them.
"""
import re

from odoo import api, models
from odoo.exceptions import AccessError, UserError

# WHO MAY ASK. Standing Rule 4: any UI restriction must be mirrored at the
# ORM/RPC layer. This module ships no menu yet, which is exactly why the gate
# has to be here — `ask()` is a public @api.model method that P5-T06's widget
# will call and that any authenticated tenant user can already reach by RPC.
#
# The default context includes receivables and invoice history, so this is
# financial data and takes the financial gate. Reusing the aggregation engine's
# per-model readability check is NOT sufficient, and that is not a guess:
# 9bb86e7 (#358) disproved the identical claim for the financial dashboards one
# day before this module was written. Core grants sales_team.group_sale_salesman
# read on account.move, and ncollection_core/hooks.py links group_role_sales to
# it at install — so a Sales-only user passes _model_readable('account.move')
# and would have received company receivables through this method.
#
# base.group_system is not boilerplate: `admin` holds it and holds no role group
# (the implication runs owner -> system, never the reverse), so omitting it locks
# admin out on every tenant and breaks every test that runs as uid 1.
_ALLOWED_GROUPS = (
    'ncollection_core.group_role_accountant',
    'ncollection_core.group_role_ceo',
    'base.group_system',
)

# A question is a sentence. Anything longer is a paste, a bug, or an attempt to
# push the real instructions out of the context window — and it burns the
# tenant's token budget (§4) on text nobody asked a question with. Capped here
# rather than at the UI, because ask() is a public @api.model method that
# P5-T06's widget will call directly and any HTTP caller can reach.
_MAX_QUESTION_CHARS = 2000

# Bounded so a tenant with a large partner list cannot turn one question into a
# full-table scan. Ordered by most-recently-written, on the reasoning that a
# question is far likelier to name a partner someone has touched lately than one
# dormant since import.
_IDENTITY_SCAN_LIMIT = 2000

# FAIL CLOSED ON FREE TEXT — and an honest statement of what that does not buy.
#
# READ THIS BEFORE ADDING ANOTHER PATTERN. An earlier version of this comment
# claimed the shape gate "bounds the class instead of chasing instances", and
# that the gate and the scrubber cover each other's gaps. Both claims were
# FALSE, and stating them is what stopped the author looking. Round 4 had two
# reviewers independently demonstrate the same hole from opposite directions:
#
#   "root password is hunter2"                     -> SENT VERBATIM (too short)
#   "the key is correcthorsebatterystaple"         -> SENT VERBATIM (no digit)
#   "ahmed.al-mansoori2026@albarari-trading.ae"    -> REFUSED  (a real email)
#
# Simultaneously too loose and too tight, which is the signature of a control
# reading the wrong signal.
#
# THE TRUTH, stated plainly so nobody re-derives it in round 6: detecting a
# secret inside arbitrary human prose is UNDECIDABLE. "correcthorsebatterystaple"
# is a passphrase; it is also five English words. No regex, and no strict
# allowlist, separates them — an allowlist strict enough to reject it also
# rejects ordinary questions, and STILL passes a lowercase passphrase.
#
# So the layers are scoped to what each can actually do, and no further:
#
#   1. pii.py shape patterns  — HIGH-CONFIDENCE STRUCTURED secrets only: vendor
#      prefixes, JWTs, PEM blocks, connection strings, IBANs, card PANs.
#   2. _declares_a_secret     — a credential NOUN with a credential-SHAPED
#      value near it, by juxtaposition or through a connector.
#   3. _looks_like_embedded_secret — high-entropy runs no word is, with emails
#      and URLs exempted so the assistant stays usable.
#
# ROUND 8 INVERTED THE VALUE TEST, and that is the structural lesson. Rounds
# 5-7 asked "is this value a known English STATE WORD?" — an open-ended
# denylist of English, the same losing shape as chasing secrets. It failed in
# both directions at once: round 7's own additions (`now`, `still`, `just`,
# `that`) let "the password is now hunter2" through, while ordinary support
# English like "the token is stored securely, right?" was refused. Asking
# instead "does this look like a credential VALUE" is a bounded, checkable
# property, and it fixed both directions together.
#
# THE RESIDUAL, STATED AS THE ACTUAL BOUNDARY RATHER THAN A FLATTERING ONE.
#
# Round 5 called this "declared vs undeclared" and a reviewer showed "the CVV
# is 123" was declared and still leaking. Round 7 named four gaps and a
# reviewer showed a fifth, easier one it had not named. So the list below is
# PINNED BY A TEST (test_the_known_residual_is_what_the_documentation_says_it_is)
# that asserts these still leak — if a change closes one, that test fails and
# the docs get corrected rather than quietly drifting.
#
#   CAUGHT      a credential named by a noun in _CREDENTIAL_NOUN and either
#                 * immediately followed by a value with INTERLEAVED digits or
#                   mixed case ("wifi password p4ssW0rd2026"), or
#                 * assigned with `=` ("SECRET_KEY=abc" — shape irrelevant), or
#                 * within _VALUE_LOOKAHEAD_TOKENS of a connector, where the
#                   value is credential-SHAPED (see _looks_like_a_credential_value);
#               a HIGH-SIGNAL noun (recovery/seed/mnemonic phrase, passphrase)
#               with any value at all, since those are six ordinary lowercase
#               words no shape rule can see; OR any single token over
#               _MAX_WORD_CHARS mixing letters and digits; OR a pii.py shape.
#
#   NOT CAUGHT  * a credential noun outside the list ("the doorcode is 4521")
#               * no noun at all ("check correcthorsebatterystaple for me")
#               * connectors spelled as words this file does not know
#                 ("wifi password equals p4ssW0rd2026", "the api key -> X")
#               * a declaration whose connector falls outside the
#                 _NOUN_VALUE_WINDOW (40 characters after the noun)
#
#   KNOWN FALSE REFUSALS, accepted as the fail-safe direction: an ERP reference
#               with interleaved digits beside a credential noun — "access code
#               AC2026Z9", "token TXN2026Q1" — is indistinguishable from a
#               secret by shape. "login history2026" and "login AB123456" are
#               NOT refused, because their digits are a trailing run.
#
# The noun and connector lists are maintainable and should grow when someone
# finds a gap. The no-noun case is the genuinely undecidable one — a lowercase
# passphrase is indistinguishable from prose — and is why this feature needs a
# zero-retention provider agreement rather than a cleverer regex (#375).
#
# EIGHT REVIEW ROUNDS. Every round that closed a gap with more pattern opened a
# false refusal somewhere else, and a control that blocks "Can you pass this
# invoice to Sarah?" gets switched off — a worse outcome than the gap it closed.
# Do not add a pattern here without running BOTH halves of the corpus.

# The credential nouns. Round 5 shipped a much shorter list and reviewers
# walked straight through the gaps: `pin`, `otp`, `cvv`, `passcode`, `login`,
# `passkey`, and `recovery phrase` / `seed phrase` are all ordinary words for a
# credential and none of them were here.
# THE BOUNDARY IS A LOOKAROUND, NOT \b. This is the round-6 CRITICAL, and it
# was proven end-to-end through the real ask() -> HTTP -> gateway path:
#
#     "DB_PASSWORD=hunter2, can you check our receivables?"   -> was SENT
#
# `\b` does not fire between two word characters and `_` IS a word character,
# so DB_PASSWORD, wifi_password and smtp_password were all invisible to this
# regex — and to the identical `\b` in pii.py's own password= pattern, so both
# layers missed them together. That is the single most common shape a real
# secret is pasted in: an env var or a config line.
#
# (?<![A-Za-z0-9]) admits a preceding `_`, `-`, `.` or start-of-string while
# still refusing a preceding LETTER, which is what keeps `monkey`, `donkey`,
# `whiskey` and `keyboard` from registering as the noun `key`. Verified both
# ways before it was applied, not after.
#
# Bare `pass` is GONE. The optional suffix group made `pass` itself a
# credential noun, so "Can you pass this invoice to Sarah?" was refused — a
# reviewer's example, and an ordinary sentence.
#
# `secret` carries an idiom guard for the same reason: "secret sauce" and
# "secret santa" are English, not credentials.
# TWO GROUPS, because the right boundary differs by noun.
#
# No idiom guard on `key` any more. Rounds 6-7 carried `key(?!\s+to\b)` to keep
# "the key to success is teamwork" askable, but that suppressed the NOUN, so
# "here are the keys to Str0ngRoom2026" stopped being seen at all. The value
# shape settles both correctly: "success"/"teamwork" are not credential-shaped,
# "Str0ngRoom2026" is. A guard that hides the signal is worse than no guard.
#
#   _NOUN_SEP_BOUNDED  `key` and `pin` are inside ordinary English words
#                      (monkey, donkey, whiskey, keyboard, spin, pinpoint), so
#                      these must not be preceded by a letter or digit.
#   _NOUN_COMPOUND_OK  `password`, `login`, `token` and friends appear inside
#                      COMPOUND CREDENTIAL NAMES far more often than inside
#                      English words — userlogin, jwt_token, smtp_password — so
#                      a letter prefix is allowed. Both were verified against a
#                      both-directions case list before being applied.
_NOUN_SEP_BOUNDED = (r'(?<![A-Za-z0-9])'
                     r'(?:keys?|pins?)'
                     r'(?![A-Za-z0-9])')
_NOUN_COMPOUND_OK = (
    r'(?:pass(?:word|wd|phrase|code|key)|pwd|'
    r'secret(?!\s+(?:sauce|santa|weapon|ingredient|to\b))|credentials?|'
    r'token|login|otp|cvv|cvc|(?:recovery|seed|mnemonic)\s+phrase|'
    r'(?:security|access|verification)\s+code)s?(?![A-Za-z0-9])')
_CREDENTIAL_NOUN = '(?:%s|%s)' % (_NOUN_SEP_BOUNDED, _NOUN_COMPOUND_OK)

# HOW A VALUE IS JUDGED — a shape ALLOWLIST, not a word denylist.
#
# Rounds 5-7 used _STATE_WORD_RE: refuse unless the value is a known English
# state word. That list is open-ended, which is the same losing shape as
# chasing secrets, and it failed in both directions at once. Round 7's own
# additions (`now`, `still`, `right`, `ok`, `fine`) created a CRITICAL:
#     "the password is now hunter2"   -> one filler word ate the connector
# while ordinary support English was still refused:
#     "the token is stored securely, right?"
#
# So the question is inverted. Instead of asking "is this an English word I
# recognise", ask "does this look like a credential VALUE" — a bounded,
# checkable property. Verified against 14 real secrets and 28 ordinary words
# before it was adopted: no secret missed, no benign word refused.
_MIN_VALUE_CHARS = 3
_MAX_DIGIT_RUN = 12
_LONG_VALUE_CHARS = 12
_MIXED_CASE_CHARS = 8


def _looks_like_a_credential_value(value):
    """True when a token looks like a secret rather than a word.

    Four shapes, each chosen against real examples:
      letters+digits    hunter2, p4ss123, jdoe2026, AbCdEf123456
      short digit run   4521, 552233, 445566 (PINs, CVVs, OTPs)
      long              greenelephant, correcthorsebatterystaple
      mixed case        Str0ngPassZZ
    "stored", "printed", "encrypted", "temporary", "rotation" and "invalid" are
    none of these, which is what keeps ordinary questions askable.
    """
    if len(value) < _MIN_VALUE_CHARS:
        return False
    has_digit = any(c.isdigit() for c in value)
    has_alpha = any(c.isalpha() for c in value)
    if has_digit and has_alpha:
        return True
    if value.isdigit() and len(value) <= _MAX_DIGIT_RUN:
        return True
    if len(value) >= _LONG_VALUE_CHARS:
        return True
    return (len(value) >= _MIXED_CASE_CHARS
            and not value.islower() and not value.isupper())


# How many tokens after a connector may be inspected. More than one, because a
# single filler word otherwise hides the value ("the password is now hunter2");
# bounded, because scanning the whole sentence re-refuses ordinary questions.
#
# 6, not 3. A reviewer found "the password is really quite simply hunter2"
# passing at 3 — the value is the FOURTH token. Raising it was measured, not
# guessed: at 3, 5, 6 and 8 the corpus reports zero false refusals, so 6 buys
# the catch for free. (The first measurement of this said otherwise and was
# wrong — a heredoc nested in a command substitution never re-read the file.
# Re-run cleanly before trusting a number like this.)
_VALUE_LOOKAHEAD_TOKENS = 6

# HIGH-SIGNAL NOUNS — any following value counts, no shape test.
# A wallet/2FA recovery phrase is SIX ORDINARY LOWERCASE WORDS
# ("apple grape ocean tiger delta ranch"), so no shape rule can ever see it.
# These nouns are also never ordinary ERP English, unlike `key` or `token`,
# so treating the mere phrasing as sufficient costs nothing real.
_HIGH_SIGNAL_NOUN_RE = re.compile(
    r'(?:(?:recovery|seed|mnemonic)\s+phrase|passphrase)s?', re.IGNORECASE)


def _looks_like_a_juxtaposed_value(value):
    """Stricter than the connector test, because juxtaposition has no verb.

    Requires digits INTERLEAVED with letters, or mixed case — not a trailing
    digit run. Without that, every ERP reference sitting beside a credential
    noun was refused: "the login history2026 export", "confirm the login
    SO2026042", "customer login AB123456 is locked out".

    Still refuses a few genuinely ambiguous references (AC2026Z9, TXN2026Q1) —
    interleaved digits make them indistinguishable from a secret by shape, so
    they fail safe. Documented rather than pretended away.
    """
    # `-`, `_` and `.` are ordinary in real secrets, and requiring isalnum()
    # meant ONE of them removed the value from detection entirely at both
    # layers: "wifi password p4ss-W0rd2026" was sent verbatim while the
    # punctuation-free "p4ssW0rd2026" was caught.
    core = value.replace('-', '').replace('_', '').replace('.', '')
    if len(value) < 6 or not core.isalnum():
        return False
    if any(c.isdigit() for c in value.rstrip('0123456789')):
        return True
    return not value.islower() and not value.isupper()


_CREDENTIAL_NOUN_RE = re.compile(_CREDENTIAL_NOUN, re.IGNORECASE)
_NOUN_VALUE_WINDOW = 40
_CONNECTOR_RE = re.compile(r'(?:\b(?:is|are|was|were|to)\b|[=:,]|\s-\s)\s*',
                           re.IGNORECASE)
# There is no third trigger. Round 5 had a co-occurrence rule; round 6
# deleted it as redundant, and round 8's value-shape test made the question
# moot — the connector path now reaches everything it did.

# TRIGGER 2 — a single high-entropy token, with no credential noun anywhere.
# 19, not 24: the old floor sat directly above the band two reviewers
# exploited, where real 20-24 character secrets were too short for pii.py's
# 32-hex / 40-base64 generic shapes. The two layers shared a blind spot rather
# than covering one another.
_MAX_WORD_CHARS = 19
_SUSPICIOUS_RUN_RE = re.compile(r'[A-Za-z0-9+/_=-]{16,}')

# Exempt from the shape gate: both are ordinary content in a business question,
# both are long, hyphen-rich and digit-bearing, and both were being refused
# outright. Neither is left unprotected — pii.py pseudonymises emails, and a URL
# carrying credentials still matches its connection-string pattern.
_EMAIL_SHAPE_RE = re.compile(r'^[\w.+-]+@[\w-]+\.[\w.-]+$')
_URL_SHAPE_RE = re.compile(r'^[a-z][a-z0-9+.-]*://[^\s@]*$', re.IGNORECASE)


def _looks_like_embedded_secret(word):
    """True when a whitespace-delimited token cannot be ordinary prose.

    Requires BOTH length and mixed character classes. The digit requirement is
    deliberate and kept: dropping it to catch all-lowercase passphrases would
    refuse ordinary long words like 'internationalisation'. Declared
    passphrases are caught by _DECLARED_SECRET_RE instead; undeclared ones are
    the documented residual above.
    """
    stripped = word.strip('.,;:!?()[]"\'')
    if len(stripped) <= _MAX_WORD_CHARS:
        return False
    if _EMAIL_SHAPE_RE.match(stripped) or _URL_SHAPE_RE.match(stripped):
        return False
    if not _SUSPICIOUS_RUN_RE.search(stripped):
        return False
    has_digit = any(c.isdigit() for c in stripped)
    has_alpha = any(c.isalpha() for c in stripped)
    return has_digit and has_alpha


def _declares_a_secret(text):
    """A credential noun with a credential-SHAPED value near it.

    Two ways a value attaches to the noun, both seen in reviewer examples:

      juxtaposition   "wifi password p4ssW0rd2026"   (no connector at all —
                      called the most common paste of all)
      connector       "the password is now hunter2"  (up to
                      _VALUE_LOOKAHEAD_TOKENS tokens after is/are/was/were/to/
                      =/:/,/ - , because ONE filler word otherwise hides it)

    The value is judged by SHAPE, not by whether it is a word this module
    happens to recognise. That inversion is round 8's change and it is the
    point: the previous denylist of English state words was open-ended, so it
    failed in both directions simultaneously — leaking "the password is now
    hunter2" while refusing "the token is stored securely, right?".

    Bounded on purpose. Scanning the whole sentence re-refuses ordinary
    questions; three tokens after a connector is enough for one filler word and
    not enough to reach an unrelated long word later in the sentence.

    See the boundary section at the top of this module for what this still does
    NOT catch. It is not complete and cannot be.
    """
    for noun in _CREDENTIAL_NOUN_RE.finditer(text):
        tail = text[noun.end():noun.end() + _NOUN_VALUE_WINDOW]
        # A high-signal noun still needs a CONNECTOR before its value counts.
        # Without that, "Does our password policy require a passphrase for
        # admin accounts?" and "what does passphrase mean here?" were refused —
        # ordinary IT-policy questions carrying no secret. With it,
        # "my recovery phrase is apple grape ocean" is still caught, because
        # the value follows "is".
        if (_HIGH_SIGNAL_NOUN_RE.fullmatch(noun.group(0).strip())
                and _CONNECTOR_RE.search(tail)):
            return True

        following = tail.split()
        if following and _looks_like_a_juxtaposed_value(
                following[0].strip('.,;:!?()[]"\'')):
            return True
        for connector in _CONNECTOR_RE.finditer(tail):
            rest = tail[connector.end():].split()[:_VALUE_LOOKAHEAD_TOKENS]
            # `=` after a credential noun is an ASSIGNMENT, not English, so the
            # value's shape is irrelevant. Without this, SECRET_KEY=abc and
            # jwt_token=abc.def.ghi walked through the shape test — "abc" is
            # too short and "abc.def.ghi" carries no digit. `:` deliberately
            # does NOT get this treatment: it is also an ordinary topic
            # separator ("api key rotation policy: how many keys...").
            # UNSPACED `=` only — glued directly to the noun, as in
            # "SECRET_KEY=abc". "key = value pairs are stored as JSON" and
            # "our login = SSO for every vendor" are ordinary integration
            # English, and refusing them was a self-inflicted denial of the
            # feature. Spaced `=` falls through to the ordinary shape test.
            if (connector.start() == 0 and connector.group(0).startswith('=')
                    and rest):
                return True
            for token in rest:
                if _looks_like_a_credential_value(
                        token.strip('.,;:!?()[]"\'')):
                    return True
    return False


_PROMPT_TEMPLATE = """You are a business analyst for {company}.

Answer the user's question using ONLY the workspace data below. If the data does
not contain the answer, say so plainly rather than guessing.

Identities appear as tokens such as PARTNER_1; use them exactly as written.
All amounts are in {currency}.

--- WORKSPACE DATA ---
{context}
--- END DATA ---

QUESTION: {question}
"""


class AiQuestion(models.AbstractModel):
    _name = 'ncollection.ai.question'
    _description = 'AI question orchestration (build, sanitise, send, rehydrate)'

    def _known_identities(self):
        """Real names to pseudonymise wherever they appear in free text.

        Read through `self.env`, so ORM record rules apply: a user only ever
        contributes names they can already see. That matters — this list is not
        a secret, but it should not become a way to learn which partners exist.

        Bounded by _IDENTITY_SCAN_LIMIT. If a tenant has more partners than
        that, a name outside the window is not pseudonymised — a real residual
        gap, stated here rather than hidden, and the reason the structured path
        (field-name matching on context rows) remains the primary control. The
        free-text scan is a second layer for user-typed input, not a promise of
        completeness.
        """
        partners = self.env['res.partner'].search(
            [('name', '!=', False)], limit=_IDENTITY_SCAN_LIMIT,
            order='write_date desc')
        names = set(partners.mapped('name'))
        names.add(self.env.company.name)
        return [name for name in names if name]

    def _free_text_enabled(self):
        """Whether this workspace accepts free-typed questions. Default NO.

        `sudo()` because the parameter is a workspace-level policy switch and a
        non-admin user must be able to hit the refusal rather than an
        AccessError on ir.config_parameter — a confusing error for a
        deliberate policy.

        Defaulting to False rather than True is the whole point: turning this
        on should be a decision someone makes, having read what it means (#375),
        not a state a workspace arrives in by installing a module.
        """
        return self.env['ir.config_parameter'].sudo().get_param(
            'ncollection_ai.enable_free_text_questions', '').strip().lower() in (
                '1', 'true', 'yes')

    def _document_prefixes(self):
        """The tenant's REAL ir.sequence prefixes.

        Shape cannot distinguish a document reference from a government ID —
        "S000042" (a sale order at padding 6) and a 1-letter passport are
        identical. Asking the database removes the guesswork, and removes the
        dependence on whatever padding Odoo happens to ship today.

        Returns (PREFIX, padding) pairs. The padding half is not decoration:
        matching on prefix alone was a live passport leak. This repo's own dev
        database returns OP, SP and WH among its prefixes — all two characters,
        exactly the width _ALNUM_ID_RE allows — so "passport WH1234567" was
        exempted as a warehouse reference and sent verbatim. A genuine WH
        reference is WH/OUT/00012 and never matches that pattern at all.
        """
        # sudo(): ir.sequence prefixes are non-sensitive document-format
        # metadata (core already grants read to base.group_user), and this read
        # only feeds a REDACTION decision — it never returns data to the caller.
        # sudo here guarantees the decision is made on the real prefixes rather
        # than failing open if a future role loses that read.
        seqs = self.env['ir.sequence'].sudo().search(
            [('prefix', '!=', False)], limit=500)
        prefixes = set()
        for seq in seqs:
            # Prefixes carry strftime placeholders like %(year)s; keep the
            # leading literal, which is what a reference actually starts with.
            head = re.split(r'%|/', seq.prefix or '')[0].strip()
            if head and head.isalpha():
                prefixes.add((head.upper(), seq.padding or 0))
        return sorted(prefixes)

    @api.model
    def ask(self, question, max_context_tokens=2000, max_answer_tokens=1024):
        """Answer a natural-language question about THIS tenant's data.

        Returns ``{'answer', 'dropped_sections', 'usage'}``.
        """
        import json  # local: keeps the module import-light for a rarely-used path

        # BEFORE anything is read. An authorization check that runs after the
        # context is built has already done the privileged read it was meant to
        # prevent — harmless here because nothing is returned, but it makes the
        # ordering load-bearing the moment someone adds logging or a cache.
        #
        # AccessError, not UserError: Odoo maps it to HTTP 403, which is what an
        # RPC caller should see for an authorization refusal.
        if not any(self.env.user.has_group(x) for x in _ALLOWED_GROUPS):
            raise AccessError(self.env._(
                "The AI assistant is available to the Accountant, the CEO and "
                "the workspace owner."))

        # FREE TEXT IS OFF BY DEFAULT. This is a scope decision, not a bug.
        #
        # P5-T03 is the Context Injection Engine. Its acceptance criteria are
        # "injection tests prove no cross-tenant data can enter a prompt" and
        # "context quality reviewed on 20 sample questions" — a curated
        # developer list. Accepting ARBITRARY end-user prose was never in this
        # ticket; it was added here, and every CRITICAL across eight review
        # rounds has been about it.
        #
        # Round 8 is where it stops. A reviewer showed that
        #     "the wifi password is sunshine"      leaks
        # while
        #     "the token is stored securely"       must not be refused
        # and those two are STRUCTURALLY IDENTICAL — noun, connector, one word.
        # Loosening the value test refuses the second; tightening it leaks the
        # first. No rule reads both correctly, because the difference is
        # semantic, not syntactic.
        #
        # So the engine ships and the free-text surface ships OFF. The filters
        # below remain as defence in depth for whoever turns it on, and #375
        # owns that decision along with the provider terms it depends on.
        if not self._free_text_enabled():
            raise UserError(self.env._(
                "Natural-language questions are not enabled on this workspace. "
                "The AI context engine is available, but sending free-typed "
                "text to an external provider must be switched on deliberately "
                "— see the AI section of the workspace settings."))

        context = self.env['ncollection.ai.context']._build(
            max_tokens=max_context_tokens)

        # Sanitise the WHOLE payload — context and question together.
        #
        # An earlier version of this comment claimed that alone was sufficient.
        # It was not, and three reviewers reproduced the leak independently: the
        # sanitiser recognises identities BY FIELD NAME, and `question` is not an
        # identity field, so a customer name typed by the user travelled to the
        # provider verbatim. Free text is a category field names cannot reach.
        #
        # So the known names are passed explicitly. The sanitiser cannot look
        # them up itself without an ORM query inside what should stay a pure,
        # testable transform.
        question = (question or '')[:_MAX_QUESTION_CHARS]

        # Fail closed BEFORE any scrubbing runs. Two independent triggers,
        # because they catch disjoint things — see the block comment above.
        declared = _declares_a_secret(question)
        shaped = any(_looks_like_embedded_secret(w) for w in question.split())
        if declared or shaped:
            raise UserError(self.env._(
                "Your question looks like it contains a password, key or "
                "token. Nothing was sent. Please remove it and ask in plain "
                "words — this assistant works from your workspace data, so it "
                "does not need one."))

        clean, mapping = self.env['ncollection.ai.pii']._sanitise(
            {'context': context['sections'], 'question': question},
            known_identities=self._known_identities(),
            doc_prefixes=self._document_prefixes(),
        )

        workspace = clean['context'].get('workspace', {})
        prompt = _PROMPT_TEMPLATE.format(
            company=workspace.get('company', 'this workspace'),
            currency=workspace.get('currency', 'the workspace currency'),
            context=json.dumps(clean['context'], indent=2, default=str),
            question=clean['question'],
        )

        response = self.env['ncollection.ai.gateway']._complete(
            prompt, max_tokens=max_answer_tokens)

        return {
            # Re-hydrated HERE, inside the database that owns the mapping.
            'answer': self.env['ncollection.ai.pii']._rehydrate(
                response.get('text', ''), mapping),
            # Surfaced rather than silent: an answer built on truncated evidence
            # should be visibly so.
            'dropped_sections': context['dropped'],
            'usage': response.get('usage', {}),
        }
