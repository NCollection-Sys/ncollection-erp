# Interface Text: labels and terminology

One engine (`ncollection_core/models/label_override.py`) changes the text people read, never what the system
does. It writes Odoo's **own** translated columns; it has no parallel string store and no DOM replacement.

## Two kinds of entry, one precedence

| Lowest → highest | What | Who | Model |
|---|---|---|---|
| 1 | The module's original text | (code) | — |
| 2 | **Terminology rule**: whole-word, case-preserving replacement in one language (e.g. *Vendor → Supplier*) | Platform Administrator (`base.group_system`); tenant reads | `ncollection.label.rule` |
| 3 | **Label override**: one element's text in one language | Workspace Administrator (`group_tenant_admin`) or Platform Administrator | `ncollection.label.override` |

A module upgrade reloads the original texts. At every registry load the engine compares one signature (installed
module versions + active rules + active overrides, `ir.config_parameter ncollection_core.label_signature`) and
re-applies rules, then overrides, only when it changed. Every change is in `ncollection.label.audit` (append-only:
no write, no unlink through the ORM).

## Scope

Odoo stores these texts **per database**, and a database is one tenant: an override is tenant-wide, in every
company of that tenant. **Per-company labels are not supported**; that would need a second string store read by every
view, and Odoo has no supported hook for it. The form shows the scope ("This whole workspace").

## Coverage matrix

| Text | Editable | How |
|---|---|---|
| Menu names | Yes | `ir.ui.menu.name` (Odoo's technical Settings tree and developer-only menus excluded) |
| Page titles (window actions) | Yes | `ir.actions.act_window.name` |
| Field labels | Yes | `ir.model.fields.field_description` (read by `fields_get` in the user's language) |
| Field help / tooltips | Yes | `ir.model.fields.help` |
| Choice labels (selection values) | Yes | `ir.model.fields.selection.name` (the stored KEY never changes) |
| Buttons, tabs, section titles, placeholders, view `string=` | Yes | `ir.ui.view.arch_db` terms; only an existing TEXT term is replaced, never a node or attribute name |
| QWeb report captions | Yes, where the text is a static term of the report template | same as views (`type = qweb`) |
| Python messages (`_()`: errors, notifications, some report captions) | **No** | loaded from each module's own `i18n` files, cannot be changed from the database |
| JavaScript strings (`_t()`: dialogs, widgets) | **No** | same |
| Data the user typed (partner names, product names, journal names) | Not by this tool | it is data: edit the record |
| Security texts (groups, record rules, access rights, roles, audit rules) and `ir.*` technical models | **Refused** | protected: a tenant cannot reword a security boundary |

Values are plain text: `< > { }` and control characters are refused, maximum 120 characters (help 1000).

## Supplier, not Vendor (measured on a copy of `ncollection`)

| Where | "Vendor" before | After |
|---|---|---|
| Menus | 8 | 0 |
| Page titles | 5 | 0 |
| Field labels | 44 | 0 |
| Choice labels | 17 | 0 |
| Views (texts) | 55 | 6 views still contain the word, only in technical names (`name="vendor"`) and code comments, never shown |
| Arabic labels "بائع" (seller) | 3 | 0 (`البائع → المورد`, `بائع → مورد`) |
| Odoo Python `_()` strings in account / purchase / product | 14 | 2 reworded by extension (below); 12 unchanged in English |
| Odoo JS `_t()` strings in purchase | 4 | unchanged in English |

### Remaining code strings, one by one

Odoo returns `_()` / `_t()` text **verbatim in English** (`odoo/tools/translate.py` `get_translation`: `if lang ==
'en_US': translation = source`), so no translation can reword them. Arabic users see Odoo's own Arabic, which already
says مورد for every one. Two are reworded in English by extending the method that builds them
(`ncollection_account_core/models/terminology_code_strings.py`, through the same platform rule).

| String (English) | Where a user meets it | Status |
|---|---|---|
| `Vendor Bills` (journal name placeholder) | creating a purchase journal | **reworded**: "Supplier Bills" |
| `Draft Vendor Credit Note` | name of a draft supplier refund in lists, breadcrumbs, links | **reworded**: "Draft Supplier Credit Note" |
| `Vendor Bills` (analytic account smart button) | Analytic account form, button label | unchanged (button text comes from Python) |
| `The field 'Vendor' is required, please complete it to validate the Vendor Bill.` | posting a bill without a partner | unchanged (error raised inside Odoo's posting check; rewording means re-implementing it) |
| `Vendor Bill Created` | chatter note on a bill created from a document | unchanged |
| `Only vendor bills allow for deductibility of product/services.` | tax deductibility error (rare) | unchanged |
| `Unable to cancel purchase order(s): … related vendor bills.` | cancelling a billed purchase order | unchanged |
| `You can only upload a bill for a single vendor at a time.` (Python and JS) | uploading bills on a purchase order | unchanged |
| `Select Vendor Bill lines…`, `…same vendor.`, `Vendor Bill lines can only be added to one Purchase Order.` | bill-line matching on purchase orders | unchanged |
| `This vendor bill has been created from: ` / `modified from: ` | chatter note | unchanged |
| `Import Template for Vendor Pricelists` | product supplier pricelist import template name | unchanged |
| JS tour texts (`Search a vendor name…`, `Send the request for quotation to your vendor.`) | Odoo's onboarding tour only, not started for tenants | unreachable in normal use |

## Developer Mode

Workspace Administration > Interface Text > Developer Mode opens `/odoo?debug=1` for Workspace and Platform
Administrators. Odoo's technical menus stay restricted to `base.group_no_one` / `base.group_system`; for a Workspace
Administrator debug mode is the field and view inspector used to find the text to rename, not technical
administration.
