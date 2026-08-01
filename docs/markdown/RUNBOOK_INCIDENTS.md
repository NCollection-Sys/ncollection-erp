# Incident Response Runbook (P3-T13)

> **Scope split** (same convention as [RUNBOOK_STAGING.md](RUNBOOK_STAGING.md)).
> The *procedure* below — severities, roles, steps, rollback trigger, post-mortem
> — ships in this repo and is ready now. The *people* half — a real on-call
> rotation with real names and a real pager — is operational and only you can put
> it in place. The rotation table below is a **template**; fill it before go-live
> (P3-T13) and keep it current. An incident runbook nobody is paged by is a
> document, not a safety net.

This is the on-call playbook for the production NCollection ERP fleet
(multi-tenant Odoo 19, **database-per-tenant**). It answers one question under
pressure: *what do I do right now?*

---

## Severities

| Sev | Definition | Examples | Ack target | Update cadence |
|---|---|---|---|---|
| **SEV-1** | Fleet down, or **any cross-tenant data exposure** | edge/nginx down, `db` unreachable, a tenant seeing another tenant's data, auth bypass | **15 min** | every 30 min |
| **SEV-2** | One tenant down, or degraded fleet | a single tenant DB corrupt, provisioning stuck, p95 latency > 5s | 1 hour | every 2 h |
| **SEV-3** | Minor / cosmetic, no data risk | one report wrong, a non-critical cron failing | next business day | on resolve |

> A **cross-tenant isolation break is always SEV-1**, even for one record — it is
> the platform's cardinal risk ([ARCHITECTURE_SECURITY.md](ARCHITECTURE_SECURITY.md),
> [REGRESSIONS.md](REGRESSIONS.md) R-004). Isolate first, investigate second.

## Roles (small team — one person may wear several)

- **Incident Commander (IC)** — owns the incident, decides mitigate-vs-rollback, keeps the timeline. The only role that *must* be filled immediately.
- **Ops** — runs the commands (deploy/rollback/restore, DB, nginx).
- **Comms** — tenant/stakeholder updates; shields IC and Ops from status-chasing.

## Response procedure

1. **Acknowledge** the page within the Sev target. Open an incident channel/thread; the first responder is IC until handed off.
2. **Assess & classify** — assign a severity (above). If in doubt, over-classify; you can downgrade.
3. **Contain first for isolation/security** — if any hint of cross-tenant exposure or breach: take the affected surface **offline** before diagnosing. A dark tenant beats a leaking one.
4. **Mitigate** — the fastest safe path to service:
   - Bad deploy → **roll back**: `./scripts/deploy/rollback.sh` (see [RUNBOOK_STAGING.md](RUNBOOK_STAGING.md)). Rehearsed, one command.
   - Data loss / corruption → **restore**: [RUNBOOK_BACKUP_PITR.md](RUNBOOK_BACKUP_PITR.md) (fleet PITR) / [RUNBOOK_TENANT_BACKUP.md](RUNBOOK_TENANT_BACKUP.md) (single tenant — restore only the affected DB, never the fleet).
   - Host/edge → [RUNBOOK_MONITORING.md](RUNBOOK_MONITORING.md) for what's firing; [RUNBOOK_SECURITY.md](RUNBOOK_SECURITY.md) if it's an attack.
5. **Verify recovery** — `./scripts/deploy/smoke-test.sh`, then confirm the alert cleared and the affected tenant(s) work.
6. **Communicate** — post updates at the Sev cadence; a final "resolved" note when done.
7. **Stand down** — IC declares the incident closed once service is stable and verified.

## Rollback decision rule

Prefer **rollback over debugging in production**. If a change merged in the last
deploy window and the symptom started after it, roll back first and diagnose from
the restored-good state — do not root-cause on a live, degraded fleet. `develop`
merges are re-verified by `canary.yml`; a red canary right after a merge is a
rollback signal (see [BRANCH_PROTECTION.md](BRANCH_PROTECTION.md)).

## On-call rotation (TEMPLATE — fill before go-live)

| Week | Primary | Secondary | Pager / contact |
|---|---|---|---|
| _fill_ | _name_ | _name_ | _method_ |

- **Escalation:** Primary → (no ack in Sev target) → Secondary → IC/owner.
- **Handoff:** end-of-rotation note — open incidents, watch-items, anything fragile.
- Keep this table current; a stale rotation pages the wrong person at 03:00.

## After the incident (blameless)

Within 3 business days, write a short post-mortem: **timeline · root cause ·
what made it better/worse · action items (owner + date)**. If it was a
regression, add a row to [REGRESSIONS.md](REGRESSIONS.md) with the **guard** that
prevents recurrence — a regression is not closed until a guard exists. Blameless:
the target is the system, never the person.

## Quick reference

| Situation | First move |
|---|---|
| Suspected cross-tenant leak | Take the surface **offline** → SEV-1 → isolate → then diagnose |
| Bad deploy | `./scripts/deploy/rollback.sh` |
| One tenant's data corrupt | Restore just that DB — [RUNBOOK_TENANT_BACKUP.md](RUNBOOK_TENANT_BACKUP.md) |
| Fleet-wide data loss | Fleet PITR — [RUNBOOK_BACKUP_PITR.md](RUNBOOK_BACKUP_PITR.md) |
| "Is it up?" | `./scripts/deploy/smoke-test.sh` |
| What's firing? | [RUNBOOK_MONITORING.md](RUNBOOK_MONITORING.md) |
