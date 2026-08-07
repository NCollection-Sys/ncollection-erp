# -*- coding: utf-8 -*-
"""PII sanitisation tests (P5-T03 / #60).

These carry the security half of the ticket. AI_PLATFORM_DESIGN.md §5 says
sanitisation happens tenant-side because "once data reaches the gateway it has
already left the tenant boundary; scrubbing there would be theatre" — so nothing
downstream re-checks this, and nothing downstream *can*: the gateway holds no DB
credentials and cannot tell a real IBAN from a plausible string.

Each test names the §5 rule it enforces.
"""
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestPiiSanitiser(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.pii = cls.env['ncollection.ai.pii']

    # ------------------------------------------------------------- never send
    def test_secret_fields_are_redacted_not_pseudonymised(self):
        """§5 'never send'. Redaction, not a token: unlike a name there is no
        analytical value in knowing two rows share a secret."""
        clean, mapping = self.pii._sanitise({
            'api_key': 'sk-live-abcdef123456',
            'password': 'hunter2',
            'amount_total': 1500.0,
        })
        self.assertEqual(clean['api_key'], '[REDACTED]')
        self.assertEqual(clean['password'], '[REDACTED]')
        self.assertNotIn('sk-live-abcdef123456', str(clean))
        # And it is NOT recoverable from the mapping — nothing to re-hydrate.
        self.assertNotIn('sk-live-abcdef123456', str(mapping))

    def test_unenumerated_secret_names_are_caught_by_hint(self):
        """The field list cannot enumerate every name a module might invent.
        A false redaction costs a little context quality; a missed one leaks a
        credential."""
        clean, _ = self.pii._sanitise({'stripe_secret_key': 'sk_test_xyz',
                                      'webhook_token': 'whsec_abc'})
        self.assertEqual(clean['stripe_secret_key'], '[REDACTED]')
        self.assertEqual(clean['webhook_token'], '[REDACTED]')

    def test_an_iban_hidden_in_free_text_is_redacted(self):
        """No field-name check can catch an IBAN pasted into a payment memo,
        so it is matched by shape as well."""
        clean, _ = self.pii._sanitise(
            {'narration': 'Paid to AE070331234567890123456 on Tuesday'})
        self.assertNotIn('AE070331234567890123456', clean['narration'])
        self.assertIn('[REDACTED]', clean['narration'])

    def test_a_LOWERCASE_iban_is_redacted(self):
        """The test the last commit CLAIMED to add and did not.

        re.IGNORECASE was added to _IBAN_RE, and the commit message said "+
        test". test_pii.py was byte-for-byte unchanged. The fix worked but was
        unguarded — anyone could drop the flag and nothing would fail. That is
        the same class of problem as the original CRITICAL: an assertion of
        verification that had not happened.
        """
        for iban in ('ae070331234567890123456', 'Ae070331234567890123456',
                     'AE070331234567890123456'):
            clean, _ = self.pii._sanitise({'narration': 'paid to %s today' % iban})
            self.assertNotIn(iban, clean['narration'])

    def test_two_letter_and_lowercase_government_ids_are_redacted(self):
        """The ID pattern's negative lookahead excluded every 2-letter-prefixed
        ID (they necessarily start with 2 letters + 2 digits), and it lacked
        IGNORECASE. Only the single-letter uppercase case — the one the original
        test used — actually worked."""
        for ident in ('A1234567', 'AB1234567', 'a1234567', 'ab123456'):
            clean, _ = self.pii._sanitise({'note': 'passport %s here' % ident})
            self.assertNotIn(ident, clean['note'], '%r leaked' % ident)

    def test_punctuation_cannot_smuggle_an_id_past_redaction(self):
        """REGRESSION. The old _looks_like_document() guard passed anything with
        a '/' within one character, so "passport /A1234567" leaked — a bypass
        reachable by a date, a fraction, "and/or", or a file path. The guard
        protected nothing (genuine references cannot match the pattern) and was
        deleted."""
        for text in ('passport /A1234567 ok', 'passport A1234567/ ok',
                     'ref 1/A1234567/2 ok'):
            clean, _ = self.pii._sanitise({'note': text})
            self.assertNotIn('A1234567', clean['note'], '%r leaked' % text)

    def test_a_connection_string_password_is_redacted(self):
        """The most plausible credential paste in an ERP/integration context,
        and nothing caught it before."""
        for secret in ('postgres://admin:Sup3rSecret123@db.internal:5432/erp',
                       'mysql://root:hunter2@10.0.0.5/db'):
            clean, _ = self.pii._sanitise({'note': 'try %s' % secret})
            self.assertNotIn('Sup3rSecret123', clean['note'])
            self.assertNotIn('hunter2', clean['note'])

    def test_a_gcp_key_and_a_pem_block_are_redacted(self):
        clean, _ = self.pii._sanitise(
            {'note': 'key AIzaSyD-1234567890abcdefghijklmnopqrstu'})
        self.assertNotIn('AIzaSyD-1234567890abcdefghijklmnopqrstu', clean['note'])
        clean, _ = self.pii._sanitise({'note': '-----BEGIN PRIVATE KEY-----'})
        self.assertIn('[REDACTED]', clean['note'])

    def test_long_hex_is_redacted_even_though_it_might_be_a_checksum(self):
        """THE ACCEPTED TRADE-OFF, pinned so nobody 'fixes' it back.

        A SHA-256 and an HMAC key are both arbitrary hex; no shape tells them
        apart. Exempting hex to spare checksums handed a free pass to every hex
        secret — verified leaking a 40-char HMAC key, which is also SHA-1
        length, so even a digest-length-only exemption failed.

        §5 makes 'never send' unconditional for secrets, and this module's rule
        is that a missed secret costs more than a redacted checksum. So a user
        asking 'does this hash match' sees [REDACTED]. That is a real loss of
        context quality, chosen deliberately.
        """
        for value in ('a' * 64, 'a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6a7b8c9d0'):
            clean, _ = self.pii._sanitise({'note': 'value %s ok' % value})
            self.assertNotIn(value, clean['note'])

    def test_a_compound_name_rehydrates_without_leaking_raw_tokens(self):
        """HIGH, round 4 — the R3 fix was still broken on its OWN example.

        "Al Barari Trading, Attn: Sarah Al Mansoori" fails the already-tokenised
        check on its colon, so it gets wrapped a second time. Storing the
        SUBSTITUTED text produced a token whose value contained other tokens:

            mapping['PARTNER_3'] = 'PARTNER_2, Attn: PARTNER_1'

        Every token is the same length, so sorted(key=len) keeps insertion
        order, and rehydrate()'s single forward pass expanded 1 and 2 BEFORE 3
        introduced them. The user read "PARTNER_2, Attn: PARTNER_1" instead of
        the contact — the R3 fix traded a cleartext leak for a garbled answer in
        the exact scenario it was written for.

        Storing the pre-tokenisation snapshot makes one pass provably enough.
        """
        names = ['Al Barari Trading', 'Sarah Al Mansoori']
        clean, mapping = self.pii._sanitise(
            {'partner_id': 'Al Barari Trading, Attn: Sarah Al Mansoori'},
            known_identities=names)

        # Nothing real crosses the boundary.
        for name in names:
            self.assertNotIn(name, clean['partner_id'])
        # No stored value may contain a token, or one pass cannot be enough.
        for value in mapping.values():
            self.assertNotIn('PARTNER_', value)
        # And the user gets the real thing back, not internal token names.
        answer = self.pii._rehydrate(
            'The contact on file is %s.' % clean['partner_id'], mapping)
        self.assertNotIn('PARTNER_', answer)
        for name in names:
            self.assertIn(name, answer)

    def test_a_two_letter_sequence_prefix_cannot_shelter_a_passport(self):
        """MEDIUM->real leak, round 5, found with this repo's ACTUAL prefixes.

        The exemption used to be a bare startswith. `_document_prefixes()` on
        the dev database returns GROUP, INT, OP, PACK, PAY, SP, WH — and OP, SP
        and WH are two characters, exactly the width _ALNUM_ID_RE allows. So
        "passport WH1234567" was exempted as a warehouse reference and sent
        verbatim, defeating a §5 NEVER-SEND category with real production data.

        Genuine WH references look like WH/OUT/00012 and never match the
        pattern at all. Matching prefix AND digit count closes it.
        """
        prefixes = (('WH', 5), ('SP', 5), ('OP', 5), ('INV', 5))
        for ident in ('WH1234567', 'SP12345678', 'wh9876543'):
            clean, _ = self.pii._sanitise(
                {'note': 'verify passport %s against the visa file' % ident},
                doc_prefixes=prefixes)
            self.assertNotIn(ident, clean['note'], ident)

    def test_a_genuine_document_reference_still_survives(self):
        """The other side: padding-aware matching must not start redacting real
        references, or every question about a specific order breaks.

        THE DIGIT COUNTS MATTER AND THIS TEST WAS ONCE VACUOUS BECAUSE OF THEM.
        The first version used SO00042/WH00012 — five digits — and
        _ALNUM_ID_RE requires SIX to nine, so the pattern never matched and
        _is_document_reference was never called. A reviewer proved it by
        forcing that method to return False; the test still passed. These
        fixtures now sit inside the 6-9 range so the exemption path actually
        executes, and the padding values match.
        """
        clean, _ = self.pii._sanitise(
            {'note': 'check SO0000042 and WH0000012 today'},
            doc_prefixes=(('SO', 7), ('WH', 7)))
        self.assertIn('SO0000042', clean['note'])
        self.assertIn('WH0000012', clean['note'])

    def test_a_long_lot_number_still_survives(self):
        """Pure digits stay exempt: a 44-digit lot or batch number is substance,
        and only the 13-19 digit PAN range is treated as sensitive."""
        lot = '1' * 44
        clean, _ = self.pii._sanitise({'note': 'lot %s' % lot})
        self.assertIn(lot, clean['note'])

    def test_a_name_is_never_tokenised_twice(self):
        """REGRESSION, and it fired on EVERY ask() call.

        A value that is both a known identity AND under a partner field was
        tokenised twice — PARTNER_1, then that literal string tokenised again
        into PARTNER_2 mapping to 'PARTNER_1'. Re-hydration restored one level
        and the user saw the raw token in their answer. It also broke the
        join-preservation invariant the whole pseudonymisation design exists for.
        """
        clean, mapping = self.pii._sanitise(
            {'partner_id': 'Al Barari Trading'},
            known_identities=['Al Barari Trading'])
        self.assertEqual(clean['partner_id'], 'PARTNER_1')
        self.assertEqual(mapping, {'PARTNER_1': 'Al Barari Trading'})
        # No token may map to another token.
        for value in mapping.values():
            self.assertFalse(value.startswith('PARTNER_'),
                             'mapping points at a token, not a real value')
        self.assertEqual(self.pii._rehydrate('PARTNER_1 owes most', mapping),
                         'Al Barari Trading owes most')

    # ---------------------------------------------------------- pseudonymise
    def test_partner_names_become_stable_tokens(self):
        """§5: 'the model reasons over structure; it does not need real
        identities'."""
        clean, mapping = self.pii._sanitise({'partner_id': 'Al Barari Trading'})
        self.assertEqual(clean['partner_id'], 'PARTNER_1')
        self.assertEqual(mapping['PARTNER_1'], 'Al Barari Trading')

    def test_the_same_partner_keeps_the_same_token(self):
        """THE REASON FOR PSEUDONYMS OVER REDACTION. 'Which customer owes most
        across these invoices' is unanswerable if every name is [REDACTED]; a
        stable token preserves the join."""
        clean, _ = self.pii._sanitise({'rows': [
            {'partner_id': 'Acme LLC', 'amount': 100},
            {'partner_id': 'Beta FZE', 'amount': 200},
            {'partner_id': 'Acme LLC', 'amount': 300},
        ]})
        tokens = [row['partner_id'] for row in clean['rows']]
        self.assertEqual(tokens, ['PARTNER_1', 'PARTNER_2', 'PARTNER_1'])

    def test_emails_and_phones_are_pseudonymised(self):
        clean, mapping = self.pii._sanitise(
            {'note': 'contact ali@albarari.ae or +971 50 123 4567'})
        self.assertNotIn('ali@albarari.ae', clean['note'])
        self.assertIn('EMAIL_1', clean['note'])
        self.assertIn('PHONE_1', clean['note'])
        self.assertEqual(mapping['EMAIL_1'], 'ali@albarari.ae')

    # -------------------------------------------------------------- send freely
    def test_the_substance_is_untouched(self):
        """§5 'send freely: amounts, dates, account types, aggregates, document
        states — the actual substance'. Over-scrubbing would make the feature
        useless, which is its own kind of failure."""
        payload = {'amount_total': 15250.75, 'state': 'posted',
                   'invoice_date': '2026-08-07', 'currency': 'AED',
                   'line_count': 12, 'is_overdue': True}
        clean, mapping = self.pii._sanitise(payload)
        self.assertEqual(clean, payload)
        self.assertEqual(mapping, {})

    def test_invoice_numbers_are_not_mistaken_for_phones(self):
        """A phone regex that is too greedy starts eating document references,
        which §5 says to send freely."""
        clean, _ = self.pii._sanitise({'name': 'INV/2026/00042'})
        self.assertEqual(clean['name'], 'INV/2026/00042')

    def test_iso_dates_are_not_mistaken_for_phones(self):
        """REGRESSION. The first phone pattern matched '2026-08-07' exactly —
        digit, eight separator-ish characters, digit — so every date became
        PHONE_n and any date-scoped question ('unpaid invoices last quarter')
        was unanswerable. Dates are named substance in §5.

        Digit count is what separates them: an ISO date has 8, a phone 9+.
        """
        for date_text in ('2026-08-07', 'due 2026-12-31', '01/02/2026'):
            clean, mapping = self.pii._sanitise({'note': date_text})
            self.assertEqual(clean['note'], date_text,
                             '%r was altered; dates must survive' % date_text)
            self.assertEqual(mapping, {})

    def test_a_real_phone_is_still_pseudonymised(self):
        """The other half of the same boundary — the fix must not disarm it."""
        for phone in ('+971 50 123 4567', '0501234567', '+44 20 7946 0958'):
            clean, mapping = self.pii._sanitise({'note': phone})
            self.assertNotEqual(clean['note'], phone,
                                '%r must not survive as-is' % phone)
            self.assertTrue(mapping)

    # ---------------------------------------------------------------- rehydrate
    def test_rehydration_restores_identities_inside_the_database(self):
        clean, mapping = self.pii._sanitise({'partner_id': 'Al Barari Trading'})
        answer = 'The largest balance belongs to %s.' % clean['partner_id']
        self.assertEqual(self.pii._rehydrate(answer, mapping),
                         'The largest balance belongs to Al Barari Trading.')

    def test_rehydration_survives_more_than_nine_partners(self):
        """PARTNER_1 is a prefix of PARTNER_10. Replacing shortest-first would
        corrupt every answer once a request has ten partners — a bug that only
        appears on real-sized data."""
        mapping = {'PARTNER_%d' % i: 'Company %d' % i for i in range(1, 13)}
        text = 'PARTNER_1 and PARTNER_10 and PARTNER_12'
        self.assertEqual(self.pii._rehydrate(text, mapping),
                         'Company 1 and Company 10 and Company 12')

    def test_rehydrate_is_a_noop_without_a_mapping(self):
        self.assertEqual(self.pii._rehydrate('nothing to do', {}),
                         'nothing to do')

    # --------------------------------------------------------------- structure
    def test_nested_structures_are_walked_entirely(self):
        """A sanitiser that only handles the top level is worse than none: it
        reads as protection while leaking everything one level down."""
        clean, _ = self.pii._sanitise({
            'summary': {'top_customers': [
                {'partner_id': 'Deep Co', 'api_key': 'sk-nested'},
            ]},
        })
        row = clean['summary']['top_customers'][0]
        self.assertEqual(row['partner_id'], 'PARTNER_1')
        self.assertEqual(row['api_key'], '[REDACTED]')

    def test_the_mapping_is_returned_separately_not_embedded(self):
        """The mapping is the one thing that must never cross the boundary.
        Returning it apart from the payload makes sending it an explicit act
        rather than an accident."""
        clean, mapping = self.pii._sanitise({'partner_id': 'Secret Corp'})
        self.assertNotIn('Secret Corp', str(clean))
        self.assertIn('Secret Corp', str(mapping))
