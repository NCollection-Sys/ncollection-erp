# Tenant URLs, routing, My Companies and provisioning time

One page for the four things that were found tangled together on the `test11` company. Everything here is
measured or read from code, not assumed.

## 1. One authority for a tenant's address

A tenant lives at **`<subdomain>.<base domain>`**. The tenant record stores its identity (`subdomain`, falling back
to `database_name`); it never stores the domain. The address is built in exactly one function:
`ncollection.tenant._nc_public_origin()` (`ncollection_subscription/models/tenant.py`).

These all call it, so they cannot disagree with each other or with routing:

| Consumer | Where |
|---|---|
| Tenant URL (list, form, My Companies) | `tenant_url` (computed, not stored) |
| Company Entry ("Enter Company") | `ncollection_saas/models/platform_entry.py` `_nc_entry_url` |
| Signup "ready" login link | `ncollection_saas/controllers/checkout.py` |
| Provisioning portal URL / welcome email / the tenant's own `web.base.url` | `provisioning_job._portal_url` |
| Domain records (FQDN) | `ncollection.domain._fqdn_for_tenant` |
| Service-channel `Host` header (config sync, entry grant) | `<db>.<base domain>` from `_nc_base_domain()` |

### Where the base domain comes from (in this order, no third place)

1. **SaaS Settings > Base Domain** (`ncollection_saas.base_domain`), an operator's explicit choice, else
2. the **`NC_BASE_DOMAIN`** environment variable of the deployment, else
3. **nothing.** There is deliberately no built-in default. With nothing configured there is no URL and the callers
   that need one refuse with a clear message. (A built-in `ncollectionerp.com` is what made a local workspace
   advertise `test11.ncollectionerp.com`.)

| Environment | `NC_BASE_DOMAIN` | Result |
|---|---|---|
| Local development | `ncollection.localhost` (default in `docker-compose.yml`) | `http://test11.ncollection.localhost` |
| Production | its `DOMAIN` (`docker-compose.prod.yml` passes it) | `https://test11.<domain>` |

Scheme: `http` for `localhost` / `*.localhost` (RFC 6761 loopback: no certificate can exist), `https` for any other
domain. Port: none, unless `ncollection_saas.url_port` is set (only for a stack with Odoo exposed directly on a port).

Retired, because they were second and third answers to the same question: `ncollection_saas.entry_url_template`,
`ncollection_saas.portal_url_template` and four copies of a hardcoded `ncollectionerp.com`. The
`19.0.6.14.0` migration removes the two templates, and removes the old *seeded, never-edited* base domain **only when
`NC_BASE_DOMAIN` is set**, so a production deployment that has not yet declared its domain keeps working unchanged.

Production: the edge is a template rendered from the same `DOMAIN`, one wildcard route serves the apex and every tenant, and the apex's first label (the platform's own database) is reserved so no tenant can take it. The contract, DNS, TLS and forwarded-header requirements are in `docs/markdown/DEPLOYMENT_DOMAIN_CONTRACT.md`; `ncollection_saas` `test_base_domain_switch` proves a new tenant's every address follows the configured domain.

## 2. Local routing: what has to be running

A browser request to `test11.ncollection.localhost` reaches the right database only if Odoo selects the database from
the host: `--db-filter=^%d$` (the first host label is the database name). That is the **routing stack**
(`docker-compose.routing.yml`, `make routing-up`). The plain `make up` stack has no database filter, so every host
shows the same database selector: the URL is right and still does not open.

Verified on the routing stack, through nginx on port 80 and straight to Odoo on 8069:

| Host | Result |
|---|---|
| `test11.ncollection.localhost` | login page of database `test11` |
| `ncollection.localhost` | the platform database |
| `bogus.ncollection.localhost` | no database: 303 to the selector, which is blocked at the edge |
| `test11.ncollectionerp.com` (wrong domain) | not served as a tenant |

Cross-tenant: the host picks the database, a session cookie is per-host and database-bound, and the Host header
cannot name a database that has no matching host label (`e2e/tests/tenant-hosts.spec.ts`).

**Decision for the owner, not taken here:** making `^%d$` the default for `make up` would make tenant hosts work with
no extra command, but changes how `localhost:8069` behaves for every developer. Until then the supported SaaS dev
mode is `make routing-up`.

## 3. My Companies

`ncollection.tenant.platform_admin_ids` is the allow-list "who may enter / see this company". The platform form
posts it **empty** on create; the "creator is authorised" default only applied when the key was *absent*, so a company
made from the form was provisioned, ready, and listed for nobody. Now an empty, cleared or missing list on create adds
the creator (a real platform administrator, not the technical superuser); a list that names someone is respected.
The `19.0.1.11.0` migration repairs existing companies with an empty list, and only those.

My Companies lists every company the user is authorised for whose status is trial/active, **including ones still
being built or whose build failed**, with their workspace stage (Provisioning / Ready / Failed). "Enter Company" is
shown only when ready, and `action_enter_company` re-checks that on the server.

## 4. Provisioning time (measured on `test11`, 17 requested modules)

From the job log of the real run, then reproduced on a throwaway database with the identical module set:

| Stage | Real run | Reproduced |
|---|---|---|
| Queue pick-up | 5 s | |
| Database create + module install (`odoo -i`) | 167 s | 159 s wall |
| Seed (admin, workspace config, branding) | 8 s | |
| Total | 180 s | |

The install loads **119** modules (17 requested plus Odoo's own dependency closure). `base` 21 s, `account` 9 s,
`stock` 8 s, `mail` 7 s. NCollection's own modules are 16 s of it. There is no duplicated install, repeated registry
load or wait in our own path; the time is Odoo initialising its standard modules into an empty database.

The only structural speed-up is cloning a pre-built template database per plan instead of installing from scratch.
That is an architectural change (template lifecycle per module set, filestore, secrets and uuid regeneration,
per-plan invalidation) and is **not** done here. What changed: every provisioning job now logs a `Timing:` line
(database + modules / localization check / seed / total), so the next measurement is a log read, not a benchmark.
The UI already has a stage (`provision_stage`: Provisioning), which My Companies now shows.
