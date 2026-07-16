# NCollection ERP: Master System Design & Execution Plan

> [!WARNING]
> **SUPERSEDED (July 16, 2026)** — This early 8-phase draft is **outdated**. The authoritative plan is [DELIVERABLE_1_SYSTEM_DESIGN.md](DELIVERABLE_1_SYSTEM_DESIGN.md) (v5.0). Kept for historical context only — do not implement tasks from it.

This document serves as the master system design and execution plan for the **entire NCollection ERP project** (all 8 phases). It outlines the architecture, workflow, and detailed task distribution for a 3-developer remote team.

## User Review Required

> [!IMPORTANT]  
> Please review the Developer Personas (DEV-1, DEV-2, DEV-3) and ensure their assigned skill sets match your actual team. The tasks for all 8 phases have been distributed based on these 3 IDs.

---

## 1. Developer Personas & Track Assignments

To ensure maximum parallelization and minimum merge conflicts, the team is divided into three distinct roles, identified by specific IDs:

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

## 2. GitHub & Collaboration Workflow

1. **GitHub Issues**: Every task below must be created as a GitHub Issue.
2. **Claude Code Integration**: Developers assign an issue to themselves, create a branch (`feature/issue-[ID]`), and instruct Claude to read the issue and implement the code.
3. **Pull Requests**: Code is pushed to GitHub, a PR is opened against the `develop` branch, and requires at least 1 approval from another DEV before merging.

---

## 3. Detailed Phase-by-Phase Task Breakdown

### Phase 1: Customer Workspace (Priority: Immediate)
**Objective**: Build the foundational SaaS experience where subscribers land directly in their isolated ERP workspace.

#### [DEV-1] Tasks
- [ ] **Infrastructure Setup**: Create `docker-compose.yml`, configure GitHub Actions CI/CD.
- [ ] **Tenant Models**: Implement `ncollection.tenant` and `ncollection.subscription` models in `ncollection_subscription`.
- [ ] **DB Routing Engine**: Implement Odoo `--db-filter` or Nginx reverse proxy to route subdomains to specific databases.
- [ ] **Customer Authentication**: Implement login and session management flows.

#### [DEV-2] Tasks
- [ ] **Module Visibility Engine**: Override Odoo menu loading to hide apps based on the active `ncollection.subscription` modules.
- [ ] **Tenant Roles**: Create XML `res.groups` for standard roles (Owner, CEO, Manager, Sales, etc.).
- [ ] **Access Stripping**: Remove access to "Apps" and "Settings" menus for standard tenant users.

#### [DEV-3] Tasks
- [ ] **Branding Implementation**: Complete `ncollection_branding` (remove Odoo mentions, custom login page, URL paths, About dialog).
- [ ] **Dynamic Tenant Branding**: Apply specific logos/colors dynamically based on the current tenant's subdomain.
- [ ] **Workspace Landing**: Build the Customer Dashboard widget (Sales, Receivables, KPIs) using OWL.

---

### Phase 2: SaaS Automation
**Objective**: Automate the business of selling ERP (provisioning, billing, renewals).

#### [DEV-1] Tasks
- [ ] **Auto-Provisioning Script**: Build the `ncollection.provisioning.job` to automatically spin up a new Postgres DB and install Odoo base modules.
- [ ] **Backup Manager**: Script automated daily backups for all tenant databases to cloud storage (e.g., AWS S3).
- [ ] **Domain Manager**: Automate subdomain assignment and SSL certificate generation (Let's Encrypt).
- [ ] **Scheduler**: Setup cron jobs for subscription expiration checks.

#### [DEV-2] Tasks
- [ ] **Billing Engine**: Generate Odoo Invoices automatically when a subscription is purchased or renewed.
- [ ] **Subscription Lifecycle**: Implement state machines for suspend, resume, and restore operations.
- [ ] **SaaS Admin Dashboard**: Build the internal views for NCollection admins to monitor tenant health.

#### [DEV-3] Tasks
- [ ] **SaaS Checkout Flow**: Build the public-facing subscription purchase and onboarding UI.
- [ ] **Email Automation**: Design and implement HTML email templates for welcome emails, renewal reminders, and suspension alerts.

---

### Phase 3: ERP Enhancement
**Objective**: Improve the core business modules and implement UAE localization.

#### [DEV-1] Tasks
- [ ] **OCA Module Integration**: Install and configure OCA Financial Reporting (`account_financial_report`).
- [ ] **Performance Tuning**: Optimize PostgreSQL configurations and Odoo worker counts for heavy ERP usage.

#### [DEV-2] Tasks
- [ ] **UAE Localization**: Implement UAE VAT 5%, AED default currency, and GCC document formats.
- [ ] **Accounting Setup**: Configure the UAE Chart of Accounts and local tax reporting formats.
- [ ] **Workflow Enhancements**: Improve CRM, Sales, and Purchase approval workflows based on client needs.

#### [DEV-3] Tasks
- [ ] **Bilingual Interface**: Ensure full Arabic/English translation completeness across all custom modules.
- [ ] **Invoice Templates**: Design custom, printable UAE-compliant PDF invoice templates.
- [ ] **Financial Report Templates**: Complete MIS Builder custom templates (Balance Sheet, P&L).

---

### Phase 4: Executive Dashboards
**Objective**: Provide high-level analytics for tenant executives.

#### [DEV-1] Tasks
- [ ] **Data Aggregation Engine**: Write optimized SQL queries/Python methods to aggregate cross-module data safely.

#### [DEV-2] Tasks
- [ ] **KPI Definitions**: Define the backend models and logic to calculate Sales, HR, Finance, and Warehouse KPIs.

#### [DEV-3] Tasks
- [ ] **CEO Dashboard**: Build an interactive OWL dashboard combining Finance and Sales.
- [ ] **Department Dashboards**: Build specific views for HR, Sales, and Warehouse managers using Chart.js or apexcharts within Odoo.

---

### Phase 5: AI Layer
**Objective**: Integrate Artificial Intelligence to assist ERP users.

#### [DEV-1] Tasks
- [ ] **LLM Integration**: Connect Odoo to an external LLM API (e.g., OpenAI or Claude API) securely.
- [ ] **Context Injection Engine**: Build the backend system that securely feeds tenant-specific context to the LLM without leaking data.

#### [DEV-2] Tasks
- [ ] **AI Insights Logic**: Write background jobs that detect anomalies in inventory or sales and generate alert records.
- [ ] **Report Generator Logic**: Map natural language queries to Odoo domain filters.

#### [DEV-3] Tasks
- [ ] **ERP Assistant UI**: Build a persistent chat widget (OWL) across the Odoo interface.
- [ ] **Smart Search UI**: Overhaul the Odoo search bar to accept and display natural language query results.

---

### Phase 6: Customer Portal
**Objective**: Allow the tenants' clients to interact with them securely.

#### [DEV-1] Tasks
- [ ] **Payment Gateway**: Integrate local payment gateways (e.g., Stripe, PayTabs, Tap) for online invoice payment.

#### [DEV-2] Tasks
- [ ] **Portal Access Rights**: Strictly define what public/portal users can see (Invoices, Orders, Tickets) without exposing internal ERP data.
- [ ] **Support Ticketing**: Implement Helpdesk/Ticketing logic for portal users.

#### [DEV-3] Tasks
- [ ] **Portal UI Overhaul**: Redesign the Odoo website portal to match NCollection's premium design standards.
- [ ] **Knowledge Base UI**: Build a self-service documentation area for portal users.

---

### Phase 7: Mobile Application
**Objective**: Deliver mobile accessibility for field workers and executives.

#### [DEV-1] Tasks
- [ ] **Mobile API**: Ensure Odoo XML-RPC/JSON-RPC endpoints are optimized for mobile consumption.
- [ ] **Push Notification Server**: Setup Firebase Cloud Messaging (FCM) integration.

#### [DEV-2] Tasks
- [ ] **Offline Sync Logic**: Handle data conflict resolution for when mobile users reconnect to the network.
- [ ] **Barcode Logic**: Optimize warehouse movement endpoints for rapid barcode scanning.

#### [DEV-3] Tasks
- [ ] **PWA / Native App**: Develop the mobile application (e.g., using React Native or Flutter, communicating with Odoo).
- [ ] **Mobile UI/UX**: Design the mobile screens for Sales, Inventory (Barcode), and Approvals.

---

### Phase 8: Platform Services
**Objective**: Open the platform for enterprise integrations and monitoring.

#### [DEV-1] Tasks
- [ ] **Public REST API**: Build a standard REST API layer (OAuth2) wrapping Odoo's internal RPC.
- [ ] **Webhooks System**: Implement event-driven outgoing webhooks for tenant integrations.
- [ ] **System Monitoring**: Setup Prometheus and Grafana dashboards for global platform health.

#### [DEV-2] Tasks
- [ ] **Audit Trail**: Implement strict tracking of every field change across critical modules.
- [ ] **Developer SDK**: Document the API endpoints and create a Python/Node.js client SDK.

#### [DEV-3] Tasks
- [ ] **API Documentation Portal**: Build a public-facing Swagger/Redoc UI for the REST API.
- [ ] **Marketplace UI**: Build an interface where tenants can browse and install third-party integrations.
