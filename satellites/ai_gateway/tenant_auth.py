# -*- coding: utf-8 -*-
"""Per-tenant request authentication for the AI gateway (#373).

THE BUG THIS CLOSES
-------------------
`gateway.py` used to do `tenant = str(payload["tenant"])` and believe it. Any
process able to reach `ai-gateway:8080` on the compose network could claim to be
any tenant — burning another workspace's AI allowance, misattributing its audit
metadata, and (before the same ticket's breaker change) tripping ONE shared
circuit breaker that served 503 to everybody.

The satellite's own proof script demonstrated it rather than caught it: it sent
self-declared tenants `probe-a`/`probe-b`/`probe-c` with a bare POST and had
them accepted and budgeted separately. The test proving "one tenant cannot
exhaust another's budget" simultaneously proved the field was unauthenticated
caller input.

THE SCHEME, WHICH IS NOT NEW
----------------------------
`ARCHITECTURE_SECURITY` §"Config sync channel" already specifies it for the
platform->tenant direction, and `AI_PLATFORM_DESIGN` §7 lists "per-tenant
authenticated channel — config-sync's HMAC keys and re-key path (#212/#283)" as
prior art Phase 5 **must reuse rather than reinvent**:

    per-tenant key = HMAC-SHA256(master, label || tenant)

Only the master exists as a stored secret. Per-tenant keys are DERIVED on both
sides, never stored anywhere — the property #212 was designed around. A leaked
DERIVED key authenticates as exactly one tenant, and rotating the master
rotates every tenant at once.

THE ACCEPTED TRADE-OFF, stated here rather than left in a ticket comment.
"Never platform-wide" is true of a leaked derived key, NOT of the master. The
master lives in the env of the Odoo container, which is ONE process serving
every tenant database — so a tenant admin able to run arbitrary Python in their
own database (a Server Action) can read it and derive any other tenant's key.

That was chosen deliberately over the alternative, which was storing a usable
key inside every tenant database — a DB dump would then yield a live
credential, the exact property #212 exists to avoid. Config-sync's master has
the identical exposure for the identical reason.

Bounded, and worth being precise about what it does NOT reach: the satellite
holds the LLM provider keys and no database credentials, and a forger receives
their own HTTP response. So the blast radius is budget theft, audit
misattribution and breaker griefing — never another tenant's prompts, responses
or data. Mitigations are master rotation and keeping tenant admins away from
Server Actions, not anything in this file.

WHY THIS FILE IS A COPY OF LOGIC THAT ALREADY EXISTS
----------------------------------------------------
`ncollection_saas/models/config_sync.py` has the same three lines. It cannot be
imported here: this process is plain Python with no Odoo, and the tenant addon
is forbidden from importing `satellites/`, which IS now asserted, by
`test_isolation.py::test_the_addon_never_imports_from_the_satellite`. Three
comments claimed that test existed before it did; a reviewer grepped and found
nothing. The test was written rather than the claim softened.

So the algorithm is deliberately duplicated, and the drift that invites is
guarded mechanically instead of by hope: `scripts/ci/invariants.py` asserts the
label and construction here match the tenant-side copy. Do not edit one without
the other; CI will fail if you try.
"""
import hashlib
import hmac
import time

# Domain separation. Distinct from config-sync's `nc-config-sync:` so a master
# reused across both channels (which it must not be, but might be) still cannot
# produce a key valid for the other one.
KDF_LABEL = b'nc-ai-gateway:'

# Signed request headers. The tenant itself travels in the SIGNED BODY, not a
# header — a header would be a second place to state the same thing, and the
# two could disagree. There is deliberately no X-NC-Tenant.
HEADER_TIMESTAMP = 'X-NC-Timestamp'
HEADER_SIGNATURE = 'X-NC-Signature'

# Replay window. The signature covers the timestamp, so a captured request
# cannot be replayed indefinitely — only within this skew. 300s is the same
# order as Stripe's webhook tolerance, which ARCHITECTURE_SECURITY already
# names as the pattern for signed inbound calls.
MAX_CLOCK_SKEW_SECONDS = 300


def derive_tenant_key(master: str, tenant: str) -> bytes:
    """Per-tenant key = HMAC-SHA256(master, KDF_LABEL || tenant).

    Mirrors ncollection_saas.config_sync.derive_tenant_key. Returns raw bytes
    here rather than base64 because this side only ever feeds it back into an
    HMAC; the config-sync copy encodes because it is presented as a bearer.
    """
    return hmac.new(master.encode(), KDF_LABEL + tenant.encode(),
                    hashlib.sha256).digest()


def sign(master: str, tenant: str, timestamp: str, body: bytes) -> str:
    """Signature over timestamp AND body, under the tenant's derived key.

    The timestamp is inside the signed material, not merely alongside it —
    otherwise an attacker replays a captured body with a fresh timestamp.
    """
    key = derive_tenant_key(master, tenant)
    material = timestamp.encode() + b'.' + body
    return hmac.new(key, material, hashlib.sha256).hexdigest()


def verify(master: str, tenant: str, timestamp: str, body: bytes,
           signature: str, now: float | None = None) -> bool:
    """True only for a signature this master could have produced for THIS tenant.

    Constant-time comparison: a byte-by-byte early return leaks the correct
    prefix to anyone able to time the endpoint, which is a practical attack
    against a network service, not a theoretical one.
    """
    if not master or not tenant or not signature or not timestamp:
        return False
    try:
        # int(), NOT float(). float('nan') parses fine, and EVERY comparison
        # against NaN is False — so `abs(now - nan) > SKEW` is False and a
        # timestamp of literally "nan" skips the freshness check entirely,
        # turning a 300-second window into a signature that never expires.
        # Found by review, confirmed live. Every legitimate sender emits
        # '%d' % int(time.time()), so nothing real is lost by refusing floats.
        sent_at = int(timestamp)
    except (TypeError, ValueError):
        return False
    current = time.time() if now is None else now
    if abs(current - sent_at) > MAX_CLOCK_SKEW_SECONDS:
        return False
    return hmac.compare_digest(sign(master, tenant, timestamp, body), signature)
