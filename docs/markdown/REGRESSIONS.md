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
