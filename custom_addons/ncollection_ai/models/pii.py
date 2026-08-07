# -*- coding: utf-8 -*-
"""PII sanitisation for prompt context (P5-T03 / #60).

WHY THIS RUNS HERE AND NOWHERE ELSE
-----------------------------------
AI_PLATFORM_DESIGN.md §5, first line: "Sanitise tenant-side, before transit.
This is the whole reason context is built in the tenant DB. Once data reaches the
gateway it has already left the tenant boundary; scrubbing there would be
theatre."

So this module runs inside the tenant database that owns the data, and its output
is what crosses the boundary. Nothing downstream re-checks it, because nothing
downstream *can*: the gateway holds no DB credentials and cannot tell a real IBAN
from a plausible string.

THE POLICY, VERBATIM FROM §5
----------------------------
* **Never send:** passwords, API keys, HMAC secrets, full bank/IBAN numbers,
  national ID numbers.
* **Pseudonymise by default:** partner names, emails, phone numbers — replaced
  with stable per-request tokens (``PARTNER_1``) and re-hydrated in the response
  tenant-side. "The model reasons over structure; it does not need real
  identities."
* **Send freely:** amounts, dates, account types, aggregates, document states —
  "the actual substance".

WHY PSEUDONYMISE RATHER THAN REDACT
-----------------------------------
Redaction destroys the relationships a question depends on: "which customer owes
the most across these three invoices" is unanswerable if every name is `[REDACTED]`.
A *stable* token keeps the join — the same partner is `PARTNER_1` everywhere in
one request — while the real identity never leaves the database. Tokens are
per-request, so nothing accumulates a reusable mapping.
"""
import re

from odoo import api, models

# Values that must NEVER cross the boundary, in any form. Redacted outright
# rather than pseudonymised: unlike a name, there is no analytical value in
# knowing that two rows share a secret.
_NEVER_SEND_FIELDS = frozenset({
    'password', 'password_crypt', 'api_key', 'apikey', 'secret', 'token',
    'hmac_key', 'private_key', 'access_token', 'refresh_token',
    'acc_number', 'iban', 'bank_account', 'national_id', 'passport_no',
    'vat_number_full', 'ssn',
})

# Substring markers, for field names this list cannot enumerate ahead of time.
# Deliberately broad: a false redaction costs a little context quality, a missed
# one leaks a credential.
_NEVER_SEND_HINTS = ('password', 'secret', 'api_key', 'apikey', 'token',
                     'iban', 'acc_number', 'private_key', 'national_id')

_REDACTED = '[REDACTED]'

# "is this value already nothing but pseudonyms" — used to decide whether a
# partner field still needs whole-field tokenisation. Allows separators so
# "PARTNER_1 / PARTNER_2" counts as fully tokenised.
_ONLY_TOKENS_RE = re.compile(r'(?:(?:PARTNER|EMAIL|PHONE)_\d+|\[REDACTED\]|[\s,;/-]+)+')

# ---------------------------------------------------------------------------
# SHAPE-BASED SCRUBBING — the free-text tier
# ---------------------------------------------------------------------------
# Field-name matching (above) protects structured data. It reaches NOTHING in
# free text, and `question` — the module's main entry point — is always free
# text. Four independent reviewers found the same hole from different angles:
# a customer name, an API key, a JWT and a passport number all travelled to the
# provider verbatim because no dict key said "secret".
#
# So every string is also scrubbed by SHAPE, regardless of where it came from.
# These patterns are deliberately eager: a false redaction costs a little
# context quality, a missed one ships a live credential to a third party.

# ISO 13616. IGNORECASE because a lowercase IBAN is still an IBAN — without it
# the value fell through to the phone pattern and was MISLABELLED as PHONE_n
# rather than redacted, which looked like protection while being an accident.
_IBAN_RE = re.compile(r'(?<![A-Za-z0-9])[A-Z]{2}\d{2}[A-Z0-9]{11,30}\b', re.IGNORECASE)

# Every prefix below is anchored with (?<![A-Za-z0-9]) rather than \b. Round 7
# fixed only the password= pattern and the comment claimed "the round-6
# CRITICAL" resolved in the past tense — a reviewer then showed the SAME bug
# alive in eleven siblings, because `\b` never fires after `_`:
#     "STRIPE_SECRET_KEY_sk_live_51exampleK2FZabcdefghijkl please rotate"
#     "prod_gh_token_ghp_1234567890abcdefghij was committed by mistake"
#     "field_passport_A1234567 on file"
# A partial fix described as a complete one is the exact failure this file's
# history is made of, so the whole family is anchored the same way.
#
# Credential shapes. Prefix-anchored where the vendor publishes one, plus a
# JWT shape and an Authorization header value. This list will never be
# complete; it does not need to be, it needs to catch what people actually
# paste into a text box.
_SECRET_SHAPES = (
    re.compile(r'(?<![A-Za-z0-9])sk[-_](?:live|test)?[-_]?[A-Za-z0-9]{16,}\b'),   # Stripe & friends
    re.compile(r'(?<![A-Za-z0-9])AKIA[0-9A-Z]{16}\b'),                            # AWS access key
    re.compile(r'(?<![A-Za-z0-9])gh[pousr]_[A-Za-z0-9]{20,}\b'),                  # GitHub tokens
    re.compile(r'(?<![A-Za-z0-9])xox[baprs]-[A-Za-z0-9-]{10,}\b'),                # Slack tokens
    re.compile(r'(?<![A-Za-z0-9])eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.'
               r'[A-Za-z0-9_-]*'),                                  # JWT
    re.compile(r'(?<![A-Za-z0-9])(?:Bearer|Basic)\s+[A-Za-z0-9+/._~-]{16,}=*',
               re.IGNORECASE),                                      # auth headers
    # Credentials embedded in a URL or connection string. The most plausible
    # paste in an ERP/integration context, and nothing else here catches it:
    #   postgres://admin:Sup3rSecret123@db.internal:5432/erp
    # Anything up to the LAST '@' before whitespace. The previous version
    # excluded ':' and '/' from the userinfo groups, so one such character in
    # the password broke the match entirely and the whole string — scheme, user
    # and password — shipped verbatim. Passwords containing ':' or '/' are
    # unremarkable, so that was a one-character bypass of this round's headline
    # coverage.
    re.compile(r'(?<![A-Za-z0-9])[a-z][a-z0-9+.-]*://[^\s@]+@\S+', re.IGNORECASE),
    # Lookaround, not \b — same round-6 CRITICAL as ai_question's noun regex.
    # `\b` does not fire between `_` and a letter, so DB_PASSWORD=hunter2 was
    # invisible HERE TOO, meaning the refusal gate and this redactor missed it
    # together. An env-var line is the most common shape a pasted secret takes.
    re.compile(r'(?<![A-Za-z0-9])(?:password|passwd|pwd)\s*=\s*\S+',
               re.IGNORECASE),
    re.compile(r'(?<![A-Za-z0-9])AIza[A-Za-z0-9_-]{30,}\b'),                     # GCP API key
    # IGNORECASE. This is the THIRD case-sensitivity miss in this file
    # (IBAN, then _ALNUM_ID_RE, now this) — added after both fixes and
    # still repeating them.
    re.compile(r'-----BEGIN[A-Z ]*PRIVATE KEY-----', re.IGNORECASE),
    # Long alphanumeric blobs, and long hex runs, are both redacted. Only pure
    # DIGIT runs are exempt (handled by the PAN pattern below).
    #
    # THE CHECKSUM TRADE-OFF, decided rather than stumbled into. A SHA-256 and
    # an HMAC key are both arbitrary hex; no shape distinguishes them. Two
    # attempts to be clever failed:
    #   * exempt all 40+ hex  -> handed a free pass to every hex secret.
    #   * exempt only digest lengths (32/40/64) -> a 40-char HMAC key IS
    #     SHA-1 length, so it sailed through. Verified leaking.
    # §5 makes "never send" unconditional for secrets, and this file's own rule
    # is that a missed secret costs more than a redacted checksum. So hex is
    # redacted, and a user asking "does this hash match" gets [REDACTED] — a
    # real, accepted loss of context quality, pinned by a test that asserts the
    # redaction rather than pretending it does not happen.
    # (?![0-9]+\b) matters more than it looks: every DIGIT is also a hex
    # character, so without it this pattern eats the 44-digit lot number the
    # must-survive test protects. Pure digits are the PAN pattern's job.
    re.compile(r'(?<![A-Za-z0-9])(?![0-9]+\b)[0-9a-fA-F]{32,}\b'),
    re.compile(r'(?<![A-Za-z0-9])(?![0-9]+\b)[A-Za-z0-9+/]{40,}={0,2}\b'),
    # Long digit runs beyond phone length. Before the E.164 upper bound these
    # were incidentally swept into PHONE_n — mislabelled, but redacted. Adding
    # the bound removed that accidental cover from 16-digit card PANs, which sit
    # in the same sensitivity class as the bank numbers §5 names.
    # 13-19 digits: the ISO/IEC 7812 PAN range. NOT "15+" — that also swallowed
    # a 44-digit lot number, destroying substance §5 says to send freely. The
    # bound has to match the thing being protected, not merely exceed phones.
    re.compile(r'(?<![A-Za-z0-9])(?:\d[ -]?){12,18}\d\b'),
)

# Government-issued identifiers that mix letters and digits — passports,
# driving licences, residence permits. The phone pattern cannot see these: one
# embedded letter breaks its character class, so they sailed through untouched.
#
# Bounded to 1-2 leading letters + 6-9 digits.
#
# NO negative lookahead, and IGNORECASE — both were bugs found on re-review:
#
#  * `(?![A-Z]{2}\d{2})` was meant to avoid overlapping the IBAN pattern, but an
#    ID like "AB1234567" NECESSARILY starts with two letters and two digits, so
#    the lookahead excluded every 2-letter-prefixed ID — a large share of real
#    passport and national-ID formats. IBANs are already redacted one step
#    earlier, so it protected nothing and blinded the pattern.
#  * Without IGNORECASE "a1234567" sailed through — repeating, one line below,
#    the exact case-sensitivity bug just fixed on the IBAN pattern.
#
# No document guard is needed: genuine Odoo references cannot match. "INV/…"
# and "BILL/…" have 3-4 leading letters (over the cap), and "SO0042" has 4
# digits (under the floor). Verified against INV/2026/00042, SO0042,
# WH/OUT/00012, BILL/2026/0007 and PO00123 — none match. The guard that used to
# sit here checked for an adjacent "/" and was trivially bypassed by ordinary
# punctuation ("passport /A1234567" leaked), so it made things strictly worse.
_ALNUM_ID_RE = re.compile(r'(?<![A-Za-z0-9])[A-Z]{1,2}\d{6,9}\b', re.IGNORECASE)
_EMAIL_RE = re.compile(r'(?<![A-Za-z0-9])[\w.+-]+@[\w-]+\.[\w.-]+\b')
# Deliberately conservative: matching more aggressively starts eating the
# substance §5 says to send freely.
#
# The candidate shape alone is NOT sufficient, and the tests proved it: an ISO
# date like "2026-08-07" matches it exactly (digit, eight separator-ish
# characters, digit). Pseudonymising every date would gut the feature — you
# cannot answer "unpaid invoices last quarter" against DATE_1.
#
# DIGIT COUNT is what separates them: an ISO date carries 8 digits, a phone
# number 9 or more. So candidates are matched loosely and then confirmed by
# counting, in _looks_like_phone().
_PHONE_CANDIDATE_RE = re.compile(r'\+?\d[\d\s().-]{7,}\d')
_MIN_PHONE_DIGITS = 9
# E.164 caps a real phone number at 15 digits. Without an UPPER bound the
# pattern swallowed any long digit run — a 44-digit lot/batch number came back
# as PHONE_1, destroying data §5 lists as substance. Found by the
# false-positive test the security re-review asked for; the lower bound alone
# was never sufficient.
_MAX_PHONE_DIGITS = 15


class AiPiiSanitiser(models.AbstractModel):
    """Sanitise a context payload, and re-hydrate the model's answer.

    Abstract: it holds no data of its own. It is a model rather than a plain
    module so tenant code can override the policy per deployment (a stricter
    jurisdiction may want names redacted outright) without patching this file.
    """

    _name = 'ncollection.ai.pii'
    _description = 'AI prompt PII sanitisation (tenant-side, pre-transit)'

    # ---------------------------------------------------------------- sanitise
    # @api.model on both public methods: Odoo's call_kw treats the first
    # positional arg as record ids and browse()s it unless _api_model is
    # set. Neither is RPC-called today, but a future caller would have had
    # `payload` silently swallowed as ids rather than erroring.
    @api.model
    def _sanitise(self, payload, known_identities=None, doc_prefixes=None):
        """Return ``(clean_payload, mapping)``.

        ``mapping`` is token -> original, used ONLY to re-hydrate the response
        inside this database. It is never sent anywhere: returning it separately
        rather than embedding it in the payload makes that hard to get wrong by
        accident.

        ``known_identities`` is a list of real names (partners, companies) to
        pseudonymise wherever they appear IN FREE TEXT. Field-name matching
        cannot help there — a user typing "how much does Al Barari Trading owe"
        produces a value under the key ``question``, which no identity-field
        list will ever match. Callers that have the names (see
        ncollection.ai.question) must pass them; the default of None keeps this
        model usable standalone and testable without an ORM fixture.
        """
        mapping = {}
        counters = {'PARTNER': 0, 'EMAIL': 0, 'PHONE': 0}
        # Longest first: pseudonymising "Al Barari" before "Al Barari Trading"
        # would leave the word "Trading" stranded beside a token.
        identities = sorted(
            {name for name in (known_identities or []) if name and len(name) >= 4},
            key=len, reverse=True)
        # (PREFIX, padding) pairs — see _is_document_reference. Normalised here
        # so callers may pass any iterable of pairs.
        prefixes = tuple((str(prefix).upper(), int(padding or 0))
                         for prefix, padding in (doc_prefixes or ()) if prefix)
        clean = self._walk(payload, mapping, counters,
                           identities=identities, doc_prefixes=prefixes)
        return clean, mapping

    def _walk(self, node, mapping, counters, field_name='', identities=(),
              doc_prefixes=()):
        if isinstance(node, dict):
            return {
                key: (_REDACTED if self._is_secret(key)
                      else self._walk(value, mapping, counters, key, identities,
                                      doc_prefixes))
                for key, value in node.items()
            }
        if isinstance(node, (list, tuple)):
            return [self._walk(item, mapping, counters, field_name,
                               identities, doc_prefixes)
                    for item in node]
        if isinstance(node, str):
            return self._scrub_text(node, mapping, counters, field_name,
                                    identities, doc_prefixes)
        # int / float / bool / None — amounts, dates-as-numbers, counts, states.
        # §5: "send freely — the actual substance".
        return node

    def _is_secret(self, field_name):
        lowered = (field_name or '').lower()
        if lowered in _NEVER_SEND_FIELDS:
            return True
        return any(hint in lowered for hint in _NEVER_SEND_HINTS)

    def _scrub_text(self, text, mapping, counters, field_name,
                    identities=(), doc_prefixes=()):
        """Redact secrets found by pattern, pseudonymise identities.

        ORDER MATTERS. Secrets are redacted before anything else gets a chance
        to tokenise part of them — a JWT contains dots and base64 that other
        patterns would happily chew into fragments, leaving a partial credential
        in the payload rather than none of it.
        """
        # 1. Credentials. Highest severity, so they go first and are REDACTED,
        #    never tokenised — there is no analytical value in a secret.
        for pattern in _SECRET_SHAPES:
            text = pattern.sub(_REDACTED, text)

        # 2. IBANs — can hide in free text where no field name helps.
        text = _IBAN_RE.sub(_REDACTED, text)

        # 3. Government identifiers that mix letters and digits.
        #
        #    Document references are protected by matching against the tenant's
        #    ACTUAL ir.sequence prefixes, passed in by the caller. Shape alone
        #    cannot separate them: "S000042" (a sale order at padding 6) and a
        #    1-letter passport are identical. The previous claim that "genuine
        #    references cannot match" held only for the padding values Odoo
        #    ships today — an admin bumping sale.order padding from 5 to 6 in
        #    Settings would have started destroying every order reference.
        #    The exemption matches prefix AND DIGIT COUNT, not prefix alone.
        #    A bare startswith was a live passport leak, demonstrated against
        #    this repo's REAL sequences: `_document_prefixes()` on the dev
        #    database returns GROUP, INT, OP, PACK, PAY, SP, WH — and OP, SP and
        #    WH are two characters, exactly the width _ALNUM_ID_RE allows. So
        #        "verify passport WH1234567 against the visa file"
        #    was sent verbatim, because it happens to start with a stock
        #    warehouse prefix. Genuine WH references look like WH/OUT/00012 and
        #    never match this pattern at all.
        text = _ALNUM_ID_RE.sub(
            lambda m: m.group(0)
            if self._is_document_reference(m.group(0), doc_prefixes)
            else _REDACTED, text)

        # Snapshot AFTER every redaction, BEFORE any tokenisation. This is what
        # a whole-field partner wrap stores as its mapping value (see step 7),
        # so the stored value can never contain a token — which is what made
        # re-hydration produce nested garbage.
        #
        # Deliberately not the raw input: a redacted secret must stay redacted.
        # Restoring the pre-redaction text would resurrect a credential into the
        # user-visible answer, which is a smaller blast radius than sending it
        # to the provider but is still the thing this module exists to prevent.
        redacted_only = text

        # 4. Known real identities appearing anywhere in the text. This is what
        #    field-name matching structurally cannot do, and it is the fix for
        #    the leak three reviewers reproduced: a customer name typed into a
        #    free-text question.
        for name in identities:
            if name in text:
                text = text.replace(
                    name, self._token('PARTNER', name, mapping, counters))

        text = _EMAIL_RE.sub(
            lambda m: self._token('EMAIL', m.group(0), mapping, counters), text)
        text = _PHONE_CANDIDATE_RE.sub(
            lambda m: (self._token('PHONE', m.group(0), mapping, counters)
                       if self._looks_like_phone(m.group(0)) else m.group(0)),
            text)

        # Partner-ish fields are pseudonymised whole: a name is not a pattern,
        # so it can only be recognised by where it came from.
        #
        # The condition is "is this value now nothing but tokens", NOT "did we
        # substitute something". Both earlier versions were wrong in opposite
        # directions:
        #
        #   * no guard at all -> a value that was both a known identity and
        #     under a partner field got tokenised TWICE, producing
        #     mapping['PARTNER_2'] = 'PARTNER_1' — a token pointing at a token.
        #     Re-hydration restored one level and the user saw the raw string.
        #   * `not substituted` -> skipped the fallback whenever ANYTHING was
        #     substituted, so a COMPOUND value leaked its remainder:
        #     "Al Barari Trading, Attn: Sarah Al Mansoori" tokenised the company
        #     and shipped the person's name in cleartext beside it. Odoo's
        #     display_name/contact_name conventions produce exactly that shape.
        #
        # Checking the RESULT closes both: fully-tokenised values are left alone
        # (no double wrap), and anything with residual text is still wrapped.
        #
        # What is WRAPPED is the substituted text; what is STORED is the
        # pre-tokenisation snapshot. That distinction is the round-4 fix. The
        # guard above only tolerates whitespace and , ; / - as residue, so a
        # compound like "Al Barari Trading, Attn: Sarah Al Mansoori" fails the
        # fullmatch on its colon and gets wrapped a second time. Storing the
        # substituted text then produced
        #     mapping['PARTNER_3'] = 'PARTNER_2, Attn: PARTNER_1'
        # and since every token is the same length, sorted(key=len) leaves them
        # in insertion order, so rehydrate()'s single forward pass expanded
        # PARTNER_1 and PARTNER_2 BEFORE PARTNER_3 introduced them — and the
        # user read "PARTNER_2, Attn: PARTNER_1" instead of the contact.
        #
        # Storing the snapshot makes the mapping value token-free by
        # construction, so one forward pass is provably enough. Widening the
        # residue character class instead would have been another instance-fix:
        # the next unlisted separator brings the nesting straight back.
        if self._is_partner_field(field_name) and text.strip() and \
                not _ONLY_TOKENS_RE.fullmatch(text.strip()):
            text = self._token('PARTNER', redacted_only, mapping, counters)
        return text

    def _is_document_reference(self, token, doc_prefixes):
        """True only for a token matching a real sequence's prefix AND padding.

        `doc_prefixes` is a tuple of (PREFIX, padding) read from the tenant's
        own ir.sequence rows. Both halves are required: prefix alone let
        "WH1234567" (a passport) pass as a warehouse reference, and padding
        alone would exempt any number of the right length.

        Padding 0 means the sequence declares none, so no digit count can be
        confirmed and nothing is exempted — fail closed, since this decides
        whether a §5 never-send identifier is redacted.
        """
        upper = token.upper()
        for prefix, padding in doc_prefixes:
            if not padding or not upper.startswith(prefix):
                continue
            if len(upper) - len(prefix) == padding and upper[len(prefix):].isdigit():
                return True
        return False

    def _looks_like_phone(self, candidate):
        """Confirm a candidate by DIGIT COUNT, not by shape.

        An ISO date carries 8 digits and matches the candidate pattern exactly;
        a phone number carries 9 or more. Without this, "2026-08-07" becomes
        PHONE_1 and every date-scoped question ("unpaid invoices last quarter")
        becomes unanswerable — while §5 explicitly lists dates as substance to
        send freely. Caught by test_the_substance_is_untouched.
        """
        digits = sum(char.isdigit() for char in candidate)
        return _MIN_PHONE_DIGITS <= digits <= _MAX_PHONE_DIGITS

    def _is_partner_field(self, field_name):
        """Field names whose VALUE is an identity.

        `company` is in this list deliberately. The gateway already knows which
        tenant is calling (it is the budget key), but the PROVIDER does not —
        and the workspace name is exactly the kind of identity §5 says the model
        does not need: "the model reasons over structure; it does not need real
        identities". Framing survives fine as "you are an analyst for COMPANY_1".

        It was missing at first, and the context builder's docstring meanwhile
        claimed the company name "is pseudonymised downstream like any other
        identity". It was not — the real workspace name went straight into the
        prompt. Caught by the review harness reporting 0 pseudonyms against a
        context that plainly contained one.
        """
        lowered = (field_name or '').lower()
        return lowered in ('partner_id', 'partner_name', 'customer', 'supplier',
                           'display_name', 'contact_name', 'company',
                           'company_name', 'company_id',
                           # Standard Odoo fields that carry an identity but
                           # match neither the partner_ prefix nor the _partner
                           # suffix. Added proactively: this is the same shape
                           # of gap `company` was, and rediscovering it the same
                           # way would be inexcusable twice.
                           'commercial_partner_id',
                           'invoice_partner_display_name', 'user_id',
                           'employee_id', 'contact_id') or \
            lowered.endswith('_partner') or lowered.endswith('_partner_id') or \
            lowered.startswith('partner_')

    def _token(self, kind, original, mapping, counters):
        """Stable within one request: the same value always gets the same token,
        so relationships survive ("PARTNER_1 appears on all three invoices")."""
        for token, value in mapping.items():
            if value == original and token.startswith(kind + '_'):
                return token
        counters[kind] += 1
        token = '%s_%d' % (kind, counters[kind])
        mapping[token] = original
        return token

    # -------------------------------------------------------------- rehydrate
    @api.model
    def _rehydrate(self, text, mapping):
        """Put the real identities back, inside this database.

        Longest token first: without it ``PARTNER_1`` would match inside
        ``PARTNER_10`` and corrupt the answer once a request has ten partners.
        """
        if not text or not mapping:
            return text
        for token in sorted(mapping, key=len, reverse=True):
            text = text.replace(token, mapping[token])
        return text
