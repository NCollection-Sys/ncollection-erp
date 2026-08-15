# -*- coding: utf-8 -*-
"""#436: throttle FAILED authentication, before any credential is resolved.

THE HOLE THIS CLOSES. `/api/v1/oauth/token` applies the per-client rate limit
only AFTER `_nc_authenticate` succeeds, so an unknown `client_id` or a wrong
`client_secret` never reached a limiter at all. Every such attempt still costs a
**PBKDF2-SHA512** — deliberately, because the dummy hash on the unknown-client
path is what makes "unknown client" and "wrong secret" indistinguishable by
timing. That defence is correct and stays; it is the *unbounded repetition* of
it that is the problem.

This is a CPU-exhaustion concern, NOT credential guessing: the secret is 160
bits of `os.urandom`, unguessable at any rate.

WHY POLICY IS BORROWED AND STATE IS NOT
---------------------------------------
Odoo core already answers "how many failures before we stop listening" in
`res.users._on_login_cooldown`, and `ncollection_auth` already ARMS it for this
platform (`base.login_cooldown_after = 5`, `base.login_cooldown_duration = 300`,
in `data/auth_params.xml`). Reusing that method means one set of tuning knobs,
already documented, already per-tenant configurable, already tested — Rule 2.

But the STATE is deliberately ours. Core keeps its counter in
`registry._login_failures`, and calling `res.users._assert_can_auth` directly
would drop API failures into that same per-IP bucket as `/web/login`. One
tenant's integration retrying with a stale secret would then lock their staff
out of the web UI from the office IP. Same policy, separate bucket.

WHERE THIS DIVERGES FROM CORE, AND WHY IT HAS TO
------------------------------------------------
**Core clears the whole bucket on any success. This does not.**

That is a deliberate divergence, found in security review. Core's `/web/login`
and this endpoint have different threat models: at `/web/login` the party
holding a valid credential is the account's own user, but here an attacker can
legitimately hold ONE live credential (their own low-tier client, a leaked but
unrevoked secret, a compromised integration) while grinding guesses at OTHER
clients. A bucket-wide clear on success let them interleave

    fail x (threshold - 1)  ->  succeed once  ->  counter wiped  ->  repeat

and sustain failed attempts forever without ever tripping the cooldown. The app
layer would then have bounded nothing, and only nginx's independent `apitoken`
zone would have been holding the line — by luck of defense-in-depth rather than
by this code doing its job.

So failures decay by TIME instead: an entry older than
`base.login_cooldown_duration` is forgotten on sight. A legitimate integration
that fixes a wrong secret waits out one cooldown window rather than being
cleared instantly, which is the price of removing the interleave.

WHY IN MEMORY, AND HOW IT IS BOUNDED
------------------------------------
#436 ranked a DB counter under `SELECT ... FOR UPDATE` first — the shape
`ncollection.api.client._nc_consume_rate_slot` uses. That is right for an
AUTHENTICATED client and wrong here: it makes every hostile request take a row
lock and a write, converting a CPU-exhaustion vector into CPU *plus* DB writes
*plus* lock contention.

The first version of this module then made the mirror-image mistake, and
security review caught it: it moved the unbounded growth from Postgres rows to
the Python heap. Reading an unknown source through a `defaultdict` INSERTED an
entry, nothing ever evicted, and a distributed caller sending one request per
address could grow the map indefinitely while staying under both the per-IP
cooldown (one attempt each) and nginx's per-IP zone. On a worker with a
`limit_memory_hard`, that is an availability bug, not a tidiness one.

Bounded three ways now, the way nginx bounds its own `limit_req_zone ... :10m`:

* reads NEVER insert (`.get`, not `[]`);
* an entry older than the cooldown window is dropped when seen and swept when
  any failure is recorded;
* a hard `MAX_TRACKED_SOURCES` cap evicts oldest-first, so the map has a
  ceiling that does not depend on attacker behaviour.

WHAT THIS STILL DOES NOT DO — read before trusting it
-----------------------------------------------------
* **Per worker, not shared.** The map lives on the registry, so with
  `workers = 4` (`config/odoo.prod.conf`) the effective allowance is 4x the
  configured number. Core documents the same limitation for its own counter. A
  constant factor, not an evasion.
* **Per database.** `env.registry` is per-database (`Registry.registries` is an
  LRU keyed on db name), so an attacker rotating across tenant subdomains gets a
  fresh bucket each time. nginx's `apitoken` zone is keyed on source IP
  GLOBALLY and closes precisely that — which is why #436 ships two layers.
* **Counters reset on a registry rebuild** (module install/upgrade), because the
  attribute hangs off a Registry object that `Registry.new()` replaces wholesale.
  `clear_cache()` does NOT reset them — it only clears `ormcache` entries.
* **Check-then-act is not atomic.** A concurrent burst from one source can all
  pass the check before any records a failure. Core documents the same for
  `_login_failures`; nginx's atomic leaky bucket in front is what bounds it.
* **Needs `proxy_mode`.** `remote_addr` is only the real client when Odoo trusts
  `X-Forwarded-For`. That is set in `config/odoo.prod.conf` and NOT in the dev
  `config/odoo.conf`, so behind dev nginx every caller shares one bucket.
"""
import datetime
import logging

from odoo import api, models

_logger = logging.getLogger(__name__)

# Attribute name on the registry. Deliberately NOT core's `_login_failures` —
# see the module docstring: sharing that bucket would let a failing integration
# lock a tenant's humans out of /web/login.
REGISTRY_ATTR = '_nc_api_auth_failures'

# Ceiling on tracked sources. Reached only by a caller rotating source addresses
# faster than entries expire; past it, the oldest are evicted. nginx's own
# `limit_req_zone ... :10m` is bounded the same way and for the same reason —
# an unbounded structure fed by unauthenticated input is a DoS in itself.
MAX_TRACKED_SOURCES = 4096

# Used only if `base.login_cooldown_duration` is unreadable. Core's own default
# is 60; this matches what `ncollection_auth` actually sets.
DEFAULT_COOLDOWN_SECONDS = 300


class NcollectionApiThrottle(models.AbstractModel):
    _name = 'ncollection.api.throttle'
    _description = 'NCollection API Authentication Throttle'

    # ---- state -----------------------------------------------------------

    @api.model
    def _nc_failures(self):
        """The per-registry ``{source: (failures, last_failure_at)}`` map."""
        registry = self.env.registry
        failures = getattr(registry, REGISTRY_ATTR, None)
        if failures is None:
            failures = {}
            setattr(registry, REGISTRY_ATTR, failures)
        return failures

    @api.model
    def _nc_window_floor(self):
        """Failures older than this can no longer throttle anyone."""
        param = self.env['ir.config_parameter'].sudo().get_param(
            'base.login_cooldown_duration', DEFAULT_COOLDOWN_SECONDS)
        try:
            seconds = int(param)
        except (TypeError, ValueError):
            _logger.warning(
                "api: base.login_cooldown_duration is not an integer (%r); "
                "falling back to %ss", param, DEFAULT_COOLDOWN_SECONDS)
            seconds = DEFAULT_COOLDOWN_SECONDS
        # `datetime.datetime.now()` and NOT `fields.Datetime.now()`, which would
        # be UTC-naive. These timestamps are handed to core's
        # `_on_login_cooldown`, which compares them against
        # `datetime.datetime.now()` — system-local naive (`res_users.py`). The
        # two clocks MUST match: using the ORM helper here would silently break
        # the window comparison on any host whose timezone is not UTC. Reviewed
        # and deliberately left as-is.
        return datetime.datetime.now() - datetime.timedelta(seconds=seconds)

    # ---- the decision ----------------------------------------------------

    @api.model
    def _nc_is_throttled(self, source):
        """True when ``source`` has failed too often, too recently.

        The decision itself is core's: `_on_login_cooldown` reads
        `base.login_cooldown_after` and `base.login_cooldown_duration`, so
        setting `..._after` to 0 disables this the same way it disables the
        login cooldown — one switch, one meaning, for both surfaces.

        Reading NEVER inserts. The first version indexed a `defaultdict`, which
        creates an entry on miss, so merely *asking* about an unknown source
        grew the map — one permanent entry per address, for a caller that need
        never authenticate. Security review caught it.
        """
        failures = self._nc_failures()
        entry = failures.get(source)
        if entry is None:
            return False
        count, previous = entry
        if previous < self._nc_window_floor():
            # Stale: it can no longer throttle, and keeping it would let a
            # count from an hour ago make this source one failure away from a
            # lockout forever. Forgetting it here is what gives failures a
            # time decay now that success no longer clears them.
            failures.pop(source, None)
            return False
        return self.env['res.users']._on_login_cooldown(count, previous)

    # ---- recording -------------------------------------------------------

    @api.model
    def _nc_record_failure(self, source):
        """Count one failed authentication against ``source``."""
        if self._nc_disabled():
            # Mechanism switched off — do not accumulate state nobody reads.
            return
        failures = self._nc_failures()
        self._nc_evict(failures)
        count, __ = failures.get(source, (0, None))
        failures[source] = (count + 1, datetime.datetime.now())
        _logger.info(
            "api: failed token request from %s (%d since it last went quiet)",
            source or '?', count + 1)

    @api.model
    def _nc_disabled(self):
        """True when `base.login_cooldown_after` is 0 — core's own off switch."""
        param = self.env['ir.config_parameter'].sudo().get_param(
            'base.login_cooldown_after', 5)
        try:
            return int(param) == 0
        except (TypeError, ValueError):
            return False

    @api.model
    def _nc_evict(self, failures):
        """Drop what can no longer throttle, then enforce the hard ceiling.

        Runs on the failure path only. That is the path an attacker drives, so
        the sweep happens exactly when growth is happening — and it accompanies
        a request that is about to pay for a PBKDF2 anyway, which is orders of
        magnitude more expensive than this scan over a capped map.
        """
        floor = self._nc_window_floor()
        for source in [s for s, (__, prev) in failures.items() if prev < floor]:
            failures.pop(source, None)
        excess = len(failures) - MAX_TRACKED_SOURCES
        if excess > 0:
            oldest = sorted(failures.items(), key=lambda kv: kv[1][1])[:excess]
            for source, __ in oldest:
                failures.pop(source, None)
            _logger.warning(
                "api: auth-throttle map hit its %d-source ceiling; evicted %d "
                "oldest. A caller is rotating source addresses.",
                MAX_TRACKED_SOURCES, excess)

    # ---- tests -----------------------------------------------------------

    @api.model
    def _nc_reset(self):
        """Drop every counter. For tests ONLY.

        The map outlives a transaction because it hangs off the registry, so a
        test that drives failures would otherwise leak them into whatever runs
        next in the same registry and fail it somewhere unrelated.

        Underscore-prefixed, so Odoo's `call_kw` refuses it over RPC — the same
        property `ncollection_ai` relies on for its gateway internals. Verified
        in security review rather than assumed.
        """
        self._nc_failures().clear()
