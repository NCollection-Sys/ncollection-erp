# Branch Protection Policy

Status: Policy of record (P1-T05). Enforcement is **convention** on the current
GitHub Free org and becomes **hard-enforced** the moment the org is on a plan
that supports protected branches / rulesets (Team, Enterprise, or a public
repo).

---

## 1. Protected branches

| Branch | Role | Protection |
|---|---|---|
| `main` | Stable / release | Highest — never receives direct pushes; only fast-forward merges from `develop` via PR |
| `develop` | Active integration | All feature work merges here via PR |

Feature branches: `feature/<issue#>-<task-id>` off `develop` (see CLAUDE.md
workflow). They are unprotected and deleted after merge.

## 2. Required to merge into `develop` or `main`

1. **All CI checks green.** The required status checks are the four blocking
   jobs in `.github/workflows/ci.yml`:
   - `lint` — flake8 + XML well-formedness + pylint-odoo (baseline gate)
   - `architecture-guard` — two-layer / Odoo-19-syntax / secret checks
   - `test` — addon install + scoped Odoo test suite on a real PostgreSQL
   - `build` — Docker compose smoke test (HTTP 200 on `/web/login`)

   The `security-scan` job (pip-audit + trivy) is **intentionally
   non-blocking** in this phase — its reports are advisory (job summary +
   uploaded artifacts) and must NOT be a required check yet.

2. **At least 1 approving review** from another team member. No self-approval,
   no self-merge — the PR author never merges their own PR.

3. **Branch up to date with the base** before merge (so `architecture-guard`
   diffs against the latest `develop`).

4. **Conversations resolved** — no unaddressed review threads.

## 3. Enabling hard enforcement (when the plan supports it)

GitHub → repo **Settings → Branches → Add branch ruleset** (or classic
*Branch protection rule*) for `develop` and `main`:

- ✅ Require a pull request before merging → **Require approvals: 1**
- ✅ Dismiss stale approvals on new commits
- ✅ Require status checks to pass → add `lint`, `architecture-guard`,
  `test`, `build` (leave `security-scan` OUT — non-blocking)
- ✅ Require branches to be up to date before merging
- ✅ Require conversation resolution before merging
- ✅ Block force pushes
- ✅ Restrict deletions

Equivalent via the API (`gh api`), for reference:

```bash
gh api -X PUT repos/NCollection-Sys/ncollection-erp/branches/develop/protection \
  -F required_status_checks.strict=true \
  -F 'required_status_checks.contexts[]=lint' \
  -F 'required_status_checks.contexts[]=architecture-guard' \
  -F 'required_status_checks.contexts[]=test' \
  -F 'required_status_checks.contexts[]=build' \
  -F enforce_admins=true \
  -F required_pull_request_reviews.required_approving_review_count=1 \
  -F restrictions=
```

## 4. Until then (Free org)

Branch protection is **not enforceable** on GitHub Free for private repos, so
today the policy above is upheld by convention and by the `/solve-issue`
workflow (which opens a PR to `develop`, waits for CI, and requires one review
before handoff). Reviewers should still manually confirm the four checks are
green before approving.

## Changelog

| Date | Change |
|---|---|
| 2026-07-19 | Initial policy (P1-T05); four required checks + 1 approval; security-scan explicitly non-blocking |
