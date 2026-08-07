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
from odoo.exceptions import UserError

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

# FAIL CLOSED ON FREE TEXT — the scope change agreed after three review rounds.
#
# Denylist scrubbing of arbitrary text is unwinnable: each round of patterns
# closed the previous round's exploit and opened a new one (a password with a
# colon, a hex-not-checksum secret, a compound display name, a 16-digit PAN).
# Every fix proved the last bug dead, never the class.
#
# So the question is now checked against an ALLOWLIST of shape before anything
# is sent. A business question is words, numbers and punctuation. A credential,
# key, token or identifier is a long unbroken run of mixed alphanumerics — which
# no English word is. If such a run survives scrubbing, the request is REFUSED
# rather than shipped, and the user is told to remove it.
#
# This bounds the class instead of chasing instances. Scrubbing stays as the
# second layer: this gate catches what patterns miss, patterns catch what the
# gate's threshold allows through.
_MAX_WORD_CHARS = 24
_SUSPICIOUS_RUN_RE = re.compile(r'[A-Za-z0-9+/_=-]{16,}')


def _looks_like_embedded_secret(word):
    """True when a whitespace-delimited token cannot be ordinary prose.

    Requires BOTH length and mixed character classes: 'internationalisation' is
    long but all letters, while 'sk_live_51H8gk3K2FZ' mixes cases and digits.
    """
    if len(word) <= _MAX_WORD_CHARS:
        return False
    stripped = word.strip('.,;:!?()[]"\'')
    if not _SUSPICIOUS_RUN_RE.search(stripped):
        return False
    has_digit = any(c.isdigit() for c in stripped)
    has_alpha = any(c.isalpha() for c in stripped)
    return has_digit and has_alpha


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

        # Fail closed BEFORE any scrubbing runs. See _looks_like_embedded_secret.
        offenders = [w for w in question.split() if _looks_like_embedded_secret(w)]
        if offenders:
            raise UserError(self.env._(
                "Your question looks like it contains an identifier, key or "
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
