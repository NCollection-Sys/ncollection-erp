# Testing Strategy — NCollection ERP

What we test, with what, where it runs, and **what a green result actually proves**.
Companion to `REGRESSIONS.md` (a regression is not closed until a guard exists) and
`BRANCH_PROTECTION.md` (why none of this can *block* a merge).

Written 2026-08-06 against the live tree: **81 test files · 869 test methods · 11
`verify_*.sh` proofs · 8 Playwright specs (12 tests) · 5 workflows** — and validated by a
full-estate baseline run on `958d8d6` the same day (§7).

> Every timing in this document is **measured**, not estimated. An earlier draft carried
> estimates that were wrong by 6–10×; they are replaced here with the numbers from the
> baseline run, and the one figure that remains unmeasured is labelled as such.

---

## 0. Three constraints that shape everything below

Any strategy that ignores these is a strategy for a different repo.

### C1 — Nothing here can block a merge

GitHub Free private repo: `branches/develop/protection` and `/rulesets` both return
**HTTP 403**. Required status checks are impossible. Every gate in this document is
**advisory**; the compensating control is `canary.yml` (re-verifies `develop` after
each merge, files a `broken-develop` issue) plus `nightly.yml` (catches upstream image
drift with no commit of ours).

**Consequence for test design:** favour tests that *fail loudly and legibly* over tests
that merely return non-zero. Nobody is stopped by the exit code — a human reads the
message. A failure whose output does not name the cause is worth much less here than it
would be in a repo with enforcement.

### C2 — One shared dev stack, no isolation between agents (Rule 14 / R-018)

Background agents share ONE Docker stack and ONE bind-mounted tree. A suite that
restarts the stack, or mutates databases another suite owns, produces **false CRITICALs
indistinguishable from real ones** — this has happened twice in one day (R-018), and
cross-suite fixture destruction happened before that (R-004).

**Consequence for test design:** every new suite must declare, up front:

1. **Which stack it runs on** — the shared dev stack, its *own* compose file + private
   Postgres (the `cronstall` / `cronscope` pattern), or CI only.
2. **Which database prefix it owns** — and it may drop **only** that prefix.
3. Both must be added to the fixture-ownership table in `CLAUDE.md` in the same PR.

A suite with no declared stack and no declared prefix is not accepted, however good the
test is.

The write direction of this rule is real too: creating an **untracked file** in the shared
tree while another session is working can get it swept into that session's commit by a wide
`git add`. That happened to this very document on 2026-08-06.

### C3 — db-per-tenant, and tenants install *different module subsets*

Plans map to module sets. What a real tenant installs is
`CORE_TENANT_MODULES = ('base', 'ncollection_core', 'ncollection_branding',
'ncollection_auth')` plus the plan's `allowed_module_names`
(`provisioning_job.py`) — **never** the 16-module set CI installs into `ci_test`. On top of
that, **99 of ~100 test classes are tagged `post_install`**, so the unit suite only ever
observes the fully-loaded world.

**What this does and does not mean.** Two local suites already install real tenant module
sets standalone and assert them at DB level:

| Suite | Plan combo proven |
|---|---|
| `verify_provisioning.sh` | `crm, account` — asserts `crm`, `ncollection_core`, `ncollection_auth` **and** the OCA transitive dep `auth_session_timeout` are `installed`, plus the `workspace.config` projection |
| `verify_financial_bootstrap.sh` | Enterprise: `account + account_financial_report + ncollection_mis_templates` installs cleanly on a fresh tenant and Trial Balance returns lines |

So the residual risk is **not** "untested" — it is "**two plan combos, local-only**". A third
plan whose set breaks alone would still escape. That is a small, bounded gap (**G7**), not
the top of the register. An earlier draft of this document ranked it first on the false
premise that nothing covered it; the workflow audit disproved that.

---

## 1. The layer map (what exists today)

| Layer | Implementation | Size | Trigger | Measured |
|---|---|---|---|---|
| Static gates | flake8 · pylint-odoo (baseline 54) · xmllint · shellcheck · `architecture_guard.py` · `invariants.py` | 8 checks | pre-push + PR | **8s** |
| Guard self-tests | `test_invariants.py` · `test_architecture_guard.py` | 2 | PR, **before** the guards | <1s |
| Supply chain | pip-audit · Trivy (fs: vuln + secret) | 2 | PR, **non-blocking** | — |
| Odoo ORM tests | `custom_addons/*/tests/` | 81 files · **869 methods** · 84 `TransactionCase` · 8 `HttpCase` | PR `test` job | **4m CI / 2m 8s local** |
| Infra proofs | 11 × `verify_*.sh` | 11 suites (7 in `verify-all`) | `make verify-all`, local | **8m 20s warm** |
| Browser E2E | `e2e/tests/`, chromium only | 8 specs · **12 tests** | PR `verify.yml` | **6m CI / 19s local** |
| Load / perf | k6 `load_test.js` · `bench_aggregation.py` | 2 | manual | — |
| Security audit | `phase1_security_audit.sh` · `phase3_security_assessment.sh` | 2 | manual / pre-launch | — |
| Post-merge | `canary.yml` (verify + **full-tree** guard) | 1 | every merge | ~12m |
| Drift | `nightly.yml` | 1 | 03:00 UTC | ~12m |

14 of 15 addons carry tests; `ncollection_demo_freshorigin` does not.

### The shape, honestly

This is **not** the classic pyramid. For an Odoo SaaS it is a pyramid with a sidecar:

```
              ▲  Playwright  (12)          ← correct size, do not grow much
            ▲▲▲  HttpCase + tours (8 + 0)  ← THE WEAK RUNG
        ▲▲▲▲▲▲▲  TransactionCase (84)      ← the backbone
    ▲▲▲▲▲▲▲▲▲▲▲  static guards (8+2)       ← enforce rules a test cannot express

    ├───────────┤  11 infra proofs — the sidecar a normal app does not have
```

The static base is load-bearing in a way it is not elsewhere: `architecture_guard.py`
and `invariants.py` encode **architectural** rules (two-layer separation, Odoo-19 syntax,
`-d` on psql, no `|| true` on load-bearing steps) that no unit test can express. Both
guards are themselves tested, which is why they are trusted.

---

## 2. Full taxonomy — every test type eligible for this system

Status key: ✅ have · ⚠️ partial · ❌ gap · ⛔ deliberately out of scope

### A. Static / no runtime

| Type | Tool | Status |
|---|---|---|
| Python lint & style | flake8 (`setup.cfg`, 119 cols) | ✅ |
| Odoo semantic lint | pylint-odoo via `scripts/ci/pylint_gate.sh` | ✅ |
| XML well-formedness | xmllint | ✅ |
| Shell lint | shellcheck (incl. `.githooks/*`) | ✅ |
| TypeScript types | `tsc --noEmit` (`e2e/`, `demo/`) | ⚠️ e2e in CI; demo only pre-push-if-changed |
| Architecture rules | `architecture_guard.py` — diff in PR, **full tree** in canary | ✅ |
| Infra invariants | `invariants.py` | ✅ |
| Secret scan | Trivy secret + guard | ✅ |
| Dependency CVE | pip-audit · Trivy · Dependabot | ⚠️ **non-blocking by design** |
| Dockerfile lint | hadolint | ❌ |
| **Image** scan (not fs) | `trivy image` on the built tag | ❌ |
| Nginx config validity | `nginx -t` as its own gate | ❌ (only indirect) |

### B. Unit (fast, no HTTP)

| Type | Tool | Status |
|---|---|---|
| Odoo ORM | `TransactionCase` × 84 | ✅ backbone |
| Pure Python, no DB | stdlib `unittest` | ✅ guards only (~0.02s) |
| **Clock-controlled logic** | `freeze_time` / patched `now` | ❌ **1 file of 81** — see G3 |
| OWL / JS component | Odoo 19 `hoot` (`static/tests/`) | ❌ **13 OWL files, 0 tests** — see G2 |
| React demo unit | vitest | ⛔ `demo/` is a throwaway prototype |

### C. Integration (real DB / real HTTP)

| Type | Tool | Status |
|---|---|---|
| Controller / JSON-RPC | `HttpCase` × 8 | ⚠️ thin for a SaaS with public endpoints |
| ACL & record rules | non-superuser `TransactionCase` | ✅ strong — the test env is genuinely not superuser |
| SQL constraints | `test_sql_constraints.py` × 3 | ✅ |
| Two-layer boundary (Rule 3) | `test_engine_boundary.py`, `test_boundary.py` | ⚠️ only 2 modules assert it |
| Per-plan module subset install | `verify_provisioning.sh` + `verify_financial_bootstrap.sh` | ⚠️ **2 combos, local-only** — G7 |
| **Module upgrade / migration path** | — | ❌ **G1** |
| **Local runnability of the unit suite** | — | ❌ **G4** |

### D. System / acceptance proofs (the sidecar)

| Proof | Script | Status |
|---|---|---|
| Subdomain → DB routing + isolation | `verify_routing.sh` | ✅ 8 assertions |
| Provisioning + forced-failure rollback | `verify_provisioning.sh` | ✅ |
| Config sync (plan change / suspend / reconcile) | `verify_config_sync.sh` | ✅ |
| Cron starvation (#310) | `verify_cron_starvation.sh` | ✅ private Postgres |
| Cron scope (#343) | `verify_cron_scope.sh` | ✅ private Postgres |
| Financial bootstrap | `verify_financial_bootstrap.sh` | ✅ |
| PITR restore to an arbitrary timestamp | `verify_pitr.sh` | ✅ asserts DB **as-of T** — `before` present, `after` gone |
| Tenant backup + restore | `verify_tenant_backup.sh` | ✅ asserts **real content** — row `live-workspace-data` **and** attachment `INVOICE-PDF-BYTES` |
| Edge hardening / pooling / monitoring | 3 scripts | ✅ |
| **Rollback drill** | — (`rollback.sh` exists, unverified) | ❌ G8 |
| **Tenant schema drift** | — | ❌ G6 |
| Major-version upgrade drill (19→20) | — | ❌ deferred |

> The two backup rows were marked ⚠️ ("check they assert content") in an earlier draft.
> They do. Both assert actual restored values, not exit codes. Corrected to ✅.

### E. End-to-end (browser, through the edge)

| Type | Status |
|---|---|
| Platform guarantees — auth, checkout, dashboard, isolation, license, roles, visibility, branding | ✅ **12 tests, 19s** |
| **In-product business flows** | ❌ — see G2 (tours are the right tool, not Playwright) |
| Cross-browser | ⛔ chromium only, deliberate |
| Visual regression | ❌ G9 — matters because we sell white-label |
| Accessibility (axe) | ❌ G9 |
| RTL / i18n | ⚠️ `test_rtl_conformance.py` asserts structure, never rendering |

### F. Non-functional

| Type | Status |
|---|---|
| Load / throughput (k6 VU sweep) | ✅ |
| Query budget / N+1 (100k rows < 500ms) | ✅ good pattern — extend to reports |
| Chaos / fault injection | ⚠️ partial — cron-starvation + forced-failure rollback *are* chaos tests |
| **Noisy neighbour** (tenant A saturates, measure tenant B) | ❌ G10 — core SaaS risk |
| Soak / endurance | ❌ |
| Memory-leak profiling | ❌ |

### G. Security

| Type | Status |
|---|---|
| Static secret + CVE scan | ✅ non-blocking |
| Attacker's-eye audit scripts | ✅ 2, manual |
| **DAST against the running app** (ZAP baseline) | ❌ G5 |
| Authz matrix (role × model × operation) | ⚠️ spot-checked, not exhaustive |
| Cross-tenant param fuzzing | ⚠️ a few paths in `isolation.spec.ts` |
| Rate-limit / abuse | ❌ |

---

## 3. Gap register

Ranked by **(risk × likelihood) ÷ effort**, after the 2026-08-06 workflow audit corrected an
earlier ranking. Each carries the C2 declaration it needs before anyone writes it.

| ID | Gap | Owning stack | DB prefix | Runs | Effort |
|---|---|---|---|---|---|
| **G1** | Module upgrade / migration path | CI container (fresh) | `upgr*` (ephemeral) | PR | M |
| **G2** | Odoo tours for the 13 OWL dashboards | existing CI container | reuse `ci_test` | PR | M |
| **G3** | Clock control on time-dependent logic | existing unit suite | none | PR | S |
| **G4** | Local runnability of the unit suite | dev stack | `baselineunit` | on demand | S |
| **G5** | ZAP baseline DAST | CI only, **never shared stack** | none | nightly | S |
| **G6** | Tenant schema-drift check | **own compose + private PG** | `drift*` | weekly | S |
| **G7** | A third plan's module subset | extend `verify_provisioning.sh` | existing `prov*` | pre-merge | XS |
| **G8** | Rollback drill | **own compose + private PG** | `rollback*` | pre-release | M |
| **G9** | Visual regression + axe | `verify.yml` stack | reuse `e2e*` | PR | M |
| **G10** | Noisy-neighbour load | **own compose + private PG** | `noisy*` | manual | M |
| **G11** | Coverage measurement | CI container | reuse `ci_test` | report-only | M |

### G1 — Module upgrade / migration path  ⭐ highest

**Risk.** db-per-tenant with live upgrades is the most direct route to corrupting a paying
customer's data. Confirmed by audit that **nothing covers it**:

- `deploy.sh` runs **no `-u` at all** — it deploys the image then `smoke-test.sh` curls
  `/web/health`. **Liveness, not correctness.**
- CI always installs fresh (`-i`), never `-u`.
- `ncollection.fleet.migration` — the actual fleet-upgrade engine — is unit-tested in
  `test_fleet_migration.py` but appears in **no** verify script and **no** workflow.
- The backup proofs assert data survives *backup/restore*. That is a different operation.

**Acceptance.** Install at commit N-1 → seed representative records → `-u` to HEAD → assert
(a) upgrade exits 0, (b) seeded records survive with expected values, (c) no traceback.

### G2 — Odoo tours for the OWL dashboards

**Risk.** 13 OWL `.js` files, zero JavaScript tests of any kind. Dashboard regressions are
caught only if a human opens the page.

**Why tours, not Playwright.** Tours run inside `HttpCase` in the container CI already
builds — no new infra, no new stack, no `/etc/hosts` mapping. Playwright stays reserved for
*cross-subdomain* behaviour, the one thing tours cannot do.

**Acceptance.** Each dashboard has a tour that loads it as the intended role and asserts a
real value renders. Together with the RPC role guards (#333/#356/#358) this closes the UI
and RPC sides at once.

### G3 — Clock control

**Risk.** Dunning schedules, auth-log retention windows, exchange-rate freshness,
subscription lifecycle transitions and cron cadence are all time-dependent. Exactly **one**
of 81 test files controls the clock (`ncollection_saas/tests/test_pipeline.py`).

**Acceptance.** Every time-dependent rule asserts behaviour on **both** sides of its
boundary under a frozen clock — not "it has not fired yet".

### G4 — Local runnability of the unit suite

**Found by the baseline run, not by reading code.** The 869 tests have never been executed
locally. They only run inside CI's disposable container, which hands them a private Postgres
and a free port 8069. Against the live dev stack both assumptions break:

| Failure | Cause |
|---|---|
| `database: default@default:default` → connect fails in 1s | `/etc/odoo/odoo.conf` carries **no** DB credentials; `ODOO_DB_ARGS` must be passed explicitly |
| `Address already in use — port 8069` | `--test-enable` binds HTTP for the 8 `HttpCase` classes; the container already serves on 8069 |

The working invocation adds `--db_host=db --db_user=odoo --db_password=odoo
--http-port=8169 --gevent-port=8172`. **Acceptance:** a documented `make` target so nobody
rediscovers this.

### G5–G11

Each needs its own stack and prefix per C2 before it is written. G5 (ZAP) is nightly-only
precisely so it never touches the shared dev stack. **G11 (coverage) is deliberately low**:
it finds no bugs, and it is not cheap here — tests run via `docker run odoo`, so it means
wrapping the in-container command with `coverage run`, pointing `--source` at
`/mnt/extra-addons`, and extracting the report out of the container. Report-only; no
`fail-under` until the real number is known. We currently claim 80% and measure nothing.

---

## 4. When each thing runs

| Stage | Contents | Measured | Blocking? |
|---|---|---|---|
| pre-push (`make hooks-install`) | flake8 · shellcheck · invariants · architecture-guard · pylint-odoo · `tsc` if `demo/` changed | **8s** | local only |
| PR — `ci.yml` | guard self-tests · lint · XML · 869 tests · SCA · build smoke | **4m** (test job) | **no** (C1) |
| PR — `verify.yml` | routing+isolation · e2e typecheck · Playwright | **6m** | **no** (C1) |
| post-merge — `canary.yml` | full verify + **full-tree** guard → files `broken-develop` | ~12m | detection |
| nightly 03:00 | full verify + upstream drift → files `nightly-drift` | ~12m | detection |
| local pre-merge (Rule 13) | `make verify-all` — 7 suites | **8m 20s warm** | discipline |
| full estate | everything in one pass | **10m 37s warm** | discipline |
| manual / pre-release | k6 load · agg-bench · security-assess · go-live-check · PITR drill | — | human gate |

**Cold time is unmeasured.** Every figure above is from a **warm** run — `rt*` and `e2e*`
fixture databases already existed and were reused. After `make routing-clean` /
`make e2e-clean`, the suites rebuild them and will be materially slower. Do not quote the
warm numbers as cold ones.

---

## 5. What we deliberately do NOT test

Recording these stops them being re-proposed every quarter.

| Not doing | Why |
|---|---|
| Mutation testing | Cost enormous; the guard self-tests already cover the "test that cannot fail" class |
| React `demo/` unit tests | Throwaway prototype, ported into OWL over Phase 1 — testing code being deleted |
| Contract-testing framework (Pact) | Single repo, single deploy unit; `test_*_boundary.py` covers the two-layer edge more cheaply |
| Full cross-browser matrix | Odoo's web client supports what it supports; chromium catches our regressions |
| 100% coverage target | 80% is the stated bar and even that is unmeasured (G11) |
| Odoo core's own suite | `--test-tags` scopes to our modules on purpose; core's `test_cli` fails in this container |

---

## 6. Rules for adding a suite

1. Declare **stack** and **DB prefix**; add both to the `CLAUDE.md` fixture table in the
   same PR. No exceptions (C2 / R-004 / R-018).
2. If it mutates or restarts anything shared, it needs its **own compose file and private
   Postgres** — follow `docker-compose.cronstall.yml`.
3. It must be **idempotent and prove it**: run twice, second run is a no-op. An echo
   claiming idempotency is not evidence (R-002).
4. Never `|| true` on a state-changing step something later depends on (R-005).
5. Derive container IDs with `docker compose ps -q <svc>`; never hardcode names (R-006).
6. `psql` and `pg_isready` always take an explicit `-d` (R-002).
7. Failure output must **name the cause**. Under C1 nobody is stopped by an exit code —
   a human reads the message and decides.
8. A runner that aggregates suites must **continue past failures** and gate at the end.
   `make verify-all` chains with `make`, so the first failure aborts the rest — fine as a
   pre-merge gate, wrong for a report.

---

## 7. Baseline — `958d8d6`, 2026-08-06

The reference point. Any future red is measured against this.

| Layer | Result | Time |
|---|---|---|
| Static gates (8) | **8/8 PASS** | 8s |
| Odoo unit + integration | **869 tests — 0 failed, 0 errors** | 2m 8s |
| Infra proof suites (7) | **7/7 PASS** | 8m 20s |
| **Total** | **17/17 green** | **10m 37s** (warm) |

Largest suites: `ncollection_saas` 310 tests, `ncollection_core` 253. Slowest infra suite:
cron-scope 3m 26s.

**Query-cost outliers** — not failures, but where a query-budget guard would bite first:

| Module | Queries / test |
|---|---|
| `ncollection_account_localization_uae` | ~416 |
| `ncollection_account_reports` | ~228 |

**A green baseline does not shrink the gap register.** It confirms every existing guarantee
holds; it says nothing about G1–G11, which are gaps precisely because nothing tests them.
