# -*- coding: utf-8 -*-
"""ECB rate source, admin side (#308).

The properties worth protecting are the ones whose failure is invisible:

1. **Fail intact.** A bad fetch must leave the previous rate standing. A zero,
   a placeholder or a partial row is worse than an old rate, because every
   consumer downstream treats it as authoritative (ARCHITECTURE_SECURITY §11).
2. **Bounded and deadline-checked.** An unbounded read of an external host is
   the gap #278/#283 closed for config-sync; re-opening it here would be a
   regression with a new file name.
3. **Backward compatible.** `sync_from_platform` RAISES on a non-whitelisted
   key, so sending the rate to a tenant running older code would fail EVERY
   sync for it -- and that channel carries licensing. A tenant that has not
   advertised support must receive a byte-identical payload.
4. **Never look AED up in the feed.** ECB does not publish it. Needing it is
   precisely the KeyError that made OCA's provider unusable here.
"""
import io

from odoo.tests import TransactionCase, tagged

_DOC = """<?xml version="1.0" encoding="UTF-8"?>
<gesmes:Envelope xmlns:gesmes="http://www.gesmes.org/xml/2002-08-01"
 xmlns="http://www.ecb.int/vocabulary/2002-08-01/eurofxref">
  <Cube><Cube time='2026-08-03'>
    <Cube currency='USD' rate='1.1535'/>
    <Cube currency='JPY' rate='170.1'/>
  </Cube></Cube>
</gesmes:Envelope>"""


@tagged("post_install", "-at_install")
class TestEcbParse(TransactionCase):
    """Parsing: anything not fully justified must be refused."""

    def setUp(self):
        super().setUp()
        self.Rate = self.env['ncollection.exchange.rate']

    def test_parses_date_and_usd(self):
        parsed = self.Rate._parse_ecb_usd(_DOC)
        self.assertIsNotNone(parsed)
        rate_date, usd = parsed
        self.assertEqual(str(rate_date), '2026-08-03')
        self.assertAlmostEqual(usd, 1.1535, places=6)

    def test_document_without_usd_is_refused(self):
        """ECB carries no AED; a doc we cannot use must not half-parse."""
        doc = _DOC.replace("currency='USD'", "currency='CHF'")
        self.assertIsNone(self.Rate._parse_ecb_usd(doc))

    def test_absurd_rate_is_refused(self):
        """A hostile response wearing a number must not reach a tenant's books."""
        self.assertIsNone(
            self.Rate._parse_ecb_usd(_DOC.replace("rate='1.1535'", "rate='9999'")))
        self.assertIsNone(
            self.Rate._parse_ecb_usd(_DOC.replace("rate='1.1535'", "rate='0'")))

    def test_non_numeric_rate_is_refused(self):
        self.assertIsNone(
            self.Rate._parse_ecb_usd(_DOC.replace("rate='1.1535'", "rate='n/a'")))

    def test_garbage_is_refused_not_raised(self):
        self.assertIsNone(self.Rate._parse_ecb_usd('<not xml'))
        self.assertIsNone(self.Rate._parse_ecb_usd(''))
        self.assertIsNone(self.Rate._parse_ecb_usd(None))


@tagged("post_install", "-at_install")
class TestEcbFetchBounds(TransactionCase):
    """The read must stay bounded and deadline-checked."""

    def setUp(self):
        super().setUp()
        self.Rate = self.env['ncollection.exchange.rate']

    def _fetch(self, reader, status=200):
        """Run the real read loop against a reader we control."""
        import requests

        class _Resp:
            status_code = status
            headers = {}

            def close(self):
                pass

        resp = _Resp()
        Tenant = self.env['ncollection.tenant']
        self.patch(type(Tenant), '_recv_reader', staticmethod(lambda _r: reader))
        self.patch(requests, 'get', lambda *a, **k: resp)
        return self.Rate._fetch_ecb_document()

    def test_oversized_response_is_refused(self):
        """A body over the cap is dropped, not buffered into memory."""
        huge = io.BufferedReader(io.BytesIO(b'x' * (5 * 1024 * 1024)))
        self.assertIsNone(self._fetch(huge))

    def test_a_normal_body_is_read_whole(self):
        """The bound must not truncate a legitimate 15 KB feed."""
        self.assertIsNotNone(
            self.Rate._parse_ecb_usd(
                self._fetch(io.BufferedReader(io.BytesIO(_DOC.encode())))))

    def test_deadline_abandons_a_trickle_AND_actually_read_first(self):
        """The deadline must fire, and the reader must genuinely have been used.

        Asserting only "returned None" cannot tell a working deadline from a
        reader that was never called -- the exact hole that made an earlier
        deadline test prove nothing (#285).
        """
        calls = []

        class _Trickle(io.BufferedReader):
            def read1(self, n=-1):
                calls.append(n)
                return b'<'

        clock = [0.0]
        self.patch(type(self.env['ncollection.tenant']), '_read_clock',
                   staticmethod(lambda: clock[0]))

        reader = _Trickle(io.BytesIO(b'x' * 1024))

        def _tick(n=-1):
            calls.append(n)
            clock[0] += 40.0  # two ticks blow any 60s deadline
            return b'<'

        reader.read1 = _tick
        self.assertIsNone(self._fetch(reader))
        self.assertGreaterEqual(len(calls), 1, "deadline fired without ever reading")

    def test_non_200_is_refused(self):
        reader = io.BufferedReader(io.BytesIO(_DOC.encode()))
        self.assertIsNone(self._fetch(reader, status=503))


@tagged("post_install", "-at_install")
class TestEcbFailIntact(TransactionCase):
    """§11: a failed fetch keeps the previous rate. No placeholder rows."""

    def setUp(self):
        super().setUp()
        self.Rate = self.env['ncollection.exchange.rate']

    def test_failed_fetch_keeps_the_previous_rate(self):
        old = self.Rate.create({'name': '2026-08-01', 'usd_per_eur': 1.10})
        self.patch(type(self.Rate), '_fetch_ecb_document', lambda s: None)
        self.assertFalse(self.Rate._cron_refresh_ecb_rate())
        self.assertEqual(self.Rate._latest(), old, "previous rate was disturbed")
        self.assertEqual(len(self.Rate.search([])), 1, "a placeholder row appeared")

    def test_unparseable_response_writes_nothing(self):
        self.patch(type(self.Rate), '_fetch_ecb_document', lambda s: '<not xml')
        self.assertFalse(self.Rate._cron_refresh_ecb_rate())
        self.assertFalse(self.Rate.search([]))

    def test_success_records_the_feeds_own_date(self):
        self.patch(type(self.Rate), '_fetch_ecb_document', lambda s: _DOC)
        rec = self.Rate._cron_refresh_ecb_rate()
        self.assertEqual(str(rec.name), '2026-08-03')
        self.assertAlmostEqual(rec.usd_per_eur, 1.1535, places=6)

    def test_same_publication_date_does_not_churn(self):
        self.patch(type(self.Rate), '_fetch_ecb_document', lambda s: _DOC)
        first = self.Rate._cron_refresh_ecb_rate()
        second = self.Rate._cron_refresh_ecb_rate()
        self.assertEqual(first, second)
        self.assertEqual(len(self.Rate.search([])), 1)


@tagged("post_install", "-at_install")
class TestRatePayloadCompatibility(TransactionCase):
    """A tenant that has not advertised support must see today's payload."""

    def setUp(self):
        super().setUp()
        self.env['ncollection.exchange.rate'].create(
            {'name': '2026-08-03', 'usd_per_eur': 1.1535})
        self.tenant = self.env['ncollection.tenant'].create({
            'company_name': 'Rate Co',
            'database_name': 'rateco',
            'database_status': 'ready',
        })

    def test_old_tenant_gets_an_unchanged_payload(self):
        """The whole point: no new key reaches a tenant running older code."""
        self.tenant.config_sync_accepts_rate = False
        vals = self.tenant._config_sync_vals()
        self.assertNotIn('ecb_usd_per_eur', vals)
        self.assertNotIn('ecb_rate_date', vals)

    def test_advertised_tenant_gets_the_rate(self):
        self.tenant.config_sync_accepts_rate = True
        vals = self.tenant._config_sync_vals()
        self.assertAlmostEqual(vals['ecb_usd_per_eur'], 1.1535, places=6)
        self.assertEqual(vals['ecb_rate_date'], '2026-08-03')

    def test_capability_is_learned_from_the_response(self):
        self.assertFalse(self.tenant.config_sync_accepts_rate)
        self.tenant._record_sync_capabilities({'ok': True, 'accepts_rate': True})
        self.assertTrue(self.tenant.config_sync_accepts_rate)

    def test_a_response_without_the_marker_keeps_it_off(self):
        """An old tenant's response omits it -- that must stay 'do not send'."""
        self.tenant.config_sync_accepts_rate = True
        self.tenant._record_sync_capabilities({'ok': True})
        self.assertFalse(self.tenant.config_sync_accepts_rate)

    def test_a_junk_response_never_raises(self):
        for payload in (None, [], 'nope', {'accepts_rate': 'yes'}):
            self.tenant._record_sync_capabilities(payload)
