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
in `data/auth_params.xml`). Reusing that method means:

* one set of tuning knobs, already documented, already per-tenant configurable,
  already covered by `ncollection_auth`'s tests;
* no second policy to keep in step with the first — Standing Rule 2.

But the STATE is deliberately ours. Core keeps its counter in
`registry._login_failures`, and calling `res.users._assert_can_auth` directly
would drop API failures into that same per-IP bucket as `/web/login`. One
tenant's integration retrying with a stale secret would then lock their staff
out of the web UI from the office IP. Same policy, separate bucket.

WHY IN MEMORY, AND NOT A DATABASE COUNTER
-----------------------------------------
#436 ranked a DB counter under `SELECT ... FOR UPDATE` first — the shape
`ncollection.api.client._nc_consume_rate_slot` uses. That shape is right for an
AUTHENTICATED client and wrong here, for two reasons:

1. it makes every hostile request take a row lock and a write, converting a
   CPU-exhaustion vector into CPU *plus* DB writes *plus* lock contention — the
   limiter becomes the amplifier;
2. counters for source addresses that were never seen before grow without
   bound, which is exactly the memory-growth objection #436 itself used to
   dismiss its own option 2.

An in-memory map costs nothing per attempt and cannot grow a table. Its price is
stated below rather than hidden.

WHAT THIS DOES NOT DO — read before trusting it
-----------------------------------------------
* **Per worker, not shared.** The map lives on the registry, so with
  `workers = 4` (`config/odoo.prod.conf`) the effective allowance is 4x the
  configured number. Core documents the same limitation for its own counter. A
  constant factor, not an evasion.
* **Per database.** `env.registry` is per-database, so an attacker rotating
  across tenant subdomains gets a fresh bucket each time. nginx's `apitoken`
  zone is keyed on source IP GLOBALLY and closes precisely that gap — which is
  why #436 ships two layers and not one, per ARCHITECTURE_SECURITY 6.
* **Needs `proxy_mode`.** `remote_addr` is only the real client when Odoo trusts
  `X-Forwarded-For`. That is set in `config/odoo.prod.conf` and NOT in the dev
  `config/odoo.conf`, so behind dev nginx every caller shares one bucket. In dev
  the nginx layer carries the weight; in production both are keyed correctly.
* **A success clears the counter** (core's semantics, kept). An attacker with no
  valid credential cannot reach that path, so it does not help them.
"""
import collections
import datetime
import logging

from odoo import api, models

_logger = logging.getLogger(__name__)

# Attribute name on the registry. Deliberately NOT core's `_login_failures` —
# see the module docstring: sharing that bucket would let a failing integration
# lock a tenant's humans out of /web/login.
REGISTRY_ATTR = '_nc_api_auth_failures'


class NcollectionApiThrottle(models.AbstractModel):
    _name = 'ncollection.api.throttle'
    _description = 'NCollection API Authentication Throttle'

    @api.model
    def _nc_failures(self):
        """The per-registry ``{source: (failures, last_failure_at)}`` map."""
        registry = self.env.registry
        failures = getattr(registry, REGISTRY_ATTR, None)
        if failures is None:
            failures = collections.defaultdict(
                lambda: (0, datetime.datetime.min))
            setattr(registry, REGISTRY_ATTR, failures)
        return failures

    @api.model
    def _nc_is_throttled(self, source):
        """True when ``source`` has failed too often, too recently.

        The decision itself is core's: `_on_login_cooldown` reads
        `base.login_cooldown_after` and `base.login_cooldown_duration`, so
        setting `..._after` to 0 disables this the same way it disables the
        login cooldown — one switch, one meaning, for both surfaces.
        """
        count, previous = self._nc_failures()[source]
        return self.env['res.users']._on_login_cooldown(count, previous)

    @api.model
    def _nc_record_failure(self, source):
        """Count one failed authentication against ``source``."""
        failures = self._nc_failures()
        count, __ = failures[source]
        failures[source] = (count + 1, datetime.datetime.now())
        _logger.info(
            "api: failed token request from %s (%d since last success)",
            source or '?', count + 1)

    @api.model
    def _nc_record_success(self, source):
        """Forget ``source``'s failures — it just proved it holds a secret."""
        self._nc_failures().pop(source, None)

    @api.model
    def _nc_reset(self):
        """Drop every counter. For tests ONLY.

        The map outlives a transaction because it hangs off the registry, so a
        test that drives failures would otherwise leak them into whatever runs
        next in the same registry and fail it somewhere unrelated.
        """
        self._nc_failures().clear()
