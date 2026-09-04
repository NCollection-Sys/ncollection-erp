# Routing — how a request finds its database

How a browser request reaches the right tenant database, what the dev stack does
differently from production and **why**, and the URLs to use for a demo (#453).

Companion to `LOCAL_DEV_AND_ARCHITECTURE.md` (setup) and
`ARCHITECTURE_DATA_PLATFORM.md` (the authoritative topology).

---

## 1. The one rule

**The hostname IS the database.** Odoo's `db_filter = ^%d$` maps the first
component of the `Host` header to a database of exactly that name:

| URL | Database |
|---|---|
| `wasla.localhost` | `wasla` |
| `albarari.localhost` | `albarari` |
| `ncollection.localhost` | `ncollection` (the platform/admin DB) |

There is no tenant lookup table in the request path, no session-carried tenant
id, and nothing to spoof: a request for a database that does not exist matches
nothing and is refused. This is the same mechanism in dev and in production —
only the domain differs (`*.localhost` vs `*.ncollectionerp.com`).

It is also why **tenant key === subdomain === database name, always**, and why
tenant database names must be alphanumeric (`^[a-z][a-z0-9]{2,62}$`):
underscores are invalid in hostnames, so an underscored database would be
unroutable. `ncollection_saas` enforces that regex before it creates anything.

---

## 2. Two stacks, on purpose

`db_filter` is deliberately **off** in everyday development and **on** for
demos, the routing proof and production.

| | `make up` (default dev) | `make saas-up` | production |
|---|---|---|---|
| Config | `config/odoo.conf` | same + routing overlay flags | `config/odoo.prod.conf` |
| `db_filter` | none | `^%d$` | `^%d$` |
| `list_db` (selector) | `True` | `False` | `False` |
| `proxy_mode` | off | on | on |
| Reach a DB by | picking it from the selector, or `?db=<name>` | its hostname only | its hostname only |
| **Background jobs run?** | **No** | **Yes** (`provisioning-runner`) | Yes |

**Which mode to use.** `make up` for ordinary addon work — it is faster and lets
you address any database. `make saas-up` for anything touching the tenant
lifecycle: without its worker, provisioning, config sync and module installs
queue and never execute (#463).

**Why the default stays permissive.** Every local workflow depends on being able
to address an arbitrary database: `make test` (`nctest`), the `verify_*.sh`
suites (`rt*`, `e2e*`, `prov*`, `fintest`, `upgr*`), the `demo/` React app's Vite
proxy, and every `-d <db>` subprocess. `db_filter` restricts **HTTP routing
only** — it never affects a `-d` invocation — but the selector and multi-DB HTTP
access are load-bearing for that work. Turning it on globally would break the
everyday loop to fix a demo-only concern.

---

## 3. SaaS development / demo stack

**This is the stack to use for any SaaS work** — anything involving tenants,
plans, provisioning, module installs or config sync.

```bash
make saas-up            # routing + the queue worker; prints the entry points
make saas-urls          # print the entry points again
make saas-jobs          # inspect background jobs + per-tenant lifecycle state
make saas-runner-logs   # follow the worker that executes them
make saas-down          # back to the permissive dev stack (then: make up)
```

It layers four overlays, one per concern, with no duplicated flags:

| Overlay | Adds |
|---|---|
| `docker-compose.yml` | db, odoo, (dev) nginx, pgadmin |
| `docker-compose.dev.yml` | the dev command + the dev nginx conf |
| `docker-compose.routing.yml` | `db_filter=^%d$`, `list_db=False`, `proxy_mode` |
| `docker-compose.saas.yml` | **`provisioning-runner`** — the queue_job worker |

`routing-up` still exists and starts the first three: it is the P1-T06 routing
*proof*, which needs no worker. `saas-up` is the superset, and the difference
between them is exactly the thing that made the SaaS lifecycle untestable
before #463.

### Why the worker matters (#463)

Everything the platform does to a live tenant is **queued**, never done inline
in the web request: provisioning, config sync, and the module installs a plan
change triggers (#461). Without `provisioning-runner` the platform still
enqueues perfectly and *nothing drains the queue* — jobs sit `pending`, the
stack looks healthy, and the feature simply never happens. That is not
hypothetical: pending `sync_workspace_config` rows in this repo's `saastest`
fixture sat unprocessed for **weeks** before anyone looked.

`scripts/ci/invariants.py` now fails if `SAAS_COMPOSE` stops composing the
runner in, because an absence like that produces no error to notice.

### Inspecting jobs

`make saas-jobs` prints the queue and the per-tenant lifecycle state.

| `queue_job.state` | Meaning |
|---|---|
| `pending` | created, waiting for a free channel slot |
| `enqueued` | claimed by the runner, about to start |
| `started` | running now |
| `done` | finished successfully |
| `failed` | raised; `exc_message` says why, and it stays visible |

Per-tenant columns tell you the *business* outcome rather than the job's:
`module_install_state` (`none`/`queued`/`running`/`done`/`failed` +
`module_install_last_error`) and `config_sync_state` (`ok`/`transient`/
`permanent`).

### Troubleshooting a failed job

1. `make saas-jobs` — find the row and read `exc_message`.
2. `make saas-runner-logs` — the runner logs the full traceback and, for module
   installs, the `odoo -i` subprocess output.
3. The tenant form (platform admin → Tenants) shows the same state plus
   **Install Licensed Modules** and **Sync Configuration Now** to retry a single
   tenant; both use the same engines as the automatic path, so a retry cannot
   behave differently from the original.
4. Channel capacity is `root:2,root.provisioning:2,root.outbound:1` — if jobs
   stay `pending` while the runner is healthy, something long-running is
   holding a slot; `make saas-jobs` shows what.

If the runner container is not up at all, `make saas-jobs` still works (it
reads the database) and every job will be `pending` — that is the #463
signature.

### Entry points

| Who | URL | Notes |
|---|---|---|
| Platform admin | `http://ncollection.localhost/` | The DB carrying `ncollection_saas`. Set `NC_PLATFORM_DB` if yours differs (the demo-tenant flow uses `ncplatform`, the verification suites `saastest`). |
| A tenant | `http://wasla.localhost/` | Any provisioned tenant, by its database name. |
| Bare `localhost` | redirects (302) to the platform host | Dev-only nginx convenience — see below. |

Port 80 goes through nginx (recommended: it exercises the real edge config).
Port 8069 talks to Odoo directly and bypasses every nginx rule, including the
DB-manager block — useful for debugging, never representative of production.

### What a tenant user cannot do

- **Reach another tenant's data by changing the URL.** A different hostname is a
  different database; there is no cross-database session.
- **See or use the database selector.** `list_db=False` disables it in Odoo
  (`/web/database/*` renders "The database manager has been disabled by the
  administrator" and discloses **no** database names), and the dev nginx conf
  additionally returns `403` for `/web/database` at the edge. Two independent
  layers, matching `ARCHITECTURE_SECURITY.md`'s defence-in-depth stance.

### The bare-`localhost` redirect

On the routing stack, bare `localhost` matches no database, so Odoo `303`s to
`/web/database/selector` — which is disabled, i.e. a dead end for the URL every
developer has used for months. `nginx/conf.d/ncollection.dev.conf` therefore
sends bare `localhost` to the platform host with a `302`.

It is an **exact** `server_name localhost` block, which nginx prefers over the
`*.localhost` wildcard, so tenant hostnames are untouched. The redirect target is
one hardcoded line — change it if your platform database is not `ncollection`.
`nginx/conf.d/ncollection.prod.conf` is **not** affected.

---

## 4. Logging in as a tenant

A provisioned tenant's admin has **no known password by design** — the
provisioning seed sets an unguessable one and forces a reset
(`scripts/provisioning/seed_tenant.py`). The owner receives a setup link:

```
http://<db>.localhost/web/reset_password?db=<db>&token=<token>
```

printed by the seed as `SEED_SETUP_URL=` and mailed by `_send_welcome_email()`.
Following it sets their own password; login then works normally at
`http://<db>.localhost/`.

To re-issue one for an existing tenant:

```bash
docker compose exec -T odoo odoo shell -d <db> \
  --db_host=db --db_user=odoo --db_password=odoo --log-level=error --no-http <<'PY'
admin = env['res.users'].search([('login','=','<admin-login>')], limit=1)
admin.partner_id.signup_prepare(signup_type='reset')
env.cr.commit()
print(admin.partner_id._get_signup_url())
PY
```

Note the token is **generated on demand** in Odoo 19 — `res.partner` no longer
stores a `signup_token` column, so it cannot be read back out of the database.

---

## 5. Gotchas that have actually bitten

- **`?db=` only works on the permissive stack.** With `db_filter` on, the
  hostname decides and `?db=` is ignored. With it off, a request that has not
  yet bound a database 302s to `/web/login?db=<name>` to set the session cookie
  first — so a raw `curl` of a deep URL 404s until it has that cookie.
- **Port 8069 skips nginx.** The edge `403` on `/web/database` and every rate
  limit are nginx rules. Probing 8069 and concluding "the DB manager is exposed"
  measures the wrong layer.
- **The websocket port differs between dev and prod** (dev 8069, prod 8072) —
  the reason `ncollection.dev.conf` and `ncollection.prod.conf` are two files.
  See the header comment in the dev file.
- **`db_filter` does not scope cron.** Odoo's scheduler uses
  `config['db_name'] or list_dbs(True)`, which never consults the filter — the
  whole of #337/#343. That is why every compose file that touches Odoo's command
  restates `--max-cron-threads=0`.
