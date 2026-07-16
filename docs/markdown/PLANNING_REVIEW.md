# NCollection ERP — Planning Review & v4.0 → v5.0 Changelog

> **Date**: July 16, 2026
> **Purpose**: The full findings of the July 2026 review of the docs/ planning set, and the exact task-ID mapping from DELIVERABLE_1 v4.0 to v5.0. Read this once to understand *why* the plan changed; the plan itself lives in [DELIVERABLE_1_SYSTEM_DESIGN.md](DELIVERABLE_1_SYSTEM_DESIGN.md).

---

## 1. Documentation Set Findings

| # | Finding | Severity | Resolution |
|---|---------|:--------:|------------|
| D1 | **Three generations of the same plan coexisted** (`PROJECT_MASTER_CONTEXT.md` with IDs `1.1–8.7`, `SYSTEM_DESIGN_AND_PLANNING.md` with no IDs, `DELIVERABLE_1` with `P1-T01` IDs) — devs and AI agents could follow stale task lists | High | Superseded banners added to the two older docs; DELIVERABLE_1 v5.0 is authoritative |
| D2 | **Task-count contradiction**: DELIVERABLE_2 §1.1 said "96 atomic tasks", its own §2.2 table summed to 78; actual v4.0 count was 78 | Medium | v5.0 has 99 tasks; DELIVERABLE_2 updated to match everywhere |
| D3 | **PRD was stale**: `ncollection_subscription` marked "Planned (Sprint 2)" though implemented; `ncollection_tenant_manager` planned as separate module though merged into subscription | Medium | PRD statuses synced |
| D4 | `scraped_chat.md` + `scraped_chat.json` (2.2 MB of raw chat) sat in docs/ | Low | Moved to `docs/archive/` |
| D5 | **Malformed tables**: Phases 4–10 in v4.0 declared 5 header columns but wrote 6-column rows (renders wrong on GitHub; regex-parseable by luck) | Medium | All tables normalized to the 6-column format the sync script expects |
| D6 | **Detail asymmetry**: Phase 1–2 tasks were rich; Phases 4–10 were one-liners — their GitHub issues would have been nearly empty | Medium | Every v5.0 task has scope, reason where non-obvious, and acceptance criteria |

## 2. Technical Findings (architecture-level)

| # | Finding | Severity | Resolution |
|---|---------|:--------:|------------|
| T1 | **`db_filter = ^%h$` was wrong.** `%h` = the *full hostname*; the subdomain token is `%d`. As written, routing required DBs named `clienta.ncollectionerp.com` | **Critical** | Corrected to `^%d$` in v5.0 §2.3 + P1-T02, with an explanatory warning |
| T2 | **Menu hiding treated as licensing security.** `_visible_menu_ids()` hides UI only — any user could reach unlicensed models via URL or XML-RPC | **Critical** | Split into P1-T09 (UI) + new P1-T10 (ORM/RPC enforcement); design in [ARCHITECTURE_SECURITY.md §4](ARCHITECTURE_SECURITY.md) |
| T3 | **Custom login-controller override planned** despite the plan's own OCA-first rule and its own "fragile" risk note | High | P1-T19 rewritten OCA-first: `auth_brute_force` + `auth_session_timeout`; custom code only as documented fallback |
| T4 | **24-hour RPO**: daily `pg_dump` was the only backup layer — a tenant could lose a full business day | High | New P2-T04 (pgBackRest PITR, ~1-min RPO) + P2-T05 (per-tenant dumps **including filestore**, which v4.0's task omitted); design in [ARCHITECTURE_DATA_PLATFORM.md §5](ARCHITECTURE_DATA_PLATFORM.md) |
| T5 | **PgBouncer risk unaddressed**: transaction pooling breaks Odoo's LISTEN/NOTIFY bus (longpolling 8072) if not split | High | Topology fixed in [ARCHITECTURE_DATA_PLATFORM.md §4](ARCHITECTURE_DATA_PLATFORM.md); implemented in P2-T09; 8072 + cron + queue go DIRECT |
| T6 | **In-process provisioning**: DB creation + module installs on live HTTP workers would spike latency for paying tenants | High | P2-T01 mandates a dedicated queue_job runner container |
| T7 | **No per-database module matrix**: docs never said which addon installs into admin vs tenant DBs | Medium | New §2.5 matrix in the master plan — now an enforcement reference |
| T8 | **No plan-change propagation task**: workspace config was written at provisioning and never updated — upgrades would silently not reach tenants | High | New P2-T03 (config sync + nightly reconciliation) |
| T9 | **OCA modules manually cloned/symlinked** — guaranteed environment drift across 3 remote devs + CI + prod | Medium | New P1-T04: `git-aggregator` with pinned `repos.yml` |
| T10 | **URL rewriting misassigned & overscoped**: Nginx work sat with DEV-3 (frontend), and rewriting internal `/odoo` routes fights Odoo's JS router on every upgrade | Medium | P1-T15: reassigned to DEV-1, scoped to public surfaces only, timeboxed to 1 day |
| T11 | Odoo 19 deprecations (`<tree>` → `<list>`, `attrs=` removal) not codified — a known source of failed installs | Medium | New Rule 6 in the master plan + AI collaboration rule |

## 3. Planning & Sequencing Findings

| # | Finding | Severity | Resolution |
|---|---------|:--------:|------------|
| P1 | **DEV-3 bottleneck** (23d vs 15d for DEV-2 in Phase 1) — flagged by v4.0 itself but not fixed | High | Rebalanced to 20/21/22: email templates → DEV-2, URL rewriting → DEV-1; DEV-3 gained E2E framework + Owner settings |
| P2 | **DEV-2/DEV-3 idle at sprint start**: skeletons (old P1-T03) depended on Docker hardening for no reason | Medium | v5.0 P1-T01 (skeletons, now DEV-2) and P1-T13 (branding) have **no dependencies** — all three devs start Day 1 |
| P3 | **Manual-only integration testing** — unsustainable by Phase 3 (8 roles × plans × tenants) | High | New P1-T20: Playwright E2E framework in CI; every phase gate re-runs it |
| P4 | **No staging/deploy task** despite DELIVERABLE_2 promising auto-deploy to staging | High | New P2-T07 |
| P5 | **No server-hardening task** despite the v4.0 security table listing it as "Phase 2" | High | New P2-T08 |
| P6 | **No production monitoring until Phase 8** — months of blind production operation | High | New P2-T10 (lightweight now); P8-T04 remains the full stack |
| P7 | **No online payment collection until Phase 6** — go-live would run on manual bank-transfer chasing | High | New P2-T13 (Stripe via Odoo's built-in `payment_stripe`); PayTabs/Tap stay Phase 6 (tenant-facing) |
| P8 | **Security assessment at Phase 10** — ~a year after real tenant data arrives | High | New P3-T12 pre-launch assessment; external pen test remains P10-T04 |
| P9 | **No trial support** despite PRD requiring it | Medium | Folded into P2-T12 lifecycle (`trial` state, `trial_days`, conversion) |
| P10 | **No data-import tooling until Phase 10** — first real tenants (Month ~4) arrive with existing data | Medium | New P3-T11 import toolkit |
| P11 | **No user-management surface for Owners**: v4.0 stripped Settings but never built the safe replacement | Medium | New P1-T12 Owner Workspace Settings |
| P12 | **Task-size rule violated by its own plan** ("max 5 days" vs 8-day tasks in P7/P8/P9/P10) | Low | All oversized tasks split; max is now 5 days |
| P13 | **Marketplace (Phase 9) before Enterprise Readiness** — third-party code ecosystem before HA/security hardening, with no demand signal | Medium | Execution order: Phase 10 before Phase 9; Phase 9 marked DEFERRED; curated Integration Directory (P8-T08) covers the near-term need |
| P14 | **Portal (Phase 6) after AI (Phase 5)** — revenue/retention features behind experimental ones | Low | Execution order swapped (numbering kept for label stability); AI phase now starts with a mandatory design spike (P5-T01) |
| P15 | Wrong dependency: P6-T01 (tenants' customers paying tenant invoices) depended on P2-T06 (SaaS billing) — unrelated flows | Low | Now depends on P2-T13 (reuses payment-provider patterns) |
| P16 | "All P1 tasks" as a dependency string — not machine-parseable | Low | Gates list explicit task IDs |
| P17 | Phase gates existed only for Phase 1 | Low | Gates for Phases 1, 2, 3 (P1-T21, P2-T18, P3-T13); later phases re-run the cumulative regression |

## 4. Tooling Findings

| # | Finding | Resolution |
|---|---------|------------|
| S1 | `github_issue_sync.py` used labels (`phase:1`) inconsistent with DELIVERABLE_2's taxonomy (`phase:1-workspace`), and `gh` fails on non-existent labels | Script v2: creates the full D2 label taxonomy idempotently |
| S2 | No duplicate protection — re-running created every issue again | Script v2: skips issues whose `[Px-Tyy]` title prefix already exists |
| S3 | No milestones | Script v2: creates/assigns per-phase milestones |
| S4 | No dry-run; interactive only | Script v2: `--dry-run`, `--phase N`, `--limit N` flags (interactive flow preserved) |

## 5. What Was Deliberately Kept

- **Database-per-tenant** — correct choice, unchanged.
- **Two-layer architecture** — correct and now enforced by the §2.5 installation matrix.
- **The completed-milestones list** — nothing already built was redesigned.
- **Phase numbering** — kept stable for labels/milestones; execution order documented separately (master plan §8).
- **The 6-column task-table format** — required by `scripts/github_issue_sync.py`.
- **Team personas & GitHub-centric workflow** — sound for a 3-dev remote team.

## 6. Task ID Mapping — v4.0 → v5.0

### Phase 1

| v4.0 | v5.0 | Notes |
|------|------|-------|
| P1-T01 Docker Environment Hardening | **P1-T02** (odoo.conf/secrets) + **P1-T03** (Nginx/TLS) | Mega-task split; `db_filter` corrected |
| P1-T02 CI Pipeline Enhancement | **P1-T05** | + security scanning |
| P1-T03 Addon Skeleton Finalization | **P1-T01** | → DEV-2, zero dependencies, +test scaffolding |
| P1-T04 Tenant Model Enhancements | **P1-T07** | Unchanged in substance |
| P1-T05 DB Routing Engine | **P1-T06** | `%d` fix; docs deliverable |
| P1-T06 Module Visibility Engine | **P1-T09** (menus) + **P1-T10** (ORM/RPC enforcement — NEW) | Defense in depth |
| P1-T07 Tenant Role Definitions | **P1-T08** | Unchanged |
| P1-T08 Apps & Settings Stripping | **P1-T11** | Unchanged |
| P1-T09 Web Client Branding | **P1-T13** | + LGPL attribution doc |
| P1-T10 Login Page Redesign | **P1-T14** | 3d → 2d |
| P1-T11 URL Path Rewriting | **P1-T15** | → DEV-1, public-only scope, 1d timebox |
| P1-T12 Dynamic Tenant Branding | **P1-T16** | Unchanged |
| P1-T13 Customer Auth Hardening | **P1-T19** | OCA-first rewrite, 4d → 3d |
| P1-T14 Customer Workspace Dashboard | **P1-T17** | Unchanged |
| P1-T15 Email Template Branding | **P1-T18** | → DEV-2 (rebalance) |
| P1-T16 Integration Testing | **P1-T21** | Explicit dep list; security-audit scope |
| — | **P1-T04** OCA Dependency Management | NEW |
| — | **P1-T12** Owner Workspace Settings & User Mgmt | NEW |
| — | **P1-T20** E2E Test Framework (Playwright) | NEW |

### Phase 2

| v4.0 | v5.0 | Notes |
|------|------|-------|
| P2-T01 Provisioning Engine Activation | **P2-T01** | + dedicated runner container |
| P2-T02 Auto-Provisioning Pipeline | **P2-T02** | Unchanged |
| P2-T03 Backup Manager | **P2-T04** (PITR — NEW scope) + **P2-T05** (dumps + filestore) | RPO 24h → ~1min |
| P2-T04 Domain & SSL Manager | **P2-T06** | Unchanged |
| P2-T05 Sub Expiration Cron | **P2-T14** | + dunning |
| P2-T06 Billing Engine | **P2-T11** | Unchanged |
| P2-T07 Lifecycle State Machine | **P2-T12** | + trial support |
| P2-T08 SaaS Admin Dashboard | **P2-T15** | Unchanged |
| P2-T09 Public Checkout Flow | **P2-T16** | Elevated to full self-service onboarding |
| P2-T10 Email Automation | **P2-T17** | Unchanged |
| — | **P2-T03** Workspace Config Sync | NEW (critical gap) |
| — | **P2-T07** Staging & CD | NEW |
| — | **P2-T08** Server Hardening | NEW |
| — | **P2-T09** PgBouncer Topology | NEW |
| — | **P2-T10** Uptime Monitoring | NEW |
| — | **P2-T13** Stripe Payment Collection | NEW |
| — | **P2-T18** Phase 2 Gate | NEW |

### Phase 3

v4.0 P3-T01→T10 map to v5.0 with three changes: **P3-T01** reassigned DEV-1 → DEV-2; two NEW tasks — **P3-T11** (Data Import Toolkit), **P3-T12** (Pre-Launch Security Assessment); go-live gate formalized as **P3-T13**.

### Phases 4–10

Same task set enriched with acceptance criteria; splits: v4.0 P7-T06 → **P7-T06/T07**; P8-T01 → **P8-T01/T02** (webhooks shift to P8-T03, monitoring P8-T04, etc. — one-position renumber); P9-T01 → **P9-T01/T02**; P10-T01 → **P10-T01/T02**; NEW **P5-T01** (AI design spike). Phase 9 marked DEFERRED after Phase 10.

## 7. Headline Numbers

| Metric | v4.0 | v5.0 |
|--------|:----:|:----:|
| Tasks | 78 (doc claimed 96) | **99** |
| Total dev-days | 333 | **368** |
| Phase 1 workload (DEV-1/2/3) | 19/15/23 | **20/21/22** |
| Phase 1 critical path | 18 days | **16 days** |
| Max task size | 8 days | **5 days** |
| RPO | 24 h | **~1 min** |
| First payment capability | Phase 6 | **Phase 2** |
| First security assessment | Phase 10 | **Phase 3 (pre-launch)** |
| Automated E2E/isolation testing | none | **Phase 1, in CI** |

---

> Sources: internal review of all docs/ files + the external architecture review received July 16, 2026 (module-visibility gap, OCA auth, PITR, PgBouncer/longpolling, provisioning isolation, DEV-3 rebalance, Playwright, git-aggregator, `<list>` views, marketplace deferral — all adopted).
