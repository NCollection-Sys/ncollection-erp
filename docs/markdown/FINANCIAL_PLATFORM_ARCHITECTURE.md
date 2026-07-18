# FINANCIAL PLATFORM ARCHITECTURE

Version: 1.0

Status: Official Architecture Document

Project: NCollection ERP

Owner: NCollection ERP Team

---

# 1. Purpose

This document defines the official financial platform architecture of NCollection ERP.

It is the single source of truth for every accounting-related decision within the project.

Every financial feature, report, dashboard, localization package, workflow, or extension must follow the architecture described in this document.

This document exists to guarantee:

- Long-term maintainability
- Upgrade compatibility
- Clean module boundaries
- Consistent developer experience
- Predictable AI-generated implementations
- Enterprise-grade scalability

Whenever implementation differs from this document, this document takes precedence unless officially revised.

---

# 2. Vision

NCollection ERP is not an accounting application.

It is an enterprise SaaS platform that includes accounting as one of its core domains.

Rather than creating a new accounting engine, NCollection builds a complete financial platform on top of the mature accounting engine provided by Odoo Community.

The project focuses on delivering a better business experience while preserving the stability and correctness of the underlying accounting engine.

This philosophy allows the project to benefit from years of proven accounting logic while investing development effort into the business value that differentiates NCollection.

---

# 3. Objectives

The financial platform must achieve the following objectives.

## Functional

- Complete General Accounting
- Financial Reporting
- Multi-company support
- Multi-currency support
- UAE VAT Compliance
- Budgeting
- Cost Centers
- Profit Centers
- Executive Dashboards
- Financial Analytics
- Audit Trails
- Approval Workflows

---

## Technical

- Fully modular
- Upgrade-safe
- Database-per-tenant compatible
- Cloud ready
- High performance
- API friendly
- Testable
- Secure

---

## Business

- Enterprise ready
- GCC focused
- UAE localization first
- Expandable worldwide
- Subscription aware

# 4. Architectural Philosophy

The financial platform follows one fundamental rule.

> Odoo owns accounting.
>
> NCollection owns the business experience.

This principle defines every future architectural decision.

The accounting engine remains untouched.

NCollection extends the platform around it.

The goal is not to compete with Odoo.

The goal is to build a superior SaaS ERP using Odoo as its financial foundation.

---

## What Odoo Owns

Odoo Community remains responsible for:

- Journal Entries
- General Ledger Logic
- Double Entry Accounting
- Posting
- Taxes
- Currency Conversion
- Payments
- Reconciliation
- Fiscal Years
- Fiscal Positions
- Bank Statements
- Accounting Integrity

These components are considered infrastructure.

They are never replaced.

---

## What NCollection Owns

NCollection is responsible for:

- Reports
- Dashboards
- Analytics
- Business Workflows
- Approval Systems
- Subscription Restrictions
- Executive Insights
- UAE Extensions
- Customer Experience
- SaaS Platform Integration

These components define the value of the product.

They evolve independently from Odoo.

# 5. Financial Layer Architecture
+------------------------------------------------+
| Executive Dashboards                           |
| KPIs                                           |
| Analytics                                      |
+------------------------------------------------+

+------------------------------------------------+
| Financial Reports                              |
| GL TB BS PL VAT Cash Flow Aging                |
+------------------------------------------------+

+------------------------------------------------+
| NCollection Business Extensions                |
| Approval Workflow Audit Budget Assets          |
| Localization Subscription Rules                |
+------------------------------------------------+

+------------------------------------------------+
| Odoo Community Accounting Engine               |
| account.move                                   |
| account.payment                                |
| account.tax                                    |
| reconciliation                                 |
+------------------------------------------------+

+------------------------------------------------+
| PostgreSQL                                     |
+------------------------------------------------+


# 6. Financial Ownership Model

The financial platform is divided into clear ownership boundaries.

Every financial component belongs to exactly one owner.

This separation prevents duplicated business logic,
reduces maintenance costs,
and keeps upgrades predictable.

No component may cross these boundaries without explicit architectural approval.

---

## Odoo Community Owns

The following components are considered part of the accounting engine.

NCollection must never replace or duplicate them.

### Accounting Engine

- account.move
- account.move.line
- account.payment
- account.partial.reconcile
- account.bank.statement
- account.bank.statement.line

### Accounting Configuration

- Chart of Accounts
- Journals
- Fiscal Positions
- Taxes
- Tax Groups
- Payment Terms
- Payment Methods
- Currencies
- Fiscal Years

### Core Accounting Logic

- Posting
- Reconciliation
- Currency Conversion
- Tax Calculation
- Exchange Difference
- Lock Dates
- Journal Sequencing

These components are infrastructure.

They are extended only through official Odoo inheritance mechanisms.

---

## NCollection Owns

The following components belong exclusively to NCollection.

### Reporting

- General Ledger
- Trial Balance
- Balance Sheet
- Profit & Loss
- Cash Flow
- Partner Ledger
- Journal Ledger
- Aged Receivable
- Aged Payable
- VAT Reports
- Executive Reports

---

### Dashboards

- CEO Dashboard
- Finance Dashboard
- Accountant Dashboard
- Cash Dashboard
- KPI Widgets

---

### Analytics

- Profitability Analysis
- Financial KPIs
- Cost Centers
- Profit Centers
- Budget Analysis
- Forecasting

---

### Business Workflows

- Approval Chains
- Closing Workflow
- Accounting Notifications
- Subscription Restrictions
- Custom Validation Rules

---

### Audit

- Audit Logs
- Change Tracking
- Financial Timeline
- Approval History

---

### Localization

- UAE VAT
- FTA Requirements
- Arabic Documents
- Regional Reports

---

## Shared Responsibility

Some features require collaboration between both layers.

Examples:

Invoice Approval

Odoo owns:
- Invoice Model
- Posting
- Accounting Integrity

NCollection owns:
- Approval Workflow
- User Experience
- Notifications
- Business Rules

---

Payment Workflow

Odoo owns:
- Payment Registration
- Journal Entries
- Reconciliation

NCollection owns:
- Approval
- Dashboard
- Analytics
- Audit Trail

---

Financial Reports

Odoo owns:
- Financial Data

NCollection owns:
- Report Generation
- Filters
- Layout
- Export
- Visualization

# 7. Module Architecture

The financial platform is intentionally divided into multiple independent modules.

Each module has a single responsibility.

This separation provides:

- Better maintainability
- Independent testing
- Easier upgrades
- Cleaner ownership
- Better scalability
- Reduced coupling

A feature must belong to exactly one financial module.

If a feature appears to belong to multiple modules, the architecture should be reviewed rather than duplicating the implementation.

---

# Financial Module Map

| Module | Purpose |
|---------|---------|
| ncollection_account_core | Accounting platform extensions |
| ncollection_account_reports | Financial reporting engine |
| ncollection_account_dashboard | Executive dashboards and KPIs |
| ncollection_account_analytics | Financial analytics |
| ncollection_account_approval | Financial approval workflows |
| ncollection_account_budget | Budget planning and control |
| ncollection_account_assets | Fixed asset management |
| ncollection_account_documents | Financial documents and attachments |
| ncollection_account_audit | Audit trail and compliance |
| ncollection_account_localization_uae | UAE localization |

---

# Module Responsibilities

## ncollection_account_core

### Purpose

Provides business extensions around Odoo Accounting without replacing the accounting engine.

### Responsibilities

- Additional Accounting Settings
- SaaS Configuration
- Subscription Restrictions
- Business Validation Rules
- Additional Wizards
- Configuration UI
- Shared Financial Utilities
- Common Mixins

### Owns

- Configuration Models
- Helper Services
- Shared Components

### Must Never Own

- Reports
- Dashboards
- Budget Logic
- Analytics
- Audit
- Assets

### Depends On

- account

---

## ncollection_account_reports

### Purpose

Owns every financial report inside NCollection ERP.

### Responsibilities

- Report Engine
- Report Templates
- Export Engine
- PDF Generation
- Excel Generation
- Financial Filters

### Reports

- General Ledger
- Trial Balance
- Balance Sheet
- Profit & Loss
- Partner Ledger
- Journal Ledger
- Aged Receivable
- Aged Payable
- Cash Flow
- Tax Report
- VAT Report
- Executive Reports

### Must Never Own

- Accounting Logic
- Approval Logic
- Dashboard Logic

### Depends On

- account
- ncollection_account_core

---

## ncollection_account_dashboard

### Purpose

Provides executive financial dashboards.

### Responsibilities

- CEO Dashboard
- Finance Dashboard
- Accountant Dashboard
- KPI Widgets
- Cash Position
- Revenue Charts
- Expense Charts
- Receivable Summary
- Payable Summary

### Must Never Own

- Report Generation
- Accounting Rules

---

## ncollection_account_analytics

### Purpose

Provides advanced financial analysis.

### Responsibilities

- Cost Centers
- Profit Centers
- Financial KPIs
- Forecasting
- Variance Analysis
- Budget Analysis
- Financial Trends

### Must Never Own

- Journal Entries
- Reports
- Accounting Configuration

---

## ncollection_account_approval

### Purpose

Controls financial approval workflows.

### Responsibilities

- Invoice Approval
- Payment Approval
- Journal Approval
- Closing Approval
- Approval Matrix
- Approval Notifications

### Must Never Own

- Posting
- Reconciliation
- Accounting Engine

---

## ncollection_account_budget

### Purpose

Manages budgeting.

### Responsibilities

- Budget Planning
- Budget Revision
- Budget Approval
- Budget Monitoring
- Budget vs Actual

---

## ncollection_account_assets

### Purpose

Manages fixed assets.

### Responsibilities

- Asset Registration
- Depreciation
- Asset Disposal
- Asset Transfer
- Asset Categories

---

## ncollection_account_documents

### Purpose

Financial document management.

### Responsibilities

- Attachments
- Document Approval
- Archive
- OCR Integration
- Supporting Documents

---

## ncollection_account_audit

### Purpose

Financial auditing.

### Responsibilities

- Audit Trail
- Change History
- User Activity
- Financial Timeline
- Compliance Logs

---

## ncollection_account_localization_uae

### Purpose

Provides UAE-specific accounting functionality.

### Responsibilities

- UAE VAT
- TRN Validation
- FTA Compliance
- Arabic Reports
- Arabic Documents
- UAE Tax Rules

# 8. Feature Placement Rules

Before implementing any feature, determine which module owns it.

Every feature must have exactly one owner.

---

## Reports

If the feature generates financial information for users,
it belongs to:

→ ncollection_account_reports

Examples:

- General Ledger
- Trial Balance
- Balance Sheet
- Cash Flow

---

## Dashboards

If the feature provides KPIs or visual summaries,
it belongs to:

→ ncollection_account_dashboard

Examples:

- CEO Dashboard
- Cash Position
- Revenue Widgets

---

## Analytics

If the feature analyzes financial data,
it belongs to:

→ ncollection_account_analytics

Examples:

- Cost Centers
- Profitability
- Budget Variance

---

## Approval

If the feature controls user approval,
it belongs to:

→ ncollection_account_approval

Examples:

- Invoice Approval
- Payment Approval

---

## Budget

If the feature manages planning,
it belongs to:

→ ncollection_account_budget

---

## Assets

If the feature manages fixed assets,
it belongs to:

→ ncollection_account_assets

---

## Audit

If the feature tracks user actions,
it belongs to:

→ ncollection_account_audit

---

## Localization

If the feature is country-specific,
it belongs to:

→ ncollection_account_localization_uae

---

## Core

If the feature extends Odoo Accounting
without fitting any previous category,
it belongs to:

→ ncollection_account_core

---

# Architecture Rule

Never place a feature into a module simply because it already depends on that module.

Ownership is determined by responsibility, not convenience.

If ownership is unclear,

STOP.

Review the architecture.

Do not duplicate business logic.

# 8. Feature Placement Rules

Before implementing any feature, determine which module owns it.

Every feature must have exactly one owner.

---

## Reports

If the feature generates financial information for users,
it belongs to:

→ ncollection_account_reports

Examples:

- General Ledger
- Trial Balance
- Balance Sheet
- Cash Flow

---

## Dashboards

If the feature provides KPIs or visual summaries,
it belongs to:

→ ncollection_account_dashboard

Examples:

- CEO Dashboard
- Cash Position
- Revenue Widgets

---

## Analytics

If the feature analyzes financial data,
it belongs to:

→ ncollection_account_analytics

Examples:

- Cost Centers
- Profitability
- Budget Variance

---

## Approval

If the feature controls user approval,
it belongs to:

→ ncollection_account_approval

Examples:

- Invoice Approval
- Payment Approval

---

## Budget

If the feature manages planning,
it belongs to:

→ ncollection_account_budget

---

## Assets

If the feature manages fixed assets,
it belongs to:

→ ncollection_account_assets

---

## Audit

If the feature tracks user actions,
it belongs to:

→ ncollection_account_audit

---

## Localization

If the feature is country-specific,
it belongs to:

→ ncollection_account_localization_uae

---

## Core

If the feature extends Odoo Accounting
without fitting any previous category,
it belongs to:

→ ncollection_account_core

---

# Architecture Rule

Never place a feature into a module simply because it already depends on that module.

Ownership is determined by responsibility, not convenience.

If ownership is unclear,

STOP.

Review the architecture.

Do not duplicate business logic.

# 10. Financial Report Specifications

## Purpose

This chapter defines the functional and technical specifications for every financial report available in NCollection ERP.

Each report specification includes:

- Business Purpose
- Intended Users
- Data Sources
- Filters
- Columns
- Calculations
- Drill-down Behavior
- Export Options
- Performance Requirements
- Security Rules
- Acceptance Criteria

Every report implementation must conform to its specification before release.

---

# Report Catalog

The financial reporting module contains the following reports.

## General Accounting

- General Ledger
- Trial Balance
- Journal Ledger
- Account Ledger

---

## Financial Statements

- Balance Sheet
- Profit & Loss
- Cash Flow Statement
- Statement of Changes in Equity

---

## Partner Reports

- Customer Ledger
- Vendor Ledger
- Customer Statement
- Vendor Statement
- Aged Receivable
- Aged Payable

---

## Tax Reports

- VAT Summary
- VAT Details
- Tax Summary
- Tax Details

---

## Executive Reports

- Financial Summary
- Revenue Analysis
- Expense Analysis
- Profitability Report

---

## Management Reports

- Budget vs Actual
- Cost Center Analysis
- Profit Center Analysis
- Department Analysis

# General Ledger

## Purpose

The General Ledger provides the complete accounting activity for one or more accounts during a selected reporting period.

It is the primary report used by accountants to verify accounting movements, balances, and transaction history.

---

## Target Users

- Accountant
- Senior Accountant
- Finance Manager
- Auditor

---

## Data Sources

Primary Models

- account.move
- account.move.line
- account.account
- account.journal
- res.partner
- res.company

---

## Available Filters

### Required

- Company
- Fiscal Year
- Date From
- Date To
- Journal
- Account
- Posted / Draft

### Optional

- Partner
- Branch
- Cost Center
- Profit Center
- Currency
- Analytic Account
- Department
- Project

---

## Report Columns

- Date
- Journal
- Entry Number
- Account Code
- Account Name
- Partner
- Description
- Debit
- Credit
- Running Balance
- Currency
- User

---

## Sorting

Default

Date ASC

Secondary

Journal

Entry Number

---

## Grouping

The report supports grouping by:

- Account
- Journal
- Partner
- Month
- Cost Center

---

## Balance Rules

Opening Balance

+

Debit

-

Credit

=

Closing Balance

Opening balances must respect the selected reporting period.

---

## Drill Down

Every journal entry must open:

Journal Entry

↓

Accounting Lines

↓

Source Document

Examples

Invoice

Payment

Vendor Bill

Credit Note

Journal Entry

---

## Export

Supported Formats

- PDF
- Excel
- Print

Future

- CSV
- API

---

## Performance

The report must support:

- Millions of accounting lines
- Pagination
- Lazy Loading
- Background Export

---

## Security

Accountant

View

Export

Finance Manager

Full Access

Auditor

Read Only

CEO

Summary Only

---

## Acceptance Criteria

✓ Opening balance is correct

✓ Closing balance is correct

✓ Running balance is correct

✓ Totals match Trial Balance

✓ Drill-down works

✓ Export matches on-screen report

✓ Multi-company filtering works

✓ Currency conversion is correct

✓ Response time under enterprise dataset limits


# Trial Balance

## Purpose

The Trial Balance summarizes the opening balance, debit movements, credit movements, and closing balance for every account within a selected reporting period.

It is primarily used to verify the mathematical integrity of the accounting records and serves as the foundation for financial statements.

---

## Target Users

- Accountant
- Senior Accountant
- Finance Manager
- Auditor

---

## Data Sources

Primary Models

- account.account
- account.move.line

---

## Available Filters

### Required

- Company
- Fiscal Year
- Date From
- Date To
- Posted Entries

### Optional

- Branch
- Currency
- Account Type
- Cost Center
- Profit Center

---

## Report Columns

- Account Code
- Account Name
- Opening Balance
- Debit
- Credit
- Closing Balance

---

## Grouping

- Account Type
- Parent Account

---

## Drill Down

Trial Balance

↓

General Ledger

↓

Journal Entry

---

## Export

- PDF
- Excel
- Print

---

## Performance

- Aggregated queries
- Pagination
- Cached balances

---

## Security

- Accountant
- Finance Manager
- Auditor

---

## Acceptance Criteria

✓ Total Debits = Total Credits

✓ Opening balances are correct

✓ Closing balances are correct

✓ Drill-down works

✓ Export matches screen

# Balance Sheet

## Purpose

Displays the financial position of the company as of a selected date.

---

## Target Users

- CEO
- CFO
- Finance Manager
- Auditor

---

## Data Sources

- account.account
- account.move.line

---

## Sections

- Assets
- Liabilities
- Equity

---

## Available Filters

- Company
- Date
- Comparison Period
- Branch

---

## Report Columns

- Account
- Current Period
- Previous Period
- Variance
- Variance %

---

## Drill Down

Balance Sheet

↓

Account

↓

General Ledger

↓

Journal Entry

---

## Export

- PDF
- Excel

---

## Performance

- Cached balances
- Aggregated calculations

---

## Acceptance Criteria

✓ Assets = Liabilities + Equity

✓ Opening balances correct

✓ Comparison calculations correct
# Profit & Loss

## Purpose

Measures company profitability during a selected accounting period.

---

## Target Users

- CEO
- CFO
- Finance Manager

---

## Data Sources

- account.move.line
- account.account

---

## Sections

- Revenue
- Cost of Sales
- Gross Profit
- Operating Expenses
- Net Profit

---

## Filters

- Company
- Branch
- Date Range
- Cost Center
- Profit Center

---

## Columns

- Category
- Current
- Previous
- Variance
- Variance %

---

## Drill Down

P&L

↓

Account

↓

General Ledger

---

## Export

- PDF
- Excel

---

## Acceptance Criteria

✓ Net Profit calculated correctly

✓ Revenue totals correct

✓ Expense totals correct
# Cash Flow Statement

## Purpose

Shows how cash moved during the selected period.

---

## Target Users

- CFO
- CEO
- Finance Manager

---

## Sections

- Operating Activities
- Investing Activities
- Financing Activities

---

## Filters

- Company
- Date
- Branch

---

## Report Columns

- Category
- Amount
- Previous Period
- Difference

---

## Export

- PDF
- Excel

---

## Acceptance Criteria

✓ Cash movement reconciles with accounting entries

✓ Ending cash equals ledger balances
# Journal Ledger

## Purpose

Displays accounting entries grouped by journal.

---

## Data Sources

- account.move
- account.move.line
- account.journal

---

## Filters

- Journal
- Company
- Date
- Posted

---

## Columns

- Date
- Entry Number
- Journal
- Account
- Debit
- Credit

---

## Drill Down

Journal

↓

Entry

↓

Source Document

---

## Export

- PDF
- Excel
# Partner Ledger

## Purpose

Displays detailed transactions for customers and vendors.

---

## Data Sources

- res.partner
- account.move.line

---

## Filters

- Partner
- Company
- Date
- Customer/Vendor

---

## Columns

- Date
- Document
- Debit
- Credit
- Balance

---

## Drill Down

Partner

↓

Invoice

↓

Journal Entry

---

## Export

- PDF
- Excel

# Aged Receivable

## Purpose

Analyzes overdue customer balances.

---

## Buckets

- Current
- 1–30 Days
- 31–60 Days
- 61–90 Days
- 91–120 Days
- Over 120 Days

---

## Filters

- Customer
- Salesperson
- Company

---

## Columns

- Customer
- Current
- 30
- 60
- 90
- 120+
- Total

---

## Export

- PDF
- Excel

---

## Acceptance Criteria

✓ Aging buckets calculated correctly

✓ Outstanding balances match customer ledger

# Aged Payable

## Purpose

Analyzes overdue vendor balances.

---

## Buckets

- Current
- 1–30 Days
- 31–60 Days
- 61–90 Days
- 91–120 Days
- Over 120 Days

---

## Filters

- Vendor
- Company

---

## Columns

- Vendor
- Current
- 30
- 60
- 90
- 120+
- Total

---

## Export

- PDF
- Excel

# VAT Report

## Purpose

Generates VAT declarations compliant with UAE FTA regulations.

---

## Sections

- Sales VAT
- Purchase VAT
- Zero Rated
- Exempt
- Adjustments

---

## Filters

- VAT Period
- Company
- Branch

---

## Columns

- Tax Code
- Taxable Amount
- VAT Amount

---

## Export

- PDF
- Excel

---

## Acceptance Criteria

✓ VAT values match accounting entries

✓ UAE FTA format supported

# Budget vs Actual

## Purpose

Compares planned budgets against actual accounting results.

---

## Filters

- Fiscal Year
- Budget
- Department
- Cost Center

---

## Columns

- Budget
- Actual
- Variance
- Variance %

---

## Drill Down

Budget Line

↓

General Ledger

---

## Export

- PDF
- Excel

# Cost Center Analysis

## Purpose

Analyzes financial performance by Cost Center.

---

## Metrics

- Revenue
- Expense
- Profit
- Margin

---

## Filters

- Cost Center
- Company
- Date

---

## Export

- PDF
- Excel

# Profit Center Analysis

## Purpose

Measures profitability for each Profit Center.

---

## Metrics

- Revenue
- Expenses
- Gross Profit
- Net Profit

---

## Export

- PDF
- Excel

# Financial Summary

## Purpose

Provides an executive summary of the organization's financial health.

---

## KPIs

- Revenue
- Expenses
- Net Profit
- Cash
- Receivables
- Payables
- Assets
- Liabilities

---

## Target Users

- CEO
- CFO
- Board Members

---

## Export

- PDF
- Excel

