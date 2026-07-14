# NCollection ERP: Project Master Context

> **Version**: 1.0  
> **Date**: July 14, 2026  
> **Purpose**: This is the single, authoritative reference document for the entire NCollection ERP project. It combines the PRD, System Architecture, Developer Personas, and the Complete 8-Phase Task Checklist.

---

## 1. Executive Summary & Vision
**NCollection ERP** is a SaaS ERP Platform built on top of **Odoo 19 Community Edition**. The platform targets small-to-medium businesses (5–100 employees) in the **UAE and GCC** region.
We are not building an ERP from scratch. We are building a **SaaS platform layer** around Odoo — including tenant management, subscription management, module licensing, branding/white-labeling, and UAE localization. The customer experience is priority #1: subscribers must land directly in an isolated ERP workspace with only the modules they purchased.

---

## 2. Developer Personas & Roles
To ensure maximum parallelization and minimum merge conflicts, tasks are assigned to three distinct developer roles:

### [DEV-1] Backend & Infrastructure Lead
- **Specialty**: Python, PostgreSQL, Docker, CI/CD, SaaS Architecture, APIs.
- **Focus Areas**: Tenant provisioning, database routing, SaaS automation, platform services, AI integration.

### [DEV-2] Odoo & Business Logic Specialist
- **Specialty**: Odoo Models (ORM), Workflows, XML Views, Access Rights, Accounting, Core ERP.
- **Focus Areas**: Module visibility, role management, OCA integrations, ERP enhancements, business logic.

### [DEV-3] Frontend & Integration Specialist
- **Specialty**: OWL (Odoo Web Library), QWeb, JavaScript, UI/UX, Mobile apps, Web portals.
- **Focus Areas**: Branding, customer dashboards, customer portal, mobile application, UI widgets.

---

## 3. Collaboration & Git Workflow
1. **GitHub Issues**: Every task below must be mapped to a GitHub Issue.
2. **Claude Code Integration**: Developers assign an issue to themselves, create a branch (`feature/issue-[ID]`), and instruct Claude to implement the code.
3. **Pull Requests**: Code is pushed, a PR is opened against `develop`, and requires at least 1 approval from another DEV before merging.

---

## 4. Development Rules
> [!CAUTION]
> 1. **Never modify Odoo Core**. Use Custom Addons (`custom_addons/`) only.
> 2. **Two-Layer Thinking**: Keep Platform Layer (SaaS/Billing) completely separate from ERP Layer (Business Modules).
> 3. **Milestone-Driven**: Do not jump to future phases until the current phase is fully stabilized.
> 4. **Maintain Compatibility**: No hacks. Must be able to upgrade Odoo versions easily.

---

## 5. Master Task Checklist (All Phases)

### Phase 1: Customer Workspace (Priority: Immediate)
**Objective**: Build the foundational SaaS experience where subscribers land directly in their isolated ERP workspace.

#### [DEV-1] Backend & Infra
- [ ] Create `docker-compose.yml` and configure GitHub Actions CI/CD.
- [ ] Implement `ncollection.tenant` and `ncollection.subscription` models.
- [ ] Implement DB Routing Engine (Odoo `--db-filter` or Nginx) to route subdomains to specific databases.
- [ ] Implement Customer Authentication (login and session management flows).

#### [DEV-2] Odoo Logic
- [ ] Implement Module Visibility Engine to hide apps based on active subscription.
- [ ] Create XML `res.groups` for standard tenant roles (Owner, CEO, Manager, Sales, etc.).
- [ ] Remove access to "Apps" and "Settings" menus for standard tenant users.

#### [DEV-3] Frontend & UI
- [ ] Complete `ncollection_branding` (remove Odoo mentions, custom login page, URL paths, About dialog).
- [ ] Implement dynamic tenant branding (logos/colors change based on subdomain).
- [ ] Build the Customer Workspace Landing Dashboard widget (Sales, Receivables, KPIs) using OWL.

---

### Phase 2: SaaS Automation
**Objective**: Automate the business of selling ERP.

#### [DEV-1] Backend & Infra
- [ ] Build `ncollection.provisioning.job` to auto-spin up Postgres DBs and install base modules.
- [ ] Script automated daily backups for all tenant databases.
- [ ] Automate subdomain assignment and SSL generation.
- [ ] Setup cron jobs for subscription expiration checks.

#### [DEV-2] Odoo Logic
- [ ] Build Billing Engine (auto-generate Odoo Invoices on subscription purchase/renewal).
- [ ] Implement subscription lifecycle states (suspend, resume, restore).
- [ ] Build internal SaaS Admin Dashboard for NCollection staff.

#### [DEV-3] Frontend & UI
- [ ] Build public SaaS Checkout Flow (purchase and onboarding UI).
- [ ] Design HTML email templates (welcome, renewal reminders, suspension alerts).

---

### Phase 3: ERP Enhancement
**Objective**: Improve core business modules and implement UAE localization.

#### [DEV-1] Backend & Infra
- [ ] Install and configure OCA Financial Reporting (`account_financial_report`).
- [ ] Optimize PostgreSQL configurations and Odoo worker counts.

#### [DEV-2] Odoo Logic
- [ ] Implement UAE VAT 5%, AED default currency, and GCC document formats.
- [ ] Configure UAE Chart of Accounts and local tax reporting formats.
- [ ] Improve CRM, Sales, and Purchase approval workflows.

#### [DEV-3] Frontend & UI
- [ ] Ensure full Arabic/English translation across all custom modules.
- [ ] Design custom, printable UAE-compliant PDF invoice templates.
- [ ] Complete MIS Builder custom templates (Balance Sheet, P&L).

---

### Phase 4: Executive Dashboards
**Objective**: Provide high-level analytics for tenant executives.

#### [DEV-1] Backend & Infra
- [ ] Write optimized SQL queries/methods to aggregate cross-module data safely.

#### [DEV-2] Odoo Logic
- [ ] Define backend models to calculate Sales, HR, Finance, and Warehouse KPIs.

#### [DEV-3] Frontend & UI
- [ ] Build interactive CEO Dashboard (OWL) combining Finance and Sales.
- [ ] Build Department Dashboards (HR, Sales, Warehouse) using Chart.js/Apexcharts.

---

### Phase 5: AI Layer
**Objective**: Integrate AI to assist ERP users.

#### [DEV-1] Backend & Infra
- [ ] Securely connect Odoo to external LLM API (OpenAI/Claude).
- [ ] Build context injection engine to feed tenant data to LLM without leakage.

#### [DEV-2] Odoo Logic
- [ ] Write background jobs detecting inventory/sales anomalies for AI Insights.
- [ ] Map natural language queries to Odoo domain filters for report generation.

#### [DEV-3] Frontend & UI
- [ ] Build persistent ERP Assistant chat widget (OWL).
- [ ] Overhaul Odoo search bar to accept natural language query results.

---

### Phase 6: Customer Portal
**Objective**: Secure portal for tenants' clients.

#### [DEV-1] Backend & Infra
- [ ] Integrate local payment gateways (Stripe, PayTabs, Tap) for online invoice payment.

#### [DEV-2] Odoo Logic
- [ ] Define portal user access rights (Invoices, Orders, Tickets).
- [ ] Implement Helpdesk/Ticketing logic for portal users.

#### [DEV-3] Frontend & UI
- [ ] Redesign Odoo website portal to match NCollection premium design.
- [ ] Build self-service Knowledge Base UI for portal users.

---

### Phase 7: Mobile Application
**Objective**: Mobile accessibility for field workers and executives.

#### [DEV-1] Backend & Infra
- [ ] Optimize Odoo RPC endpoints for mobile consumption.
- [ ] Setup Firebase Cloud Messaging (FCM) integration.

#### [DEV-2] Odoo Logic
- [ ] Handle data conflict resolution for offline mobile sync.
- [ ] Optimize warehouse movement endpoints for barcode scanning.

#### [DEV-3] Frontend & UI
- [ ] Develop PWA / Native App (React Native/Flutter).
- [ ] Design mobile screens for Sales, Inventory, and Approvals.

---

### Phase 8: Platform Services
**Objective**: Open platform for enterprise integrations.

#### [DEV-1] Backend & Infra
- [ ] Build standard REST API layer (OAuth2) wrapping Odoo RPC.
- [ ] Implement event-driven outgoing webhooks.
- [ ] Setup Prometheus/Grafana system monitoring dashboards.

#### [DEV-2] Odoo Logic
- [ ] Implement strict Audit Trail across critical modules.
- [ ] Create Python/Node.js Developer client SDK.

#### [DEV-3] Frontend & UI
- [ ] Build public Swagger/Redoc UI for the REST API.
- [ ] Build Marketplace UI for tenants to browse third-party integrations.
