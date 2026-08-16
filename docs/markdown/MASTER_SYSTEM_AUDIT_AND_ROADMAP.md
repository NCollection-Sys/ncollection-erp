# NCollection ERP — Master System Audit, Architecture & Strategic Roadmap

> **Authoritative System Handover for Claude & Engineering Team**  
> **Date:** August 2026 · **Status:** Active Master Reference  
> **Estate Overview:** 22 Custom Addons · 192 Merged PRs · 183 Closed Issues · 51 Open Issues · 109 Test Files (1,258 Test Methods) · 13 Verification Scripts · 8 Verify-All Suites · Full Invariant & Regression Ledger.

---

## 1. Executive System State & Architectural Principles

NCollection ERP is a multi-tenant SaaS Enterprise Resource Planning platform for the UAE & GCC markets, built on **Odoo 19 Community** with a custom SaaS control plane, database-per-tenant architecture, native financial computation engines (ADR #15), scoped OAuth2 REST APIs, and automated invariant verification.

### Core Architectural Invariants
1. **Modular Monolith + Satellite Workers (No Classic Microservices):**  
   Core ERP business models (`res.partner`, `sale.order`, `account.move`, `stock.quant`) reside in a single transactional relational schema to guarantee ACID transactions and foreign-key integrity. Asynchronous tasks (provisioning, heavy reports, webhooks) run in dedicated background worker containers; external AI requests run in an isolated FastAPI satellite.
2. **Two-Layer Multi-Tenant Separation (Rule 3):**  
   Platform control plane modules (`ncollection_saas`, `ncollection_subscription`, `ncollection_reseller`, `ncollection_billing`) run on platform databases (`saastest`/`ncplatform`) and **never query tenant ERP models directly**. Cross-layer interactions occur via loopback JSON/REST RPC with explicit `Host: <db>.<base_domain>` headers for `db_filter=^%d$` routing.
3. **Database-Per-Tenant Isolation (No DDBMS Overhead):**  
   Every tenant operates in an independent PostgreSQL database. Scaling is achieved via connection pooling (PgBouncer) and physical database-level sharding in Phase 10, avoiding the latency and schema incompatibility of distributed database management systems (DDBMS).
4. **Odoo 19 View & Security Standards:**  
   Strict `<list>` views (no legacy `<tree>`), no `attrs=`, SQL constraints declared via `models.Constraint`, and all `auth='none'` mutating routes explicitly declare `readonly=False`.

---

## 2. Master Technical Debt, Refactoring & Code Cleanup Register

The following table provides the comprehensive list of modules and components across the entire codebase that require code updates, deprecations, refactoring, or bug fixes:

| Area / Component | Current State | Root Cause & Problem | Required Action & Architectural Solution | Target Task / Phase |
|---|---|---|---|---|
| **Financial Reporting Bootstrap** | `account_financial_report`, `mis_builder`, `ncollection_mis_templates` | Temporary OCA bootstrap modules used before native financial engine was completed. | **Sunset and remove:** Native `ncollection_account_reports` (Balance Sheet, P&L, Cash Flow, Partner Ledger, Trial Balance) is now complete per ADR #15. | **#117 (F2-T07)** |
| **Financial Audit Trail** | `ncollection_audit` + OCA `auditlog` | OCA `auditlog`'s `write_full` recurses ~950 frames on `account.move.line` (#431); `ThrowAwayCache` causes KeyErrors on `res.partner` (#429); breaks `res.users` seat checks (#428). | **Decouple & Build Native:** Keep `ncollection_audit` for lightweight system models; implement native immutable financial timeline logging in `ncollection_account_audit`. | **#124 (F4-T05) & #431** |
| **UAE FTA Compliance & Tax Reports** | `ncollection_account_localization_uae` | TRN validation and bilingual PDF invoices exist (#126, #49), but official FTA VAT 201 return and FAF (FTA Audit File) export are missing. | **Implement Box Aggregator:** Build FTA VAT 201 box mapping (Box 1a–1g Emirate split, Box 2–12 input/output VAT) and standard FAF CSV/XML export. | **#116 (F2-T06)** |
| **Fixed Asset Auto-Posting Cron** | `ncollection_account_assets` | Upstream OCA depreciation cron runs in an unbounded loop across all assets, risking database lockup (#425). | **Queue & Batch:** Wrap asset depreciation calculation in a chunked, time-budgeted `queue_job` worker per `DESIGN_CRON_AND_QUEUE_TOPOLOGY.md`. | **#425 / Phase 8** |
| **Dev Environment Config Mounts** | `docker-compose.dev.yml` / `config/` | Config files are mounted individually rather than by directory; Nginx mount freshness is unmonitored (#432, #438). | **Directory Mount Refactor:** Relocate Odoo and Nginx configuration files into dedicated directory mounts to prevent stale Docker inode references. | **#432, #438** |
| **CI Shellcheck Parity** | `.github/workflows/ci.yml` & `.githooks/` | Local pre-push hook shellcheck version differs from CI container version (#434). | **Version Pinning:** Pin exact shellcheck binary version across both CI container and local developer tooling docs. | **#434** |
| **Go-Live Verification Gate** | Platform Infrastructure | Production deployment checklist, on-call rotation, and live backup restore drill remain open. | **Execute Gate Checklist:** Complete staging dry-run, rehearse rollback, verify live PITR on production, record sign-off evidence. | **#53 (P3-T13)** |

---

## 3. Security Audit & Penetration Assessment

### Live Security Controls
- **Edge Layer:** Nginx blocks `/web/database/{manager,selector,create}` (403 Forbidden), enforces TLS 1.2/1.3, strict HSTS, and rate limits `/web/login` and `/nc/checkout/*`.
- **API Security:** Scoped OAuth2 Bearer tokens (`ncollection_api`), pre-auth in-memory PBKDF2 hash throttling (`ncollection.api.throttle`), and row-locked slot rate limiting (`SELECT ... FOR UPDATE`).
- **Authorization:** Controller routes execute under the client user's identity (`with_user(uid)`) without `sudo()` elevation, preserving Odoo record rules and group ACLs.
- **Tenant Isolation:** Platform database enforces `UNIQUE(database_name)` with strict regex validation (`^[a-z][a-z0-9]{2,62}$`), preventing tenant database takeover.

### Security Action Items for Upcoming Sprints
1. **Enforce `readonly=False` on All Mutating API Routes:** Add an invariant test ensuring any `@http.route` with `methods=['POST'|'PUT'|'DELETE']` explicitly passes `readonly=False`.
2. **Handle Uninstalled Modules in REST Endpoints (#78):** Prevent 500 tracebacks on plans lacking optional modules by checking model availability in `request.env` and returning `422 module_not_installed`.
3. **Outbound Webhook Cryptographic Signing (#79):** Implement HMAC-SHA256 request signatures with timestamp nonces to prevent payload tampering and replay attacks.
4. **Public Signup Security:** Maintain `ncollection_core.public_signup_enabled = False` on all production tenant workspaces.

---

## 4. Architecture Decisions: Microservices, Containers & Databases

### A. Why Microservices are NOT Recommended
- **Data Integrity:** Splitting ERP modules (Sales, Accounting, Inventory) into standalone microservices breaks relational database foreign keys, requires distributed transactions (Sagas/2PC), and introduces severe network latency.
- **Current Optimal Pattern:** **Modular Monolith + Satellite Workers**. Business logic remains in modular Odoo addons; asynchronous tasks (provisioning, webhooks, backups) run in background worker containers; external AI processing runs in the FastAPI satellite service.

### B. Docker Topology: Dev Overlays vs Lean Production Fleet
The multiple compose files (`docker-compose.*.yml`) in the repo are **modular overlays and test harnesses**, not an oversized production fleet.

```
DEVELOPMENT OVERLAYS (On Demand)                  PRODUCTION FLEET (6 Lean Containers)
├── docker-compose.dev.yml (Base Dev)             ├── 1. Nginx (Edge Reverse Proxy & TLS)
├── docker-compose.ai.yml (AI Satellite)          ├── 2. Odoo Web (HTTP Workers, workers=4-8)
├── docker-compose.saas.yml (Provisioning)        ├── 3. Odoo Worker (Cron & queue_job, workers=2)
├── docker-compose.pooling.yml (PgBouncer)        ├── 4. PgBouncer (Connection Pooling)
└── docker-compose.cron*.yml (Isolated Tests)     ├── 5. PostgreSQL 16 (Primary DB on NVMe)
                                                  └── 6. Observability (Prometheus + Grafana)
```

### C. Database Architecture: Why DDBMS is Unnecessary
- **Odoo ORM Incompatibility:** Odoo relies on PostgreSQL-specific schema modification, `pg_class` catalog inspection, transaction savepoints, and advisory locks. Distributed SQL engines (CockroachDB, Cassandra, Spanner) do not support Odoo ORM metadata operations.
- **Natural Horizontal Sharding:** Because NCollection uses **database-per-tenant**, customer data is already partitioned.
- **Scaling Roadmap:**
  - **1 to 1,000 Tenants:** Single PostgreSQL 16 instance with PgBouncer connection pooling and NVMe storage.
  - **1,000+ Tenants (Phase 10):** Physical tenant sharding across multiple PostgreSQL nodes routed at the connection pooler/Nginx level.

---

## 5. Master Roadmap: Open Phases & Future Issues

```
                                  NCOLLECTION ROADMAP STATUS
                                  
  PHASE 1: Workspace (100%)       PHASE 2: SaaS Engine (100%)    PHASE 3: ERP & UAE (92%)
  [████████████████████] 21/21    [████████████████████] 18/18   [██████████████████░░] 12/13
  
  PHASE 4: Dashboards (100%)      PHASE 5: AI Platform (71%)     PHASE 6: Portal (40%)
  [████████████████████] 4/4      [██████████████░░░░░░] 5/7     [████████░░░░░░░░░░░░] 2/5
  
  PHASE 7: Mobile App (0%)        PHASE 8: Platform (22%)        PHASE 10: Enterprise (11%)
  [░░░░░░░░░░░░░░░░░░░░] 0/7      [████░░░░░░░░░░░░░░░░] 2/9     [██░░░░░░░░░░░░░░░░░░] 1/9
```

### Phase 8 — Platform Services (Immediate Priority)
- **#78 [P8-T02] REST Business Endpoints:** Full CRUD for Contacts, Products, Sales, Invoices, Stock levels, and CRM leads with OpenAPI 3.1 schema and Bruno collection.
- **#79 [P8-T03] Webhooks System:** Asynchronous event dispatch (`queue_job`), HMAC-SHA256 payload signatures, exponential backoff retries, and delivery log UI.
- **#80 [P8-T04] Full Observability Stack:** Prometheus metrics exporter (per-tenant latency, queue depth, pool saturation) + Grafana dashboards + alert rules.
- **#82 [P8-T06] Developer SDKs & #83 [P8-T07] API Documentation Portal:** Python/TypeScript client SDKs and branded Redoc/Swagger try-it-out sandbox.

### Financial Platform Stream (Parallel Priority)
- **#116 [F2-T06] Native UAE VAT & FTA Reports:** FTA VAT 201 box structure (Box 1a–1g Emirate allocation) + FAF export format.
- **#117 [F2-T07] OCA Reporting Sunset:** Retire `account_financial_report`, `mis_builder`, and `ncollection_mis_templates`.
- **#124 [F4-T05] Native Financial Audit Trail:** Immutable ledger change logs and financial timeline viewer.

### Phase 5 & 6 — AI & Customer Portal
- **#63 [P5-T06] AI Chat Widget & #64 [P5-T07] Smart Search UI:** OWL frontend interfaces connected to the AI Gateway.
- **#65 [P6-T01] Regional Payment Gateways:** GCC payment integration (Tap Payments / PayTabs / Telr) for tenant invoice settlement.
- **#68 [P6-T04] Portal UI Redesign & #69 [P6-T05] Knowledge Base:** Customer portal branding and help articles.

### Phase 7 — Mobile Application
- **#70 [P7-T01] Mobile API Optimization & #74 [P7-T05] Framework Decision:** Mobile app scaffold (Flutter/React Native) with offline sync logic (#72) and barcode scanning endpoints (#73).

### Phase 10 — Enterprise Readiness
- **#93–#95: High Availability & Sharding:** PostgreSQL streaming replication, automated failover (Patroni), and tenant sharding.
- **#98 [P10-T06] Multi-Company Consolidation:** Intercompany transactions and consolidated reporting.

---

## 6. Input & Decision Matrix Required from Omar

To prevent blockers in upcoming sprints, the following decisions, files, and credentials are required:

| Priority | Item | Required Input / Decision from Omar | Impacted Issues |
|---|---|---|---|
| **P1 (Immediate)** | **REST Scopes** | Confirm granular scope names (`contacts:read`, `sales:write`, etc.) | **#78** |
| **P1 (Immediate)** | **Webhook Policy** | Confirm exponential backoff retry schedule (5 retries up to 2h) | **#79** |
| **P2 (Near-Term)** | **FTA Document** | Provide official FTA VAT 201 return sample / Emirate mapping approval | **#116** |
| **P2 (Near-Term)** | **Payment Gateway** | Select primary GCC gateway (**Tap Payments** / **PayTabs** / **Telr**) + Sandbox API Keys | **#65** |
| **P2 (Near-Term)** | **Mobile Stack** | Select mobile framework (**Flutter** vs **React Native**) | **#74** |
| **P3 (Strategic)** | **AI Data Residency** | Approve cloud LLMs with client disclosure vs requiring Azure UAE endpoints | **#323** |
| **P3 (Strategic)** | **Go-Live Schedule** | Set target production deployment window and sign off on runbook | **#53** |

---

## 7. Execution Protocol for Claude Sessions

When starting any new task in this repository, follow this standardized execution protocol:

1. **Verify Branch & Invariants:**
   - Branch off `develop` using `feature/<issue_number>-<task_id>`.
   - Never mutate shared Docker stacks mid-verification (Rule 14 / R-018).
2. **Execute Implementation:**
   - Follow strict Odoo 19 conventions (`<list>`, `models.Constraint`, `readonly=False` on mutating routes).
   - Never use `.sudo()` for business CRUD; execute as `request.env[model].with_user(uid)`.
3. **Verify Locally Before Submitting PR:**
   - `make test m=<module_name>` (must pass 100%).
   - `python scripts/ci/invariants.py` & `python scripts/ci/architecture_guard.py`.
   - `make verify-all` (all 8 verification suites green).
4. **Submit PR & Request Review:**
   - Include `Closes #<issue_number>` in PR description.
   - Run automated Strix security review before merging into `develop`.
