# NCollection ERP — Project Context (read this first)

Auto-loaded every session. Stable facts + pointers; volatile detail lives in the
linked docs. To start a plan issue, run **`/solve-issue <number>`**.

## The team (who is who)
| Role | GitHub | Lane |
|------|--------|------|
| **DEV-1** | `omaressam7704` (Omar — repo owner, **the person you are usually talking to**) | Backend / Infra |
| DEV-2 | `aibrahimhlms` | Odoo / Logic |
| DEV-3 | `bakr33934-svg` | Frontend |

When suggesting next issues, prioritize the **`dev:DEV-1`** lane for Omar (he sometimes
drives other lanes with AI too, but DEV-1 is his own work).

## Communication style — for Omar (DEV-1) only
Omar's standing request: **respond like a caveman** — short blunt sentences, simple
words, caveman flavor ("Me run tests. Tests green. Fire good."). BUT this changes only
the wrapping, **never the substance**: still explain everything, include every check,
number, caveat, and risk; tables and evidence stay. Never sacrifice completeness for
the bit. (Teammates who use this repo get normal professional style.)

## What this is
A multi-tenant SaaS ERP for the GCC/UAE market, built on **Odoo 19 Community** with a
custom SaaS layer on top. Database-per-tenant. Repo: `NCollection-Sys/ncollection-erp`
(private, GitHub Free org — no enforced branch protection yet).

## The two "websites" (do not confuse them)
1. **The product** — Odoo 19 + `custom_addons/`, served at `localhost:8069`, real
   PostgreSQL. This is what ships.
2. **`demo/`** — a standalone React/Vite prototype at `localhost:5173`. Business screens
   use **mock data**; **login & signup are REAL** (they authenticate against the `ncollection`
   database via the Vite proxy). It's a design reference to be ported into Odoo OWL/QWeb
   over the Phase-1 tickets. Never deployed as-is.

## Runtime map
- `docker compose up -d` → two containers: `odoo` (`odoo:19`, port 8069) + `db`
  (`postgres:16`). DB data is in the `postgres_data` volume; filestore in `odoo_data`.
- Custom addons are **bind-mounted** `./custom_addons → /mnt/extra-addons` (live edits;
  apply with a module upgrade, no image rebuild).
- Dev config: `config/odoo.conf` (dev bootstrap — production config is ticket P1-T02).
- Postgres is exposed to the host on **port 5433** (for pgAdmin/DBeaver; 5432 avoided to
  not clash with a local Postgres). Odoo DB-manager master password (dev): `ncollection_dev`.
- The working database is **`ncollection`** (admin login `admin`/`admin`). `make bootstrap
  db=ncollection` creates it and installs our modules.

## Custom addons (current state)
- `ncollection_subscription` — **substantive**: Tenant / Subscription / Plan / Provisioning
  / SaaS-admin Dashboard models + views + security.
- `ncollection_branding` — theme, logo, colors.
- `ncollection_core` — near-empty; has the demo signup controller. Fills out over Phase 1.
- `ncollection_saas` — empty skeleton (Phase 2).

In Odoo, a feature = **one module** holding both backend (`models/*.py`, `controllers/`)
and frontend (`views/*.xml`, `static/src/*.js` OWL, `*.scss`). There is no separate
frontend/backend deploy — it's a monolith.

## How we work
- **Branches:** `main` = stable, `develop` = active integration. Both currently in sync.
  Feature work branches off `develop`.
- **Per issue:** `/solve-issue <n>` → it checks the issue is open, in order, and its
  dependency issues are closed → branch `feature/<n>-<task-id>` off `develop` → PR to
  `develop` with **`Closes #<n>`** → CI → 1 review → merge.
- **Convention: a CLOSED issue = a COMPLETED task.** This is what makes dependency checks
  work, so only close an issue when its work is truly merged.
- **CI** (`.github/workflows/ci.yml`, runs on PRs): `lint` (flake8 + pylint-odoo baseline),
  `architecture-guard` (`scripts/ci/architecture_guard.py`), `test` (installs addons +
  `--test-tags` scoped to ours), `build` (compose smoke test). No CD yet (Phase 2/3).
- **The 100 GitHub issues** are the plan (`[P<phase>-T<nn>]` task IDs, phase + dev labels).
  Phase-9 (marketplace) is deferred until after Phase 10.

## Standing rules (binding — full list in docs/markdown/TASK_PROMPT_TEMPLATE.md)
1. Odoo 19 views: `<list>` not `<tree>`; no `attrs=`.
2. Never modify Odoo core; extend via addons.
3. Two-layer separation: platform addons (`ncollection_saas`, `ncollection_subscription`)
   must not directly query tenant ERP models — go through RPC.
4. Any UI restriction (menu/`groups=`) must be mirrored at the ORM/RPC layer.
5. OCA-first for mature infrastructure and security concerns.
  Business features that become part of the NCollection product should gradually migrate to native ncollection_* modules according to the project roadmap.
  Never introduce a new OCA dependency without checking the project architecture first.
6. Small incremental commits, each verified. Run `make hooks-install` **once** and the
   pre-push hook runs the fast gates for you (flake8 · shellcheck · `invariants.py` ·
   `architecture_guard.py`). Add `cd demo && npx tsc --noEmit` if `demo/` changed.
7. No secrets in git; dev creds live in `.env` (gitignored; template `.env.example`).
8. The architecture documents are authoritative.
9. **Postgres CLI tools need an explicit `-d`.** `psql` and `pg_isready` default the target
   database to the *username* — the role here is `odoo` and no such database exists, so a
   missing `-d` fails silently-ish with `FATAL: database "odoo" does not exist`. This
   disabled the routing suite's idempotency for weeks (REGRESSIONS.md R-002).
   `dropdb`/`createdb` are fine without it — they default to the `postgres` maintenance db.
10. **Never `|| true` on a state-changing step you later depend on.** Fail loud with an
    actionable message. A swallowed restart failure once printed "✅ ready" over a stale
    cache (R-005).
11. **Derive container IDs** via `docker compose ps -q <service>`; never hardcode
    `ncollection-*` names — they break under a non-default `COMPOSE_PROJECT_NAME` (R-006).
12. **Verification scripts must be idempotent *and prove it*** — run twice; the second run
    must be a no-op. Claiming idempotency in an echo is not evidence (R-002).
13. **Before merging, run `make verify-all`** — routing + provisioning + e2e — not just the
    suite for your own lane. A ticket that proves only its own lane cannot see a cross-suite
    regression, which is exactly how breakage stayed invisible.
If a requested implementation appears to conflict with
DELIVERABLE_1_SYSTEM_DESIGN.md,
ARCHITECTURE_DATA_PLATFORM.md,
or ARCHITECTURE_SECURITY.md,
STOP and ask before changing the architecture.

## Odoo 19 gotchas (live-verified here — save yourself the debugging)
- HTTP JSON routes are `type='jsonrpc'` (`type='json'` is deprecated).
- `res.users` groups field is `group_ids` (was `groups_id`); `base.default_user` template
  was removed — create users with an explicit `group_ids` set.
- Sending the `X-Odoo-Database` header on `/web/session/authenticate` makes Odoo skip the
  session cookie — only send it on session-less public calls (e.g. signup).

## Make cheat-sheet (`make help` for all)
`up` `down` `restart` `logs` `ps` · `bootstrap db=<db>` · `install m=<mod> db=<db>` ·
`upgrade m=<mod> db=<db>` · `psql db=<db>` · `shell` · `demo` (runs the React app).
**First run:** `make hooks-install` (pre-push gates) · `make doctor` (diagnose the env).
**Before merging:** `make verify-all` (routing + provisioning + e2e).

## Test fixture ownership (do not cross the streams)
Each suite owns a database namespace and may **only** drop its own. These used to be
shared, so running one suite silently destroyed another's fixtures (REGRESSIONS.md R-004).

| Suite | Owns | Cleanup |
|---|---|---|
| Routing proof (P1-T06) | `rtclienta` · `rtclientb` · `rtadmin` | `make routing-clean` |
| E2E (P1-T20) | `e2eclienta` · `e2eclientb` · `e2eadmin` | `make e2e-clean` |
| Provisioning (P2-T01) | `prov*` | — |

Fixture names must be **alphanumeric**: `db_filter=^%d$` routes a subdomain to the database
of the same name, underscores are invalid in hostnames, hyphens need Postgres quoting.
So tenant key === subdomain === database name, always.

## What a green check does NOT mean
- **`architecture-guard`** checks secrets on every changed file and XML on every `.xml`, but
  its two-layer/Odoo-syntax rules are scoped to `custom_addons/`. Infra surfaces (shell,
  compose, workflows) are covered by `scripts/ci/invariants.py` instead. It also reports
  `0 file(s)` legitimately on a local pre-commit run — untracked files are not in `git diff`.
- **CI cannot block a merge here.** Branch protection is unavailable on GitHub Free private
  repos (verified: HTTP 403), so a red PR is merge-able. `canary.yml` re-verifies `develop`
  after every merge and files a `broken-develop` issue — that is **detection, not a gate**.
  Treat such an issue as top priority. See `docs/markdown/BRANCH_PROTECTION.md`.

## Docs index (`docs/markdown/`, PDFs in `docs/pdf/`)
- `LOCAL_DEV_AND_ARCHITECTURE.md` — onboarding + runtime + workflow (start here for setup).
- `DELIVERABLE_1_SYSTEM_DESIGN.md` — the 100 tasks (scope, deps, acceptance) + §8 exec order.
- `SPRINT_SCHEDULE.md` — parallelization / sprint grid.
- `ARCHITECTURE_DATA_PLATFORM.md` · `ARCHITECTURE_SECURITY.md` — backend & security deep-dives.
- `TASK_PROMPT_TEMPLATE.md` — canonical Standing Rules + manual issue template.
- `BRANCH_PROTECTION.md` — required CI checks + 1-approval policy, and why none of it is
  **enforceable** on the current GitHub plan (verified 403) — read before assuming CI blocks.
- `DEMO_TENANT.md` — the populated **Al Barari Trading** workspace (`make demo-tenant`):
  what it seeds, which login shows which role, and how to rebuild it.
- `REGRESSIONS.md` — the regression ledger: symptom → root cause → the guard that now
  prevents recurrence. **A regression is not closed until a guard exists.**
- `PRD.md` · `DELIVERABLE_2_TIMELINE_AND_TOOLING.md` · `PLANNING_REVIEW.md` — product & planning.

Architecture priority (highest first)

1. DELIVERABLE_1_SYSTEM_DESIGN.md
2. ARCHITECTURE_DATA_PLATFORM.md
3. ARCHITECTURE_SECURITY.md
4. DELIVERABLE_2_TIMELINE_AND_TOOLING.md
5. SPRINT_SCHEDULE.md

If older documents contradict these, treat them as historical only.

## Architecture Safety

Never:

- redesign the architecture without approval
- add new OCA modules without approval
- modify module states or databases unless explicitly requested
- replace existing code when extending it is sufficient

When unsure:
STOP.
Explain.
Ask.

## AI Expectations
Think like a senior software architect.

Do not optimize for writing code quickly.

Optimize for:

- maintainability
- scalability
- security
- backward compatibility
- minimal scope
- long-term architecture

Always preserve existing architectural decisions unless explicitly instructed otherwise.
