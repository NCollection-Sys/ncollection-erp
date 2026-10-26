# Release compatibility gate

**The invariant.** A database never receives normal application traffic from code that requires a newer module or schema state than the database
has. It gets a controlled, System-Branded HTTP 503 instead, and the platform sees exactly why. Detection (the registry-load log, the preflight)
told an operator *after* a user had hit an HTTP 500. The gate makes the 500 unreachable for that cause.

## The failure, as reproduced for this decision

On a neutralised copy of `ncollection` served with the current code (2026-10-23, the real database untouched):

```
GET /web/login -> 500  "Internal Server Error ... the server is overloaded or there is an error in the application"
  odoo/http.py  Application.__call__ -> Request._serve_db            (the registry LOADED fine; /web/health = 200)
  -> ir.http._dispatch -> QWeb web.login_layout -> ncollection_branding nc_brand()
  -> ORM fetch of ncollection.product.branding -> SELECT ... browser_title ...
  -> psycopg2.errors.UndefinedColumn: column ncollection_product_branding.browser_title does not exist
```

The code declares a field the database was never upgraded to have. Odoo never compares the version a database is at with the code on disk, so
nothing stops the request. Same class as R-012b, R-017, R-019 and R-037 (Atlas-eg: `res_users.nc_must_change_password`).

## Decision

| Question | Answer |
|---|---|
| **Where traffic is blocked** | In the Odoo HTTP dispatch, **before the registry is loaded**: `ncollection_core/release_gate.py` wraps `odoo.http.Request._serve_db`, the one method every request that names a database goes through, and whose first act is `Registry(db)`. A model / controller / `ir.http` check would run after the point a stale database already fails. `ncollection_core` is a **server-wide module** (`server_wide_modules = base,web,ncollection_core` in `config/odoo.conf` and `odoo.prod.conf`), so the wrapper exists from the first request; its `post_load` installs it once per process. No Odoo core file is modified. nginx keeps doing coarse host routing only (one upstream, no per-tenant state), because the authority is the database and the code, not the edge. |
| **What decides READY** | Deterministic, the same comparison as the release preflight. The **release contract** is the code this process serves: `{module: version}` of every module under the configured NCollection / OCA addons roots (never Odoo core), read once at start, with a sha256 **release fingerprint**. The database is read with **plain SQL, no registry**: `ir_module_module` and one parameter. READY ⇔ the module table exists ∧ no module is `to install/upgrade/remove` ∧ no installed module of the release universe is older than its code ∧ no installed `ncollection_*` module is missing from the code ∧ no schema gap was found against the database's current module rows (a registry load's `schema_drift`, or a request that hit `UndefinedColumn` / `UndefinedTable`) ∧ no hold. A **database fingerprint** (sha256 of its installed release-universe versions) identifies what was validated. **Never cached by time**: every gated request reads one token (md5 of every module row's name / state / version, the latest module write, the hold) and the evaluation is reused only while that token is unchanged, so a hold, a module entering `to upgrade` or a version written by `-u` is seen by the very next request of every worker process. |
| **Platform or tenant** | The **role** is read from the database's own module table: `platform` when `ncollection_saas` is installed (the database that runs provisioning), `tenant` otherwise - never a configured database name. The role changes **who may ask** about a database, never **whether stale code may serve it**: a stale platform database is blocked before its registry exactly like a stale tenant (no bypass, no "trusted" registry load, `web.login_layout` never renders). |
| **Where state lives** | Two halves, one owner each. **Computed** on the tenant side, per request, from the code and the database: nothing to keep in sync. **Explicit** holds in the tenant database (`ir_config_parameter['ncollection.release.hold']` = `provisioning` / `upgrading` / `upgrade_failed` / `blocked` [`:reason`]), written only by the provisioning install hook and by the platform. **Mirrored** on the platform's `ncollection.tenant` (`nc_release_state`, `nc_release_detail`, `nc_release_checked_at`, `nc_release_fingerprint`, `nc_release_last_ok_at`, `nc_release_last_error`), refreshed over HTTP. The platform never reads a tenant database (Rule 3). No second tenant model, no second provisioning state. |
| **States** | `READY`, `UPGRADE_REQUIRED` (modules behind), `UPGRADING` (mid-upgrade, or held while upgrading / validating), `UPGRADE_FAILED` (held after a failed upgrade), `BLOCKED` (held by an operator, a module not deployed, a schema gap), `PROVISIONING` (being created, or held until the engine validated it). The platform adds `unknown` and `unreachable` for answers it could not get. |
| **The release channel** | Three pre-registry endpoints on every database, answered with plain SQL before the registry (so they work for exactly the databases whose application cannot run): `GET /ncollection/release/status` (state, role, `required` = the exact `-u` list, modules behind with both versions, missing modules, release and database fingerprints, hold, `last_ok`, `last_error`, `checked_at`), `POST /ncollection/release/hold`, `POST /ncollection/release/open` (refused with 409 while the code is ahead; records `last_ok`). An `upgrade_failed` hold records `last_error`. **Who is accepted** - one key derivation, no new user, nothing new stored: on a **tenant**, its own config-sync bearer, checked against its stored key hash in `res_users_apikeys` with Odoo's `KEY_CRYPT_CONTEXT`; on the **platform** database (no service account exists there), the same HMAC derivation of the platform master `NC_CONFIG_SYNC_KEY` for that database's own name, compared in constant time - the master lives only in the environment of the server that serves the platform (never in git, never in a database). Anyone else gets 404, so this is not a database-information endpoint. While a database is compatible and held for `provisioning` or `upgrading`, an accepted bearer passes the hold, so the platform (or the operator, on the platform database) can validate real pages before anybody else gets in. |
| **What a user sees** | HTTP 503, `Retry-After: 120`, `X-NC-Workspace-State: <state>`; a page in the system's identity (System Branding name, browser title, primary / secondary colours as `--nc-color-*`, login logo), read defensively with SQL from that database's own System Branding row (a missing column, row or file falls back to the shipped defaults: the page exists for databases whose schema may be behind). English and Arabic side by side (no catalogue exists before the registry). No traceback, no "server overloaded", no module, version or database name, no claim that anything succeeded. JSON clients get a JSON 503. |
| **Schema errors** | A schema gap the versions cannot show (a column lost at an unchanged version) raises `UndefinedColumn` / `UndefinedTable` out of the original `_serve_db` - directly, or as the explicit `__cause__` of Odoo's own `RegistryError` / `QWebError`. Exactly those become the 503 (state `BLOCKED`, logged at ERROR with the database's one-line message, never the statement, which can carry customer values) and hold the database in that process until its module rows change; **every other exception propagates untouched** to Odoo's own handling. |
| **The seam** | `seam_problem()` checks, at install, that `Request._serve_db` still exists, still takes only `self`, still loads `Registry(`, and that `Application.__call__` still calls it. If not, the gate is **not** installed and a CRITICAL line says so ("Release gate NOT INSTALLED: ..."); `test_the_seam_is_the_one_this_gate_was_written_for` fails CI on such an Odoo update rather than leaving a wrapper that never runs. |
| **Logging** | Through Odoo's own logging (`odoo.addons.ncollection_core.release_gate`, propagating to the root handlers `odoo.netsvc` installs; pinned by `test_the_gate_reason_reaches_odoo_s_configured_log_handlers` and, in a live server, by `verify_platform_release.sh`). One line per change of a database's decision per process: `Release gate: db=<db> role=<role> state=<before>-><after> ... release=<fp> database=<fp> required=<modules> behind=<module db<code> ...> hold=<hold>`; holds, opens, refusals to open, schema interceptions; `Release gate installed: release=<fp> modules=<n>` at start. Never a header, bearer, password or connection string. **Reading it on Docker Desktop:** use `docker compose logs --tail <n> odoo`; on this machine `docker logs --since <t>` and an oversized `--tail` returned an old window and hid every recent line (2026-10-24). |
| **Background work (cron, `-d` preload)** | The HTTP wrapper never sees crons: a serving process with no `-d` runs every database's crons (`cron_database_list()` = `list_dbs(True)`), and a process started with `-d` loads that registry at boot. The same `post_load` wraps three more seams, decided with the same plain-SQL state BEFORE any registry: `IrCron._process_jobs(db_name)` (the entry of both cron runners, `ThreadedServer.cron_thread` and the prefork `WorkerCron`), `IrCron._acquire_one_job` (re-checked before EACH job of a poll, so a hold or an `-u` that starts mid-poll stops the next job before it loads the registry; the manual "Run" button, `include_not_ready=True`, is an HTTP request and was already gated) and `odoo.service.server.preload_registries` (a database that is not READY is not preloaded; a process that installs / upgrades / tests - `-i`, `-u`, `--test-enable`, the release step itself - is never gated). **Only READY runs crons**: no hold is passable here, nothing validates a release through a cron, and a state that cannot be read runs nothing. One log line per change of decision (`Release gate: db=<db> role=<role> state=<before>-><after> crons NOT run ...`, `... crons run again`, `... registry NOT preloaded at start`). Each seam has its own `*_seam_problem()` and refuses LOUDLY (CRITICAL) if an Odoo update moves it. **The gate exists only in a process that loads `ncollection_core` server-wide**: `server_wide_modules` in both configs AND every `--load=` on a command line, because `--load` REPLACES that list - the provisioning runner ran `--load=base,web,queue_job` and was the one process running the platform's crons with no gate at all (R-039). `scripts/ci/invariants.py` `rule_odoo_processes_load_the_release_gate` now fails such a `--load`. Proven with Odoo's real cron code against real databases in verify_upgrade.sh arm 9 (`scripts/upgrade/cron_gate_probe.py`). |
| **What it never does** | Run `-u` (nothing on the request path upgrades anything), hide the cause, edit a schema (its only writes are the hold, `last_ok` and `last_error` parameters, from authenticated release calls), gate static files, or load a registry for `/web/health` (liveness is answered by Odoo's own no-database dispatch, `Request._serve_nodb`, whatever the state: on a one-database server Odoo resolves every request, `/web/health` included, to that database). If the state itself cannot be read (database unreachable), Odoo's own handling decides, as before. Odoo's test server runs with the gate off (`test_enable`: its at-install HTTP tests run while modules are still being installed); its decision path is tested directly (`_gate`), its HTTP behaviour against real servers (verify_upgrade.sh arm 8, verify_platform_release.sh). |

## Provisioning reaches READY only through validation

`DRAFT (not_provisioned) -> PROVISIONING -> VALIDATING -> READY`, all in the existing engine (`ncollection.provisioning.job`):

1. `odoo -i` runs with `NC_RELEASE_HOLD=provisioning`; `ncollection_core`'s `post_init_hook` writes the hold **in the install's own commit**, so
   the database is never routable before step 3. (Before that commit the module table is incomplete or `to install`: not READY either.)
2. `database_status = validating`; `_nc_validate_release`: the tenant's gate must answer *compatible + held for provisioning*; `GET /web/login`
   as the platform must answer 200.
3. `open` -> the gate lifts the hold only because the database is compatible, and answers `READY`. Then `_mark_done` (`ready`).

Any failure raises, and the engine rolls the new database back as it does for every other step. A developer database or a test fixture is not held
(the environment variable is the engine's alone): it is READY as soon as its code and modules agree.

## Company Entry checks readiness before any token exists

`action_enter_company` refreshes the tenant's release state over HTTP **before** generating the token. Anything but `READY` returns a sticky
warning notification ("not ready for the current release ... No entry was issued") - a notification rather than a `UserError`, so the recorded
reason on the tenant form is not rolled back with it. No grant is pushed, so no token
exists that could lead into a broken tenant. Independently, the tenant's own routes (the grant json2 call, `/ncollection/platform/enter`) are behind
the gate: even a stale token meets a 503, never a 500 or a session.

**The Release section** (tenant form, System tab; a banner at the top of the form when the tenant is not READY) shows what the tenant's gate answered over HTTP, before the tenant's registry loads, so it is there for exactly the tenants whose application cannot run: the state, the database, the **required action** (per state: the exact modules to upgrade together through Fleet Migration; wait for validation; read the failure and re-run; deploy or remove a missing module; targeted upgrade for a schema gap; lift an operator hold), **modules to upgrade** (the exact `-u` list), the detail (each module behind with database < code version, missing modules, the schema gap, the hold and its reason), the **code release** and **database** fingerprints, when it was checked, the last successful release and the last failure (an error older than the last successful release is cleared as history).

## The release flow

1. Backup. 2. Deploy code. 3. Restart Odoo (and the provisioning runner). From this moment **every** database the code is ahead of answers the
maintenance page by itself (no operator action, no global outage: READY databases keep serving). 4-5. `scripts/upgrade/release_plan.sh`
(read-only): every registered tenant and the platform database -> modules behind -> database version -> code version -> state, deterministic.
6-7. Incompatible tenants are already gated (`UPGRADE_REQUIRED`). 8. `make upgrade-rehearse db=<db>` on a copy of a representative of each module
set. 9-14. **Fleet Migration**, per tenant: the exact module set comes from that tenant's gate (refused if the migration does not cover it), the
tenant is held `upgrading`, upgraded with `-u` of exactly that set, smoke-probed, opened (only if the gate answers READY), success recorded. 15. A
failure keeps that tenant held `upgrade_failed` (restored from its snapshot when `auto_restore` is on) with the reason on its form; the wave
continues with the others.

**The platform database first - enforced.** It is the control plane: Fleet Migration, Company Entry and provisioning run from it, and the gate
treats it like any other database (behind -> maintenance page). Fleet Migration refuses to start (`action_start`, `action_run_sync`) and every
queued line refuses before any tenant work (`run_line`) unless the platform's own gate state - read fresh with the gate's plain SQL from the
platform's own tables - is `READY`: "Release the platform database first: <db> is <STATE> ... Upgrade exactly its required modules (...)".
Nothing upgrades the platform automatically.

## Operator status: the platform database while it cannot serve its application

The platform's UI is behind the same gate, so while the platform database is not READY the operator uses the release channel directly, from the
container that serves it (the master is in that container's environment; the bearer is derived there and never printed):

```bash
make release-status db=ncollection        # = docker compose exec -T odoo python3 - http://127.0.0.1:8069 ncollection status < scripts/upgrade/release_status.py
```

It prints the gate's JSON (state, role, `required`, `behind`, fingerprints, hold, `last_ok`, `last_error`, `checked_at`) and markers
(`RELEASE_STATE=`, `RELEASE_REQUIRED=`, ...). The same tool performs the platform's release steps:

1. `release_status.py ... ncollection hold upgrading "platform release"` - normal traffic gets the maintenance page `UPGRADING`.
2. `odoo -d ncollection -u <RELEASE_REQUIRED> --no-http --stop-after-init` - exactly the required set (never `-u all`), after
   `make upgrade-rehearse db=ncollection` passed with the same list.
3. Registry + schema validation (setup-log guard, `make schema-drift`, preflight PASS) and a held smoke: the operator bearer passes the hold
   (`gate_probe.py ... ncollection GET /web/login ncollection` -> 200), everyone else still gets 503.
4. `release_status.py ... ncollection open` - refused (409) unless compatible; READY, `last_ok` recorded, normal traffic.

`scripts/upgrade/verify_platform_release.sh` (`make upgrade-platform-verify`) runs this whole sequence on a scratch copy (`upgrplat`) of the real
platform database and proves each step, including that the source is unchanged.

## Rollback

Restore the backup taken in step 1 **and** redeploy the previous code. A database restored to older versions under older code is READY again by the
same comparison. Code rolled back under a database that was already upgraded is *not* reported behind (a database ahead of the code is not drift
the gate can see); that combination must not be served, which is why the backup is restored together with the code.

## Limits (stated, not hidden)

* A wrapper around one Odoo core method is the price of acting *before* the registry; `seam_problem()` refuses (CRITICAL) and
  `test_the_seam_is_the_one_this_gate_was_written_for` fails if an Odoo update moves it. Refusing means the gate is OFF on that server: the
  CRITICAL line is the signal, the server still starts.
* One token query per gated request (one aggregate over `ir_module_module` + one parameter), in exchange for no stale-allow window. A request
  admitted before a transition committed is not recalled.
* A schema gap at an unchanged version is known only after a registry load, or after the first request that hits it (that request gets the 503).
* A WebSocket opened while a database was READY is not re-checked per message. It cannot carry code newer than its database (new code means a restarted server, which drops every socket, and a new socket is an HTTP request through the gate); a hold set while it is open applies to its next HTTP request.
* **Fail open on HTTP, closed on crons.** A database whose module table cannot be read cannot load a registry either, so an HTTP request gets Odoo's own error, exactly as without the gate (logged once per change); a cron runs nothing.
* **A schema error blocks the database in that worker process** until its module rows change (an `-u`) or the process restarts, whatever raised it - also a tenant administrator's own server action selecting a column that does not exist (self-inflicted, that database only).
* **The release state is tenant-reported.** A tenant administrator (`base.group_system`) can write the hold / `last_ok` / `last_error` parameters of their own database. That changes only what their own database answers; a hold is a release-coordination state, never a security or suspension control (suspension is the platform's `ncollection.tenant.status`).
* **The maintenance response names the state publicly** (`X-NC-Workspace-State`, the JSON `workspace_state`): `upgrade_required` / `upgrading` / `upgrade_failed` / `blocked` / `provisioning`, never a module, version or reason. The release channel (`/ncollection/release/*`) is reachable from the edge and protected by its bearer only (404 otherwise). An edge `deny` for `/ncollection/release/` on the public server block (the platform calls tenants on the internal address) is a recommended hardening, not made in this change: it touches the production edge.
* **Rehearsal copies** (`upgrreh<db>`) carry the source's users. They are held while their `-u` runs (no cron can run them), neutralised, and dropped on every exit path; a copy is still routable by its own host name while it exists. Run rehearsals of production databases on a stack whose edge does not route `upgr*` names.
* A tenant without the platform's config-sync key is still gated for users; the platform just records `unknown`. The platform database's status
  needs `NC_CONFIG_SYNC_KEY` in the environment of the server that serves it (the deployment already requires it for config sync).
* The role is read from the database's modules: a tenant that installed `ncollection_saas` would accept the derived master bearer for its own
  name - the same key its config-sync account already accepts, so it grants nothing new.
