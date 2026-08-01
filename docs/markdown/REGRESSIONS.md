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
| `architecture_guard.py` — addon architecture, secrets, XML | `scripts/ci/architecture_guard.py` | CI + pre-push |
| Cross-suite `verify` job | `.github/workflows/verify.yml` | CI on every PR |
| Post-merge canary | `.github/workflows/canary.yml` | push to `develop` |
| Nightly drift check | `.github/workflows/nightly.yml` | cron (once on `main`) |
| Dependency/CVE watch | `.github/dependabot.yml` | weekly + advisories |
| Stale module dependencies + modules behind their code version, across all DBs | `scripts/dev/doctor.sh` | `make doctor` |
| Fixture namespace separation | `Makefile`, `e2e/` | structural |
| Config-sync push carries `Host: <db>.<base-domain>` (db_filter routing) | `ncollection_saas/tests/test_config_sync.py` + `.../scripts/provisioning/verify_config_sync.sh` | CI `test` + `make verify-all` |

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

## Open items without a guard

| Item | Why no guard yet | Owner |
|---|---|---|
| **F8** — E2E gates `e2eclientb` by access-denial, not `menuVisible`, so a P1-T09 *menu-hiding* regression would slip | Depends on the menu-root behaviour for group-holding users | DEV-2 |
| **R-011** enforcement | Requires a paid GitHub plan | Omar |

`KNOWN_PENDING` in `scripts/ci/invariants.py` is currently **empty** — no known violation is
being shipped. If an entry appears there, it belongs in this table too.
