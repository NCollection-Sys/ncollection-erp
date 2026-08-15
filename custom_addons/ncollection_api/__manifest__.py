# pylint: disable=manifest-required-author
# (C8101 wants the OCA as author; this is a proprietary NCollection module.)
{
    'name': 'NCollection Public API',
    'version': '19.0.1.0.0',
    'category': 'Technical',
    'summary': 'Public REST API foundation: OAuth2 client-credentials, scoped '
               'tokens, versioned /api/v1 routing, rate limiting (P8-T01)',
    'author': 'NCollection',
    'website': 'https://ncollection.com',
    'license': 'LGPL-3',
    # BUILD-CUSTOM, and the ticket asked for the evaluation in writing.
    # DELIVERABLE_1 P8-T01 says "evaluate OCA base_rest / the FastAPI addon
    # first — document the choice". Both were evaluated against the 19.0
    # branch, not against their reputations:
    #
    #   base_rest    DEPRECATED upstream. OCA directs new work to FastAPI. On
    #                the 19.0 branch it sits at version "18.0.1.1.1" with
    #                `installable: False`, as do base_rest_auth_api_key and
    #                base_rest_pydantic. An uninstallable module is a WARNING,
    #                then "Modules loaded.", then exit 0 — it would have
    #                installed nothing while CI stayed green.
    #
    #   OCA fastapi  Genuinely the community's direction, and genuinely
    #                premature here. It declares five external Python
    #                dependencies — fastapi, python-multipart, ujson, a2wsgi,
    #                parse-accept-language — and NONE are in the stock odoo:19
    #                image (verified inside the running container, all five
    #                MISSING). Adopting it means a custom Dockerfile, which is
    #                the exact cost this repo already refused once: queue_job
    #                is pinned to an OLDER commit purely to avoid one pip
    #                dependency ("Pinning here keeps the stock image").
    #                It also needs a second pin (endpoint_route_handler), is
    #                development_status Beta, and its auth modules are not on
    #                19.0 — fastapi_auth_api_key is open PR #619 (2026-06-17)
    #                and there is no 19.0 migration PR for JWT at all. So the
    #                OAuth2 layer this ticket needs would be hand-written
    #                either way.
    #
    # REVISIT TRIGGER, dated and falsifiable rather than "we chose native
    # once": when fastapi_auth_api_key (#619) and a JWT equivalent are merged
    # to 19.0 AND the stock-image constraint is deliberately lifted. Recorded
    # in docs/markdown/OCA_DEPENDENCIES.md. repos.yml is UNCHANGED.
    #
    # EXTEND BEFORE REPLACING (Rule 2). Odoo 19 core already ships
    # `res.users.apikeys` — hashed at rest, secret not exposed as an ORM field
    # (`_auto = False`), short plaintext index for lookup, expiry with a
    # per-group ceiling, and an identity check on removal. This module builds
    # OAuth2 semantics ON TOP of that rather than beside it; see
    # models/api_token.py.
    'depends': ['base', 'ncollection_core'],
    'data': [
        'security/ir.model.access.csv',
        'security/api_security.xml',
        'views/api_client_views.xml',
    ],
}
