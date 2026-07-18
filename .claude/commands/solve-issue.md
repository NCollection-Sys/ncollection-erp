---
description: Start work on a GitHub plan issue with automated readiness checks (open? in order? dependencies done?)
argument-hint: [issue-number]
---

You are starting work on ONE GitHub issue of the NCollection ERP project
(repo `NCollection-Sys/ncollection-erp`). Follow the six phases below IN ORDER.
Do not write or edit any file before Phase 5 is approved. Project-wide context
is in `CLAUDE.md` (auto-loaded); deep docs live in `docs/markdown/`.

Issue number provided as argument: "$ARGUMENTS"

## Phase 1 — Identify

If the argument above is empty, ask the user exactly one question — the GitHub
issue number — and nothing else. Do not preload docs or guess an issue.

## Phase 2 — Automated verification (read-only)

Run every check below, in order. Collect results; do not stop midway except on
check 1 failing (no issue = nothing else to check). Never use GitHub search
syntax with `[brackets]` (search strips them) — always fetch JSON and filter
with `--jq` as shown.

1. **Issue exists and is OPEN**
   `gh issue view <N> --repo NCollection-Sys/ncollection-erp --json state,title,body,labels,url`
   → `state` must be `OPEN`. If `CLOSED`: hard fail — report when/why it closed
   (`gh issue view <N> --json closedAt --comments` for the closing context).

2. **Task ID** — extract `[P<phase>-T<nn>]` from the title.
   If there is no task ID, this is not a plan issue: skip checks 3–5, tell the
   user, and confirm the scope with them manually before continuing.

3. **Deferred-phase guard** — if labels contain `phase:9-marketplace`: hard
   fail. Phase 9 is deferred until after Phase 10 by decision recorded in
   `DELIVERABLE_1_SYSTEM_DESIGN.md` §8.

4. **Dependency gate (the hard gate)** — in the issue body, find the line
   `**Dependencies**: <value>`. Value is `None` or comma-separated task IDs
   (e.g. `P1-T07, P1-T09`). For EACH dependency ID:
   `gh issue list --repo NCollection-Sys/ncollection-erp --state all --limit 300 --json number,title,state --jq '.[] | select(.title | startswith("[<ID>]"))'`
   Convention: **closed issue = completed task**. Every dependency must be
   `CLOSED`. Any `OPEN` dependency → hard fail; list each blocker as
   `#<number> [<ID>] <title>` with its URL.

5. **Chronology (advisory, never a hard block by itself)** — read the task's
   full row in `docs/markdown/DELIVERABLE_1_SYSTEM_DESIGN.md` (grep for
   `**<ID>**`) and its sprint placement in `docs/markdown/SPRINT_SCHEDULE.md`
   (grep for `<ID>`). Warn if this task is scheduled in a later sprint while
   earlier-sprint tasks assigned to the same developer are still open — but
   dependencies (check 4) remain the only hard ordering constraint.

6. **Duplicate-work guard** —
   `gh pr list --repo NCollection-Sys/ncollection-erp --state all --limit 100 --json number,title,state --jq '.[] | select(.title | contains("<ID>"))'`
   An existing PR for this task ID → warn (open PR = probably in progress by
   someone; merged PR = the issue may just need closing).

7. **Environment preflight** — `git status` must be clean (if not: stop and ask
   before touching anything); `git checkout develop && git pull --ff-only`;
   `make ps` — if containers are down, offer `make up`.

## Phase 3 — Report and gate

Print a verification table: one row per check, ✓ / ✗ / ⚠, with a one-line
detail each. Then:

- **All ✓ (⚠ allowed)** → continue to Phase 4.
- **Any ✗** → STOP. Use AskUserQuestion with exactly these options:
  (a) **Switch** to the blocking dependency issue and run this same workflow on
  it instead; (b) **Override** — proceed anyway at the user's explicit risk
  (record the override in the eventual PR description); (c) **Abort**.
  Never proceed past a ✗ silently.

## Phase 4 — Load task context

With the gate passed, assemble working context:

1. The task's full DELIVERABLE_1 row: scope, reason, risks, **acceptance
   criteria** — quote it, don't paraphrase.
2. `docs/markdown/TASK_PROMPT_TEMPLATE.md` → the **Standing Rules** section
   (Odoo 19 conventions, two-layer separation, OCA-first, security mirroring,
   local gates). These are binding.
3. Domain deep-dive, chosen by task subject:
   - auth / licensing / roles / security → `docs/markdown/ARCHITECTURE_SECURITY.md` (§4 defense-in-depth, §11 platform risks)
   - provisioning / database / backups / infra / scaling → `docs/markdown/ARCHITECTURE_DATA_PLATFORM.md`
   - demo-UI porting → `demo/README.md` (porting map) + `docs/markdown/LOCAL_DEV_AND_ARCHITECTURE.md`
4. Restate to the user, briefly: scope, the acceptance criteria as a checklist,
   files likely in scope, and explicit out-of-scope items. Flag anything
   ambiguous as a question now, not later.

## Phase 5 — Plan gate

Present a mini implementation plan: ordered steps, files to create/modify,
test approach, risks. **Wait for explicit user approval before any edit.**

## Phase 6 — Execute, verify, ship

1. Branch: `git checkout -b feature/<N>-<task-id-lowercase> develop`
   (e.g. `feature/5-p1-t01-addon-skeleton`).
2. Work in small, reviewable commits: `<type>: <summary>` with `Refs #<N>` in
   bodies. Verify each piece as it lands (install/upgrade the module, run the
   app, run tests) — never batch unverified work.
3. **Local gates before every push** (CI mirrors these; catch failures here):
   - `python3 -m flake8 custom_addons/`
   - `python3 scripts/ci/architecture_guard.py --base origin/develop`
   - if `demo/` was touched: `cd demo && npx tsc --noEmit`
4. Push and open the PR: base `develop`, title `[<ID>] <Task Name>`, body with
   what/why, test evidence per acceptance criterion, and **`Closes #<N>`**
   (plan issues auto-close on merge — this keeps future dependency checks
   truthful). Include any recorded override from Phase 3.
5. Watch all four CI checks (`lint`, `architecture-guard`, `test`, `build`)
   to completion. Fix failures on the same branch. **Never merge your own PR**
   — hand it to the user for review.
6. Wrap up with: (a) an acceptance-criteria table with evidence per row,
   (b) anything deliberately not done, and (c) **the issues this unblocks** —
   `gh issue list --repo NCollection-Sys/ncollection-erp --state open --limit 300 --json number,title,body --jq '.[] | select(.body | test("\\*\\*Dependencies\\*\\*:.*<ID>")) | "#\(.number) \(.title)"'`
   — suggest them as the next `/solve-issue` candidates.

## Guardrails (always)

- ONE issue per chat. Do not touch, close, or edit any other issue.
- No scope creep: if work outside the issue's acceptance criteria seems
  needed, propose it as a follow-up issue instead of doing it.
- Stop and ask whenever acceptance criteria are ambiguous.
- Never commit secrets; dev credentials stay in `.env` (gitignored).
- Odoo 19 gotchas (all live-verified in this repo): HTTP routes are
  `type='jsonrpc'`; `res.users` groups field is `group_ids`; views use
  `<list>` not `<tree>`, no `attrs=`; the `X-Odoo-Database` header makes Odoo
  skip the session cookie — only send it on session-less public calls.
