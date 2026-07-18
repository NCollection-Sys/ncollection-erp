# NCollection ERP — Project Context (read this first)

Auto-loaded every session. Stable facts + pointers; volatile detail lives in the
linked docs. To start a plan issue, run **`/solve-issue <number>`**.

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
5. OCA-first: check for an existing OCA module before building custom; record the decision.
6. Small incremental commits, each verified. Run local gates before pushing:
   `flake8 custom_addons/` + `scripts/ci/architecture_guard.py --base origin/develop`
   (+ `cd demo && npx tsc --noEmit` if `demo/` changed).
7. No secrets in git; dev creds live in `.env` (gitignored; template `.env.example`).

## Odoo 19 gotchas (live-verified here — save yourself the debugging)
- HTTP JSON routes are `type='jsonrpc'` (`type='json'` is deprecated).
- `res.users` groups field is `group_ids` (was `groups_id`); `base.default_user` template
  was removed — create users with an explicit `group_ids` set.
- Sending the `X-Odoo-Database` header on `/web/session/authenticate` makes Odoo skip the
  session cookie — only send it on session-less public calls (e.g. signup).

## Make cheat-sheet (`make help` for all)
`up` `down` `restart` `logs` `ps` · `bootstrap db=<db>` · `install m=<mod> db=<db>` ·
`upgrade m=<mod> db=<db>` · `psql db=<db>` · `shell` · `demo` (runs the React app).

## Docs index (`docs/markdown/`, PDFs in `docs/pdf/`)
- `LOCAL_DEV_AND_ARCHITECTURE.md` — onboarding + runtime + workflow (start here for setup).
- `DELIVERABLE_1_SYSTEM_DESIGN.md` — the 100 tasks (scope, deps, acceptance) + §8 exec order.
- `SPRINT_SCHEDULE.md` — parallelization / sprint grid.
- `ARCHITECTURE_DATA_PLATFORM.md` · `ARCHITECTURE_SECURITY.md` — backend & security deep-dives.
- `TASK_PROMPT_TEMPLATE.md` — canonical Standing Rules + manual issue template.
- `PRD.md` · `DELIVERABLE_2_TIMELINE_AND_TOOLING.md` · `PLANNING_REVIEW.md` — product & planning.
