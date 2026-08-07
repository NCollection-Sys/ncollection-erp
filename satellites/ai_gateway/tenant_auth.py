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
key authenticates as exactly one tenant, never platform-wide, and rotating the
master rotates every tenant at once.

WHY THIS FILE IS A COPY OF LOGIC THAT ALREADY EXISTS
----------------------------------------------------
`ncollection_saas/models/config_sync.py` has the same three lines. It cannot be
imported here: this process is plain Python with no Odoo, and the tenant addon
is forbidden from importing `satellites/` (asserted by
`ncollection_ai/tests/test_isolation.py` — the HTTP boundary is the whole point
of the satellite topology).

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

# Signed request headers.
HEADER_TENANT = 'X-NC-Tenant'
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
        sent_at = float(timestamp)
    except (TypeError, ValueError):
        return False
    current = time.time() if now is None else now
    if abs(current - sent_at) > MAX_CLOCK_SKEW_SECONDS:
        return False
    return hmac.compare_digest(sign(master, tenant, timestamp, body), signature)
