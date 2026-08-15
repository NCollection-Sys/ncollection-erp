=====================
NCollection Public API
=====================

The public REST API foundation (P8-T01, issue #77): ``/api/v1``, OAuth2
client-credentials, scoped tokens, per-client rate limiting, and metadata-only
request logging.

Why native, not OCA
===================

``DELIVERABLE_1`` P8-T01 asks to *"evaluate OCA base_rest / the FastAPI addon
first — document the choice"*. Both were evaluated against the **19.0 branch**,
not against their reputations.

**base_rest — deprecated.** OCA directs new work to FastAPI. On 19.0 it sits at
version ``18.0.1.1.1`` with ``installable: False``, as do
``base_rest_auth_api_key`` and ``base_rest_pydantic``. An uninstallable module
produces a WARNING, then ``Modules loaded.``, then **exit 0** — it would have
installed nothing while CI stayed green.

**OCA fastapi — right direction, premature here.** It declares five external
Python dependencies (``fastapi``, ``python-multipart``, ``ujson``, ``a2wsgi``,
``parse-accept-language``) and **none are in the stock odoo:19 image** —
verified inside the running container, all five MISSING. Adopting it means a
custom Dockerfile, the exact cost this repo already refused once by pinning
``queue_job`` to an older commit to avoid a single pip dependency. It also needs
a second pin (``endpoint_route_handler``), is ``development_status`` Beta, and
its auth modules are not on 19.0 — ``fastapi_auth_api_key`` is open PR #619 and
there is no 19.0 migration PR for JWT at all, so the OAuth2 layer is hand-written
either way.

**Revisit trigger**, dated and falsifiable: when ``fastapi_auth_api_key`` (#619)
and a JWT equivalent are merged to 19.0 **and** the stock-image constraint is
deliberately lifted. ``repos.yml`` is unchanged.

Built on core, not beside it
============================

Odoo 19 ships ``res.users.apikeys``, which already does the part that is easy to
get wrong: the secret is **hashed**, the model is ``_auto = False`` so the key
column is not an ORM field at all, expiry and ``user.active`` are enforced in
SQL, and removal is identity-checked. Standing Rule 2 says extend before
replacing, so this module adds only what core cannot express.

Core's check is ``scope IS NULL OR scope = <asked>`` — **one** scope, exact, and
``NULL`` means everything. OAuth2 tokens carry a *set*. So:

- **core** proves the token is real, unexpired and belongs to a live user
- **this module** decides what it may do

A security property falls out of that, and a test asserts it: every token is
written with ``scope='ncollection_api'``. Odoo's RPC authentication asks for
``'rpc'``. Neither matches, so **an API token cannot be replayed as a general
RPC credential**. A ``NULL`` scope would have made every token a master key to
the whole ORM.

The defect that produced perfect responses
==========================================

Odoo 19 defaults ``auth='none'`` routes to a **read-only transaction**
(``http.py``: ``default_mode = routing.get('readonly', default_auth == 'none')``).
Every request-log INSERT failed with ``ReadOnlySqlTransaction``, was caught by
the logger's own guard, and vanished. The API answered correctly throughout —
but the log was empty, and **the rate limiter counts that log**, so it counted
zero and would never have limited anything. Found only because ``make test``
greps the log for tracebacks. Both routes now pass ``readonly=False``.

Permissions
===========

Reads run as the **client's user**, never ``sudo``. A client cannot read what
that Odoo user could not read — record rules and ACLs apply unchanged, so there
is no second permission model to keep in step. Proved by revoking the user's
access and asserting the same token stops returning rows.

What this does NOT cover
========================

* **The authorization-code flow.** It needs a consent screen, redirect-URI
  validation and PKCE, and exists to let a *third-party app act for a user* —
  marketplace territory (Phase 9), not the machine-to-machine integration this
  ticket's acceptance criterion describes. Filed separately rather than
  half-built.
* **One resource endpoint.** ``/api/v1/contacts`` exists to prove the chain
  end-to-end; the business endpoints are P8-T02 (#78).
* **Token refresh.** Client-credentials tokens are short-lived (1h) and the
  client re-authenticates; RFC 6749 §4.4.3 says a refresh token SHOULD NOT be
  issued for this grant.
* **The app-layer rate limit is per client, in-database.** It is the second
  layer; nginx's ``limit_req`` (P1-T03) is the first, and neither replaces the
  other.

Testing
=======

``make test m=ncollection_api`` — 15 tests, driven over real HTTP rather than by
calling controller methods, because a route that works as a Python function and
404s over the wire has proved nothing.
