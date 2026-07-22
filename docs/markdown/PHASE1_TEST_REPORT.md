# Phase 1 — Integration Test & Security Audit Report (P1-T21)

**Status: ✅ PASSED — Phase 1 gate cleared.**
Date: 2026-07-22 · Auditor: DEV-1 · Issue: [#22](https://github.com/NCollection-Sys/ncollection-erp/issues/22)

The Phase-1 gate: features that pass in isolation can fail together. This audit runs every
guarantee against a live multi-tenant stack, tries to break the security boundaries on purpose,
and signs off only if the boundaries hold. **No critical findings; two lower-severity findings
ticketed** (#177 LOW, #178 MEDIUM).

## Environment

| Tenant | Plan | Modules | Role |
|---|---|---|---|
| `e2eclienta` | Pro | `crm, sale, account` + core/branding | licensed-feature tenant |
| `e2eclientb` | Basic | same installed; `allowed_module_names='crm'` | unlicensed-feature tenant |
| `albarari` | Demo | `crm, sale, account` + all 8 roles + real data (+`ncollection_auth` for item 6) | role-matrix + dashboard tenant |

All tenants verified on **current code** before testing (`ncollection_core 19.0.1.8.0`,
`ncollection_branding 19.0.1.4.0`) — `make doctor` clean of schema drift. Reproduce the whole
audit with the [regression checklist](PHASE1_REGRESSION_CHECKLIST.md).

## Result summary

| # | Audit item | Method | Result |
|---|---|---|---|
| 1 | Full E2E suite + exploratory | `make verify-all` (routing + e2e + provisioning) | ✅ routing 8/8 · e2e 9/9 · provisioning 8/8 |
| 2 | 8-role click-through matrix | `phase1_security_audit.sh` §C, all 8 roles | ✅ all 8 as expected |
| 3 | Cross-tenant RPC attacks **must fail** | `phase1_security_audit.sh` §A | ✅ both directions rejected |
| 4 | License bypass (URL + RPC) **must fail** | `phase1_security_audit.sh` §B | ✅ Ring 2 denies; Ring 1 note → #177 |
| 5 | Branding audit — zero "odoo" | `e2e/tests/branding.spec.ts` | ✅ no "odoo" in public entry URL |
| 6 | Auth: lockout, session timeout, reset | live probes on `albarari` | ✅ works; **finding → #178** |
| 7 | Dashboard correctness per role | `e2e/tests/dashboard.spec.ts` + demo-tenant proof | ✅ per-role, real figures |
| 8 | Email rendering | P1-T18 branded layout + `test_mail_branding` | ✅ QWeb; live-client = manual (noted) |
| 9 | Regression checklist committed | [PHASE1_REGRESSION_CHECKLIST.md](PHASE1_REGRESSION_CHECKLIST.md) | ✅ published |
| 10 | Test report in `docs/` | this document | ✅ published |

---

## Evidence

### 1 · Functional suites (integration)
```
Routing & isolation (P1-T06)      SUMMARY: 8 passed, 0 failed.  ✅ Routing is bulletproof.
E2E platform guarantees (P1-T20)  9 passed
Provisioning (P2-T01/T02)         SUMMARY: 8 passed, 0 failed.  (incl. R-014 role-sync guard)
```

### 2 · 8-role access matrix (`albarari`)
Decisive probes per role: can it read `account.move.line`, can it read `sale.order`, does it see
the **Settings** menu. The security guarantees this proves: **only the Owner reaches Settings**
(P1-T11), and roles with no operational groups (warehouse/hr/employee) see **nothing** financial
or sales. Business roles read both models via **standard Odoo cross-app ACLs** (salesman →
own `account.move.line`; accountant → `sale.order` for invoicing), verified live — not an
over-permission.

| Role | account.move.line | sale.order | Settings |
|---|---|---|---|
| Owner | ✅ | ✅ | ✅ |
| CEO | ✅ | ✅ | ❌ |
| Manager | ✅ | ✅ | ❌ |
| Sales | ✅ | ✅ | ❌ |
| Warehouse | ❌ | ❌ | ❌ |
| HR | ❌ | ❌ | ❌ |
| Accountant | ✅ | ✅ | ❌ |
| Employee | ❌ | ❌ | ❌ |

### 3 · Cross-tenant RPC isolation (attacks rejected)
Forcing `e2eclienta`'s session cookie onto `e2eclientb` (and the reverse) yields **no uid** —
sessions are database-scoped. A login on one tenant is worthless on another.

### 4 · License enforcement (Ring 2 — bypass attempts denied)
On the Basic tenant (`sale` installed but unlicensed), with the Sales groups held by `biz`:
- **RPC** read of `sale.order` → **AccessError** (P1-T10). ✅
- **URL** (`/odoo/action-sale.action_orders`) → 303, but the data stays ORM-gated. ✅
- Control: the same RPC on the Pro tenant **succeeds**. ✅
- Ring 1 (menu) note: the Sales menu root remains visible to the sale-group holder (**F8**,
  ticket #177) — a UX gap, not a breach; Ring 2 blocks the data, as proven above.

### 5 · Branding
`e2e/tests/branding.spec.ts`: the public entry URL contains no "odoo". Public URL rewriting
(P1-T15) + white-labeling (P1-T13/14/16) verified by the e2e suite.

### 6 · Auth hardening (`ncollection_auth` on `albarari`)
- **Session timeout:** `inactive_session_time_out_delay = 7200` (2h idle logout) — OCA
  `auth_session_timeout`. ✅
- **Audit log:** `ncollection.auth.log` recorded `login_success` and `login_failed` (with IP)
  live. ✅
- **Brute-force:** Odoo core login cooldown + the **nginx edge 429 rate-limit** (`/web/login`,
  10 r/m, P1-T03) — the latter proven repeatedly during E2E. ✅
- **Reset:** `auth_signup` (Odoo core, time-limited single-use tokens). ✅
- **Finding (#178, MEDIUM):** `ncollection_auth` is **not** in `CORE_TENANT_MODULES`, so
  provisioned tenants lack the app-level audit log + idle timeout by default (the nginx edge
  limit still applies to all). Policy + P2-T01 engine change — out of scope here.

### 7 · Dashboard per role
`e2e/tests/dashboard.spec.ts` + the live demo-tenant proof: Owner sees AED 243.8K sales (+108%),
283.3K receivables, 250K cash and both charts; Accountant sees financials only; Sales sees the
pipeline only; Employee sees activities only. Gating is server-side (a denied widget is absent
from the payload, not hidden).

### 8 · Email rendering
Branded base layout (`mail.mail_notification_layout` override, P1-T18) present; `test_mail_branding`
covers QWeb rendering. **Live Gmail/Outlook cross-client rendering is manual** and out of scope
for automation here — carried on the checklist as a manual step.

---

## Findings

| # | Severity | Finding | Ticket |
|---|---|---|---|
| F-1 | LOW | Unlicensed module's menu root visible to users holding that module's group (F8) — Ring 1 UX gap; Ring 2 blocks the data | [#177](https://github.com/NCollection-Sys/ncollection-erp/issues/177) |
| F-2 | MEDIUM | `ncollection_auth` not in the default tenant module set — provisioned tenants lack app-level auth hardening by default | [#178](https://github.com/NCollection-Sys/ncollection-erp/issues/178) |

**No CRITICAL findings.** Acceptance ("all criticals fixed or ticketed") is met: there are no
criticals, and both lower findings are ticketed.

## Sign-off

The Phase-1 platform holds together under combined testing. Tenant isolation, license
enforcement (defense-in-depth Rings 1/2), the owner-only administrative surface, role
differentiation, branding, auth hardening and per-role dashboards all behave correctly, with the
two documented gaps ticketed for follow-up.

**Phase 1 gate: PASSED.** — DEV-1, 2026-07-22.
