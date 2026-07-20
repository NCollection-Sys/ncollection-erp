# Branch Protection Policy

> **Status: NOT ENFORCEABLE on the current GitHub plan.** This is not "we haven't got
> round to it" — it is blocked by the plan. Verified against the live API, not assumed:
>
> ```console
> $ gh api repos/NCollection-Sys/ncollection-erp/branches/develop/protection
> HTTP 403: "Upgrade to GitHub Pro or make this repository public to enable this feature."
>
> $ gh api repos/NCollection-Sys/ncollection-erp/rulesets
> HTTP 403: same
> ```
>
> Protected branches and rulesets require Pro / Team / Enterprise for **private**
> repositories; GitHub Free only supports them on public repos. **Consequence: every CI
> gate in this repo is bypassable by clicking merge.** A red PR can be merged, and one
> already was. Any document (including earlier versions of this one) implying CI blocks a
> merge here is wrong.

---

## 1. Protected branches (the intent)

| Branch | Role | Intended protection |
|---|---|---|
| `main` | Stable / release | Highest — never receives direct pushes; only fast-forward merges from `develop` via PR |
| `develop` | Active integration | All feature work merges here via PR |

Feature branches: `feature/<issue#>-<task-id>` off `develop`. Unprotected, deleted after merge.

## 2. Required to merge into `develop` or `main`

1. **All CI checks green.** The blocking jobs are:
   - `lint` — flake8 + XML well-formedness + pylint-odoo baseline + **shellcheck** + **`invariants.py`**
   - `architecture-guard` — two-layer / Odoo-19-syntax / secret checks (addon surface)
   - `test` — addon install + scoped Odoo test suite on real PostgreSQL
   - `build` — Docker compose smoke test
   - **`verify`** — cross-suite: routing/isolation (P1-T06) + e2e typecheck + e2e guarantees (P1-T20)

   `security-scan` (pip-audit + trivy) stays **intentionally non-blocking** — advisory only.

2. **At least 1 approving review.** No self-approval, no self-merge.
3. **Branch up to date with the base** before merge.
4. **Conversations resolved.**

Today items 1–4 are upheld by **convention and the `/solve-issue` workflow only**. Nothing
technically enforces them.

## 3. Compensating controls (what actually exists today)

Because we cannot *prevent* a bad merge, the strategy is fast *detection*. These are real
and running:

| Control | What it does | Prevents? |
|---|---|---|
| `.githooks/pre-push` (`make hooks-install`) | flake8 · shellcheck · invariants · architecture-guard, in seconds, before anything leaves your machine | No — bypassable, by design |
| `verify` job on every PR | routing + typecheck + e2e against one stack | No — mergeable while red |
| **`canary.yml`** | Re-runs the full suite on every push to `develop`; on failure files a `broken-develop` issue naming the commit, author and failing run, de-duplicating onto an open one | **No — detects in ~12 min** |
| `nightly.yml` | Same suite on a cron; catches upstream drift with no commit of ours. Files under `nightly-drift` | No |
| Dependabot | Weekly grouped updates + immediate security advisories | No |

**A green canary is not a passing gate.** It means "develop was healthy 12 minutes ago".
Treat a `broken-develop` issue as top priority: fix forward or revert before merging anything
else.

## 4. Enabling real enforcement (when the plan allows)

Requires **GitHub Team** (or Pro/Enterprise) — roughly $4/user/month; ~$12/month for three
developers. Once upgraded:

```bash
bash scripts/ci/enable_branch_protection.sh          # develop + main
bash scripts/ci/enable_branch_protection.sh develop  # one branch only
```

That script applies exactly the policy in §2. Or via the UI —
**Settings → Branches → Add branch ruleset** for `develop` and `main`:

- ✅ Require a pull request before merging → **Require approvals: 1**
- ✅ Dismiss stale approvals on new commits
- ✅ Require status checks to pass → `lint`, `architecture-guard`, `test`, `build`, `verify`
  (leave `security-scan` OUT — non-blocking)
- ✅ Require branches to be up to date before merging
- ✅ Require conversation resolution before merging
- ✅ Block force pushes · ✅ Restrict deletions

**After enabling**, `canary.yml` becomes redundant as a safety net (though still useful as a
post-merge smoke signal) — see its header.

⚠️ **Do not add `paths-ignore` to `verify.yml` before enabling this.** A skipped job reports
"expected — waiting" against a *required* check and blocks merges permanently.

## Changelog

| Date | Change |
|---|---|
| 2026-07-19 | Initial policy (P1-T05); four required checks + 1 approval; security-scan explicitly non-blocking |
| 2026-07-21 | Rewritten around the **verified 403**: enforcement is impossible on GitHub Free private repos, not merely unconfigured. Added `verify` to the required-check list (supersedes `e2e`), documented the canary/nightly/hooks as *detection* rather than prevention, and added `scripts/ci/enable_branch_protection.sh` so the upgrade is one command (INFRA-03) |
