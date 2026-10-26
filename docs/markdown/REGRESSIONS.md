# Regression Ledger

Every regression we hit, recorded as **symptom → root cause → the guard that now
prevents recurrence**.

**House rule:** a regression is not closed until a guard exists, or until it is written
down here *why* one cannot be built. Fixing the instance without adding the guard means
we will meet it again with a different filename.

Why this file exists: incidents were living in commit messages and one person's memory.
That is not recoverable knowledge — it is folklore. Guards are.

## Guard inventory

| Guard | Where | Enforced |
|---|---|---|
| `invariants.py` R1 — `psql`/`pg_isready` need explicit `-d` | `scripts/ci/invariants.py` | CI `lint` + pre-push |
| `invariants.py` R2 — no `\|\| true` on state-changing docker commands | same | CI `lint` + pre-push |
| `invariants.py` R3 — no hardcoded container names | same | CI `lint` + pre-push |
| `shellcheck` over every `*.sh` and `.githooks/*` | `.github/workflows/ci.yml` | CI `lint` + pre-push |
| e2e fixtures upgraded on reuse (schema drift) | `e2e/scripts/setup_e2e_tenants.sh` | `make e2e-verify` (local only — CI builds fresh DBs) |
| `architecture_guard.py` — addon architecture, secrets, XML | `scripts/ci/architecture_guard.py` | CI + pre-push |
| Cross-suite `verify` job | `.github/workflows/verify.yml` | CI on every PR |
| Post-merge canary | `.github/workflows/canary.yml` | push to `develop` |
| Nightly drift check | `.github/workflows/nightly.yml` | cron (once on `main`) |
| Dependency/CVE watch | `.github/dependabot.yml` | weekly + advisories |
| Stale module dependencies + modules behind their code version, across all DBs | `scripts/dev/doctor.sh` | `make doctor` |
| Fixture namespace separation | `Makefile`, `e2e/` | structural |
| Config-sync push carries `Host: <db>.<base-domain>` (db_filter routing) | `ncollection_saas/tests/test_config_sync.py` + `.../scripts/provisioning/verify_config_sync.sh` | CI `test` + `make verify-all` |
| Shared-stack races → false CRITICALs (R-018) | CLAUDE.md Rule 14, `scripts/dev/stack_settled.sh`, `.claude/agents/verify-runner.md` | agent convention + `verify-runner` retry-before-escalate |
| Report menus aligned to the ORM; OCA report wizards restricted to accounting read-only; per-role hub (R-027, R-028) | `ncollection_account_reports` (`_REPORT_MENU_GROUPS`, `_nc_harden_oca_wizards`), `tests/test_report_hub.py`, `tests/test_oca_wizard_access.py` | CI `test` + `e2e/manual/reports/*` |
| Preview and Print render ONE document; the dev container reaches the tenant URL (R-034) | `ncollection_account_core` + `ncollection_sale_documents` tests, `scripts/dev/pdf_hosts.sh`, `make doctor` | CI `test` + `make doctor` |
| PDF is styled in dev, and a blocked download is explained (R-029) | `scripts/dev/pdf_hosts.sh`, `make doctor`, `report_viewer.js` | `make doctor` + manual |
| Newer code under an un-upgraded database answers 500 (R-037): drift detector + registry-load log, upgrade rehearsal with HTTP smoke, drift arm in the upgrade proof | `ncollection_core/models/schema_drift.py`, `scripts/upgrade/rehearse_upgrade.sh`, `scripts/upgrade/verify_upgrade.sh` arm 6 | CI `test` (detector) + `make verify-all` (arm 6) + `make upgrade-rehearse` before serving new code to a database |
| The production domain is configuration at the edge too, and Odoo is only reachable through nginx (R-038) | `nginx/conf.d/ncollection.prod.conf` (template), `docker-compose.prod.yml`, `scripts/deploy/verify_edge_domain.sh`, `scripts/ci/invariants.py` (`rule_no_hardcoded_tenant_domain`, `rule_prod_odoo_is_not_published`), `ncollection_saas` `test_base_domain_switch` | pre-push `invariants` + CI `test`; `make edge-verify` |

---

## R-001 — Websocket bound to the wrong port (P1-T20)

**Symptom.** The Odoo web client hung for ~1.5 min and browser journeys timed out;
container logs showed
`RuntimeError: Couldn't bind the websocket. Is the connection opened on the evented port (8072)?`

**Root cause.** Odoo serves the realtime bus on a port that depends on worker mode —
**8069** at `workers=0` (threaded) and **8072** at `workers>0` (gevent). The dev Nginx
routes `/websocket` → `odoo:8069`. Running the stack with `--workers=2` moved the bus to
8072 while Nginx still pointed at 8069.

**Guard.** The E2E stack pins `workers=0`, and the contract is documented in
`nginx/README.md`, `e2e/README.md` and the `verify.yml` header.

**Guard deliberately NOT built.** A static rule "`workers>0` ⇒ nginx must route
`/websocket`→8072" would flag *correct* code: `docker-compose.saas.yml` legitimately runs
`--workers=2` on the provisioning runner, a background queue_job container that sits behind
no Nginx and serves no web client. Checking it properly needs compose→nginx topology
modelling. A guard that cries wolf gets ignored, so this one stays documentation.

---

## R-002 — "Idempotent" routing setup rebuilt every database, every run

**Symptom.** `verify_routing.sh` printed *"Setting up test databases (idempotent)…"* and then
re-created the routing fixtures (then named `clienta`/`clientb`/`admin`) on **every** invocation, wasting minutes and churning
fixtures. Undetected for weeks.

**Root cause.** `db_exists()` ran `psql -U odoo` with **no `-d`**. Postgres CLI tools default
the target database to the **username**; no database `odoo` exists, so the query always died
with `FATAL: database "odoo" does not exist`, and the function always returned false.

**Guard.** `invariants.py` R1, enforced in CI and pre-push. **Proven to bite**: reintroducing
the bug fails the guard with a precise message.

---

## R-003 — `pg_isready` healthcheck FATAL spam ✅ FIXED

**Symptom.** `ncollection-db` logged `FATAL: database "odoo" does not exist` every 10 seconds,
forever. Harmless in itself, but constant false errors train you to ignore the database log —
which is where a real failure would appear.

**Root cause.** Same trap as R-002: the healthcheck was `pg_isready -U ${DB_USER:-odoo}` with
no `-d`, so every probe asked for a database named after the *user*. Cosmetic only —
`pg_isready` needs just a *response* to confirm liveness, so the container stayed healthy and
nothing appeared broken.

**Guard.** `invariants.py` R1 (the same rule that catches R-002) — it had this entry
registered in `KNOWN_PENDING` rather than silently exempted, which is what kept it visible
until it was fixed. That entry is now deleted, and `KNOWN_PENDING` is empty.

**Why it shipped alone.** This healthcheck gates `depends_on: db: condition: service_healthy`
for the odoo service, so a syntax slip stops the entire stack booting. It was isolated behind
a full `down` → `up` proof (never `-v`, which would destroy the postgres volume): odoo must
still reach healthy through the dependency gate, and the db log must stay clean.

---

## R-004 — Two suites shared one fixture namespace

**Symptom.** `make routing-clean` destroyed the E2E tenants; conversely the E2E setup dropped
and rebuilt the routing suite's databases.

**Root cause.** `clienta`/`clientb`/`admin` were owned by three different consumers
(`verify_routing.sh`, `setup_e2e_tenants.sh`, `make routing-clean`) with no coordination —
shared mutable global state across test suites.

**Guard.** Namespaces are now separated and each suite may only drop its own:

| Suite | Owns | Cleanup |
|---|---|---|
| Routing (P1-T06) | `rtclienta` · `rtclientb` · `rtadmin` | `make routing-clean` |
| E2E (P1-T20) | `e2eclienta` · `e2eclientb` · `e2eadmin` | `make e2e-clean` |
| Provisioning (P2-T01) | `prov*` | — |

Names must stay **alphanumeric**: `db_filter=^%d$` routes a subdomain to the database of the
same name, underscores are invalid in hostnames, hyphens need Postgres quoting.

**Guard deliberately NOT built.** Static "one owner per fixture DB" checking is unreliable
because the drops are parameterised (`drop_db "$1"`). The separation removes the hazard
structurally instead, which is better than detecting it.

---

## R-005 — A failed restart reported success

**Symptom.** `setup_e2e_tenants.sh` printed **"✅ E2E tenants ready"** even when the Odoo
restart failed, leaving a stale `@ormcache` and producing baffling test results.

**Root cause.** `docker restart … || true` on a load-bearing step. Enforcement
(license/menu visibility) is `@ormcache`'d per process, so the restart is what makes the
seeded config take effect.

**Guard.** `invariants.py` R2. The step now fails loudly with an actionable message, and the
readiness checks no longer exhaust silently.

*Footnote:* this fix immediately exposed a second latent bug — `docker compose` alone loads
only the base file, which does not define `nginx`, so the Nginx restart had been failing
silently all along.

---

## R-006 — Hardcoded container names

**Symptom.** Scripts referenced `ncollection-odoo` / `ncollection-nginx` directly, breaking
under a non-default `COMPOSE_PROJECT_NAME`.

**Root cause.** Convenience; no convention forbade it.

**Guard.** `invariants.py` R3. IDs are derived via `docker compose ps -q <service>`.

---

## R-007 — Every `*-clean` target silently dropped nothing

**Symptom.** `make routing-clean` reported
`dropdb: error: database removal failed: ... is being accessed by other users` and left every
database in place.

**Root cause.** Odoo holds pooled connections. The Makefile targets called `dropdb` directly,
while the verification scripts' `drop_db()` helpers correctly ran `pg_terminate_backend`
first. The Makefile never did.

**Guard.** A single `drop_database` macro in the `Makefile`, used by `dropdb`,
`routing-clean` and `e2e-clean`, terminating sessions before dropping.

**How it was found.** Only by *running* the fixture-isolation proof instead of assuming it —
which is the argument for `make verify-all` over reasoning about correctness.

---

## R-008 — Shell scripts were never linted in CI

**Symptom.** R-002, R-005 and R-006 all shipped, and all three live in shell scripts.

**Root cause.** `CLAUDE.md` mandated `shellcheck` locally while CI never ran it. A rule that
is only documented is not enforced — the largest promise-vs-enforcement gap in the repo.

**Guard.** `shellcheck` in CI over every `*.sh` and `.githooks/*`, plus the pre-push hook.
It has already caught a real defect in its own author's code (SC2164 in `scripts/dev/doctor.sh`).

---

## R-009 — A green check misread as full coverage *(misdiagnosis, corrected)*

**Symptom.** `architecture-guard` was reported as having inspected nothing
(`clean (0 file(s) checked)`) on a 16-file PR — apparently a green check that reviewed nothing.

**Root cause.** **The diagnosis was wrong.** Against the real commit range the guard reports
`clean (17 file(s) checked)`; it checks secrets on every changed file and XML on every `.xml`.
The `0 file(s)` came from a **local pre-commit run**, where untracked files legitimately do not
appear in `git diff`.

**Guard.** The guard now states its scope explicitly and says so when 0 files are in range,
so the empty case can never again be misread as a passing review.

**Lesson recorded on purpose:** a plausible-sounding root cause that was never verified against
the real data nearly drove a redesign of a working gate. Verify the claim before building on it.

---

## R-010 — A CVE found only because someone looked

**Symptom.** `@playwright/test` 1.49.1 carried **GHSA-7mvr-c777-76hp** (high). It was found by
running `npm audit` by hand during a review.

**Root cause.** No automated dependency or advisory watch existed.

**Guard.** Dependabot (`.github/dependabot.yml`) — weekly grouped updates plus immediate
security advisories, across `github-actions`, `docker-compose` and npm. It also manages the
floating image tags (`certbot/certbot:latest`, `dpage/pgadmin4:latest`) that R-011 depends on.

---

## R-011 — No enforcement is possible on this GitHub plan ⚠️ STRUCTURAL

**Symptom.** A PR was merged without review while CI was still the only gate; nothing stopped it.

**Root cause.** Verified, not assumed:

```
gh api repos/NCollection-Sys/ncollection-erp/branches/develop/protection
→ HTTP 403 "Upgrade to GitHub Pro or make this repository public"
```

Branch protection **and** rulesets are unavailable on a GitHub Free org private repo. Required
status checks are therefore impossible: **every gate we own is bypassable by merging anyway.**

**Guard (compensating, not preventive).** Detection instead of prevention — the post-merge
canary files a `broken-develop` issue naming the commit and author within ~12 minutes, and the
nightly catches upstream drift. See `BRANCH_PROTECTION.md`.

**Real fix.** A paid plan (Team). Until then, no amount of tooling makes a merge blockable
here, and any document claiming otherwise is wrong.

---

## R-012 — A module's dependency grew, and existing databases never got it

**Symptom.** `ncollection_branding` silently stopped loading on the `ncollection` database.
The dashboard and branding were simply absent, and the only signal was **one** line at startup:

```
ERROR odoo.modules.loading: Some modules are not loaded, some dependencies or manifest may be missing: ['ncollection_branding']
```

It was reported as "the application is fundamentally broken". It was one module on one database.

**Root cause.** `ncollection_branding` gained `http_routing` as a dependency during P1-T14.
**Odoo does not retroactively install newly-declared dependencies into existing databases** —
only an upgrade (`-u`) does. `ncollection` had been bootstrapped *before* that manifest change,
so it kept the old dependency set; `e2eclienta`, created *after*, was fine.

This is not specific to that module. **Every** database created before any `depends` grows drifts
the same way, and the failure is near-invisible.

**Guard.** `make doctor` now scans every database for installed modules whose declared
dependencies are not installed, naming the database, the module, the missing dependency, and the
exact `make upgrade` command. Proven twice: it caught the real `ncollection` defect before the
fix, and flagged a deliberately broken throwaway database.

**Remediation when it fires:** `make upgrade m=<module> db=<database>`.

### R-012b — the other half: a module's *schema* falls behind its code (INFRA-08)

The dependency check above is only one way an existing database drifts. The other is a module
whose installed **version** is behind the code, so its *new fields* were never migrated — because
Odoo migrates a schema only on upgrade, for the same reason it does not backfill dependencies.

**Symptom.** Provisioning threw `column res_company.nc_primary_color does not exist`. The local
`ncplatform` had `ncollection_branding` at `19.0.1.3.0` while the code (after P1-T16) was
`19.0.1.4.0`; the new colour fields existed in code but not in that database's schema. It looked
like a code regression; it was a stale local database.

**Guard.** `make doctor` also compares each `ncollection_*` module's installed `latest_version`
against `custom_addons/<mod>/__manifest__.py` and warns (advisory, not a blocker — the module
still loads) when a database is behind, with the `make upgrade` fix. Proven both ways: it flagged
real drift across several databases, and the specific line cleared after the recommended upgrade.
Scoped to our modules; core/OCA manifests live in the container and rarely bump mid-development.

---

## R-013 — `cash_bank` always read zero, and empty data hid it

**Symptom.** The dashboard's Cash & Bank tile showed `0` on a tenant that
demonstrably held 250,000 in the bank.

**Root cause.** The provider summed **every line in the bank/cash journals**.
Every journal entry balances, so that total is *always* exactly zero: an opening
balance posts +250k to the bank account and −250k to equity, and **both lines
carry the bank journal**. The correct measure is the balance of the cash/bank
**ledger accounts** (`account_type = 'asset_cash'`).

**Why it survived review, unit tests and CI.** Every tenant was empty. An empty
tenant returns `0` under the wrong query *and* the right one, so the tile looked
correct everywhere it was checked. It only became visible the moment real data
existed.

**Guard.** The demo tenant (`make demo-tenant`, `docs/markdown/DEMO_TENANT.md`)
now carries posted bank entries, so this widget is exercised against real figures.
The broader lesson is recorded rather than automated: **a KPI that reads 0 on
empty data is not evidence that it works.** Widgets are only meaningfully verified
against a populated tenant.

---

## R-014 — Provisioned tenants get roles that grant no app access ✅ FIXED (P2-T02)

**Symptom.** On a freshly provisioned tenant, an Accountant logs in to an empty
dashboard. The role resolves the `financial` widget group correctly, but every
financial widget is dropped because reading `account.move.line` is denied.

**Root cause.** `ncollection_core.hooks._sync_role_implications()` links the
NCollection roles to the underlying Odoo app groups
(Accountant → `account.group_account_user`, Sales →
`sales_team.group_sale_salesman`, …). It runs from `post_init_hook` and
deliberately **skips modules that are not installed yet**. The provisioning engine
installs `ncollection_core` alongside `sale`/`account` in a **single** `-i`
command, so when core's hook fires those groups may not exist — and nothing
re-runs it afterwards. `hooks.py` states the contract explicitly: *"re-run
`_sync_role_implications` after any module install/uninstall."*

Confirmed live: re-running it on the demo tenant linked 5 implications, and the
Accountant and Sales dashboards immediately populated.

**Status.** **Fixed in P2-T02.** The provisioning seed
(`custom_addons/ncollection_saas/scripts/provisioning/seed_tenant.py`) now
re-runs `ncollection_core.hooks._sync_role_implications(env)` inside the tenant
DB **after** the plan's modules are installed, so the role→app-group links that
core's `post_init_hook` had to skip are established. Idempotent (safe if it was
already linked).

**Guard.** `custom_addons/ncollection_saas/scripts/provisioning/verify_provisioning.sh`
provisions a tenant whose plan includes `account` and asserts the Accountant
role implies `account.group_account_user` post-install ("R-014" assertion). It
runs in `make verify-all` (the local heavy proof; CI installs modules in a
single pass and cannot exercise the two-step provisioning path).

---

## R-015 — Config-sync push 404s under `db_filter=^%d$`; every tenant sync silently no-ops ✅ FIXED (P2-T18)

**Symptom.** During the Phase-2 gate (`make verify-all`), `verify_config_sync.sh`
regressed to 3/7: the service-account checks passed but every push-dependent check
failed — "module set not propagated", "max_users not propagated", "suspension not
propagated", "reconcile did NOT heal". The odoo log showed the loopback push
returning `POST /json/2/ncollection.workspace.config/sync_from_platform HTTP/1.1"
404`. CI stayed green throughout, and the same script had passed when P2-T03 merged.

**Root cause.** `config_sync._config_sync_push` posts to
`ncollection_saas.internal_base_url` (`http://localhost:8069`) with header
`X-Odoo-Database: <db>`, trusting the header to select the tenant DB. It does not:
under `db_filter=^%d$` (the routing stack **and** the documented production config),
Odoo filters the DB by the request **Host** — `odoo/http.py:db_filter()` derives
`domain = Host.partition('.')[0]`, so `Host: localhost` compiles `^localhost$`, which
matches no tenant. `X-Odoo-Database` does **not** bypass this — it is itself passed
through `db_filter([header], host=Host)` (`http.py` ~L1837). With no DB selected, the
`/json/2/<model>/<method>` model route cannot resolve → 404 → the push soft-fails and
the sync silently no-ops. Proven empirically: same endpoint, `Host: localhost` → 404,
`Host: <db>.localhost` → 401 (DB + route resolved, only bearer auth differs).

It stayed invisible on two blind spots at once: (1) the unit tests
`patch('requests.post')` — they assert the URL/bearer/`X-Odoo-Database` but never make
a real round-trip, so `db_filter` was never exercised; (2) `verify_config_sync.sh` had
only ever been validated standalone on the plain dev stack, where `db_filter` is off
and `localhost` resolves. **Left unfixed this ships broken config-sync to production:**
under `db_filter=^%d$` the loopback push would 404 for *every* tenant, so plan changes,
suspensions, and the nightly reconcile would never reach any workspace.

**Status.** **Fixed in P2-T18.** `_config_sync_push` now sends
`Host: <db>.<base_domain>` (reusing `ncollection_saas.base_domain`, the same param the
domain layer uses), so `db_filter`'s `^%d$` selects the tenant DB. DNS-free: the request
still connects to the loopback IP:port from `internal_base_url`; only the presented
virtual host changes. Env-agnostic: `^%d$` keys on the first Host label, so it works
whether `base_domain` is `localhost` (dev) or `ncollectionerp.com` (prod).

**Guard.** Two, at both layers: (1) `test_config_sync.py::test_push_builds_bearer_request`
now asserts the push `Host` header equals `<db>.<base-domain>` (CI `test`); a revert to
`localhost` fails the unit suite. (2) `verify_config_sync.sh` runs inside
`make verify-all` while the routing overlay's `db_filter=^%d$` is active, exercising the
**real** cross-DB json2 round-trip end-to-end — a Host/db_filter regression fails loudly
there. (CI's `verify.yml` runs routing + e2e only; config-sync + provisioning remain the
local heavy proof, so `make verify-all` before merge is the enforcement point — Rule 13.)

---

## R-016 — `verify_provisioning.sh` left platform fixtures → 2nd consecutive run collided ✅ FIXED (#256)

**Symptom.** Running `make verify-all` (or `make provisioning-verify`) twice back-to-back:
the first run passed, the second failed with `duplicate key value violates unique constraint
"ncollection_subscription_plan_code_unique" … (code)=(PROV)`. The cross-suite gate was not
safely re-runnable, so a red 2nd run was a red herring unrelated to the change under test.
Surfaced by the `verify-runner` idempotency double-run during PR #255.

**Root cause.** The proof dropped its tenant DBs (`provclient`/`provfail`) but never removed
the rows it `create()`s in the **platform DB** — the `PROV`/`FAIL` subscription plans, the two
tenants, and their jobs. The next run's `create({'code': 'PROV'})` collided on the unique
code. A Rule-12 violation, same class as R-002.

**Guard.** `platform_cleanup()` in `verify_provisioning.sh` ORM-unlinks jobs → tenants → plans
scoped to `provclient`/`provfail` + `code IN ('PROV','FAIL')`, at startup (self-heals a prior
aborted run) and end — with `active_test=False` (an archived leftover still collides), a
`PLATFORM_DB` test-DB guard (it deletes rows, so it refuses a non-test DB), and loud-fail on
error (Rule 10). Proven: the script and `make verify-all` each ran twice green. The
verify-runner idempotency double-run (run twice; second = no-op) remains the standing
detection for this whole class.

---

## R-017 — Reused e2e fixtures kept yesterday's schema; every model change broke `verify-all` locally ✅ FIXED (#264)

**Symptom.** After a branch added fields to `ncollection.tenant`, `make verify-all` failed at
the e2e stage with

```
psycopg2.errors.UndefinedColumn: column ncollection_tenant.config_sync_activity_id does not exist
```

raised from inside `setup_e2e_tenants.sh`'s seed block — whose output goes to `/dev/null`, so
the visible failure was a bare `make: *** [e2e-verify] Error 1` with no cause. It bit twice on
the same branch: once for the four fields the feature commit added, then again for the fifth
that the review commit added.

**Root cause.** `create_tenant()` skipped creation when a fixture already existed
(`tenant_provisioned` → "skip create") and the `e2eadmin` block skipped install when
`ncollection_saas` was already installed — but **neither ever ran `odoo -u`**. A reused fixture
therefore keeps the schema it was built with, while the code under test has moved on. Same
class as R-012 (a module's dependency grew and existing databases never got it): state that is
reused across runs must be *reconciled*, not assumed current.

**Why CI never caught it.** The `test` and `verify` jobs build fresh databases every run, so
the reuse path does not exist there. This was structurally invisible to CI and hit only local
runs — i.e. every developer, right after pulling a schema change.

**Guard.** Both reuse paths in `e2e/scripts/setup_e2e_tenants.sh` now run `odoo -u` on the
`ncollection_*` modules before seeding (derived from the module list already passed in; crm/sale
are not re-upgraded, since they do not drift), and both **fail loud** with the log tail and a
pointer to `make e2e-clean` instead of `>/dev/null 2>&1` (Rule 10 — a swallowed failure here is
what made the original symptom unreadable). Proven rather than asserted: `config_sync_activity_id`
was dropped from `e2eadmin` by hand (5 `config_sync*` columns → 4), the script re-run, and it
exited 0 having healed all three fixtures back to 5.

---

## R-032 — "UncaughtPromiseError > InvalidStateError: The database connection is closing" on every screen ✅ FIXED

**Symptom.** The web client's error dialog: `Failed to execute 'transaction' on 'IDBDatabase': The database
connection is closing`, stack `web.assets_web.min.js` (an `idb-keyval` frame, line 15596 of bundle `cc2d725`).

**Root cause.** Not NCollection code and not Odoo's `@web` IndexedDB layer (that one is mutex-serialised and
reopens per call). It is the *mail* module: `@mail/core/web/store_service_patch` builds ONE idb-keyval 3.2.0 `Store`
at load time. That library opens a single `IDBDatabase` connection in its constructor and keeps it for the life of
the page; `Store.updateAppBadge()` then calls `idbKeyval.set("unread", n, store)` and drops the promise. When the
browser closes that connection under the page (site data cleared or evicted, a frozen/restored tab, another context
deleting the database) the next badge update runs `db.transaction()` on a closing connection and nothing handles the
rejection. Intermittent by nature: a clean profile with 25 navigate/reload cycles never raises it; closing the
connection and updating the badge raises it every time (reproduced RED before the fix).

**Fix.** `ncollection_branding/static/src/js/mail_unread_store_patch.js` overrides only `updateAppBadge`: on
`InvalidStateError` it drops the dead store and reopens ONCE. Any other error is not swallowed and a second failure
is not retried (no reconnect loop, no leaked connection). IndexedDB stays enabled; nothing in Odoo core is edited.
A browser holding the OLD bundle keeps the old behaviour until the new bundle (new hash) is loaded: one hard reload.

**Guard.** `e2e/tests/idb-unread-store.spec.ts`: simulates the browser closing the connection and asserts no uncaught
error and that the value is written through the reopened connection; plus repeated navigation/reload cycles.

---

## R-031 — A multi-partner statement could be e-mailed to every partner, and a report could be built for a company the user is not in ✅ FIXED

**Symptom.** (1) "Send by Email" on a statement run for several customers attached ONE PDF holding every
customer's statement and addressed the composer to all of them: each customer would have received everyone
else's balances. (2) `ncollection.account.report.*.create({'company_id': <another company>})` succeeded; the
company check lived only in `nc_report_run`, `AFTER` the wizard was created, while the letterhead (name,
address, tax id, logo) is read from the wizard's company.

**Root cause.** (1) `action_send` was written for one partner and never asked how many documents the run held.
(2) The rule was enforced in one entry point instead of on the model. An `@api.constrains` cannot do it: Odoo
runs constraints as superuser, so `env.user.company_ids` is every company.

**Fix.** `action_send` refuses a run of more than one partner with an actionable (translated) message.
`NcollectionAccountReport.create`/`write` call `_check_company_allowed` (sudo callers exempt), so it holds for
every report model and every way of creating or editing a wizard.

**Guard.** `test_partner_statement.py`: `test_send_refuses_a_multi_partner_run...`,
`test_the_multi_partner_refusal_is_actionable_in_english_and_arabic`. `test_print_system.py`:
`test_a_user_of_company_a_cannot_print_company_b`, `test_every_report_model_refuses_a_company_the_user_is_not_in`
(a model that overrode `create` without `super()` would fail it), `test_a_user_of_both_companies_prints_each_company...`.

---

## R-030 — Every printed report drew a hairline box around every cell, and a PDF per partner hung wkhtmltopdf ✅ FIXED

**Symptom.** After the print redesign the first PDFs looked "boxed": hairline outlines around every table
cell although every border was set to `0`. Separately, a statement run for all customers (header and
footer repeated once per partner) never finished (>120 s) where one partner took 2 s.

**Root cause.**
1. **`border-collapse: collapse` in wkhtmltopdf 0.12.6 (patched Qt, PDF backend).** With a collapsed border
   model the backend strokes every cell edge as a hairline *even at `border: 0`*. Found by bisecting the
   stylesheet with `wkhtmltopdf` run directly, not through Odoo. Browsers do not show it, so an HTML
   preview of the same template looks clean.
2. **One layout per partner.** wkhtmltopdf's cost grows with (header/footer instances × pages); N layouts for
   N partners multiplied it. The same engine ignores flexbox, grid, CSS variables and logical properties
   (`margin-inline`, `inset-*`) and has no `unicode-bidi: isolate`.

**Fix.** Every table uses `border-collapse: separate; border-spacing: 0` plus `border="0" cellspacing="0"
cellpadding="0"` and a reset rule; a statement is ONE layout for the whole run with
`page-break-before: always` per partner (all-customers run: 81 pages ~9 s vs ~27 s before; 120 partners /
120 pages 13 s; 152-page General Ledger 25 s, linear at ~0.17 s/page). Bidi: dates are wrapped in an LRE…PDF
run so an Arabic page does not print the end date first.

**Guard.** `ncollection_account_reports/tests/test_print_system.py`:
`test_css_stays_inside_what_wkhtmltopdf_can_draw` (bans `border-collapse: collapse`, flex/grid/var(--)/
logical properties/`isolate`), `test_every_table_switches_the_border_attributes_off`,
`test_a_statement_renders_one_layout_for_the_whole_run`,
`test_dates_are_left_to_right_islands_so_arabic_pages_keep_their_order`.

**Known limit (documented, not fixed).** In an RTL cell the Qt bidi engine places a leading account code to the
LEFT of an Arabic name but to the RIGHT of an English one ("Main Safe 101005" vs "حساب تحويل وسيط 100001").
Both read correctly; `isolate`, RLM and RLE do not change it.

---

## R-029 — "Arbitrary Uncaught Python Exception" on PDF export was not Python, and dev PDFs were silently unstyled ✅ FIXED (client) / ⚠️ STRUCTURAL (the download manager)

**Symptom.** Print → PDF showed `RPC_ERROR — Arbitrary Uncaught Python Exception`. The previous fix (a QWeb `%` formatting
crash) was real but did not touch it. Separately, PDFs generated in development had no letterhead, no borders and
paginated differently (377 pages instead of 232 for the same ledger) — with exit status 0.

**Root cause — two independent things.**

1. *Internet Download Manager* hooks the network stack of every browser on the machine and answers `POST /report/download`
   with `HTTP/1.0 204 Intercepted by the IDM Advanced Integration` and an empty body (captured with the Chrome DevTools
   Protocol; Odoo's own log shows `200` and "The PDF report has been generated"). Odoo's client helper treats any non-200 as a
   server error and, failing to parse JSON from an empty body, prints that literal message. There is no Python exception.
2. wkhtmltopdf runs *inside* the odoo container and loads the stylesheet/letterhead from `<tenant>.localhost`. glibc in the
   container does not resolve `*.localhost` (browsers do), so every asset fetch failed (`wkhtmltopdf: Exit with code 1 due to
   network error: HostNotFoundError`) and Odoo still returned an unstyled PDF with exit 0.

**Guards.** (2): `scripts/dev/pdf_hosts.sh` (`make pdf-hosts`, idempotent — 17 added, then 0) + a `make doctor` check that fails
loudly where the PDF would silently degrade. (1): the viewer catches the failed download, explains it (Arabic included) and
opens the report in a tab. **No guard can be built for the download manager itself** — it is software on the user's machine; the
remedy (exclude `localhost` in IDM) is documented in `e2e/manual/reports/README.md` and CLAUDE.md. `pdf_quality.js` /
`xlsx_quality.js` inspect the real files (pages, size, text, RTL, numeric cells) so "a PDF came back" is no longer the test.

---

## R-028 — The OCA ledger wizards were open to every internal user ✅ FIXED

**Symptom.** None visible. A server-side audit as each role found that a **Sales** user and a **Manager** could create the OCA
General Ledger and Trial Balance wizards and read the whole ledger over RPC, contradicting ROLE_MATRIX D5.

**Root cause.** `account_financial_report` grants its wizards to `base.group_user`; the menus were hidden from non-accountants, and
a menu is not a control. Core also grants those roles read on `account.move.line`, so the wizard ran.

**Guard.** `_nc_harden_oca_wizards` moves the six wizards to Accounting read-only on every registry load (an OCA upgrade resets
them, the hook re-applies). `tests/test_oca_wizard_access.py` *attempts* the create as a plain internal user and as an
accountant, proves idempotence and the post-upgrade reset, and fails (7 failures) if the hardening is removed — measured.
Open decision: core's own read on journal items for those roles (D-2 in REPORTING_ARCHITECTURE_RECOMMENDATION.md).

---

## R-027 — Sales, Purchases and Inventory reports existed but no role could open them ✅ FIXED (Sales, Inventory) / decision open (Purchases)

**Symptom.** The user reported Sales, Purchases and Inventory reporting as missing. They were not missing: they were invisible.
Every earlier test passed because it ran as `admin`.

**Root cause.** The native analysis menus are gated on Odoo's *Administrator* groups (`Sales / Administrator`, `Purchase /
Administrator`, `Inventory / Administrator`). No NCollection role carries them, so the three hub categories were empty for Owner and
CEO too. A superuser holds every group, so an admin-run check proves nothing about roles.

**Guard.** A curated menu→group map (`_REPORT_MENU_GROUPS`) aligns each menu to the least group that already holds the ORM read right
(no data access granted; the hub still hides a card whose model the user cannot read). `test_report_hub.py` pins: the Administrator
gate is replaced, a missing module never widens a menu, every curated menu is catalogued. `roles_ui.js` opens the hub as each of the
8 roles in a real browser and asserts the exact categories. **Purchases** stays empty for Owner/CEO/Manager because no role holds a
Purchase group — a role-matrix change (D-1), not taken unilaterally.

---

## R-026 — The report service accepted another user's run id ✅ FIXED

**Symptom.** `nc_report_export` / `nc_report_detail` returned an export action for a run that belonged to someone else.

**Root cause.** `browse(id).exists()` skips record rules, so a foreign id was "found". The download routes refused it (403 for every
role but the creator, owner and CEO included — measured), so no data leaked; the service was simply not the place that said no.

**Guard.** `check_access('read')` after `exists()`; `test_tax_analysis.test_another_accountant_cannot_read_my_run` asserts the
`AccessError` through the service.

---

## R-025 — A guard's regex could not see the file it was meant to guard ✅ FIXED (#343)

**Symptom.** None, again — and from the guard's side, worse than none: a clean report.

`invariants.py` R5 was added by #337 to stop Odoo containers running the crons of every
database on the server. `docker-compose.saas.yml`'s `provisioning-runner` had the identical
defect and was **not** caught. Measured: R5's matcher

```python
ODOO_COMMAND_RE = re.compile(r"^\s*command:.*\bodoo\b")
```

matches **0 lines** in that file, because the command is written as a folded YAML block:

```yaml
command: >
  odoo -c /etc/odoo/odoo.conf
       --max-cron-threads=1
       --db-filter=^${PLATFORM_DB:-ncollection}$$
```

The flags live on continuation lines. Even had someone added the file to `CRON_SCOPED_FILES`,
R5 would have reported its "the command is gone" fallback rather than checking anything.

**Root cause.** A **line-shaped rule guarding a block-shaped setting.** The rule's unit of
analysis was one line; the thing it governs is a multi-line YAML scalar. That mismatch is
invisible in review, because the regex is correct for every file it was written against —
and both files it was pointed at happen to use the inline form.

Worth separating from the cron bug itself: #337 was a defect, #343's *interesting* half is
that the guard written to prevent a recurrence had a structural blind spot for the very next
instance. A guard's coverage is not what it checks; it is what it can **see**.

**The guard that now prevents recurrence.** `invariants.py` **R6**
(`rule_cron_enumeration_declared`):

- gathers folded (`>`) and literal (`|`) `command:` blocks before matching, so the unit of
  analysis is the command, not the line;
- scans **every** `docker-compose*.yml`, not a hand-maintained file list — the other half of
  why R5 missed this;
- treats a **missing** `--max-cron-threads` as cron-enabled, because it inherits
  `max_cron_threads = 1` from `odoo.prod.conf`. "Absent" and "zero" are not the same, and
  conflating them would have skipped prod and staging entirely;
- requires `-d`, or an entry in `CRON_ENUMERATORS_ALLOWED` **with a written reason**, so a
  container that legitimately serves every tenant's crons (`odoo-bus`, prod, staging) is a
  recorded decision instead of looking identical to the accident.

RED-proved end to end against a temporary repo root: fires with `-d` removed, silent with it
present, silent when `-d` is removed but threads are `0` (correctly R5's territory).

**Runtime proof:** `make cron-scope-verify` (in `verify-all`), two arms one flag apart on a
private Postgres — see `DESIGN_CRON_AND_QUEUE_TOPOLOGY.md` §8.4.

---

## R-024 — A whole module's test suite had never run in CI, and nothing was red ✅ FIXED (#328)

**Symptom.** None. That is the entry.

`ncollection_account_dashboard` was created by #119 (F3-T01) with a full test suite.
It appeared in **neither** `ci.yml`'s `-i` install list nor its `--test-tags`, and **no
module declares a manifest dependency on it**, so CI never installed it and never ran a
single one of its tests. Every PR touching it went green on the strength of tests that
never executed.

It surfaced only sideways. While building #56 (P4-T03) on top of that module, two
reviewers independently found a real crash on the happy path:

```
AttributeError: 'tuple' object has no attribute 'get'
```

`ncollection.aggregation.engine.aggregate()` returns rows as **tuples** (`engine.py`
builds them with `tuple(self._flatten_cell(cell) for cell in row)`); the consuming code
called `row.get(...)`. Any tenant with CRM/Sales installed and populated would have
500'd the entire CEO dashboard — including the financial KPIs that are specifically
designed to survive a missing panel. The `albarari` demo tenant had 6 qualifying
`sale.order` records, so it would have crashed there immediately.

**Root cause — two independent failures that lined up.**

1. **The module was invisible to CI.** Coverage is expressed as two hand-maintained
   comma-separated lists in `ci.yml`. Nothing reconciled them against
   `custom_addons/`. A module absent from both is silently skipped.
2. **The test that should have caught the crash agreed with it.** The mock returned
   *dict*-shaped rows, encoding the author's assumption rather than the engine's
   contract, so it went green against the bug. Even had CI run it, it would have
   passed.

Failure 1 is why nobody noticed failure 2.

**Why it is dangerous, not just untidy.** Every other guard in this repo announces
itself when it trips. This one produced *no signal at all*: the build is green, the
test count looks plausible because it is buried in a 798-test total, and the only way
to detect it is to diff `ci.yml` against `ls custom_addons/` by eye. It also recurs by
**default** — every new module starts uncovered, so the next one repeats this exactly
unless someone remembers.

**Fix.** Two parts, deliberately split:

- **#329** added the module to both lists. Proven, not assumed: the CI log for that run
  contains `ncollection_account_dashboard: 15 tests` — a line that had never appeared in
  any CI log before — with the full suite at 798/798.
- **#328** added the guard below, so it cannot recur silently.

**The guard.** `scripts/ci/invariants.py` **R4** (`rule_ci_module_coverage`): every
directory under `custom_addons/` holding an `__manifest__.py` must appear in **both**
`ci.yml`'s `-i` list and its `--test-tags`. Exemptions live in `CI_EXEMPT_MODULES` and
each **must** carry a reason — currently one entry, `ncollection_demo_freshorigin`
(demo-tenant seeding, installed via `make demo-tenant`). A module that *is* covered
while still listed is reported as a **stale exemption**, so the allowlist cannot rot
into a second hiding place.

Two design points worth keeping:

- R4 is a **whole-repo set comparison**, not a per-line pattern like R1–R3, so it runs
  once from `main()` and **runs even under `--files`**. This is load-bearing: adding a
  module without touching `ci.yml` is precisely the change whose diff does *not* contain
  `ci.yml`, so a diff-scoped version could never see its own bug — the same trap as
  [R-023](#r-023).
- If the lists cannot be located at all (someone reformats the odoo invocation), it
  emits **one** clear finding saying so, rather than reporting every module as uncovered
  and burying the real problem.

RED-proved on five paths, each in isolation: missing from `--test-tags` only; missing
from both; a brand-new module nobody wired in; a stale exemption; and a reformatted
workflow.

**Transferable lesson.** *A passing test suite proves nothing until you have confirmed
it ran.* Coverage expressed as a hand-maintained list will drift from reality, and the
drift is silent by construction. Same family as [R-022](#r-022) (tests in the wrong
class, never collected) and [R-023](#r-023) (a guard that could not see the branch it
guarded): in all three the check existed, looked healthy, and was inspecting nothing.

---

## R-023 — `architecture-guard` was red on `develop` itself, and neither CI nor the canary could ever see it ✅ FIXED (#304)

**Symptom.** Running the guard locally on a clean `develop` failed with two two-layer violations
in `ncollection_reseller/tests/`. Every PR was green. The canary was green. `develop` was red.

**Root cause — two independent blind spots that lined up.**

1. **The guard is diff-scoped.** CI runs
   `architecture_guard.py --base origin/${{ github.base_ref }}` — it only ever inspects files the
   PR touched. A violation already sitting on `develop` is invisible by construction. The canary
   was assumed to cover this, but the canary re-runs `verify.yml`, and **`verify.yml` has no guard
   step at all** — so Rule 3 was never re-checked against `develop`'s actual tip after any merge.

   This is on the record, not hypothetical. Commit `c6b759f` widened `PLATFORM_ADDONS` to include
   `ncollection_reseller` and its own message says the guard *"DOES flag pre-existing
   `self.env['res.partner']` uses in two reseller test files… this PR does not modify them, so the
   diff-scoped CI guard passes."* It merged green, knowingly, and nothing surfaced them again.

2. **The rule only matched one spelling.** `TENANT_MODEL_HINTS` was built from `self.env[...]`
   only. `setUpClass` fixtures use `cls.env[...]`, so **five further identical lines** in the same
   four test files had never been flagged — before or after. The guard's coverage depended on
   which alias a test happened to use, which means "clean" was true for the wrong reason.

**The findings themselves were false positives**, verified before being silenced:
`ncollection.reseller.partner_id` is a required `Many2one('res.partner')`, `ncollection.reseller`
is admin-DB only, and `ncollection_reseller` is never in `CORE_TENANT_MODULES` (the addon set
provisioning installs into a tenant DB). So it is platform-owned data — but a guard that is red on
the mainline trains everyone to ignore it, which is the actual damage.

**The guards that now prevent recurrence.**

- `scripts/ci/architecture_guard.py` matches `cls.env[...]` as well as `self.env[...]`. Proved
  differentially: with a marker stripped, the widened rule **fails** and the old one is **clean**.
  (Aliased locals — `env = self.env` — are still missed; documented, unchanged.)
- `.github/workflows/canary.yml` gained a `guard` job that runs the rule over the **whole tree**,
  not a diff, on every push to `develop`, and is wired into `report`'s `needs` so a violation files
  a `broken-develop` issue. Proved red-then-green by stripping a marker (exit 1) and restoring it
  (exit 0). This would have caught `c6b759f` the day it landed.
- The marker gained a domain-neutral spelling, `# arch-guard: admin-db-platform`. The reseller
  fixtures were reusing `admin-db-billing`, which was a lie — nothing there is billing — and it
  would have polluted any future audit that grepped the billing marker to review billing carve-outs.

**Transferable lesson.** *A diff-scoped gate proves things about a diff, never about the branch.*
Any rule whose value is "this property holds on `develop`" needs a periodic full-tree run, or it
silently degrades into "this property holds on whatever the last PR happened to touch."

## R-022 — Five tests were written into the wrong class and never ran; the local command hid them, CI would not have ✅ FIXED (#286)

**Symptom.** On #278 I added five tests to `test_config_sync_health.py`, placed them by mistake
in `TestRequiredCronHealth` (which has no `_push` helper, so every one of them would have
errored), and the suite reported

```
0 failed, 0 error(s) of 19 tests
```

Nineteen — exactly the count from *before* the five were added. I only noticed by comparing the
collected count against the previous run.

**The issue I filed for this blamed CI, and that was wrong.** #286 asserted "CI would not have
caught it either". It would have. CI runs `--test-tags /ncollection_core,/ncollection_saas,…` —
**module** scoped, no class — so the misplaced class *is* collected and the methods *do* run.

Proven by planting a method that only works in the other class and running both forms against
the same database:

| Command | Tests | Planted method | Exit |
|---|---|---|---|
| `--test-tags /ncollection_saas:TestConfigSyncHealth` (what I ran) | 41 | never ran | 0 |
| `--test-tags /ncollection_saas` (what CI runs) | 202 | **ERROR** | **1** |

**Root cause.** Not a CI gap — a local habit. A **class-scoped** `--test-tags` filter silently
excludes anything outside that class, so "0 failed" says nothing about code you just wrote into
a neighbouring class. The narrower the filter, the more confident and the less informative the
green.

**Guard.** Two parts, because the honest fix is smaller than the issue implied:

1. `architecture_guard.check_test_collectability` — every test class must carry a `@tagged`
   whose phase a module-scoped run selects. It does **not** re-implement Odoo's tag algebra; it
   defends the uniformity that makes CI's coverage true. All 77 classes currently use
   `post_install, -at_install`; a class with no `@tagged`, tagged `-standard`, or tagged out of
   both phases would run **nowhere** and no count would move. RED-proved against all three
   shapes; the correct shape passes.
2. The habit: **verify with the tag form CI uses** before believing a green run. A class filter
   is for iterating, never for concluding.

**What is NOT guarded.** Nothing here detects "you wrote a test in a class where it makes no
sense" — CI does that, by running it and erroring. That is sufficient and was always sufficient.

---

## R-021 — Routing CHECK 2 reported "isolation breach" when the overlay simply was not enforcing ✅ FIXED (#263)

**Symptom.** One `make verify-all` run failed the routing proof with

```
rtclienta.localhost GRANTED db=rtclientb (uid=2) — isolation breach!
```

That is the `db_filter=^%d$` check — the one whose failure mode reads as **cross-tenant
access**. It never reproduced: four subsequent runs, including CI, were 8/8.

**Root cause, now reproducible on demand.** CHECK 2 asks a host for another tenant's database
and treats a granted session as a breach. That reading is only valid while `db_filter` is
actually enforcing. Under the permissive base/dev config there is no filter, so the server
grants **correctly** — and the check called it a breach.

The trigger is narrower than the original guess. It is **not** "the overlay is down": the
script's pre-existing edge probe already exits 2 in that case, because tearing the overlay down
also removes nginx. The dangerous state is **nginx up while odoo runs WITHOUT `--db-filter`** —
exactly what happens when someone recreates only the odoo container
(`docker compose -f base -f dev up -d --no-deps odoo`) while the edge keeps running. The edge
probe passes, because nginx answers in both modes.

**Reproduced deliberately:** construct that state, run the old script → `❌ FAIL … isolation
breach!`, exit 1, with no isolation bug anywhere. Run the new script on the identical stack →
`REFUSING: the routing overlay is NOT active`, exit 2, zero FAIL lines.

**Guard.** `assert_routing_overlay_active()` runs before any check and reads **pid 1's argv
inside the odoo container** — the ground truth for what the server is actually running, unlike
the declared `Config.Cmd` or the edge probe. It exits **2**, not 1, so "did not run" stays
distinguishable from "a check failed", and the message names the fix (`make routing-up`) and
prints the argv it saw. CHECK 2's own failure text now states that preconditions were asserted,
so a future reader knows a breach reported there is real.

**Cost, recorded because it is the lesson.** Producing the broken state deliberately meant
recreating the odoo container. That left nginx holding a **stale upstream IP** (502), and the
routing fixtures had to be rebuilt via `make routing-clean` + a re-run. Three consecutive
"failures" in between were self-inflicted, not findings. This is R-018's hazard from the other
side: the agent causing the churn is just as capable of misreading its own damage as a
concurrent one is. `stack_settled.sh` correctly reported UNSETTLED throughout.

**Follow-through.** `make routing-up` does not restart nginx, so any workflow that recreates
odoo must restart nginx too, or the edge serves 502 against a perfectly healthy server.
## R-020 — A test fixture derived its DB name from `hash()`, so CI failed at random ✅ FIXED (#276)

**Symptom.** `TestSaasDashboard.test_mrr_field_normalization` errored intermittently in the
`test` job — same 581 tests, same addon code, different outcome. PR #272 passed on identical
addon code; a re-run of the failing branch passed. Postgres logged a
`ncollection_subscription_plan_code_unique` violation one second earlier.

**That Postgres line was a red herring** and it sent the original triage the wrong way. The
issue guessed a shared plan-code collision. `DASHGROWTH` is used exactly once in the entire
test suite — there is no cross-test plan-code clash. The failure was in `_tenant()`.

**Root cause.** `test_dashboard.py`'s helper built the tenant name as:

```python
'database_name': db or ('t%d' % (abs(hash(name)) % 10000))
```

Two faults in one expression:

* Python randomises **str** hashing per process (`PYTHONHASHSEED`), so the same test produced
  a different `database_name` on every run — an irreproducible fixture by construction.
* Folding into 10 000 buckets meant two distinct names could land on the same one, and
  `unique(database_name)` (`ncollection_subscription/models/tenant.py:87`) then failed the
  `create`. Order-independent, roughly one run in a few hundred, never reproducible on demand.

**Why it resisted diagnosis.** Every property that makes a flake hard was present at once: the
trigger is re-randomised each process, the symptom surfaces in a *different* test than the one
that misbehaves, and an unrelated Postgres error appeared next to it in the log.

**Guard.** The helper now uses a monotonic `itertools.count`, so names are unique **by
construction** — no hashing, no modulo, nothing probabilistic left to argue about. Plus
`test_fixture_db_names_are_unique_and_stable`, which asserts 25 consecutive fixtures get
distinct, alphanumeric names. Asserting "the last run passed" would have proven nothing; the
assertable thing is the property that replaced the gamble.

**Proven, not argued.** A seed hunt was attempted first and was *wrong* — it split
`'Expiring Co'` on whitespace, so a repeated `'Co'` token faked a collision. The real proof is
narrowing the bucket to `% 4`: the collision then fires every run and takes out six tests,
**including `test_mrr_field_normalization`** — the exact test CI reported. Restore the counter
and all six pass.

**Follow-through.** `hash()` appears in no other test fixture in the repo (checked). Fixture
identity should be a counter or an explicit literal — never a hash, which is unstable across
processes by design.

---

## R-019 — R-017's fix was applied to the e2e fixtures but not the PLATFORM db; the same schema drift killed `verify-all` again ✅ FIXED (#283)

**Symptom.** On a branch whose only schema change was two added fields on `ncollection.tenant`,
`make verify-all` failed:

```
psycopg2.errors.UndefinedColumn: column "cron_report_miss_count"
of relation "ncollection_tenant" does not exist
```

Routing passed 8/8, **provisioning failed on its first tenant create**, and config-sync, financial
bootstrap and e2e never ran at all. The failure looked like a defect in the branch. It was not:
the code was correct and the database was stale.

**Root cause.** `verify_provisioning.sh` and `verify_config_sync.sh` both run against a
**persistent** platform database (`PLATFORM_DB`, default `saastest`) and **never upgraded the
module on it**. The model gained a field; the database did not.

This is **R-017 exactly** — reused state keeping the schema it was built with — and R-017's fix
was real, but it was applied only to `e2e/scripts/setup_e2e_tenants.sh`. The identical reuse
pattern in the two P2 verify scripts was left untouched, so the class of bug was closed for one
suite and left open for two others. Nothing rediscovered it for months because no ticket in that
window added a field to `ncollection.tenant`.

**Why CI never caught it.** Same reason as R-017: the `test` and `verify` jobs build databases
from scratch every run, so the reuse path does not exist there. It is structurally invisible to
CI and hits only local runs — i.e. the Rule 13 gate every developer is required to run before
merging. A gate that fails on legitimate work is a gate people learn to route around, which is
the failure mode #221, #264 and #267 each fixed elsewhere.

**Guard.** Both scripts now call `platform_schema_sync()` before doing anything else: it runs
`odoo -d "$PLATFORM_DB" -u ncollection_saas --stop-after-init` and **exits 1 with an actionable
message** if that upgrade fails (Rule 10 — a swallowed upgrade would print the suite's own
"ready" over a stale schema, R-005's shape).

Proven rather than asserted: `saastest` was left with the stale schema (0 of the 2 new columns),
the script was run **without any manual repair**, its own new step healed the database, and the
suite reported `10 passed, 0 failed`. Re-run immediately afterwards: `10 passed, 0 failed` again
(Rule 12 — idempotent, and shown to be, not claimed).

**Follow-through.** When a guard is written for reused state, check every other reuse site in the
repo at the same time. R-017 fixed one of three.

---

## R-018 — Background agents sharing one Docker stack produced two false CRITICALs in one session

**Symptom.** Two unrelated incidents on the same day, both while background
review/verify agents ran concurrently against the single shared dev stack:

1. `verify-runner` reported 7 e2e failures including
   `CRITICAL: e2eclienta session was valid (uid=2) on e2eclientb — isolation BREACHED`
   — the single most serious class of finding this platform can produce.
2. A concurrently-running `code-reviewer` installing a module hit
   `Class ProvisioningJob has no _name → action_run is not a valid action`, could not
   reproduce it in 3 retries, and reported it as a possible real defect it could not
   explain.

Neither was real. For (1): `develop` was equally broken *before* `make routing-up`
(`clienta.localhost/web/login` → 303, e2e refused to start with "E2E stack not
ready") and 12/12 green after; the feature branch was 12/12 green too. For (2): the
error cleared once the concurrent RED-proof edit that had briefly broken the
bind-mounted module was reverted.

**Root cause.** Background agents share ONE working tree (bind-mounted live into the
odoo container — see CLAUDE.md's runtime map) and ONE Docker Compose stack, with no
isolation between them. In incident 1, `code-reviewer`'s own process notes record that
it ran `docker compose up -d db odoo` to run tests of its own, recreating the odoo
container while `verify-runner`'s e2e suite was mid-flight against that same server;
the suite's requests landed on a half-started server (403s / a database-selector page)
and the isolation assertions produced nonsense. In incident 2, a RED proof
(deliberately deleting a line to prove a test fails) broke the bind-mounted module for
~40 seconds; a different, concurrently-running `code-reviewer` installed the module in
exactly that window.

**Why it is dangerous, not just annoying.** A false CRITICAL reads exactly like a real
one — the only way to tell them apart was 30+ minutes of manual disproof. That is the
same "gate that cries wolf" failure mode this repo has already fixed in three other
places (#221 skip-as-failure, #264 generic alerting, #267 CI-only lint gate).

**Guard.**
1. **Prevention (convention — CLAUDE.md Rule 14).** An ad hoc `docker compose
   up/down/restart` (outside a suite's own documented flow, e.g. `e2e-verify`'s
   load-bearing odoo/nginx restart), or deliberately breaking a bind-mounted file for a
   RED proof, must happen **before** fanning out background reviewers/`verify-runner`,
   never during. No agent may "fix" a shared stack it doesn't own by restarting it.
2. **Detection (mechanical — `scripts/dev/stack_settled.sh`).** Read-only; reports
   UNSETTLED if `db`/`odoo` show `Up <N> second…` in `docker compose ps` — i.e. one of
   them was (re)started in roughly the last minute. Any agent can run it in ~2 seconds
   before trusting a scary finding.
3. **Retry-before-escalate (`verify-runner`).** On any suite FAIL — especially a
   CRITICAL/isolation finding — re-run that ONE suite once before reporting it as real
   (`.claude/agents/verify-runner.md`). Both false CRITICALs in this entry did not
   reproduce on the very next clean run; a real regression will.

**Guard deliberately NOT built: per-agent stack isolation.** Giving each background
agent its own `COMPOSE_PROJECT_NAME`/compose project (or git worktree) was considered
and rejected for now:
- CLAUDE.md Rule 11 / R-006 already documents that hardcoded `ncollection-*` container
  names break under a non-default project name — every script that shells out to
  Docker would need a fresh audit to stay correct under N concurrent stacks, which is
  a materially bigger change than the bug it would fix.
- It multiplies Postgres/Odoo memory and boot time by the number of concurrent agents
  on one dev machine, for a failure mode whose actual cause was an *avoidable* ad hoc
  mutation, not a structural need for concurrent Docker access.
- This repo's real workflow is one orchestrator fanning out short-lived, mostly
  *read-only* reviewers against a platform that is *already* isolated per tenant at
  the database layer (R-004's fixture namespacing). Duplicating that isolation again
  at the Docker layer, for the harness itself, would be solving the same problem
  twice for a much smaller payoff.

If concurrent background agents doing real Docker *mutation* work (not just review)
becomes routine rather than incidental, revisit this.

---

## Open items without a guard

| Item | Why no guard yet | Owner |
|---|---|---|
| **F8** — E2E gates `e2eclientb` by access-denial, not `menuVisible`, so a P1-T09 *menu-hiding* regression would slip | Depends on the menu-root behaviour for group-holding users | DEV-2 |
| **R-011** enforcement | Requires a paid GitHub plan | Omar |

`KNOWN_PENDING` in `scripts/ci/invariants.py` is currently **empty** — no known violation is
being shipped. If an entry appears there, it belongs in this table too.

## R-033 — "A css error occured, using an old style to render this page" + a view error toast on every screen ✅ FIXED

**Symptom.** Red banner at the bottom of every backend page, unstyled/fallback look, and repeated "View error"
notifications (Arabic UI: «خطأ في الشكل…»). Reproduced on the dev `ncollection` DB and the `e2eclienta/b` fixtures.

**Root cause.** Two Sass errors, both mine: `grid-template-columns: …minmax(min(100%, 22rem), 1fr)` in
`ncollection_core/static/src/dashboard/dashboard.scss` and `max-width: min(32rem, 90vw)` in
`ncollection_sale_attachments/static/src/viewer/nc_file_viewer.scss`. Sass evaluates `min()` itself and rejects
mixed units ("Incompatible units: 'rem' and '%'", then 'vw' and 'rem'). A SCSS failure does not raise: Odoo stores a
**stub bundle** that paints the banner, and keeps serving it. It went unseen because Python tests and Playwright
`pageerror` listeners cannot see it, and because the bundle is compiled lazily on first page load.

**Fix.** `unquote("min(…)")` so the browser, not Sass, evaluates `min()`. Odoo caches the asset checksum in memory, so
the new bundle appears after the registry cache is invalidated (module upgrade of the two modules, no Docker restart).

**Guards.** `ncollection_core/tests/test_assets_compile.py` compiles the real `web.assets_web` bundle (LTR and RTL,
never the cached attachment) and fails on any compile error (RED proven: the old line fails both). `release-smoke.spec.ts`
now also fails on the banner, on a stylesheet bundle containing `css_error_message`, and on any error notification.
Rule of thumb for new SCSS: never write bare `min()/max()/clamp()` with mixed units; wrap them in `unquote("...")`.

## R-034 — Preview showed one document, Print/Download produced another ✅ FIXED

**Symptom.** The invoice's Preview looked right; the PDF that Print / Download produced was a different document: titled
"Proforma Invoice" on a POSTED invoice, and, on a dev stack, with no letterhead, no table rules and different pagination.

**Root causes (three, independent).**
1. *Two paths.* Odoo's **Preview** opens the customer-portal page (its own request: partner language, partner template,
   its own pro-forma rule; for a quotation a completely different template, `sale_order_portal_template`), while **Print**
   renders the report with the user's context. Two paths can only agree by accident.
2. *A posted invoice printed as a pro-forma.* `account.move.action_print_pdf` sets `proforma_invoice = not
   invoice_pdf_report_id`: an invoice that was never sent has no stored PDF, so Print marked even a posted one "Proforma".
3. *wkhtmltopdf could not reach the stylesheet.* The dev odoo container resolved `<db>.localhost` (`pdf_hosts.sh`), but the
   tenant URL is now `<db>.<base domain>` on port 80 (`_nc_public_origin`), a name nothing in the container resolved and a
   port nothing there listens on. Odoo returned a PDF, exit 0, unstyled (R-029 again, for the new URL scheme).

**Fix.** One path: `account.move._nc_print_report()` + `_nc_print_context()` feed BOTH `action_print_pdf` and
`preview_invoice`, and Preview opens the very document Print produces (R-035 replaced the stock `/report/pdf` URL by an owned inline endpoint; a posted
invoice is never a pro-forma, and a draft reads "Draft Invoice" in both, as the gear-menu Print does). `sale.order.action_preview_sale_order` opens `sale.report_saleorder`
the same way. `pdf_hosts.sh` maps `<db>.<base domain>` to the nginx edge's address (and `doctor.sh` checks both names).

**Guards.** `ncollection_account_core/tests/test_duplicate_and_print.py` (Print and Preview pass the same report and
context; the Preview URL serves the same document the report engine renders; a posted invoice is never a pro-forma),
`ncollection_sale_documents/tests/test_sale_documents.py` (same for a quotation, and it is not the portal URL),
`make pdf-hosts` (second run adds 0) and `make doctor`. Rule of thumb: never add a second rendering of a printed
document for "preview"; open the report.

## R-035 — Printed documents looked like raw HTML, View was not the printed PDF, and the stamp designer crashed ✅ FIXED

**Symptom.** Invoices, bills, receipts and orders came out with no letterhead, no table rules and system fonts (and, in
Arabic, a black grid on every table); the Payment Receipt listed the payment itself as an invoice-allocation row; View
opened a different rendering (or forced a download) instead of the PDF Print produces; the stamp could not be placed
per document type; and opening a stamp form in a real browser raised a server error.

**Root causes (six, independent).**
1. *wkhtmltopdf could not reach the stylesheet (environment).* `scripts/dev/pdf_hosts.sh` mapped `<db>.localhost` to
   loopback, but a tenant whose `web.base.url` has no port is fetched on :80, where nothing listens inside the container.
   Odoo returned a PDF with exit 0, unstyled (R-029/R-034 again, for another URL shape). The script now derives the
   mapping from each database's own `web.base.url` (no port -> the nginx edge, an explicit port -> loopback) and is
   idempotent.
2. *The layout itself (design).* Every company still used Odoo's stock "Light" layout and the stock receipt. The
   NCollection layout (`ncollection_branding`: `report.layout` + `external_layout_nc`, company colour validated as hex,
   RTL aware, page X / Y, stamp-safe margins) is now the default for companies that never chose one, and the receipt is
   rebuilt from the real payment / reconciliation data (what was applied, to which documents, what is not applied).
3. *Stock RTL stylesheet.* It draws a black border on every `table/tr/td`; the layout CSS resets them before opting in to
   the lines it wants.
4. *View was not the printed document.* View now opens `/ncollection/document/view/<model>/<id>` (ncollection_core): the
   same report and context as Print, `Content-Disposition: inline` with the document's filename, no stored copy needed,
   in a new tab, no popup-blocking round trip.
5. *Stamps.* `ncollection.document.stamp` templates (image, document types, mm geometry, page scope, paper format,
   priority) are applied by overlaying the PDF in `_render_qweb_pdf_prepare_streams`, per record, so View, Print, email
   and stored copies carry the same stamp at the same millimetres (tests read the position back out of the bytes).
6. *`bin_size`.* The web client reads every binary field as its size text ("9 bytes"); a computed field that
   `b64decode`d `image` crashed the stamp form. Found only in a real browser. Binary reads that decode now say
   `with_context(bin_size=False)`.

**Guards.** `ncollection_branding/tests` (`test_stamp_overlay`, `test_document_stamp` incl. the bin_size case and the preview
route), `ncollection_account_core/tests/test_document_stamp.py` (real wkhtmltopdf end to end),
`ncollection_core/tests/test_duplicate_and_print.py` family (View URL and parity), `make pdf-hosts` (second run adds 0).
Rule of thumb: a PDF is only proven styled by looking at it (render it, convert to an image, read it), and a form is only
proven by opening it in a browser.

## R-036 — The company stamp image could be read by any internal user, and the product name / favicon were hard-coded ✅ FIXED

**Symptom.** (1) The binary of a document stamp (and the company's legacy seal) had no field-level group, so any internal user could fetch the raw
seal from `/web/image/ncollection.document.stamp/<id>/image` or RPC `read`. (2) The product name, favicon and logos were literals scattered over
templates, JS and Python (`NCollection ERP`, `favicon.ico`; the shipped `favicon.ico` was a 0-byte file served as the tab icon), and some stock
pages still said "Odoo" (Settings labels, `odoo.com` documentation links).

**Root causes.** The stamp image was an unrestricted Binary; only the *record* was protected. There was no single source of truth for the product
identity.

**Fix.** `image` / `nc_stamp` carry `groups='ncollection_branding.group_stamp_manager'`; the overlay reads them with a narrow `sudo()`. The product
identity is ONE record (`ncollection.product.branding`) resolved by `nc_brand()` (a `nc_product_brand()` helper in every QWeb template, `session_info`
for the web client) and served by `/ncollection/brand/<kind>` (versioned URL, content-sniffed PNG / JPEG / WebP / ICO, no SVG), distinct from the
company brand. See `SYSTEM_BRANDING.md` (formerly `PRODUCT_BRANDING.md`). A real multi-size `favicon.ico` now ships. The `(?)` documentation links to the upstream vendor's site are not
rendered.

**Guards.** `ncollection_branding/tests/test_stamp_image_security.py` (restricted over the model, over `/web/image`, report context, cross-company,
overlay still works), `test_product_branding.py` (one record, fallbacks, content validation, versioned URLs, sign-in page carries the name and no vendor
name), `test_translation_coverage`, Playwright `product-branding.spec.ts`. Rule of thumb: protecting a record does not protect its binary fields; test
the `/web/image` URL as an ordinary user.

**Addendum to R-036 (final release gate).** (3) An Accounting Adviser, who holds the stamp-manager right through the accounting group, could create or edit a stamp for a
NON-accounting kind over RPC because only the form's domain restricted the kinds. Fixed in the model (`ncollection.document.type.manager_group_ids` +
`ncollection.document.stamp._nc_check_kinds_allowed`); guards: `ncollection_account_core/tests/test_document_stamp.py::TestStampKindAuthorisation` (the five kinds
allowed; foreign, mixed and crafted payloads refused on create / write / copy / unlink, with a hostile context; admin unaffected; company isolation kept). (4) Vendor
wording survived in module-authored mail templates, action help, portal / digest / two-factor pages and the system user's name; guards: `test_vendor_wording.py`.

---

## R-037 — Newer code under a database that was never upgraded answers HTTP 500 on the first real page ✅ GUARDED

(R-012b, R-017 and R-019 are earlier instances of the same class; this entry is the root cause and the structural guard.)

**Symptom.** After a branch checkout, a merge or a deploy, `POST /web/login` (or `GET /web/login`) answered **HTTP 500** while `/web/health` kept answering 200.
Reproduced on 2026-10-23 two ways, each on a throwaway copy of the database (the real ones untouched):

* the `ncollection` data served by the UAE e-invoicing branch's code: `POST /web/login` → 500, `psycopg2.errors.UndefinedColumn: column res_partner.nc_trn does not exist`
  (the branch's `ncollection_account_localization_uae` 1.5.0 adds stored `res.partner` fields; the database was at 1.4.0);
* a stale tenant served by `develop`: `GET /web/login` → 500, `column res_users.nc_must_change_password does not exist`, and `brand logo` → 500 on
  the missing `ncollection_product_branding` table (`ncollection_core` 1.19.0 installed, 1.22.0 on disk; six modules behind, 7 tables and 5 columns missing).
  Of 25 databases on the dev server, 18 had no `ncollection_product_branding` table at all.

**Root cause (the lifecycle, not "upgrade the module").** Odoo builds a model's SELECT from the fields its CODE declares and changes a schema only on `-u`; it **never compares
the version recorded in the database with the version on disk**. So the moment newer code is served under an un-upgraded database, every query on a changed model names a column
that is not there. Nothing warns first: the registry loads, `/web/health` is green, and the first request that reads the model dies. Three facts of this repository make it
recurring: (1) the dev stack serves ONE bind-mounted tree to EVERY database, so one `git checkout` puts new code under a dozen databases at once; (2) a running Odoo process
keeps the module code it already imported, so a checkout does not change what is served until the container restarts, and a restart then applies the new code to every database
simultaneously (the symptom appears or disappears at the restart, not at the checkout); (3) no gate ever requested a page from a database behind its code:
`verify_upgrade.sh` proved migrations replay (with `--no-http`), `make doctor` warned by hand.

**What was verified not to be the cause.** Routing / proxy (the edge answered 200 for the same database under matching code), security / access rules, a frontend error (the 500
is a server response with a SQL traceback), a bad migration (the targeted upgrade of the behind modules ran clean and cured it), dependency order.

**Guard.**
1. `ncollection_core/models/schema_drift.py`: `version_drift` (code newer than the database), `schema_gaps` (stored fields / models with no column / table: the condition that actually
   500s, and it also catches a gap at an unchanged version), and a registry-load hook that logs ONE `SCHEMA DRIFT` error naming the modules and the repair, so the first line of the log is the
   cause. It never raises and never suppresses a traceback. Tests: `ncollection_core/tests/test_schema_drift.py`.
2. `scripts/upgrade/rehearse_upgrade.sh` (`make upgrade-rehearse db=<db>`): previous state (a COPY of the database, only read) → drift report → HTTP smoke (expected to 500 when stale) → targeted `-u` of
   ONLY the modules reported behind (never `-u all`, no hand-written SQL) → setup-log guard → drift report → HTTP smoke of the sign-in sequence (health, login page, POST login, web client,
   session info with the system brand, brand logo, websocket; any 5xx fails) → `REHEARSAL=PASS|FAIL`. Run on a real stale tenant it reproduced the 500s and then passed.
3. `scripts/upgrade/verify_upgrade.sh` arm 6 (part of `make verify-all` suite 7): fabricates "one release behind", asserts the report names the column and the module, that the stale state
   500s on `GET /web/login`, that the targeted upgrade cures it, and that an insufficient upgrade (the wrong module) is REFUSED; `--modules all` and a non-scratch database are refused.

**Rule of thumb.** A green `/web/health` and a green registry load say nothing about whether a database matches its code. Before serving new code to a database that has data, rehearse
its upgrade on a copy (`make upgrade-rehearse`), and never use `-u all` to "fix" it.

**Addendum: Company Entry on `atlaseg` was this same failure, plus a second defect.** Entering the company *Atlas-eg* answered
"Atlas-eg did not accept the entry (HTTP 500). Its workspace modules are probably out of date: upgrade ncollection_core on it". Reproduced on a copy of the real
database `atlaseg` (the source untouched) with the Company Entry sequence added to the HTTP smoke: the platform's grant call
(`POST /json/2/ncollection.platform.entry/grant_from_platform`, bearer-authenticated against `res.users`) answered **500**,
`psycopg2.errors.UndefinedColumn: column res_users.nc_must_change_password does not exist`. Four modules were behind (`ncollection_core` 19.0.1.18.0 < 19.0.1.22.0, `ncollection_branding`
1.8.0 < 1.12.0, `ncollection_auth`, `ncollection_account_core`). After ONE targeted upgrade of those four the whole entry passed: grant, bridge, redeem (303 to `/odoo`), tenant Home in the right
database as `Platform Admin: ...`, return to the platform address, signed out, token single-use. So it is R-037, not a routing, token or session defect.
*Second defect:* the on-screen remedy, "upgrade ncollection_core on it (Fleet Migration)", **does not work**. The same copy upgraded with `-u ncollection_core` alone aborts with
`ParseError` in `ncollection_core/views/workspace_admin_views.xml` (its menu references `ncollection_branding` records that only the branding upgrade creates), leaving the tenant on 500, and Fleet Migration
runs exactly the module list it is given. The message now says to upgrade ALL of the tenant's NCollection modules together (`test_the_remedy_for_a_tenant_that_is_behind_is_all_of_its_modules_not_core_alone`).
*Further guards:* `rehearse_upgrade.sh` runs the Company Entry probe on any database that holds the platform's service credential and prints the server's own exception lines; `verify_upgrade.sh` arm 6
asserts the stale tenant 500s on the grant and the upgraded one completes the entry; arm 7 and `make upgrade-preflight db=<db>` (`scripts/upgrade/preflight_versions.sh`) are the READ-ONLY release gate
against a real database (exit 1 names the modules behind, exit 2 is "could not check", never a silent pass); `verify_provisioning.sh` asserts a freshly provisioned tenant starts with no drift and the canonical portal URL.
Procedure: `docs/markdown/RUNBOOK_RELEASE_UPGRADE.md`. Two more ways the same symptom arrives, now written down: the persistent platform fixture `saastest` behind new code, and a long-running odoo container still
holding the old Python while a freshly provisioned tenant loads the new templates.

---
## R-038 — The production domain was a literal in the edge, and Odoo's port was reachable around it ✅ GUARDED

**Symptom / risk found while preparing the production domain.** The application builds every tenant address from one configured base domain, but the production nginx config named
`ncollectionerp.com` twice in `server_name` and in the certificate path (`.env.example` said so: "currently hardcodes ncollectionerp.com ... templating is a future enhancement"), so the edge could serve
exactly one domain and a new one meant editing the file. The same audit found that production runs Odoo with `proxy_mode = True` (it trusts `Host` and `X-Forwarded-*` from whoever connects) while the
base compose file publishes `8069:8069` and the production override reset only Postgres' port: nginx was not the only way in.

**Root cause.** The domain was made configuration in the application (#476) but never in the deployment layer, and nothing scanned the infrastructure for it; the published port came from a development
default that the production override never took back.

**Guard.**
1. `nginx/conf.d/ncollection.prod.conf` is now an envsubst template (only `${DOMAIN}` is substituted, `NGINX_ENVSUBST_FILTER=^DOMAIN$`); `docker-compose.prod.yml` takes `DOMAIN` (required) and passes it to nginx and,
   by default, to Odoo's `NC_BASE_DOMAIN`. `make edge-verify` (`scripts/deploy/verify_edge_domain.sh`) renders it in the real nginx image for `balancecloud.ae` and a second domain and runs `nginx -t`.
2. `scripts/ci/invariants.py` `rule_no_hardcoded_tenant_domain`: the retired production domain may not appear in code or configuration (comments, tests, migrations, translations and docs are out of scope); the
   development domain may not appear in production-facing files; a stale exemption fails. Its tests run before the guard (`scripts/ci/test_invariants.py`).
3. `rule_prod_odoo_is_not_published`: the production override must say `ports: !reset []` for the odoo service. Applied in `docker-compose.prod.yml`.
4. A tenant can never take the apex's first label (the platform's own database under `db_filter ^%d$`): `_nc_subdomain_availability`.
5. `ncollection_saas` `test_base_domain_switch`: for a new tenant, every generated address (tenant URL, Company Entry URL and service Host, provisioning portal URL, the seed's `NC_PORTAL_URL` that becomes the tenant's
   `web.base.url`, domain FQDN, signup login link) follows the configured domain across `ncollection.localhost`, `balancecloud.ae` and `example.org`, and names no other.
Contract: `docs/markdown/DEPLOYMENT_DOMAIN_CONTRACT.md`.

**Rule of thumb.** A setting that is configuration in the code is not configuration until the edge, the compose files and the certificate path take it from the same place; and a proxy-trusting server must not be
reachable except through the proxy.

## R-039 — The release gate covered HTTP only: crons (and the `-d` preload) ran a database the code was ahead of ✅ GUARDED

**Symptom.** While the real `ncollection` platform database was `UPGRADE_REQUIRED` and its sign-in page answered the
gate's 503, "NCollection: advance fleet migrations" kept running on it every minute (found during the 2026-10-24
platform release). Its registry - the stale code against the older schema - was loaded and its jobs executed.

**Root cause.** Two, stacked. (1) The gate wrapped only `odoo.http.Request._serve_db`; cron runners never make a
request: both enter a database through `IrCron._process_jobs(db_name)`, and a process started with `-d` loads the
registry at boot through `preload_registries`. (2) The process that actually runs the platform's crons, the
provisioning runner, starts with `--load=base,web,queue_job`; `--load` REPLACES `server_wide_modules`, so
`ncollection_core` - the module whose `post_load` installs the gate - was never loaded there: that process had no gate
of any kind, HTTP included.

**Guard.**
1. `ncollection_core/release_gate.py` wraps `IrCron._process_jobs` (entry), `IrCron._acquire_one_job` (before each job)
   and `odoo.service.server.preload_registries`; only READY runs crons, `-i` / `-u` / `--test-enable` preloads are never
   gated, each seam refuses LOUDLY if Odoo moves it. Unit tests: `tests/test_release_gate_cron.py`.
2. `docker-compose.saas.yml`: the runner's `--load` includes `ncollection_core`.
3. `scripts/ci/invariants.py` `rule_odoo_processes_load_the_release_gate`: every `server_wide_modules` and every compose
   `--load=` must include `ncollection_core` (tests in `scripts/ci/test_invariants.py`, run before the guard).
4. `scripts/upgrade/verify_upgrade.sh` arm 9 + `scripts/upgrade/cron_gate_probe.py`: Odoo's real
   `IrCron._process_jobs`, in a process booted like a server, against real fixture databases: a stale database loads
   no registry and runs no job while its READY neighbour in the same process runs; every hold, a module mid-upgrade and
   a database with no module table run nothing; READY again runs again.

**Rule of thumb.** A gate on the request path is not a gate on the database: list every way a process reaches a
registry (request, cron, boot preload) and every process that does it (including the ones started with their own
`--load`).

## R-040 — A renamed label kept its OLD translation on every existing database ✅ GUARDED

**Symptom.** After the release that renamed the platform app to "Business Management" (with "إدارة الأعمال" in
`ar.po`), the Arabic interface of the platform still showed "NCollection SaaS". English was right.

**Root cause.** Odoo loads a module's `.po` on upgrade with `overwrite=False`
(`loading.py`: `overwrite_existing_translations`, off by default). A record whose English source changes keeps every
other language it already had; the new msgid's translation is "already there" and is skipped. Any label we rename is
therefore stale in Arabic on every database that existed before the rename, and correct on a new one - which is why
new-tenant tests never saw it.

**Guard.** Every module whose labels change in a release reloads ITS OWN terms with overwrite in its post-migrate
(`migrations/<version>/post-migrate.py` in ncollection_core, _branding, _account_core, _saas, _subscription:
`ir.module.module._load_module_terms([module], langs, overwrite=True)`), through Odoo's own loader, nothing else.
Proven on the real platform database after its upgrade (the menu's `ar_001` value).

**Rule of thumb.** Renaming a translated label is a data migration, not only a `.po` edit.

## R-041 — "Add User" failed before the dialog opened: required value for field "name" is missing ✅ GUARDED

**Symptom.** Tenant > Users & Access > New user: `Create a tenant user (ncollection.tenant.user.create): Cannot complete
operation: required value for field "name" is missing.` No dialog was ever shown.

**Root cause.** `TenantUserCreate._nc_open` CREATED the wizard record (tenant and offered roles only) and opened it by
`res_id`. `name` and `login` are `required=True`; only the operator can type them, after the dialog opens. The
existing test created the wizard directly with a name, so the open path was never exercised.

**Guard.** The dialog opens with `context` defaults and no record (`wizards/tenant_user_dialogs.py`); a role is
required; `test_tenant_admin.py::test_add_user_opens_an_empty_dialog_the_operator_fills_in` drives the real button
(`action_create_user`) and `default_get`, then creates, and `test_a_user_needs_a_role`.

**Rule of thumb.** A wizard with required user input is opened with defaults, never pre-created; test the button.

## R-042 — Every email left "from noreply@ncollection.com", and Add User failed in a platform entry session ✅ GUARDED

**Symptom 1.** Workspace and platform emails were sent as `noreply@ncollection.com` (filter `ncollection.com`) on every
database, whatever the system or company was called.

**Root cause 1.** `ncollection_branding/data/system_parameters.xml` seeded `mail.default.from` / `mail.default.from_filter`
with the old vendor address (noupdate, so it stayed forever).

**Guard 1.** No sender is seeded any more; `ncollection.workspace.config._nc_clear_placeholder_mail_sender` removes the
two parameters where they still hold exactly the shipped value (tenants: on every company-identity push; the platform:
ncollection_saas 6.23.0 post-migrate). A sender an operator set is kept.
`test_platform_identity.py::test_the_placeholder_sender_is_removed_and_an_operators_sender_kept`.

**Symptom 2.** Workspace Administration > Users > Add User, from a Platform Administrator's Enter Company session:
"A platform administrator session cannot set passwords."

**Root cause 2.** `_nc_create_workspace_user` always wrote a password, and a proxy may write none (the credential would
outlive the session). The platform's own Users & Access already creates users with a generated temporary password that
must be changed; the workspace path had no such branch.

**Guard 2.** For a proxy actor the user is created with a server-generated temporary password and
`nc_must_change_password` forced on; a typed password is still refused.
`test_workspace_admin.py::test_a_platform_administrators_entry_session_cannot_set_a_password_here`.

**Rule of thumb.** A vendor address in seed data is identity, not configuration: never seed one.

## R-043 — Odoo's own Delete removed a tenant record and left its database and files behind ✅ GUARDED

**Symptom.** Tenants > Actions > Delete (Odoo's generic delete, or an RPC `unlink`) removed the platform's tenant row; the
PostgreSQL database and the filestore stayed, routable by host and owned by nobody.

**Root cause.** `ncollection.tenant.unlink` only purged the backup files; nothing tied the record's removal to its database.

**Guard.** `ncollection.tenant._nc_refuse_orphaning_unlink`: a tenant whose database (`pg_database`) or filestore still
exists cannot be unlinked by anyone, the superuser included. Delete Tenant drops both first, then unlinks.
`test_tenant_deletion.py::test_a_plain_unlink_cannot_orphan_a_live_database`.

**Rule of thumb.** A record that stands for an external resource is removed by the flow that removes the resource.
