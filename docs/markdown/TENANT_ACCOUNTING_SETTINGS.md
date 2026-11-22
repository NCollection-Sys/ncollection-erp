# Tenant Accounting Settings

Where a tenant administrator changes the company's accounting configuration, and why some native
Odoo settings are not offered to them.

## Root cause of the gap (2026-11-21)

Under **Option A**, the tenant Owner holds `ncollection_core.group_tenant_admin` and
`account.group_account_manager`, but **not** `base.group_system` or `base.group_erp_manager`.
Odoo's own settings page (`res.config.settings`) needs those groups, so it is refused to the Owner.
That is deliberate: the same page installs modules and edits technical configuration.

The replacement is **Settings > Accounting** (`ncollection.business.settings`, built in
`ncollection_core`). It applies only an explicit, code-defined list of settings. Before this change,
`ncollection_account_core` listed only 10 accounting settings.

**Main Currency, Fiscal Country, Chart of Accounts, cash-basis taxes, Multi-Currencies, the credit
limit and the others below were not in that list, so no tenant administrator could reach them.**
The other suspected causes were checked and ruled out:

- **Plans and entitlements:** not the cause. The list is the same on every plan.
- **Config-sync:** not the cause. It does not touch these settings.
- **Hidden views and provisioning defaults:** not the cause.

Records such as taxes, fiscal positions, payment terms, journals and currencies were already
editable through their own menus.

## Availability matrix

| Native setting (`res.config.settings`) | Offered | How it is applied |
|---|---|---|
| `currency_id` Main Currency | yes, while the company has no accounting entries | handler: tenant administrator only, refused after entries; Odoo's own `res.company.write` check still runs |
| `account_fiscal_country_id` Fiscal Country | yes, while there are no entries | handler: refused after entries (stricter than Odoo: an unsafe localization change after posting) |
| `chart_template` Chart of Accounts | only for a company with **no chart yet**, no entries, **installed** localization only | handler: Odoo's own `try_loading`. A company that already has a chart is refused: Odoo's loader would DELETE its accounts, taxes, journals and fiscal positions (and its branches'), even with nothing posted. A chart needing an uninstalled module is refused (entitlement). Replacing a chart is a platform operation |
| `sale_tax_id`, `purchase_tax_id`, `tax_calculation_rounding_method`, `account_price_include` | yes | company field (delegation) |
| `tax_exigibility`, `tax_cash_basis_journal_id`, `account_cash_basis_base_account_id` | yes | company field (delegation; same-company check) |
| `group_multi_currency`, `group_analytic_accounting`, `group_cash_rounding`, `group_sale_delivery_address` | yes | feature group (`_apply_group` / `_remove_group`) |
| `qr_code`, `display_invoice_amount_total_words`, `display_invoice_tax_company_currency`, `incoterm_id`, `quick_edit_mode`, `autopost_bills`, `account_use_credit_limit` | yes | company field (delegation) |
| `account_storno` | only where Odoo offers it (country), on the root company, before entries | handler |
| `tax_cash_basis_journal_id` type, discount account types, cash basis off while on-payment taxes exist | | Odoo checks these only in its view; enforced at the ORM here |
| `use_invoice_terms`, `show_sale_receipts` | yes | configuration parameter |
| `account_default_credit_limit` | yes | the same `ir.default` row Odoo writes |
| `account_discount_income_allocation_id`, `account_discount_expense_allocation_id` | yes | company field (delegation; same-company check) |
| Default accounts (receivable, payable, income, expense, transfer, suspense, exchange, early-payment, accrual) | yes, on Settings > Accounting Defaults | already existed |
| Taxes, Fiscal Positions, Payment Terms, Journals, Currencies (records) | yes, linked from the page | native screens, native rights |
| `module_*` (install an app) | **no** | installs modules; entitlement only (platform) |
| `restrictive_audit_trail` | **no** | irreversible once on; platform decision |
| `account_peppol*`, `link_qr_code`, `terms_type` + `invoice_terms` | not yet | PEPPOL registration needs the platform; the link QR needs online payment; the terms format without its text editor would be half a setting |

## Safety rules

- **Localization consistency.** A chart is compatible with a fiscal country when Odoo registers it for that country (every chart of a country with several, e.g. de_skr03 / de_skr04), or it is the generic chart for a country with no localization of its own. A fiscal country that does not fit the loaded chart is refused. A company already in that state (an Egyptian company on the generic chart and its US taxes) is flagged on the page, and its currency, fiscal country and chart are frozen with an explanation: a controlled localization migration (map and keep the existing accounts, taxes and journals) is a platform procedure, not a setting. Existing tenants are never repaired automatically.
- **Currency consistency.** A country chart keeps its country's currency; the change is refused while a journal already holds the new currency as a foreign currency.
- **A chart loads only on an empty company tree:** no chart anywhere in the tree, no account, tax, journal or fiscal position, and every company of the tree one the user belongs to (Odoo's loader deletes what it finds there).
- **Section delegates.** The Accounting app is not sent to a Business Settings delegate (`groups=` on the app, so not even an empty sidebar entry) and is refused on save and on a direct company write; only a Workspace Administrator changes accounting settings. The Accounting Defaults section stays delegable, as before: its form is editable for that delegate, with the company's tax settings read-only.
- **Accounting Defaults in the browser.** Until 2026-11-22 the form opened read-only for the Owner (Odoo marks a `res.company` form non-editable without the ACL write right). It is now editable for exactly the users the delegation accepts; see WORKSPACE_AND_TENANT_ADMINISTRATION.md, "The delegated forms in the browser".

- **Fixed after entries:** once the company tree has accounting entries
  (`root_id._existing_accounting()`), the page shows currency, fiscal country and chart read-only,
  with the reason, and the server refuses them. Nothing is ever changed automatically.
- **Tenant administrator only:** the company identity (currency, fiscal country, chart) can be
  changed only by a tenant administrator, never by a delegate of the Business Settings section.
- **Branches:** currency, cash basis and storno follow the parent company (Odoo delegates them to the root); refused on a branch.
- **Current company only:** always `env.company`, never a company sent by the client. Many2one
  values must belong to that company.
- **No privilege escalation:** the Owner still holds neither `base.group_system` nor
  `res.config.settings`.
- **Audit:** every change is recorded as `admin_settings_change`.

## Tests

`ncollection_account_core/tests/test_accounting_settings_complete.py`:

- **Regression guard:** every offered setting must stay on the Owner's Settings page and must still
  be a native setting. If one disappears, the build fails.
- **Coverage:**
  - values persist;
  - the company identity changes while there are no entries, and is refused once entries exist;
  - a chart needing an uninstalled localization is refused, and no module is scheduled;
  - a non-administrator is refused;
  - the Owner is still not a system administrator;
  - another company is never touched.
