# NCollection ERP: Project Master Context

> **Version**: 2.0  
> **Date**: July 14, 2026  
> **Purpose**: This is the single, authoritative reference document for the entire NCollection ERP project. It combines the PRD, System Architecture, Developer Personas, and an exhaustive, highly detailed task breakdown for all 8 phases.

---

## 1. Executive Summary & Vision
**NCollection ERP** is a SaaS ERP Platform built on top of **Odoo 19 Community Edition**. The platform targets small-to-medium businesses (5–100 employees) in the UAE and GCC region. We are building a **SaaS platform layer** around Odoo (tenant management, subscription licensing, branding, UAE localization) so that subscribers land directly in an isolated ERP workspace containing only the modules they purchased.

---

## 2. Developer Personas & Roles
To ensure maximum parallelization, tasks are strictly assigned to three distinct developer roles:

### [DEV-1] Backend & Infrastructure Lead
- **Specialty**: Python, PostgreSQL, Docker, CI/CD, SaaS Architecture, APIs.
- **Focus**: Tenant provisioning, DB routing, SaaS automation, API, AI integration.

### [DEV-2] Odoo & Business Logic Specialist
- **Specialty**: Odoo Models (ORM), Workflows, XML Views, Access Rights, Core ERP.
- **Focus**: Module visibility, roles, OCA integrations, accounting logic, workflows.

### [DEV-3] Frontend & Integration Specialist
- **Specialty**: OWL, QWeb, JavaScript, UI/UX, Mobile apps, Portals.
- **Focus**: Branding, customer dashboards, portals, mobile application, UI widgets.

---

## 3. Collaboration & Rules
1. **Never modify Odoo Core**. Use Custom Addons (`custom_addons/`) only.
2. **Issue Tracking**: Every task below maps to a GitHub Issue.
3. **Claude Code Workflow**: Assign task -> Branch -> Claude Code implementation -> Pull Request.
4. **Strict Dependencies**: Do not start a task if its listed dependency is incomplete.

---

## 4. Master Task Breakdown (All Phases)

### Phase 1: Customer Workspace (Priority: Immediate)
**Objective**: Build the foundational SaaS experience where subscribers land directly in their isolated ERP workspace.

| ID | Task Name | Description | Assigned | Dependencies |
|---|---|---|---|---|
| **1.1** | Docker & CI/CD Setup | Setup `docker-compose.yml` for PostgreSQL 16 and Odoo 19. Configure GitHub Actions to run `flake8`/`pylint-odoo` on PRs. | `[DEV-1]` | None (Start immediately) |
| **1.2** | Custom Addons Skeleton | Create the empty folder structure for `ncollection_core`, `ncollection_saas`, `ncollection_subscription` with `__manifest__.py`. | `[DEV-1]` | 1.1 |
| **1.3** | Tenant & Sub Models | Create `ncollection.tenant` and `ncollection.subscription` models with fields for database status, UUIDs, and limits. | `[DEV-1]` | 1.2 |
| **1.4** | DB Routing Engine | Implement logic using Odoo `--db-filter` or Nginx mapping to route subdomains (e.g. `client.ncollection.com`) to specific databases. | `[DEV-1]` | 1.3 |
| **1.5** | Customer Authentication | Override standard Odoo login flow to handle SaaS session management and "Remember Me" securely. | `[DEV-1]` | 1.2 |
| **1.6** | Module Visibility Engine | Write Python logic to intercept menu loading and hide Odoo apps based on the tenant's active subscription tier. | `[DEV-2]` | 1.3 |
| **1.7** | Tenant Roles Config | Define XML `res.groups` for standard SaaS roles: Owner, CEO, Manager, Sales, Warehouse, HR, Accountant. | `[DEV-2]` | 1.2 |
| **1.8** | App/Settings Stripping | Remove access to standard Odoo "Apps" and "Settings" menus for all standard tenant users to prevent tampering. | `[DEV-2]` | 1.7 |
| **1.9** | Web Client Branding | Complete `ncollection_branding`: replace Odoo logos, customize login page UI, update URL paths, and replace the About dialog. | `[DEV-3]` | 1.2 |
| **1.10** | Dynamic Tenant Branding | Inject CSS variables dynamically so each tenant database loads its own specific primary colors and logo upon login. | `[DEV-3]` | 1.4, 1.9 |
| **1.11** | Workspace Dashboard | Build the main Customer Dashboard widget (Sales, Receivables, quick actions) using the OWL framework. | `[DEV-3]` | 1.2 |

---

### Phase 2: SaaS Automation
**Objective**: Automate the business of selling ERP (provisioning, billing, renewals).

| ID | Task Name | Description | Assigned | Dependencies |
|---|---|---|---|---|
| **2.1** | Provisioning Job Model | Create the `ncollection.provisioning.job` model to track DB creation queues and logs. | `[DEV-1]` | 1.3 |
| **2.2** | Auto-Provisioning Script | Write the Python script that executes Odoo CLI commands to spin up a new Postgres DB and install base modules automatically. | `[DEV-1]` | 2.1 |
| **2.3** | Backup Manager Script | Create a cron job/script to take `pg_dump` backups of all active tenant databases and upload to secure cloud storage. | `[DEV-1]` | 2.2 |
| **2.4** | Domain & SSL Manager | Automate Let's Encrypt SSL generation when a new subdomain is mapped to a tenant. | `[DEV-1]` | None |
| **2.5** | Sub Expiration Cron | Create a scheduled action to check subscription end dates and update tenant states to 'expired' or 'suspended'. | `[DEV-1]` | 1.3 |
| **2.6** | Billing Engine | Logic to automatically generate Odoo Invoices in the main Admin DB when a tenant purchases or renews a subscription. | `[DEV-2]` | 1.3 |
| **2.7** | SaaS Admin Dashboard | Build the internal views and action menus for NCollection staff to monitor tenant health and trigger manual provisions. | `[DEV-2]` | 1.3 |
| **2.8** | Public Checkout Flow | Build the frontend Odoo eCommerce/Website pages for users to select a plan and register their company. | `[DEV-3]` | None |
| **2.9** | Email Automation | Design HTML templates for welcome emails, renewal reminders, and suspension alerts, integrated with mail records. | `[DEV-3]` | None |

---

### Phase 3: ERP Enhancement
**Objective**: Improve core business modules and implement UAE localization.

| ID | Task Name | Description | Assigned | Dependencies |
|---|---|---|---|---|
| **3.1** | OCA Financials Setup | Install and configure the `account_financial_report` OCA module. Ensure Odoo 19 compatibility. | `[DEV-1]` | 1.1 |
| **3.2** | Performance Tuning | Optimize `odoo.conf` (workers, memory limits) and Postgres configurations for heavy multi-tenant ERP usage. | `[DEV-1]` | 1.1 |
| **3.3** | UAE VAT & Currency | Configure base UAE localization: 5% VAT tax records and AED default currency setup via XML data. | `[DEV-2]` | 1.1 |
| **3.4** | UAE Chart of Accounts | Map and create the standard UAE Chart of Accounts using Odoo accounting templates. | `[DEV-2]` | 3.1, 3.3 |
| **3.5** | Workflow Enhancements | Customize standard CRM, Sales, and Purchase models to include multi-level approval workflows requested by clients. | `[DEV-2]` | None |
| **3.6** | Translation Audit | Export `.po` files and ensure 100% Arabic/English translation completeness across all `ncollection_*` modules. | `[DEV-3]` | None |
| **3.7** | UAE PDF Invoices | Design custom QWeb PDF templates for invoices that comply with UAE commercial and tax standards. | `[DEV-3]` | 3.3 |
| **3.8** | MIS Builder Templates | Complete the MIS Builder configurations for high-quality Balance Sheet and P&L financial reports. | `[DEV-3]` | 3.1 |

---

### Phase 4: Executive Dashboards
**Objective**: Provide high-level analytics for tenant executives.

| ID | Task Name | Description | Assigned | Dependencies |
|---|---|---|---|---|
| **4.1** | Data Aggregation Engine | Write optimized SQL queries or Python methods to safely aggregate cross-module data (Sales + HR + Finance) quickly. | `[DEV-1]` | None |
| **4.2** | KPI Logic Models | Define backend models and computed fields to calculate specific metrics (e.g., Revenue Growth, Employee Turnover). | `[DEV-2]` | 4.1 |
| **4.3** | CEO Dashboard UI | Build a responsive OWL dashboard combining high-level Finance and Sales charts for the CEO role. | `[DEV-3]` | 4.2 |
| **4.4** | Department Dashboards | Build specific, detailed dashboard views for HR, Sales, and Warehouse managers using Chart.js/Apexcharts within Odoo. | `[DEV-3]` | 4.2 |

---

### Phase 5: AI Layer
**Objective**: Integrate Artificial Intelligence to assist ERP users.

| ID | Task Name | Description | Assigned | Dependencies |
|---|---|---|---|---|
| **5.1** | LLM Gateway API | Securely connect the Odoo backend to an external LLM API (OpenAI/Claude) using a centralized authentication key. | `[DEV-1]` | None |
| **5.2** | Context Injection Engine | Build the backend system that safely queries the tenant's specific data to provide context to the LLM without leaking. | `[DEV-1]` | 5.1 |
| **5.3** | Anomaly Detection | Write background scheduled actions that detect outliers in inventory movements or sales trends and generate alert records. | `[DEV-2]` | None |
| **5.4** | NL to Domain Mapper | Build logic that translates natural language requests (e.g., "Show me sales > 1000") into safe Odoo ORM domain filters. | `[DEV-2]` | 5.1 |
| **5.5** | ERP Assistant Widget | Build a persistent, floating chat widget (OWL) across the Odoo interface for users to talk to the AI. | `[DEV-3]` | 5.2 |
| **5.6** | Smart Search UI | Overhaul the standard Odoo search bar to accept natural language, displaying results fed from Task 5.4. | `[DEV-3]` | 5.4 |

---

### Phase 6: Customer Portal
**Objective**: Allow the tenants' clients to interact with them securely.

| ID | Task Name | Description | Assigned | Dependencies |
|---|---|---|---|---|
| **6.1** | Payment Gateways | Integrate regional payment gateways (Stripe, PayTabs, Tap) allowing customers to pay invoices directly via the portal. | `[DEV-1]` | 2.6 |
| **6.2** | Portal Access Rights | Strictly define XML record rules limiting what portal users can see (only their own Invoices, Orders, Tickets). | `[DEV-2]` | 1.7 |
| **6.3** | Support Ticketing | Implement Helpdesk/Ticketing backend logic enabling portal users to submit issues that route to the tenant's CRM. | `[DEV-2]` | None |
| **6.4** | Portal UI Redesign | Redesign the standard Odoo `/my` portal templates to match the NCollection premium, modern design language. | `[DEV-3]` | None |
| **6.5** | Knowledge Base UI | Build a self-service documentation layout for portal users to search for FAQs and guides. | `[DEV-3]` | None |

---

### Phase 7: Mobile Application
**Objective**: Deliver mobile accessibility for field workers and executives.

| ID | Task Name | Description | Assigned | Dependencies |
|---|---|---|---|---|
| **7.1** | Mobile API Optimization | Secure and optimize Odoo XML-RPC/JSON-RPC endpoints for lightweight mobile consumption (pagination, stripped fields). | `[DEV-1]` | 1.1 |
| **7.2** | Push Notification Server | Setup Firebase Cloud Messaging (FCM) integration on the backend to trigger mobile alerts for approvals. | `[DEV-1]` | None |
| **7.3** | Offline Sync Logic | Implement conflict-resolution logic on the backend to handle data pushed from mobile devices that went offline. | `[DEV-2]` | 7.1 |
| **7.4** | Barcode Endpoints | Create hyper-optimized endpoints specifically for rapid warehouse movement barcode scanning. | `[DEV-2]` | None |
| **7.5** | Mobile App Development | Scaffold and develop the mobile application (React Native / Flutter) integrating with the Odoo API. | `[DEV-3]` | 7.1 |
| **7.6** | Mobile UI/UX Design | Design and implement mobile screens tailored specifically for Sales entry, Inventory scanning, and Manager approvals. | `[DEV-3]` | None |

---

### Phase 8: Platform Services
**Objective**: Open the platform for enterprise integrations and monitoring.

| ID | Task Name | Description | Assigned | Dependencies |
|---|---|---|---|---|
| **8.1** | Public REST API Layer | Build a standard REST API layer utilizing OAuth2 authentication, wrapping Odoo's internal RPC for public developer consumption. | `[DEV-1]` | None |
| **8.2** | Webhooks System | Implement an event-driven outgoing webhooks manager so tenants can push updates to external systems (e.g., Zapier). | `[DEV-1]` | None |
| **8.3** | System Monitoring | Setup Prometheus exporters and Grafana dashboards to monitor global platform health (CPU, DB connections, request latency). | `[DEV-1]` | 1.1 |
| **8.4** | Strict Audit Trail | Implement overriding logic on critical models (Accounting, CRM) to track exactly who changed what fields, when, and from where. | `[DEV-2]` | None |
| **8.5** | Developer SDK | Document the API endpoints and create a foundational Python and Node.js client SDK for third-party developers. | `[DEV-2]` | 8.1 |
| **8.6** | API Swagger/Redoc UI | Build a public-facing Swagger/Redoc UI webpage that automatically generates from the REST API routes. | `[DEV-3]` | 8.1 |
| **8.7** | Marketplace UI | Build an interface within the ERP where tenants can browse and install verified third-party integrations. | `[DEV-3]` | None |
