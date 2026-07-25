# SaaS Admin Dashboard (P2-T15)

The operational cockpit for **NCollection platform staff** — surfaced under the
**NCollection SaaS** menu, gated by the new **Platform Admin** group.

## Access — the Platform Admin group

- Group: `ncollection_subscription.group_platform_admin` (Settings → Users, category
  **NCollection**). In Odoo 19 the category lives on a `res.groups.privilege`
  (`Platform`) that the group points at via `privilege_id`.
- `base.group_system` **implies** it, so existing system administrators keep access.
- The restriction is **mirrored at the ORM** (Rule 7): the SaaS root menu carries
  `groups="…group_platform_admin"`, and matching `ir.model.access` rows grant the
  group read (+ write for quick actions) on the dashboard, tenants, subscriptions,
  plans and provisioning jobs. A user without the group is denied at the ORM, not
  just in the UI.

## What it shows

| Section | Purpose |
|---|---|
| **KPI cards** | tenants (total/active/trial), active subscriptions, **MRR**, trial-conversion %, churn %, expiring ≤ 30 days |
| **Failed Provisioning** monitor | the acceptance-critical list — a failed provision is visible at a glance (click through to the job to retry) |
| **At-risk Subscriptions** monitor | active subs expiring ≤ 30 days + suspended/expired ones |
| **Storage Health** | database count + total cluster storage + per-database size table |
| **Revenue Analytics** menu | native Odoo **graph** (MRR trend by month) + **pivot** (MRR by plan × status) |
| **Tenant quick actions** | Activate / Suspend / Expire buttons on the tenant form (valid transitions only) |

## Metric definitions (pragmatic v1)

There is no status-transition history table yet, so churn and conversion are
**point-in-time proxies**, not true 30-day rates:

- **MRR** = Σ active subscriptions' plan price normalized to monthly (`subscription.mrr`,
  a stored measure so the graph/pivot can aggregate it).
- **Churn %** = churned / (active + churned), where churned = `cancelled` + `expired`.
- **Trial conversion %** = active / (active + trial).

A follow-up can add a transition log for true time-windowed rates.

## Storage health & two-layer isolation

Database sizes come from a **read-only `pg_database_size`** query over the maintenance
connection — the same sanctioned infra channel the provisioning engine uses for
existence checks. It is **not** a tenant-data ORM query, so database-per-tenant
isolation (Rule 3) is preserved. Fixed SQL, no user input; a transport error degrades
gracefully (the dashboard still renders).

## Design tokens

The dashboard SCSS consumes the `--nc-*` design-system tokens from
`ncollection_branding` (UI-T01/#128) with hex fallbacks, so it stays on-brand and
survives the token layer being absent.
