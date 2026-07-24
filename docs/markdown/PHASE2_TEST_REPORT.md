# Phase 2 — Integration Test & E2E Gate Report (P2-T18)

**Status: ✅ PASSED — Phase 2 gate cleared.**
Date: 2026-07-25 · Auditor: DEV-1 · Issue: [#40](https://github.com/NCollection-Sys/ncollection-erp/issues/40)

The Phase-2 gate: the tenant lifecycle features shipped across Phase 2 (checkout,
provisioning, config-sync, backup, suspend/reactivate) must hold together on a live
multi-tenant stack — and the Phase-1 guarantees must still hold underneath them. This
audit extends the Playwright suite with the public **checkout journey**, exercises the
**lifecycle propagation** end-to-end against a `db_filter`-routed stack, and re-runs the
full Phase-1 regression checklist.

**One real regression was found and fixed during this gate** (config-sync 404 under
`db_filter=^%d$` — R-015, detailed below). That is the gate doing its job: the failure
was invisible to CI and to every standalone run, and would have shipped a broken
config-sync to production. No other new findings; the two Phase-1 gaps stay ticketed
(#177 LOW, #178 MEDIUM).

## Environment

| Tenant / DB | Plan | Modules | Role in this audit |
|---|---|---|---|
| `e2eclienta` | Pro | `crm, sale, account` + core/branding | licensed-feature tenant |
| `e2eclientb` | Basic | same installed; `allowed_module_names='crm'` | unlicensed-feature tenant |
| `e2eadmin` | — | `ncollection_saas` + seeded `E2ESTARTER` plan | platform DB (public checkout endpoints) |
| `albarari` | Demo | `crm, sale, account` + all 8 roles + real data (+`ncollection_auth`) | role-matrix + dashboard tenant |
| `saastest` | — | `ncollection_saas` | platform DB (provisioning + config-sync proofs) |
| `provclient` / `provsync` | ephemeral | provisioned per-run, dropped after | provisioning + config-sync round-trip fixtures |

Stack: routing overlay up (`db_filter=^%d$`, `list_db=False`, nginx edge). Code under test
on branch `feature/40-p2-t18-integration-e2e-gate` at merge-base `c5effd1`
(`ncollection_core 19.0.1.9.0`, `ncollection_saas 19.0.5.0.0`,
`ncollection_branding 19.0.1.6.0`, `ncollection_subscription 19.0.1.1.0`). Reproduce with
the [regression checklist](PHASE1_REGRESSION_CHECKLIST.md) + `make verify-all`.

## Result summary

| # | Deliverable / gate item | Method | Result |
|---|---|---|---|
| a1 | **Checkout journey** added to Playwright | `e2e/tests/checkout.spec.ts` (availability · register→draft · validation) | ✅ 3/3 |
| a2 | **Lifecycle journey** — plan-upgrade / suspend / reconcile propagation | `verify_config_sync.sh` (real cross-DB json2 round-trip under `db_filter`) | ✅ 7/7 |
| b1 | Phase-1 §1 functional suites | `make verify-all` | ✅ routing 8/8 · provisioning 8/8 · config-sync 7/7 · e2e 12/12 |
| b2 | Phase-1 §2 security audit | `scripts/audit/phase1_security_audit.sh` | ✅ 16/16, 1 known (#177) |
| b3 | Phase-1 §3 manual | walked (see §6) | ⚠ human leg — carried forward |
| c1 | **Signed-off test report** in `docs/` | this document | ✅ published |
| c2 | Zero manual server intervention in the lifecycle | R-015 fix (auto-propagation) + provisioning engine | ✅ see §5 |
| c3 | E2E suite green **in CI** | `verify.yml` runs routing + e2e (12/12) | ✅ green |

---

## Evidence

### 1 · Full cross-suite gate (`make verify-all`) — every suite green
```
[1/4] routing & isolation (P1-T06)   SUMMARY: 8 passed, 0 failed.  ✅ Routing is bulletproof.
[2/4] provisioning (P2-T01/T02)      SUMMARY: 8 passed, 0 failed.  (create + rollback + R-014)
[3/4] config sync (P2-T03)           SUMMARY: 7 passed, 0 failed.  ✅ (propagate + suspend + reconcile)
[4/4] end-to-end (P1-T20 + checkout) 12 passed  (auth · isolation · license · visibility · roles ·
                                                 branding · dashboard · checkout ×3)
✅ verify-all: every suite green.   exit 0
```

### 2 · Checkout journey (new — `e2e/tests/checkout.spec.ts`)
The public self-service front door (P2-T16), exercised against the `e2eadmin` platform DB
(`ncollection_saas` + a seeded `E2ESTARTER` plan):
- **Subdomain availability** — a fresh name is `available:true`; an existing tenant DB name
  is `available:false`. ✅
- **Register → draft** — `/nc/checkout/register` creates a **draft** `ncollection.tenant`
  + `ncollection.subscription` (status `trial`, `database_status='not_provisioned'`), and
  `/nc/checkout/status` reports `not_provisioned` / `verified:false`. Platform records
  confirmed via authenticated `search_read`, then unlinked so re-runs stay idempotent. ✅
- **Validation before side effects** — empty required fields → `success:false`,
  `error:'missing_fields'`, no tenant created. ✅

The full paid flow (email-verify → provision → login-ready → Stripe payment) needs the
staging VPS + mailer + queue runner; the deterministic public entrypoint is what runs in
CI (see §7 for the staging-only legs).

### 3 · Lifecycle config-sync propagation (P2-T03) — and the R-015 fix
This is the headline of the gate. `verify_config_sync.sh` provisions a tenant, then drives
the platform-side lifecycle and asserts each change **propagates into the tenant DB** over
the real json2/bearer round-trip:

| Lifecycle event (platform) | Tenant-DB effect asserted | Result |
|---|---|---|
| plan upgrade (`crm` → `crm,sale,account`) | `workspace.config.allowed_module_names` updated | ✅ |
| seat-cap change (`max_users` → 20) | `workspace.config.max_users` = 20 | ✅ |
| `action_suspend()` | `subscription_status='suspended'` (interstitial trigger) | ✅ |
| nightly reconcile after tampering | tampered config healed back to source-of-truth | ✅ |

**Regression found & fixed (R-015).** These four checks *failed* on first run of the gate:
the loopback push returned **HTTP 404** and the sync silently no-op'd. Root cause: the push
posts to `http://localhost:8069` (Host `localhost`) trusting `X-Odoo-Database` to select the
tenant — but under `db_filter=^%d$` Odoo selects the DB from the **Host** (and filters
`X-Odoo-Database` through the same Host check, `odoo/http.py`). `Host: localhost` matches no
tenant → no DB → the model route 404s. Proven empirically (same endpoint, only the Host
varied: `localhost` → 404; `<db>.localhost` → 401 = DB+route resolved). It was masked because
the unit tests `patch('requests.post')` (no real round-trip) and the script had only ever run
on the plain dev stack where `db_filter` is off. **Left unfixed it breaks config-sync for
every tenant in production.** Fix: `_config_sync_push` now sends `Host: <db>.<base_domain>`
(reusing `ncollection_saas.base_domain`), DNS-free — same loopback connection, correct vhost.
Guards: unit assertion on the `Host` header + this script now runs under `db_filter` in
`make verify-all`. Full write-up: [REGRESSIONS.md R-015](REGRESSIONS.md).

### 4 · Phase-1 regression re-run — still holds under Phase-2 code
Section 1 (functional) is the `verify-all` block above. Section 2 (security audit,
`phase1_security_audit.sh`) against the live routed stack:
```
A — cross-tenant RPC isolation ....... ✅ both directions rejected
B — license enforcement (Ring 2) ..... ✅ Basic denied (RPC) / 303 (URL); Pro allowed (control)
C — 8-role access matrix (albarari) ... ✅ all 8 roles exactly as expected (only Owner sees Settings)
D — DB manager unreachable ........... ✅ manager/selector/list → 403 at the edge
SECURITY AUDIT SUMMARY: 16 passed, 0 failed, 1 known (ticketed).
```
No Phase-1 guarantee regressed under the Phase-2 code. Tenant isolation, Ring-2 license
enforcement, the owner-only surface, and the edge DB-manager block all still hold.

### 5 · Zero manual server intervention (acceptance)
The lifecycle now runs with no hand-holding on the server:
- **Provision** — `verify_provisioning.sh` drives create → login-ready and forced-failure →
  rollback with **no manual DB step**; a half-built DB is auto-dropped (8/8, incl. R-014
  role-sync). 
- **Propagate** — plan/seat/status changes reach the tenant automatically via config-sync.
  *This is exactly what R-015 restored:* before the fix the push 404'd, so keeping tenants in
  sync would have required a **manual** per-tenant DB edit. After the fix it is hands-off, and
  the nightly reconcile self-heals drift.
- **Suspend / reactivate** — `action_suspend()` / `action_activate()` project
  `subscription_status` into the tenant, driving the interstitial gate with no server touch.

### 6 · Manual legs (human half of the checklist — carried forward)
Automation cannot cover these; they remain the human sign-off per the Phase-1 checklist §3
and are unchanged by Phase-2 code:
- **Email cross-client rendering** (Gmail/Outlook, mobile+desktop) — QWeb layout covered by
  `test_mail_branding`; live-client visual check is manual.
- **Live auth flows** on a tenant with `ncollection_auth` (idle-timeout logout, brute-force
  throttle, single-use reset) — covered live during P1-T21; `ncollection_auth` still absent
  from the default tenant set (#178).
- **Exploratory** click-through of all 8 roles on `albarari` — no crash, menus match remit.

---

## Findings

| # | Severity | Finding | Status |
|---|---|---|---|
| F-1 | HIGH | **Config-sync push 404s under `db_filter=^%d$`** — every tenant sync silently no-ops; would break plan-change / suspend / reconcile propagation in production | ✅ **FIXED this ticket** — [R-015](REGRESSIONS.md), Host-header fix + 2 guards |
| F-2 | LOW | Unlicensed module's menu root visible to a group-holder (F8) — Ring 1 UX gap; Ring 2 blocks the data | Ticketed [#177](https://github.com/NCollection-Sys/ncollection-erp/issues/177) (carried from Phase 1) |
| F-3 | MEDIUM | `ncollection_auth` not in the default tenant module set — provisioned tenants lack app-level auth hardening by default | Ticketed [#178](https://github.com/NCollection-Sys/ncollection-erp/issues/178) (carried from Phase 1) |

**No CRITICAL findings.** The one HIGH (F-1) is fixed in this PR with a regression guard;
the two carried findings remain ticketed.

## What this report does NOT cover (staging-only legs)

The acceptance names a full staging journey; these legs require the staging VPS and external
services and are **not** exercised by the local/CI gate. They are covered by shipped tooling,
tested there, not here:
- **Stripe payment (test mode)** — P2-T05 billing; needs Stripe test keys + webhook endpoint
  on staging. Verified against Stripe test mode on staging, not in CI.
- **Backup + tenant restore** — P2-T06/T13 (`pgbackrest` PITR + per-tenant
  `ncollection.backup`); the create/restore round-trip runs on staging with real volumes.
- **Live email delivery** — a real SMTP relay (staging), vs the QWeb/layout unit coverage here.

These are declared gaps, not silent ones: the deterministic platform logic is proven locally
+ in CI; the infrastructure-bound legs are proven on staging.

## Sign-off

The Phase-2 tenant lifecycle holds together on a live, `db_filter`-routed multi-tenant stack:
public checkout creates a draft workspace, provisioning is fully automated with rollback,
plan/seat/status changes propagate into tenants automatically (config-sync, restored by the
R-015 fix), and suspend/reactivate drive the interstitial — all with **zero manual server
intervention**. The full Phase-1 regression (functional + security) still passes underneath.
The single real regression this gate surfaced is fixed with a guard; two prior gaps stay
ticketed.

**Phase 2 gate: PASSED.** — DEV-1, 2026-07-25.
