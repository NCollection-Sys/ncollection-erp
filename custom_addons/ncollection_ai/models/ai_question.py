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
#   2. _DECLARED_SECRET_RE   — a credential NAMED and handed a value
#      ("password is X", "api key: Y"). This is how a person actually leaks one
#      in a chat box, and it catches the short/lowercase/multi-word cases that
#      shape never can, because the giveaway is the noun, not the value.
#   3. _looks_like_embedded_secret — high-entropy mixed-alphanumeric runs that
#      no word is, with emails and URLs exempted so the assistant stays usable.
#
# RESIDUAL, ACCEPTED, NOT A TODO: an UNDECLARED secret that is shaped like
# ordinary prose still reaches the provider. Nothing here catches
# "check correcthorsebatterystaple for me". That is inherent, it is documented
# in README.rst, and it is the reason this feature needs a zero-retention
# provider agreement rather than a cleverer regex.

# The credential nouns themselves. Bare `key` is included and covers the
# api/access/private/ssh compounds for free — enumerating them instead missed
# "here is the key:", which is how a person actually writes it.
_CREDENTIAL_NOUN = (r'\b(?:pass(?:word|wd|phrase)?|pwd|secret|credentials?|'
                    r'key|token)\b')

# TRIGGER 1 — a credential noun handed a value. The `is|are|=|:` is
# load-bearing in BOTH directions: it catches "password is hunter2", and it is
# what stops "the secret santa invoice" and "does the password reset email
# work" from being refused.
_DECLARED_SECRET_RE = re.compile(
    _CREDENTIAL_NOUN + r'\s*(?:is|are|=|:)\s*\S', re.IGNORECASE)

# TRIGGER 3 — co-occurrence. A credential noun ANYWHERE plus an unusually long
# token ANYWHERE, even when no `is`/`:` joins them. This is what catches
#     "ssh key fingerprint check: myapikeyisabcdefghijklmnop"
# where the value hangs off "check:" rather than off the noun, and is 26
# lowercase letters with no digit — invisible to both other triggers.
#
# Requiring BOTH signals is what keeps it quiet: an ordinary question mentioning
# a password carries no 20-character token, and a question carrying a long token
# usually mentions no credential. Neither alone refuses anything.
_CREDENTIAL_NOUN_RE = re.compile(_CREDENTIAL_NOUN, re.IGNORECASE)

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


def _looks_like_a_long_value(word):
    """A token too long to be an ordinary word, whatever it is made of.

    No digit requirement — that is the whole point, since this is the signal
    that catches all-lowercase passphrases. It is safe to be this loose ONLY
    because the caller also requires a credential noun in the same question;
    on its own it would refuse 'internationalisation'.
    """
    stripped = word.strip('.,;:!?()[]"\'')
    if len(stripped) <= _MAX_WORD_CHARS:
        return False
    return not (_EMAIL_SHAPE_RE.match(stripped) or _URL_SHAPE_RE.match(stripped))


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
        """
        seqs = self.env['ir.sequence'].sudo().search(
            [('prefix', '!=', False)], limit=500)
        prefixes = set()
        for seq in seqs:
            # Prefixes carry strftime placeholders like %(year)s; keep the
            # leading literal, which is what a reference actually starts with.
            head = re.split(r'%|/', seq.prefix or '')[0].strip()
            if head and head.isalpha():
                prefixes.add(head.upper())
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

        context = self.env['ncollection.ai.context'].build(
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
        words = question.split()
        declared = _DECLARED_SECRET_RE.search(question)
        shaped = any(_looks_like_embedded_secret(w) for w in words)
        co_occurs = bool(_CREDENTIAL_NOUN_RE.search(question)) and \
            any(_looks_like_a_long_value(w) for w in words)
        if declared or shaped or co_occurs:
            raise UserError(self.env._(
                "Your question looks like it contains a password, key or "
                "token. Nothing was sent. Please remove it and ask in plain "
                "words — this assistant works from your workspace data, so it "
                "does not need one."))

        clean, mapping = self.env['ncollection.ai.pii'].sanitise(
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

        response = self.env['ncollection.ai.gateway'].complete(
            prompt, max_tokens=max_answer_tokens)

        return {
            # Re-hydrated HERE, inside the database that owns the mapping.
            'answer': self.env['ncollection.ai.pii'].rehydrate(
                response.get('text', ''), mapping),
            # Surfaced rather than silent: an answer built on truncated evidence
            # should be visibly so.
            'dropped_sections': context['dropped'],
            'usage': response.get('usage', {}),
        }
