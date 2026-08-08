# NCollection Tenant Role Matrix

Status: Authoritative (referenced by ARCHITECTURE_SECURITY.md §11 checklist)

Source of truth pairing: this document ↔ `ncollection_core/hooks.py::ROLE_IMPLICATIONS`
and `ncollection_core/security/role_groups.xml`. **They must change together** —
the transitive-closure test in `ncollection_core/tests/test_roles.py` fails CI when
a role's implied set exceeds what this matrix allows.

Task: [P1-T08]. Audited by: [P1-T21] Phase-1 security audit.

---

## 1. Design rules

1. **Ring-3 safety.** Provisioning installs only the plan's modules
   (ARCHITECTURE_SECURITY §4). Role XML therefore contains **zero** references
   to optional-module groups; those are runtime-linked by
   `_sync_role_implications()`, which skips modules that are not installed.
2. **Re-sync contract.** After any module install/upgrade on a tenant DB,
   `_sync_role_implications(env)` must be re-run (wired via provisioning
   P2-T01 and config sync P2-T03). Idempotent by design.
3. **Roles are additive.** Checkbox groups in one category ("NCollection
   Roles"); a user may combine roles (e.g. Manager + Sales). There is no
   exclusive-selection radio.
4. **Escalation audit.** Any change widening a chain must update this file in
   the same PR, or CI fails.

## 2. The matrix

| Role (xml-id) | Implies (static, base only) | Implies (runtime-linked, when module installed) | Rationale |
|---|---|---|---|
| **Employee** `group_role_employee` | `base.group_user` | — | Self-service floor: own documents only. Every other role stands on it. |
| **Sales** `group_role_sales` | Employee | `sales_team.group_sale_salesman` | Own-documents salesperson scope (leads, quotations, SOs). NOT `..._all_leads` — that is Manager territory. |
| **Warehouse** `group_role_warehouse` | Employee | `stock.group_stock_user` | Operational stock user: receipts, deliveries, transfers. Not inventory *manager* (adjustments/config stay above). |
| **HR** `group_role_hr` | Employee | `hr.group_hr_user` | HR officer: employee records administration. Not HR manager (contracts/payroll config stay above). |
| **Accountant** `group_role_accountant` | Employee | `account.group_account_user` | Full accounting features (Odoo "Show Full Accounting Features"). Financial WRITE role. |
| **Manager** `group_role_manager` | Employee | `sales_team.group_sale_salesman_all_leads`, `stock.group_stock_user`, `hr.group_hr_user` | Department-level oversight: ALL-documents sales visibility + operational access across installed op modules. Deliberately NO financial groups and NO Settings. |
| **CEO** `group_role_ceo` | Manager (chain) | `account.group_account_readonly` | Sees everything; financials strictly READ-ONLY (`group_account_readonly`, never `group_account_user`). No Settings. |
| **Owner** `group_role_owner` | CEO (chain), `base.group_system` | `account.group_account_user` | Full workspace control incl. billing and user management. `base.group_system` grants Settings — its dangerous surface (Apps, debug, technical menus) is stripped by P1-T11, which depends on this task. Financial write overrides CEO's read-only. |

## 3. Inheritance chains (transitive view)

```
Employee ── base.group_user
Sales ────── Employee + [sale_salesman]
Warehouse ── Employee + [stock_user]
HR ───────── Employee + [hr_user]
Accountant ─ Employee + [account_user]
Manager ──── Employee + [salesman_all_leads, stock_user, hr_user]
CEO ──────── Manager + [account_readonly]
Owner ────── CEO + base.group_system + [account_user]
```

Bracketed = runtime-linked (present only when the module is installed).

## 4. Explicit decisions & their reasons

| # | Decision | Reason |
|---|---|---|
| D1 | Owner implies `base.group_system` | "Full control incl. billing" requires Settings/user management. Accepted risk pre-P1-T11; that task strips Apps/debug/technical surface. Approved 2026-07-19 (issue #9 plan gate). |
| D2 | CEO gets `account.group_account_readonly`, never `account_user` | Issue text: "read-only financials". The readonly group is the exact Odoo mechanism. |
| D3 | Owner adds `account_user` despite inheriting CEO's readonly | implied_ids are additive — readonly + user = user wins (superset). Owner must be able to act on billing. |
| D4 | Manager gets `..._all_leads`, Sales only `..._salesman` | Department-level vs own-documents is exactly the difference between these two Odoo sales groups. |
| D5 | Manager gets NO financial group | Financial visibility at management level is CEO/Accountant/Owner territory; least privilege (ARCHITECTURE_SECURITY principle 5). |
| D6 | No role implies portal/public groups | Internal-user roles only; portal access is P6-T02 scope. |
| D8 | The scheduler service account is NOT in this matrix's inheritance graph | `group_cron_service` (#347) is not a human role. It is the identity tenant crons run as, so Ring-2 licence enforcement applies to scheduled work — an `ir.cron` with no `user_id` runs as superuser, and `env.su` bypasses Odoo's access-check machinery entirely (`check_access`/`has_access`/`_filtered_access` all short-circuit before any override, and `search()` ignores ACLs). It carries NO implied groups and appears in no inheritance chain. |
| D9 | The scheduler gets per-model read-only ACLs, never app-user groups | The first attempt granted `sales_team.group_sale_salesman_all_leads`, `stock.group_stock_user` and `hr.group_hr_user` under a comment saying "read grants only". Review proved that false: those carry write/create on `sale.order`/`crm.lead` and **unlink** on stock lots, packages and `hr.employee` — the delete rights of three apps for a non-interactive account. Replaced with read-only (1,0,0,0) rows on exactly the five models the detectors read (`sale.order`, `account.move`, `hr.attendance`, `stock.quant`, `stock.warehouse.orderpoint`), created in code because a CSV row cannot be conditional on a module being installed. Write access is `ncollection.alert` only, no unlink. |
| D7 | Cross-module links via runtime hook, not manifest deps or bridge modules | Hard deps would force-install all ERP modules on every tenant, breaking Ring 3. Bridge modules (4 extra addons) deferred as unnecessary weight; revisit if the re-sync contract proves fragile. Approved 2026-07-19 (issue #9 plan gate, Option A). |

## 5. Escalation-audit checklist (P1-T21 input)

- [ ] Each role's transitive `implied_ids` closure matches §2 exactly (automated: `test_roles.py::test_no_unexpected_escalation`)
- [ ] No role reaches `base.group_erp_manager`/`base.group_system` except Owner
- [ ] CEO cannot post/modify journal entries on a full-accounting tenant
- [ ] Manager sees no Accounting menus
- [ ] Employee sees only self-service surface
