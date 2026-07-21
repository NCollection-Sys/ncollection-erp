# Demo Tenant — Al Barari Trading

A populated workspace you can actually log into and look at. Built to answer
"what does this product look like?", and to validate the P1-T17 dashboard against
real numbers rather than empty series.

```bash
make routing-up      # the stack (db_filter ON, nginx edge)
make demo-tenant     # build/refresh it (a few minutes on first run)
```

| | |
|---|---|
| **URL** | http://albarari.localhost |
| **Owner** | `owner@albarari.ae` / `demo1234` |
| **Company** | Al Barari Trading (currency **AED**) |
| **Plan** | `DEMO` — licensed `crm,sale,account` |

## Log in as different people

The dashboard is **role-aware**, and that is easiest to see by switching users.
All accounts share the password `demo1234`.

| Login | Role | Sees |
|---|---|---|
| `layla@albarari.ae` | Owner | everything — 5 KPIs, both charts, all quick actions |
| `omar@albarari.ae` | CEO | everything (financials read-only) |
| `fatima@albarari.ae` | Accountant | receivables · payables · cash · revenue chart · New Invoice — **no pipeline** |
| `yousef@albarari.ae` | Sales | sales KPI · top-customers chart · New Quotation — **no financials** |
| `sara@albarari.ae` | Manager | pipeline + operations |
| `bilal@albarari.ae` | Warehouse | operations |
| `noura@albarari.ae` | HR | personal only |
| `aisha@albarari.ae` | Employee | personal only — no charts, no actions |

Gating is enforced **server-side**: a widget a role may not see is absent from
the payload, not hidden in the browser (Standing Rule 4).

## How it is built

Three stages, wired in `scripts/demo/build_demo_tenant.sh`:

1. **Platform DB** (`ncplatform`) with `ncollection_saas` — the SaaS control plane.
2. **Provisioned through the P2-T01 engine** (plan → tenant → job → `action_run_sync`),
   the same path a real customer signup takes. **The engine is not modified**: it
   still passes `--without-demo=True`, so no paying customer can ever receive
   Odoo's demo data.
3. **Seeded** by `scripts/demo/seed_demo_data.py`, run in an `odoo shell`
   subprocess bound to the tenant database — never a cross-database ORM call
   (Standing Rule 3), the same contract `seed_tenant.py` follows.

## What gets seeded, and why

Content mirrors `demo/src/mock/data.ts` so the product matches the prototype:
customers **Emaar Properties, Majid Al Futtaim, Nakheel, Al-Futtaim Group, DAMAC,
Aldar Properties**, staff across all 8 roles, vendors, and a service product.

Every record exists because a specific widget queries it. Remove any one and that
tile silently returns to zero:

| Widget | Needs |
|---|---|
| `sales_this_month` (+ trend) | confirmed orders in **this and last** month |
| `top_customers` | confirmed orders across **≥5** partners |
| `receivables` / `payables` | **posted**, unpaid invoices / bills |
| `revenue_6m` | posted income lines across **6 months** |
| `cash_bank` | a posted entry on the bank journal |
| `open_activities` | `mail.activity`, spread across assignees |

Two details that are easy to get wrong:

- **Salesperson on orders.** Odoo's record rules scope a salesperson to their own
  orders. Without `user_id`, a Sales user sees `0` and the demo looks broken.
- **Currency first.** AED is set before any accounting entry exists; Odoo resists
  changing a company's currency once journal items reference it.

## Rebuilding

```bash
make demo-tenant              # idempotent — skips what already exists
REBUILD=1 make demo-tenant    # drop the tenant and start clean
SEED_FORCE=1 make demo-tenant # re-seed an existing tenant
make demo-clean               # drop the tenant AND the platform DB
```

The demo owns the `albarari` / `ncplatform` names. Fixture namespaces are
separate by design — routing owns `rt*`, e2e owns `e2e*` — so `make routing-clean`
and `make e2e-clean` cannot touch this tenant.

## Known gap

A freshly provisioned tenant needs `_sync_role_implications()` re-run before its
roles grant Odoo app access — the seed does this, but the **provisioning engine
does not**, so real tenants are affected too. Tracked separately; see
`REGRESSIONS.md` R-014.
