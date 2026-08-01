# Go-Live Checklist — First Production Deployment (P3-T13)

> **This is the Phase-3 go-live gate.** No later-phase work is meant to merge
> until this gate is green ([DELIVERABLE_1_SYSTEM_DESIGN.md](DELIVERABLE_1_SYSTEM_DESIGN.md) §8).
>
> **Scope split** (same convention as [RUNBOOK_STAGING.md](RUNBOOK_STAGING.md)).
> Part A is **automated** — the go-live *instrument* ships in this repo and is
> verified by `make go-live-check` (read-only, no deploy). Part B is **manual** —
> real-world steps on real paid infrastructure that only the operator can do, and
> whose evidence only the operator can produce. **#53 closes only when Part B is
> complete and every item has linked evidence** (a PR that merges Part A does
> **not** close it — see the note at the bottom).

**Acceptance (verbatim):** *production serves a real paying tenant; every
checklist item has linked evidence.*

Run the automated half any time:

```bash
make go-live-check      # verifies Part A; lists Part B as reminders
```

---

## Part A — Automated readiness (verified by `make go-live-check`)

Each row is checked by [`scripts/deploy/go_live_check.sh`](../../scripts/deploy/go_live_check.sh).
Green here means the tooling/procedure exists — not that we are live.

- [ ] **Deploy pipeline present** — `Dockerfile`, `docker-compose.staging.yml`, `deploy-staging.yml`, `scripts/deploy/{deploy,rollback,smoke-test,harden}.sh` — _evidence: `make go-live-check` §A_
- [ ] **Rollback runnable + clean** — `rollback.sh` present, executable, shellcheck-clean — _evidence: §B_
- [ ] **Host hardening artifacts** — `verify_hardening.sh` + `config/hardening/*` (P2-T08) — _evidence: §C_
- [ ] **Monitoring runbook** — [RUNBOOK_MONITORING.md](RUNBOOK_MONITORING.md) (P2-T10) — _evidence: §D_
- [ ] **Backup/PITR runbooks** — [RUNBOOK_BACKUP_PITR.md](RUNBOOK_BACKUP_PITR.md) + [RUNBOOK_TENANT_BACKUP.md](RUNBOOK_TENANT_BACKUP.md) (P2-T04/T05) — _evidence: §D_
- [ ] **Security runbook** — [RUNBOOK_SECURITY.md](RUNBOOK_SECURITY.md) (P2-T08) — _evidence: §D_
- [ ] **Incident runbook** — [RUNBOOK_INCIDENTS.md](RUNBOOK_INCIDENTS.md) (this task) — _evidence: §D_
- [ ] **Regression suite wired** — `make verify-all` target + [PHASE1_REGRESSION_CHECKLIST.md](PHASE1_REGRESSION_CHECKLIST.md) — _evidence: §E_
- [ ] **Secret hygiene** — `.env.example` present, `.env` gitignored — _evidence: §E_

## Part B — Manual go-live (operator-only, real infrastructure)

These are **not** auto-verifiable. Do each, then paste the evidence link.

- [ ] **Security assessment (P3-T12) signed off** against the production build — _evidence: <link>_
- [ ] **PITR verified ON PRODUCTION** — an actual restore-test on the prod cluster, not just config present — _evidence: <link>_
- [ ] **Tenant backup + restore verified ON PRODUCTION** for a real tenant DB — _evidence: <link>_
- [ ] **Monitoring + alerting live** — an alert actually fired to the on-call channel from the prod host — _evidence: <link>_
- [ ] **UAE compliance sanity check** — CoA (P3-T05), AED (P3-T06), FTA invoice (P3-T09) validated on prod data — _evidence: <link>_
- [ ] **Full regression + E2E green** on the release commit — `make verify-all` output — _evidence: <link>_
- [ ] **Rollback rehearsed** on staging/prod — timed, one command, recorded outcome — _evidence: <link>_
- [ ] **Incident runbook + on-call rotation agreed** — rotation table filled, pager tested — _evidence: <link>_
- [ ] **Production deployed** — `deploy.sh` run, `smoke-test.sh` green against the public hostname — _evidence: <link>_
- [ ] **First real paying tenant onboarded** — signed up, provisioned, using it — _evidence: <link>_

---

## How to complete this gate

1. `make go-live-check` → Part A all ✅ (fix any ❌ before proceeding).
2. Stand up production and work Part B top-to-bottom (see [RUNBOOK_STAGING.md](RUNBOOK_STAGING.md) for the one-time server + secrets steps the deploy pipeline needs).
3. Paste each Part-B evidence link above **and in issue #53**.
4. Only when Part B is fully evidenced: close **#53** — it is the hard gate the
   dependency checks treat as "done". Closing it before production actually
   serves a paying tenant would give every downstream task a false green.

> **Why #53 stays open after the tooling PR merges:** the PR that adds this
> checklist, the incident runbook, and `go-live-check` delivers the *instrument*.
> The *acceptance* ("production serves a real paying tenant") is a real-world
> event. Per project convention **closed issue = completed task**, so #53 must not
> be closed until that event has happened and is evidenced here.
