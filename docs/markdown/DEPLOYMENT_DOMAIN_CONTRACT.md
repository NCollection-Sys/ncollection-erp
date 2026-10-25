# Production domain contract: `balancecloud.ae` and `*.balancecloud.ae`

What the application, the edge and the outside world must each provide for the platform to be served on one base
domain with HTTPS. Written for `balancecloud.ae`; nothing in the code or the edge configuration knows that name. The
same contract holds for any other domain: only `DOMAIN` changes.

Nothing in this repository changes DNS, certificates or a server. This page says what has to be done outside it, and
`make edge-verify` and the tests named below prove the repository side.

## 1. The one setting

| Where | Name | Meaning |
|---|---|---|
| Deployment environment (`.env`) | `DOMAIN` | The base domain the stack is served on: `balancecloud.ae`. Required in production: the prod compose file refuses to start nginx without it. |
| Deployment environment | `NC_BASE_DOMAIN` | The application's base domain. Optional in production: it defaults to `DOMAIN` (`docker-compose.prod.yml`). Development defaults to `ncollection.localhost`. |
| SaaS Settings > Base Domain (`ncollection_saas.base_domain`) | | An operator's explicit override. Wins over the environment. Leave empty in production. |

The application has no third place and no built-in default (`docs/markdown/TENANT_URLS.md`). Changing the domain is
changing `DOMAIN`, issuing the certificate for it and recreating nginx. No source file is edited.

## 2. Shape of the traffic: one wildcard route, no per-tenant DNS

```
https://balancecloud.ae            the platform host (see 6)
https://<tenant>.balancecloud.ae   one tenant = one database named <tenant>
```

A tenant is **a database and a row**. Creating one adds no DNS record, no certificate and no nginx block: nginx has one
server block whose `server_name` is `.balancecloud.ae` (the apex and every subdomain), and Odoo selects the database
from the first host label (`db_filter = ^%d$`, `config/odoo.prod.conf`). The tenant's address is built in one place,
`ncollection.tenant._nc_public_origin()`, as `https://<subdomain>.<base domain>`, and `TENANT_URLS.md` lists every
consumer (tenant list, Company Entry, signup login link, provisioning portal URL, the tenant's own `web.base.url`,
domain records, the service-channel `Host`).

## 3. DNS (to be created by whoever owns the zone; not done here)

| Record | Name | Value |
|---|---|---|
| `A` (and `AAAA` if the edge has IPv6) | `balancecloud.ae` | the edge's public IP |
| `A` / `AAAA` | `*.balancecloud.ae` | the same IP (a wildcard `CNAME` to a host name that resolves there works too) |
| `CAA` (recommended) | `balancecloud.ae` | `0 issue "letsencrypt.org"` and `0 issuewild "letsencrypt.org"` |
| `TXT` (temporary) | `_acme-challenge.balancecloud.ae` | written by Certbot during issuance and renewal (see 4) |

One wildcard record is the whole tenant DNS story. There is no DNS step in provisioning.

## 4. TLS

* **One certificate with two names: `balancecloud.ae` and `*.balancecloud.ae`.** A wildcard certificate does not cover
  the apex, so the apex must be named explicitly. The commands in `nginx/README.md` already request both
  (`-d "$DOMAIN" -d "*.$DOMAIN"`).
* **DNS-01 only.** A wildcard cannot be validated over HTTP-01. Issuance and unattended renewal need the DNS provider's
  Certbot plugin and a scoped API token kept outside git (`nginx/README.md`, options A and B).
* The files must exist **before nginx starts**: `/etc/letsencrypt/live/balancecloud.ae/fullchain.pem` and `privkey.pem`
  (the lineage is named after the first `-d`, the apex). The template derives that path from `DOMAIN`.
* TLS 1.2 and 1.3, strong GCM ciphers and HSTS (`max-age=31536000; includeSubDomains`) come from the same server block.
  `includeSubDomains` means every subdomain of the domain must be HTTPS-capable before HSTS is trusted: do not point
  an HTTP-only service at a name under it.

## 5. Reverse proxy, forwarded headers, trusted proxy

nginx (`nginx/conf.d/ncollection.prod.conf`, mounted as an envsubst template and rendered from `DOMAIN` only) hands
Odoo, for every proxied request (`nginx/snippets/proxy.conf`):

| Header | Value |
|---|---|
| `Host` | `$host`: the tenant host. This is the **routing identity**: its first label picks the database |
| `X-Forwarded-Host` | `$host` |
| `X-Forwarded-Proto` | `$scheme`: `https` at the edge, so Odoo builds https URLs and `Secure` cookies |
| `X-Forwarded-For`, `X-Real-IP` | the client address |

Odoo runs with `proxy_mode = True` (`config/odoo.prod.conf`), so it trusts those headers. **That is only safe when
nginx is the sole way in.** The production compose file therefore unpublishes Odoo's own port
(`docker-compose.prod.yml`: `ports: !reset []`, guarded by `scripts/ci/invariants.py`
`rule_prod_odoo_is_not_published`); nginx is the only service that publishes ports (80, 443). Any other deployment
that keeps 8069 reachable must firewall it to the proxy, or a client could choose a tenant by `Host`, claim https and
skip the edge's rate limits. Postgres is not published either.

Realtime: `/websocket` and `/longpolling` go to the gevent bus on 8072 (not 8069) in production.

## 6. The platform host, and two things that depend on it

The platform's own database is selected like any other: by the first label of the host it is served on.

* On the **apex** `balancecloud.ae` the first label is `balancecloud`, so the platform database must be **named
  `balancecloud`**. That label is therefore reserved: a tenant can never take it, whatever the base domain is
  (`_nc_subdomain_availability`, test `test_a_tenant_can_never_take_the_label_of_the_platforms_own_apex_database`).
* Alternatively the platform is served on `admin.balancecloud.ae` with a database named `admin` (already a reserved
  label). **Which of the two is a deployment decision that is still open.** Either works with the code; the other
  settings below follow it.

Required on the platform database, because the platform builds links from it, not from the base domain:

* `web.base.url` = the platform's public origin (`https://balancecloud.ae` or `https://admin.balancecloud.ae`) and
  `web.base.url.freeze = True`. The signup verification e-mail links to `<web.base.url>/nc/checkout/verify/<token>`;
  without the freeze Odoo rewrites it from whichever host an administrator last used.
* Company Entry's "Back to Platform" returns to the same `web.base.url`.

A tenant's own `web.base.url` is written once, at provisioning, from the canonical builder; its password-reset and
verification links use it.

## 7. Cookies, sessions, cross-origin

* The application sets no cookie `Domain` attribute, so a session cookie is host-only: a tenant's cookie is never sent
  to another tenant's subdomain, and a session is bound to its database (`e2e/tests/tenant-hosts.spec.ts`,
  `verify_platform_entry.sh`: "A's session is NOT valid on B").
* The application defines no CORS headers: it is same-origin. A browser integration on another origin is not
  supported; the public REST API (`/api/v1`) is called server to server.

## 8. What stored addresses do when the domain changes

Addresses are computed, not stored, except two snapshots taken at provisioning: the tenant's `portal_url` and the
tenant database's own `web.base.url`. A tenant provisioned under one domain keeps them if the domain is later changed.
The only automated repair is for the retired development template (`http://<db>.localhost:8069`). For a new
production domain this is moot (tenants are created under it); re-pointing tenants that already exist under another
domain is a deliberate, per-tenant operation and is **not** automated.

## 9. How the repository proves its half

| Claim | Proof |
|---|---|
| The prod edge follows `DOMAIN` (server_name = apex + every subdomain, certificate path, forwarded Host and scheme, `nginx -t`) for `balancecloud.ae` and a second domain | `make edge-verify` (`scripts/deploy/verify_edge_domain.sh`) |
| No production-domain literal in code or infrastructure; no development domain in production-facing files | `scripts/ci/invariants.py` `rule_no_hardcoded_tenant_domain` (R-038) |
| Odoo's port is not published in production | `rule_prod_odoo_is_not_published` |
| Every generated tenant address changes with the configured domain, with no code change, for a new tenant | `ncollection_saas` `test_base_domain_switch` |
| A freshly provisioned tenant starts consistent and records the canonical portal URL | `verify_provisioning.sh` |
| Company Entry goes to the canonical address with no stale port, and isolates sessions | `verify_platform_entry.sh` |

## 10. Checklist for the person doing the deployment

1. `DOMAIN=balancecloud.ae` in the production `.env`; leave SaaS Settings > Base Domain empty.
2. DNS: apex and wildcard to the edge (section 3).
3. Certificate for both names under `live/balancecloud.ae/` (section 4).
4. Decide the platform host (section 6); create the platform database with the matching name.
5. `web.base.url` and `web.base.url.freeze` on the platform database.
6. Start the stack (`docker compose -f docker-compose.yml -f docker-compose.prod.yml ...`); confirm only 80 and 443 are
   published (`docker compose ps`).
7. Then follow `docs/markdown/RUNBOOK_RELEASE_UPGRADE.md` before any traffic.
