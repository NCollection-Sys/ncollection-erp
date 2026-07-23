---
description: Start work on a GitHub plan issue with automated readiness checks (open? in order? dependencies done?)
argument-hint: [issue-number]
---

You are starting work on ONE GitHub issue of the NCollection ERP project
(repo `NCollection-Sys/ncollection-erp`). Follow the six phases below IN ORDER.
Do not write or edit any file before Phase 5 is approved. Project-wide context
is in `CLAUDE.md` (auto-loaded); deep docs live in `docs/markdown/`.

Issue number provided as argument: "$ARGUMENTS"

## Global Architecture Rules (always active)

These rules override all other instructions.

1. The architecture documents are authoritative:
   - `DELIVERABLE_1_SYSTEM_DESIGN.md`
   - `ARCHITECTURE_DATA_PLATFORM.md`
   - `ARCHITECTURE_SECURITY.md`

   Never redesign, replace, or contradict an architectural decision from these documents unless the GitHub issue explicitly requires it or the user approves.

2. Extend before replacing.
   Prefer extending existing modules and code over rewriting or replacing them.
   Large refactors outside the issue scope require explicit approval.

3. Never introduce new architectural dependencies (especially OCA modules, infrastructure components, or external services) unless:
   - they already exist in the architecture documents, or
   - the user explicitly approves them.

4. Treat completed milestones as stable.
   Do not redesign or replace completed infrastructure, modules, or architecture unless the issue explicitly requests it.

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

Present a mini implementation plan: ordered steps, files to create/modify, test
approach, risks. It MUST also include:

- **A blast-radius table** — which ALREADY-SHIPPED work could this touch?
  Scripts, compose files, nginx configs, workflows, fixture databases, shared
  addons. "None — new files only" is a fine answer when it is true. Cross-suite
  breakage stayed invisible for weeks precisely because nobody was asked this.
- **A rollback plan** — how to undo this cleanly if it misbehaves after merge.
- **OCA-first check (Rule 2/5)** — when the ticket could reuse an existing OCA
  module or needs a new dependency, delegate the survey to the **`oca-scout`**
  subagent and record its REUSE / ADOPT / BUILD-CUSTOM recommendation (with the
  architecture-check result) in the plan. Never propose a new OCA dependency
  without it.

Then explain briefly how the implementation preserves:

- Two-layer architecture
- Database-per-tenant isolation
- Security architecture
- Existing project architecture

If any architectural assumption changes, STOP and ask before implementation.

**Wait for explicit user approval before any edit.**


## Phase 6 — Execute, verify, ship

1. Branch: `git checkout -b feature/<N>-<task-id-lowercase> develop`
   (e.g. `feature/5-p1-t01-addon-skeleton`).
2. Work in small, reviewable commits: `<type>: <summary>` with `Refs #<N>` in
   bodies. Verify each piece as it lands (install/upgrade the module, run the
   app, run tests) — never batch unverified work.
3. **Local gates before every push.** Run `make hooks-install` once and the
   pre-push hook runs the fast ones for you (flake8 · shellcheck ·
   `invariants.py` · `architecture_guard.py`). Add `cd demo && npx tsc --noEmit`
   if `demo/` changed.
   **Then run `make verify-all`** (routing + provisioning + e2e) — NOT just the
   suite for your own lane. A ticket that proves only its own lane cannot see a
   cross-suite regression. If you touched shared infra or a verification script,
   show the OTHER suites still passing. This gate MAY be delegated to the
   **`verify-runner`** subagent (run it in the background while you finish the
   write-up) — it runs `make verify-all` and reports per-suite pass/fail.

   **Delegate review before the PR (parallel subagents).** Once the code is
   written and the local gates pass, fan out review IN PARALLEL and address every
   CRITICAL/HIGH before opening the PR:
   - **`code-reviewer`** — always (general quality/security).
   - **`odoo-reviewer`** + **`tenant-isolation-auditor`** — whenever `custom_addons/**`
     changed (Odoo-19 conventions, two-layer/db-per-tenant isolation).
   - **`security-reviewer`** — when auth, user input, secrets, or payments are touched.

   Relay each verdict and fix findings on the branch. The workers ADVISE only —
   you (the orchestrator) still own the commits, the PR, and (never) the merge.
   For a large or high-risk diff, prefer `/code-review ultra` instead.
4. Push and open the PR: base `develop`, title `[<ID>] <Task Name>`, body with
   what/why, `make verify-all` evidence per acceptance criterion, an explicit
   **"What this does NOT cover"** section (an undeclared gap reads as coverage),
   a rollback note, and **`Closes #<N>`**. Include any Phase-3 override.
   ⚠️ `Closes #<N>` does **NOT** auto-close here: GitHub only auto-closes from
   the DEFAULT branch (`main`), and we merge to `develop`. The keyword is for
   traceability; the issue must be closed BY HAND after merge (see Phase 7).
   Getting this wrong silently breaks the "closed issue = completed task"
   convention every dependency gate relies on.
5. Watch the CI checks to completion — `lint`, `architecture-guard`, `test`,
   `build`, `verify` (cross-suite). `security-scan` is advisory, not blocking.
   Fix failures on the same branch. **Never merge your own PR** — hand it to the
   user for review.
   ⚠️ Green CI does NOT block a bad merge here: branch protection is unavailable
   on this GitHub plan (verified 403). See `docs/markdown/BRANCH_PROTECTION.md`.
   Before completing the task, verify that:

- No architectural decision was unintentionally changed.
- No dependency was added without approval.
- Existing completed milestones continue to work.
- The implementation stays within the issue scope.

6. Wrap up with: (a) an acceptance-criteria table with evidence per row,
   (b) anything deliberately not done, and (c) **the issues this unblocks** —
   `gh issue list --repo NCollection-Sys/ncollection-erp --state open --limit 300 --json number,title,body --jq '.[] | select(.body | test("\\*\\*Dependencies\\*\\*:.*<ID>")) | "#\(.number) \(.title)"'`
   — suggest them as the next `/solve-issue` candidates.

## Phase 7 — After the merge (do not skip)

The user merges, not you. Once they have:

1. **Close the issue by hand** — `gh issue close <N> --comment "Completed in PR
   #<pr> (merged to develop, commit <sha>)."` `Closes #<N>` does not fire on a
   `develop` merge (see Phase 6.4), and the dependency gate in Phase 2 treats a
   CLOSED issue as a COMPLETED task. Leaving it open silently blocks every task
   that depends on it.
2. **Watch the canary.** Merging to `develop` triggers `canary.yml`, which
   re-runs the full suite. If it files a `broken-develop` issue, that is now the
   top priority — fix forward or revert before anything else is merged. A green
   canary means "develop was healthy 12 minutes ago", not "the merge was gated".
3. **Refresh the tracker** — `python3 scripts/github_issue_sync.py --report`.
   It is GENERATED; never hand-edit it. Commit only if a task's status actually
   changed (a date-only diff is churn — discard it).
4. **If this fixed a regression**, add an entry to `docs/markdown/REGRESSIONS.md`:
   symptom → root cause → **the guard that now prevents recurrence**. A
   regression is not closed until a guard exists, or until it is written down why
   one cannot be built.

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

- Never modify project planning or architecture documentation unless the GitHub issue explicitly targets documentation.

- Never execute destructive database operations (SQL, module state manipulation, uninstalling modules, deleting databases, or changing installed modules) unless the issue explicitly requires it and the user approves.

- If implementation requires changing the documented architecture, STOP and ask for approval before writing any code.