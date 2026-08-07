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
#      prefixes, JWTs, PEM blocks, connection strings, IBANs, card PANs. These
#      have real, checkable structure. This layer works; it is not the gap.
#   2. _declares_a_secret     — a credential NOUN handed a value that is not an
#      ordinary state word. The giveaway is the noun, not the value, which is
#      why this reaches short, lowercase and multi-word secrets that shape never
#      can.
#   3. _looks_like_embedded_secret — high-entropy mixed-alphanumeric runs that
#      no word is, with emails and URLs exempted so the assistant stays usable.
#
# THE RESIDUAL, STATED AS THE ACTUAL BOUNDARY RATHER THAN A FLATTERING ONE.
#
# Round 5 described this as "declared secrets are caught, undeclared ones are
# not", and a reviewer correctly called that an overclaim: "the CVV is 123" was
# DECLARED in plain English and still leaked, because the noun was missing from
# the list. Nothing undecidable about it — it was simply unimplemented, and the
# undecidability argument was being used to cover a gap it did not apply to.
#
# The real boundary, precisely:
#
#   CAUGHT      a credential named by a noun in _CREDENTIAL_NOUN and either
#                 * immediately followed by a letters-and-digits token
#                   ("wifi password p4ssW0rd2026"), or
#                 * joined within _NOUN_VALUE_WINDOW characters by a connector
#                   in _CONNECTOR_RE whose value is not in _STATE_WORD_RE;
#               OR any single token over _MAX_WORD_CHARS mixing letters and
#               digits; OR anything matching a pii.py structural shape.
#
#   NOT CAUGHT, each demonstrated by a reviewer and each pinned in the RESIDUAL
#   section of the gate corpus so the count stays visible:
#                 * a noun outside the list ("the doorcode is 4521")
#                 * no noun at all ("check correcthorsebatterystaple for me")
#                 * a DECOY CLAUSE that puts a state word in the value slot and
#                   the real secret further along:
#                       "the password is not accepted, it's actually p4ss123"
#                   Fixable only by scanning every token near the noun, which
#                   was measured and rejected: it refuses "the api key rotation
#                   policy: how many keys are older than 90 days?" because
#                   "rotation" is eight characters.
#                 * a declaration whose connector falls outside the window
#
# The noun list is maintainable and should grow when someone finds a gap. The
# rest is the genuinely undecidable part — a lowercase passphrase is
# indistinguishable from prose — and is why this feature needs a zero-retention
# provider agreement rather than a cleverer regex.
#
# SIX REVIEW ROUNDS ESTABLISHED THIS. Every round that tried to close the gap
# with more pattern opened a false-refusal somewhere else, and a control that
# blocks "Can you pass this invoice to Sarah?" gets switched off, which is a
# worse security outcome than the gap it was closing.

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
#   _NOUN_SEP_BOUNDED  `key` and `pin` are inside ordinary English words
#                      (monkey, donkey, whiskey, keyboard, spin, pinpoint), so
#                      these must not be preceded by a letter or digit.
#   _NOUN_COMPOUND_OK  `password`, `login`, `token` and friends appear inside
#                      COMPOUND CREDENTIAL NAMES far more often than inside
#                      English words — userlogin, jwt_token, smtp_password — so
#                      a letter prefix is allowed. Both were verified against a
#                      both-directions case list before being applied.
_NOUN_SEP_BOUNDED = (r'(?<![A-Za-z0-9])'
                     r'(?:key(?!\s+to\b)|pin)'
                     r'(?![A-Za-z0-9])')
_NOUN_COMPOUND_OK = (
    r'(?:pass(?:word|wd|phrase|code|key)|pwd|'
    r'secret(?!\s+(?:sauce|santa|weapon|ingredient|to\b))|credentials?|'
    r'token|login|otp|cvv|cvc|(?:recovery|seed|mnemonic)\s+phrase|'
    r'(?:security|access|verification)\s+code)(?![A-Za-z0-9])')
_CREDENTIAL_NOUN = '(?:%s|%s)' % (_NOUN_SEP_BOUNDED, _NOUN_COMPOUND_OK)

_CREDENTIAL_NOUN_RE = re.compile(_CREDENTIAL_NOUN, re.IGNORECASE)
_NOUN_VALUE_WINDOW = 40
_CONNECTOR_RE = re.compile(r'(?:\b(?:is|are|was|were|to)\b|[=:,]|\s-\s)\s*',
                           re.IGNORECASE)
_STATE_WORD_RE = re.compile(
    r'(?:in)?valid|expired?|wrong|missing|null|none|empty|blank|broken|'
    r'incorrect|correct|working|required|mandatory|optional|unavailable|'
    r'unset|set|rejected|refused|denied|accepted|active|inactive|disabled|'
    r'enabled|not|no|never|always|the|a|an|still|now|used|unused|gone|ok|'
    r'fine|failing|failed|different|same|right|bad|good|new|old|down|up|'
    # Question words. "The api key rotation policy: how many keys are older
    # than 90 days?" was refused because the topic-separator colon was read
    # as a connector and "how" was not recognised. A value that is a question
    # word is never a credential.
    r'how|what|when|why|which|who|where|whether|any|all|only|just|it|its|'
    r'is|are|was|were|that|this|there|here|can|could|should|would',
    re.IGNORECASE)

# There is no third trigger. Round 5 had one — a credential noun
# co-occurring with a long token — and round 6 deleted it: once trigger 1
# read the FIRST connector instead of skipping to any connector, it caught
# everything trigger 3 did, including the case trigger 3 was written for
# ("ssh key fingerprint check: myapikeyis..."). Measured, not assumed: with
# trigger 3 disabled the corpus still refuses all 21 exploits. All it still
# contributed was a false refusal of
#     "customer creditworthiness key metrics ... internationalisation project"
# where `key` and the long word were unrelated. A redundant control that only
# produces false positives is worse than no control.

# 19, not 24. The old floor sat directly above the band two reviewers exploited
# — real secrets of 20-24 characters (`Tr0ub4dor3xKcvQmZpL9` is 20) passed the
# gate and were then too short for pii.py's 32-hex / 40-base64 generic shapes.
# The two layers shared a blind spot rather than covering one another.
_MAX_WORD_CHARS = 19
_SUSPICIOUS_RUN_RE = re.compile(r'[A-Za-z0-9+/_=-]{16,}')

# Exempted from the shape gate: both are ordinary content in a business
# question, both are long, hyphen-rich and digit-bearing, and both were being
# refused outright. Neither is unprotected by the exemption — pii.py
# pseudonymises emails, and a URL carrying credentials still matches the
# connection-string pattern there.
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


def _value_after(tail, connector):
    """The first whitespace token after a connector, stripped of punctuation."""
    rest = tail[connector.end():].split()
    return rest[0].strip('.,;:!?()[]"\'') if rest else ''


def _looks_like_a_credential_value(value):
    """Value-shaped rather than merely not-a-state-word.

    Applied only to connectors AFTER the first, where the bar has to be higher
    — see _declares_a_secret. A credential carries a digit or real length;
    "can", "ask" and "you" carry neither.
    """
    return len(value) >= 8 or any(c.isdigit() for c in value)


def _declares_a_secret(text):
    """Trigger 1: a credential noun handed a value that is not a state word.

    THE FIRST connector after the noun is judged leniently: any value that is
    not an ordinary state word means a declaration. That is what catches
    "the CVV is 123" and "root password is hunter2", and what ALLOWS
    "The API key is invalid" — reading only the first connector gives
    "invalid", so the question goes through.

    LATER connectors are judged strictly, and that asymmetry is the point. A
    filler clause beginning with a state word would otherwise mask the real
    declaration:
        "the password, which was rotated last night, is p4ss123"
    ("which" is a state word.) But scanning on with the lenient rule would
    re-break the benign case above, because its later comma yields "can". So
    later connectors additionally require a value-SHAPED value.

    Both halves are pinned by the corpus in tests/test_ai_question.py, in both
    directions. Six review rounds established that this trigger cannot be made
    complete — see the boundary section at the top of this module for exactly
    what it does and does not catch.
    """
    for noun in _CREDENTIAL_NOUN_RE.finditer(text):
        tail = text[noun.end():noun.end() + _NOUN_VALUE_WINDOW]

        # JUXTAPOSITION — no connector at all, which a reviewer identified as
        # "arguably the single most common way people paste a credential":
        #     "wifi password p4ssW0rd2026"      "ssh key AbCdEf123456"
        # Only the IMMEDIATELY following token counts, and it must mix letters
        # AND digits, which is what keeps ordinary noun phrases quiet —
        # "password reset email", "api key rotation policy", "login page" all
        # have a plain word there.
        following = tail.split()
        if following:
            candidate = following[0].strip('.,;:!?()[]"\'')
            if (len(candidate) >= 6
                    and any(c.isdigit() for c in candidate)
                    and any(c.isalpha() for c in candidate)):
                return True

        for position, connector in enumerate(_CONNECTOR_RE.finditer(tail)):
            value = _value_after(tail, connector)
            if not value:
                continue
            if not _STATE_WORD_RE.fullmatch(value):
                if position == 0 or _looks_like_a_credential_value(value):
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
