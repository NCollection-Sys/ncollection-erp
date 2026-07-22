# Phase 1 Regression Checklist

The guarantees Phase 1 established. **Every later phase re-runs this before its own sign-off** —
a Phase-3 feature that quietly breaks tenant isolation or license enforcement must be caught
here, not in production.

Most of it is automated. Run the automated block first; it either passes wholesale or points at
exactly what broke.

## 0 · Preconditions (do this first — stale schema causes false failures)

```bash
make routing-up                                   # the stack (db_filter ON, nginx edge)
make doctor                                       # MUST be clean of "schema behind" warnings
make e2e-clean && bash e2e/scripts/setup_e2e_tenants.sh   # fresh Pro/Basic tenants on current code
REBUILD=1 make demo-tenant                         # fresh albarari with all 8 roles + data
```

> If `make doctor` reports a module "installed at X but code is Y (schema behind)", upgrade or
> rebuild that database first — an out-of-date schema crashes auth and produces misleading audit
> failures (this happened during the P1-T21 audit itself). See REGRESSIONS.md R-012 / R-012b.

## 1 · Automated — the functional suites

```bash
make verify-all        # routing isolation + e2e platform guarantees + provisioning
```

- [ ] **Routing & isolation (P1-T06):** `verify_routing.sh` → `8 passed, 0 failed`
- [ ] **E2E platform guarantees (P1-T20):** `9 passed` (auth · isolation · license · visibility · roles · branding · dashboard)
- [ ] **Provisioning (P2-T01/T02):** `8 passed, 0 failed` — create → login-ready, forced-failure → rollback, R-014 role-sync

## 2 · Automated — the security audit

```bash
bash scripts/audit/phase1_security_audit.sh       # exits 0 only if the boundaries hold
```

- [ ] **Cross-tenant RPC isolation:** a session on tenant X is rejected on tenant Y (both ways)
- [ ] **License enforcement (Ring 2):** an unlicensed model's read is denied by AccessError (RPC + URL); the licensed tenant still reads it
- [ ] **8-role access matrix:** every role reaches exactly its surface; only the Owner sees Settings; warehouse/hr/employee see nothing financial or sales
- [ ] **DB manager unreachable:** `/web/database/manager|selector|list` → 403 at the edge
- [ ] Summary: `passed, 0 failed` (known/ticketed gaps — currently F8 #177 — are noted, not failures)

## 3 · Manual — what automation cannot cover

- [ ] **Email rendering (P1-T18):** send a branded invitation / invoice / password-reset and open
      it in **Gmail and Outlook** (mobile + desktop) — layout intact, no "odoo".
- [ ] **Auth flows live (P1-T19):** on a tenant with `ncollection_auth`, confirm idle-session
      timeout logs out after the configured delay, repeated bad logins are throttled (edge 429 +
      core cooldown), and a password-reset link is single-use and expires.
- [ ] **Branding spot-check:** log in to a real tenant, confirm no "odoo" in any public-facing
      URL, the tenant's colours apply (P1-T16), and the dashboard loads under 2s.
- [ ] **Exploratory:** click through each of the 8 roles on `albarari`; nothing crashes, each
      role's dashboard and menus match its remit.

## 4 · Known / accepted gaps (must stay ticketed, not silently reintroduced)

| Gap | Severity | Ticket |
|---|---|---|
| Unlicensed menu root visible to a group-holder (F8) — Ring 1 UX only, Ring 2 holds | LOW | [#177](https://github.com/NCollection-Sys/ncollection-erp/issues/177) |
| `ncollection_auth` not in the default tenant module set | MEDIUM | [#178](https://github.com/NCollection-Sys/ncollection-erp/issues/178) |

## Sign-off rule

Phase N is not "done" until section 1 and 2 pass wholesale, section 3 is walked manually, and any
**new** finding is fixed or ticketed. A green run of sections 1–2 is the machine-checkable half;
section 3 is the human half. See [PHASE1_TEST_REPORT.md](PHASE1_TEST_REPORT.md) for the reference
Phase-1 result.
