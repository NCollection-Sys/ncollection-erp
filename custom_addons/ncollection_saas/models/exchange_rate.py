# -*- coding: utf-8 -*-
"""ECB exchange-rate source for tenant currency freshness (#308) — admin DB.

Implements the decisions in ``docs/markdown/DESIGN_EXCHANGE_RATE_FRESHNESS.md``
and the ``ARCHITECTURE_SECURITY`` §11 *Outbound rate fetch* control:

* the fetch runs **here**, on the admin database, once per cron tick. No tenant
  database ever makes an outbound call.
* one allowlisted host, ``www.ecb.europa.eu``.
* the read is **bounded and deadline-checked**, reusing the helpers
  ``ncollection.tenant`` already hardened for config-sync (#278/#283) rather
  than a bare ``urlopen`` with no size cap.
* a failed or partial fetch **leaves the previous rate intact** and logs loudly.
  It never writes a zero, partial or placeholder row — a confidently wrong rate
  is worse than an old one, because nothing downstream can tell them apart.

**What is stored is ECB's own number — ``USD per 1 EUR`` — not a derived AED
rate.** The tenant derives against its own base currency (see
``ncollection_core``'s ``_nc_apply_pushed_rate``), because

    EUR per CC = (EUR per USD) × (USD per CC)

holds for *any* company currency. So the platform never needs to know a
tenant's localization, nor duplicate the CBUAE peg constant that lives in
``ncollection_account_localization_uae``. One source of truth, no cross-module
constant.

ECB publishes **no AED and no GCC currency** (measured: the feed carries 29
currencies, none of them AED/KWD/SAR/QAR/OMR/BHD). That is *why* we never look
AED up in the feed — doing so is precisely the ``KeyError`` that makes OCA's
``currency_rate_update`` unusable for an AED-base company, and is why #236
chose BUILD over ADOPT.
"""
import logging
import xml.etree.ElementTree as ET
from xml.parsers import expat

import requests

from odoo import api, fields, models

from .config_sync import (
    _MAX_RESPONSE_BYTES,
    _READ_CHUNK,
    _RPC_DEADLINE,
    _RPC_TIMEOUT,
)

_logger = logging.getLogger(__name__)

# The single allowlisted outbound host (ARCHITECTURE_SECURITY §11). Hardcoded
# rather than a config parameter on purpose: a settable URL is a settable
# egress target, and the security control names exactly one host.
_ECB_URL = 'https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml'

# ECB's daily file is ~15 KB. _MAX_RESPONSE_BYTES (64 KiB) is already a
# generous ceiling, so we reuse it rather than inventing a second limit.

# A sanity band on the parsed rate. ECB's EUR/USD has never left roughly
# 0.8–1.6 in the euro's lifetime; anything outside 0.1–10.0 is a parse error or
# a hostile response wearing a number, and must not reach a tenant's books.
_RATE_MIN = 0.1
_RATE_MAX = 10.0

# The band above only catches garbage. A compromised feed returning 9.99 sits
# inside it and would misprice every EUR invoice ~6x. What actually fits a DAILY
# feed is a day-over-day move cap: EUR/USD has never moved anything like 25% in
# one publication, so a jump that large is a fault, not a market.
_MAX_DAILY_MOVE = 0.25


class NcExchangeRate(models.Model):
    """One row per ECB publication date, on the admin DB only.

    A model rather than an ``ir.config_parameter`` because §11 requires the
    staleness of this feed to be *observable*: a dated row makes "the last
    successful fetch was N days ago" a query, not a guess, and keeps an audit
    trail of what we pushed and when.
    """

    _name = 'ncollection.exchange.rate'
    _description = 'ECB exchange rate snapshot (platform)'
    _order = 'name desc'

    name = fields.Date(
        string='Rate Date', required=True, index=True,
        help="The publication date ECB itself stamped on the feed — not the "
             "date we fetched it. A feed that stops updating keeps returning "
             "the same date, which is how staleness becomes visible.")
    usd_per_eur = fields.Float(
        string='USD per EUR', required=True, digits=(16, 6),
        help="ECB's published USD rate against its EUR base, stored raw. "
             "Tenants derive their own base currency from it.")
    fetched_at = fields.Datetime(required=True, default=fields.Datetime.now)

    _sql_constraints = [
        ('name_uniq', 'UNIQUE(name)',
         'Only one ECB snapshot per publication date.'),
    ]

    # ---- fetch -----------------------------------------------------------

    @api.model
    def _fetch_ecb_document(self):
        """Return the ECB XML as text, or ``None`` if it could not be read.

        Bounded and deadline-checked exactly like the config-sync response
        read: ``read1`` on the underlying ``BufferedReader`` so the deadline is
        evaluated per ``recv`` rather than per 8 KiB chunk, with the fallback
        path when the internals move. Never raises — every failure returns
        ``None`` so the caller can keep the previous rate.
        """
        Tenant = self.env['ncollection.tenant']
        try:
            resp = requests.get(
                _ECB_URL, timeout=_RPC_TIMEOUT, stream=True,
                # The "single allowlisted host" control is worth nothing if the
                # client will chase a 301 off it. requests follows up to THIRTY
                # redirects by default, each with its own timeout budget, so a
                # hijacked DNS entry or a CDN migration would silently move the
                # fetch to a host nobody allowlisted. config_sync.py:928 already
                # pins this for the same reason.
                allow_redirects=False,
                # identity: we want bytes we can bound, not a stream the HTTP
                # layer may inflate on our behalf (#278).
                headers={'Accept-Encoding': 'identity'},
            )
        except requests.RequestException as exc:
            _logger.warning("ECB fetch failed (request): %s", exc)
            return None

        try:
            if resp.status_code != 200:
                _logger.warning("ECB fetch failed: HTTP %s", resp.status_code)
                return None
            recv = Tenant._recv_reader(resp)
            if recv is None:
                # config_sync.py falls back to resp.raw.read here, and documents
                # that it "LOOPS internally until it has n bytes" -- so the outer
                # deadline cannot fire mid-call, and decode_content=True undoes
                # the decompression-bomb protection read1 was picked for. That
                # fallback is tolerable against our own loopback Odoo; it is not
                # against a real CDN-fronted host where chunked is unremarkable.
                # A daily rate can afford to fail; an unbounded read cannot.
                _logger.warning(
                    "ECB response offers no bounded reader (chunked?) — "
                    "refusing rather than falling back to an unbounded read")
                return None
            deadline = Tenant._read_clock() + _RPC_DEADLINE
            buf = bytearray()
            while len(buf) <= _MAX_RESPONSE_BYTES:
                if Tenant._read_clock() > deadline:
                    _logger.warning(
                        "ECB fetch abandoned: exceeded %ss deadline after "
                        "%s byte(s)", _RPC_DEADLINE, len(buf))
                    return None
                want = min(_READ_CHUNK, _MAX_RESPONSE_BYTES + 1 - len(buf))
                try:
                    chunk = recv.read1(want)
                except Exception as exc:  # noqa: BLE001 - never break the cron
                    _logger.warning("ECB fetch failed (read): %s", exc)
                    return None
                if not chunk:
                    break
                buf.extend(chunk)
            else:
                _logger.warning(
                    "ECB response exceeded %s bytes — refusing it",
                    _MAX_RESPONSE_BYTES)
                return None
            return bytes(buf).decode('utf-8', errors='replace')
        finally:
            resp.close()

    @api.model
    def _assert_no_doctype(self, text):
        """Raise ``ET.ParseError`` if the document declares a DOCTYPE.

        A remote XML document needs no DTD to carry an exchange rate, and every
        entity-expansion and external-entity attack requires one. Rejecting the
        whole construct is therefore both complete and free — no new dependency,
        nothing to keep in sync with a threat list.

        Why this exists at all: the #308 security review tested the deployed
        runtime (Python 3.12, libexpat 2.6.1) and found billion-laughs and XXE
        already rejected — but ONLY because libexpat >= 2.4.0 rejects them, a
        property nothing in this repo selects, pins, tests or documents. A base
        image bump to an older/backported expat, or a later switch to lxml for
        speed, removes that protection silently and no test notices. This makes
        the control ours and explicit; ``test_ecb_rate.py`` fails if it goes.

        Implemented as a small expat pre-scan because Python 3.12's C
        ``ElementTree.XMLParser`` no longer exposes the underlying parser
        (verified: it has no ``.parser`` attribute), so the handler cannot be
        attached to the tree parse itself. The document is already capped at
        _MAX_RESPONSE_BYTES, so scanning it twice is bounded and cheap for a
        once-daily cron.
        """
        scanner = expat.ParserCreate()
        scanner.StartDoctypeDeclHandler = self._reject_doctype
        scanner.Parse(text, True)

    @api.model
    def _reject_doctype(self, *_args):
        raise ET.ParseError("DOCTYPE declaration is not allowed")

    @api.model
    def _parse_ecb_usd(self, text):
        """Return ``(date, usd_per_eur)`` from the ECB document, or ``None``.

        Rejects anything it cannot fully justify: missing USD cube, a rate that
        is not a finite number, or one outside the sanity band. A partial parse
        is a failed parse — half a rate is not a rate.
        """
        if not text:
            return None
        try:
            self._assert_no_doctype(text)
            root = ET.fromstring(text)
        except (ET.ParseError, expat.ExpatError) as exc:
            _logger.warning("ECB document did not parse: %s", exc)
            return None

        # Scope the USD lookup to the SAME time-bearing node that supplies the
        # date. Tracking both independently across the whole document let a
        # crafted feed pair today's date with a six-year-stale rate -- and both
        # values pass every check individually, so nothing downstream notices.
        # Reproduced before fixing: ('2026-08-04', '1.08').
        rate_date, usd = None, None
        for node in root.iter():
            if 'time' not in node.attrib:
                continue
            for child in node.iter():
                if (child.attrib.get('currency') == 'USD'
                        and 'rate' in child.attrib):
                    rate_date, usd = node.attrib['time'], child.attrib['rate']
                    break
            if usd is not None:
                break
        if not rate_date or usd is None:
            # AED is deliberately NOT looked for here: ECB does not publish it,
            # and needing it would be the OCA KeyError we chose BUILD to avoid.
            _logger.warning(
                "ECB document lacked a date or a USD rate — ignoring it")
            return None
        try:
            value = float(usd)
            parsed_date = fields.Date.to_date(rate_date)
        except (TypeError, ValueError):
            _logger.warning("ECB USD rate %r / date %r unusable", usd, rate_date)
            return None
        if parsed_date and parsed_date > fields.Date.today():
            _logger.warning(
                "ECB document is dated in the future (%s) — refusing it",
                parsed_date)
            return None
        if not parsed_date or not (_RATE_MIN < value < _RATE_MAX):
            _logger.warning(
                "ECB USD rate %r outside the sane band (%s, %s) — refusing it",
                value, _RATE_MIN, _RATE_MAX)
            return None
        return parsed_date, value

    @api.model
    def _cron_refresh_ecb_rate(self):
        """Fetch once and record the snapshot. Safe to run repeatedly.

        Returns the stored/existing record, or an empty recordset when nothing
        could be fetched — in which case the previous snapshot stands and the
        tenants keep the rate they already have. That is the §11 contract:
        **fail intact, never write a placeholder.**
        """
        parsed = self._parse_ecb_usd(self._fetch_ecb_document())
        if not parsed:
            _logger.warning(
                "ECB refresh produced nothing; keeping the previous rate "
                "(latest on file: %s)", self._latest().name or 'none')
            return self.browse()
        rate_date, usd_per_eur = parsed
        previous = self._latest()
        if previous and previous.usd_per_eur:
            move = abs(usd_per_eur - previous.usd_per_eur) / previous.usd_per_eur
            if move > _MAX_DAILY_MOVE:
                _logger.warning(
                    "ECB rate moved %.1f%% (%s -> %s) since %s — refusing it and "
                    "keeping the previous rate; a move that size is a fault, not "
                    "a market", move * 100, previous.usd_per_eur, usd_per_eur,
                    previous.name)
                return self.browse()
        existing = self.search([('name', '=', rate_date)], limit=1)
        if existing:
            # Same publication date: ECB has not moved. Not an error, and not
            # a reason to churn a row.
            return existing
        record = self.create({'name': rate_date, 'usd_per_eur': usd_per_eur})
        _logger.info("ECB rate recorded: %s = %s USD per EUR",
                     rate_date, usd_per_eur)
        return record

    @api.model
    def _latest(self):
        """The newest snapshot, or an empty recordset."""
        return self.search([], order='name desc', limit=1)


class TenantRateSync(models.Model):
    """Ride the EXISTING config-sync payload; never widen the sync account.

    `group_config_sync` can read/write `ncollection.workspace.config` and
    nothing else (ir.model.access.csv: 1,1,0,0) — ARCHITECTURE_SECURITY §11
    requires that least privilege. Granting it `res.currency.rate` would trade
    a security property for convenience, so instead the rate rides the payload
    as an apply-key and the TENANT does the privileged write, exactly as #101
    does for reseller branding.
    """

    _inherit = 'ncollection.tenant'

    config_sync_accepts_rate = fields.Boolean(
        string='Accepts Pushed Rates', readonly=True, copy=False,
        help="Set from the tenant's own sync RESPONSE. The platform sends the "
             "rate key only after a tenant has advertised it.")

    def _record_sync_capabilities(self, payload):
        """Learn what this tenant can accept, from its own response (#308).

        Backward compatibility is the whole point. `sync_from_platform` RAISES
        on a non-whitelisted key, so pushing a new field at a tenant running
        older `ncollection_core` would fail EVERY sync for it — and that
        channel carries licensing, so the blast radius is menu/ORM enforcement,
        not a currency. An old tenant simply omits the marker, stays False, and
        never receives the key. It starts receiving rates one cycle after it is
        upgraded, which is a delay, not a fault.

        Never raises: a capability probe that could break a successful push
        would be worse than the staleness it fixes.
        """
        self.ensure_one()
        accepts = bool(isinstance(payload, dict) and payload.get('accepts_rate'))
        if accepts != self.config_sync_accepts_rate:
            self.sudo().write({'config_sync_accepts_rate': accepts})

    def _config_sync_record(self, state, error=None):
        """Reset the rate capability on any non-ok push, so a stuck tenant heals.

        Without this the flag is a one-way ratchet: it is only ever learned from
        a SUCCESSFUL response, so a tenant that starts rejecting the rate key --
        a rolling deploy, a rollback, any regression in the tenant-side pop --
        fails `sync_from_platform` BEFORE `config.write(vals)` runs. That fails
        the WHOLE push, not just the rate, and a 422 is classified transient and
        retried forever with the same doomed payload. The channel carries
        licensing, so the cost of that ratchet is menu/ORM enforcement silently
        freezing, which is exactly what this feature promised it could not do.

        Dropping to False makes the next attempt byte-identical to the
        pre-#308 payload, which is known to work.
        """
        if state != 'ok':
            for tenant in self:
                if tenant.config_sync_accepts_rate:
                    tenant.sudo().write({'config_sync_accepts_rate': False})
        return super()._config_sync_record(state, error=error)

    def _config_sync_vals(self):
        """Add the ECB rate for tenants that have advertised support.

        Sends ECB's raw `USD per EUR`, not a derived rate: the tenant computes
        against its OWN base currency, so the platform needs no knowledge of a
        tenant's localization and the CBUAE peg constant stays in exactly one
        module.
        """
        vals = super()._config_sync_vals()
        if not self.config_sync_accepts_rate:
            return vals  # old tenant: byte-identical to today's payload
        latest = self.env['ncollection.exchange.rate'].sudo()._latest()
        if latest:
            vals['ecb_usd_per_eur'] = latest.usd_per_eur
            vals['ecb_rate_date'] = fields.Date.to_string(latest.name)
        return vals
