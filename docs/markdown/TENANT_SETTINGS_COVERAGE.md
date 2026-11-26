# Tenant Settings Coverage

What a tenant's Workspace Administrator (and section delegates) can configure on **Settings**
(`ncollection.business.settings`), measured against every native setting (`res.config.settings`)
of the installed modules. Measured on 2026-11-26 on a copy of a fully populated database
(154 modules, 45 of them contributing settings).

## Why some settings were missing

Under Option A the tenant Owner does not hold `base.group_system`, so Odoo's own settings page is
refused; the tenant's Settings page offers an explicit, code-defined list instead
(`_nc_settings_spec`), extended by the module that owns each setting. Accounting (release 403b07a),
Sales, Purchase and Inventory had their lists. **No NCollection module depends on CRM, Employees,
Attendances, Expenses, Manufacturing, Project, Point of Sale, Email Marketing or Snail Mail**, so
those applications had no list at all, and their settings were unreachable for a tenant that had
them installed. "Verify VAT Numbers" (`base_vat`) and the GCC invoice layout had the same problem.

## The fix

One table, `ncollection_core/wizards/app_settings.py` (`APP_SETTINGS`): setting, owning module,
Settings section, how it is applied, administrator-only flag.

- A row is offered **only while its native setting exists**, i.e. its module is installed for this
  tenant. A tenant without the app sees nothing of it and the server never reads or writes it.
- Settings with side effects in the module's own `set_values` (Work Orders, Leads, Pricelists,
  Project Stages, Quotation Templates, Storage Locations / Multi-Step Routes, Mailing Campaigns,
  the Attendance and Expense settings) are applied by **Odoo's own `set_values`** on a settings
  record holding today's values plus the one change, after every check. `execute()` (which
  installs modules) is never called.
- Everything else is the page's existing machinery: section level checked server-side for every
  changed value, current company only, validation against the native field, audit.
- Company fields of the new settings join the company delegation allowlist; accounting controls
  (`vat_check_vies`, `l10n_gcc_dual_language_invoice`, `reconcile_aggregate`) are Workspace
  Administrator only, on the page and on a direct `res.company` write.
- A test fails if a native field changes type under the table (`test_app_settings.py`).

## Coverage after the change (installed modules with settings)

| Module | Native settings | Offered | Not offered, and why |
|---|---|---|---|
| account | 62 | Settings > Accounting + Accounting Defaults (all business ones) | `module_*` (installs apps), restrictive audit trail (irreversible), PEPPOL, link QR, terms format (see TENANT_ACCOUNTING_SETTINGS.md) |
| base_vat | 1 | Verify VAT Numbers | — |
| l10n_gcc_invoice | 2 | GCC Formatted Invoices | country flag (computed) |
| account_reconcile_oca | 1 | Reconcile Aggregate | — |
| sale / sale_management / sale_stock | 37 | 12 existing + Quotation Templates, Picking Policy, Security Lead Time | `module_*`; down-payment account and invoice e-mail template (records: their own screens) |
| product | 6 | Variants, Units (existing) + Pricelists, weight / volume units | `module_*` |
| crm | 19 | Leads, Recurring Revenues, Multi Teams | paid IAP lead enrichment, lead-mining, internal scoring fields, auto-assignment scheduler |
| purchase / purchase_stock | 14 | 5 existing + Days to Purchase | `module_*` |
| stock / stock_account | 26 | 9 existing + Storage Locations, Multi-Step Routes, GS1, Replenishment Horizon, Email Confirmation, Lots on Invoices | `module_*`; SMS confirmation (needs the SMS template record) |
| mrp | 10 | Work Orders, Dependencies, By-Products, Unlock, Allocation Report | `module_*` |
| project | 2 | Project Stages | `module_*` |
| hr | 11 | Presence controls, contract and work-permit reminders | `module_*`; Company Working Hours (a record: Employees > Configuration) |
| hr_attendance | 14 | 11 settings | kiosk URL (computed), overtime tolerances (applied by the module's own wizard logic, not yet mapped) |
| hr_expense | 8 | Record expenses by email | `module_*`; expense journal and payment methods (records) |
| point_of_sale | 90 | Stock update, Presets, Self-service invoicing, Ticket code | the **shop configuration** (`pos_*`, 57 fields): the shop's own form, for Point of Sale managers |
| mass_mailing | 6 | Campaigns, Blacklist option, 24H reports | dedicated mail server (platform) |
| snailmail | 4 | Colour, Cover page, Both sides | — |
| mail, auth_signup, auth_totp_mail, google_gmail, microsoft_outlook, digest, web_unsplash, base_geolocalize, google_address_autocomplete, partner_autocomplete, web, ncollection_saas | — | — | **platform / technical**: mail servers, OAuth, signup, 2FA policy, API keys, profiling. Never tenant settings. |

## Roles

| Role | Business settings of installed apps | Accounting controls | Odoo's own settings page |
|---|---|---|---|
| Tenant Owner / Workspace Administrator | edit (platform level permitting) | edit | refused (no `base.group_system`) |
| Business Settings delegate | edit the Business section; others read-only per policy | refused (page and direct write) | refused |
| Section delegate (Sales / Purchase / Inventory) | its own section | refused | refused |
| Accounting clerk, other users | page refused | refused | refused |
| Platform Administrator | native path, unchanged | native | native |
