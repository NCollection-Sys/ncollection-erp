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
| Fixture namespace separation | `Makefile`, `e2e/` | structural |

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
re-created `clienta`/`clientb`/`admin` on **every** invocation, wasting minutes and churning
fixtures. Undetected for weeks.

**Root cause.** `db_exists()` ran `psql -U odoo` with **no `-d`**. Postgres CLI tools default
the target database to the **username**; no database `odoo` exists, so the query always died
with `FATAL: database "odoo" does not exist`, and the function always returned false.

**Guard.** `invariants.py` R1, enforced in CI and pre-push. **Proven to bite**: reintroducing
the bug fails the guard with a precise message.

---

## R-003 — `pg_isready` healthcheck FATAL spam ⚠️ STILL OPEN

**Symptom.** `ncollection-db` logs `FATAL: database "odoo" does not exist` every 10 seconds.

**Root cause.** Same trap as R-002: the healthcheck is `pg_isready -U ${DB_USER:-odoo}` with no
`-d`. Cosmetic only — `pg_isready` needs just a *response* to confirm liveness, so the
container stays healthy.

**Status.** **Not yet fixed.** It is registered in `KNOWN_PENDING` in `invariants.py` rather
than silently exempted, so the guard stays honest about it. The fix is one line
(`-d ${DB_NAME:-postgres}`) but it edits the healthcheck that gates
`depends_on: service_healthy` — a slip there stops the whole stack booting — so it needs its
own change with a full `down`→`up` proof.

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
| Routing (P1-T06) | `clienta` · `clientb` · `admin` | `make routing-clean` |
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

## Open items without a guard

| Item | Why no guard yet | Owner |
|---|---|---|
| **R-003** `pg_isready` healthcheck | One-line fix, but it gates `depends_on: service_healthy`; needs its own change with a full `down`→`up` proof | DEV-1 |
| **F8** — E2E gates `clientb` by access-denial, not `menuVisible`, so a P1-T09 *menu-hiding* regression would slip | Depends on the menu-root behaviour for group-holding users | DEV-2 |
| **R-011** enforcement | Requires a paid GitHub plan | Omar |
