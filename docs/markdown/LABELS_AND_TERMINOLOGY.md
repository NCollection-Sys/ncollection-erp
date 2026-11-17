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

Odoo stores these texts **per database**, and a database is one tenant. Two scopes:

* **Whole workspace** (default): Odoo's own translation of the text is changed, so every surface shows it.
* **One company**: field labels, field help, **choice labels** and menu names. Stored with `company_id`, NOT written
  to the shared translations; layered on at read time for users working in that company (`ir.model.fields.
  get_field_string / get_field_help / get_field_selection`, `ir.ui.menu.load_menus`), from a cache keyed by company and
  language.

| Text | One company | Why |
|---|---|---|
| Field label (the field's own label) | yes | read through `get_field_string`, overlaid per company |
| Field help | yes | `get_field_help` |
| Choice label (selection value) | yes (this release) | `get_field_selection` |
| Menu name | yes | `load_menus` |
| Screen text: button, tab, section, a label set by the screen (`string=` in a view) | no | one translation of the combined view, shared by every company and cached per view |
| Page title (action name) | no | one translated action record |

The in-page editor used to save a company-only edit of a SCREEN text for the whole workspace without saying so (the
company was ignored on view targets). It now refuses it ("applies to the whole workspace ... Choose Whole workspace")
and saves nothing (test `test_screen_text_is_never_silently_widened_to_the_workspace`). In a multi-company tenant this
matters most for field labels that a screen overrides with `string=`: those are screen texts.

Precedence (lowest first): original < platform terminology rule < workspace override < company override.

## In-page editor

A switch in the top bar (offered when the user reaches Interface Text at edit level). While on, editable texts are
outlined; a click on a field label, a list column, a menu or a button opens a dialog with the current text, the new
text, the scope (workspace / my company, where supported), Save and Reset. The element is identified from Odoo's own
metadata (the label's field widget, the column's field name, the menu id, the button text within the current model),
never by searching page text, and saved through `ncollection.label.override.nc_quick_edit`, which applies the model's
validation, rights, platform policy and audit. A field label that the view overrides with its own `string=` is renamed
where the user sees it (the view text) as well as on the field. After saving, the web client's caches are dropped and
the page reloads; the label version is also mixed into the web client's cache key, so other users' browsers drop their
cached views and menus on their next load. Values are plain text (`< > { }` refused).

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
says مورد for every one. A string is reworded in English ONLY when an Odoo method **returns** it: that method is
extended and the platform rule is applied to its result (`ncollection_account_core/models/terminology_code_strings.py`).
A string Odoo RAISES in an error, posts inside a method, or builds in JavaScript is left alone: rewording it would mean
catching exceptions or messages by their text, the unsafe interception this project does not do.

| # | String (English) | User-visible? | Mechanism in Odoo | Decision |
|---|---|---|---|---|
| 1 | `Vendor Bills` (journal name placeholder) | yes, creating a purchase journal | returned by `_compute_name_placeholder` | **reworded**: Supplier Bills |
| 2 | `Draft Vendor Credit Note` | yes, lists, breadcrumbs, links | returned by `_get_move_display_name` | **reworded** |
| 3 | `Vendor Bill Created` | yes, chatter of every new bill | returned by `account.move._creation_message` | **reworded (this release)**: Supplier Bill Created |
| 4 | `Vendor Bills` (analytic account button: list title) | yes | returned by `action_view_vendor_bill` | **reworded (this release)** |
| 5 | `Import Template for Vendor Pricelists` | yes, import dialog of supplier pricelists | returned by `get_import_templates` | **reworded (this release)** |
| 6 | `The field 'Vendor' is required, please complete it to validate the Vendor Bill.` | yes, posting a bill without partner | collected and RAISED inside `_post` | unchanged (needs exception interception) |
| 7 | `Only vendor bills allow for deductibility of product/services.` | rarely | raised in a constraint | unchanged |
| 8 | `Unable to cancel purchase order(s): ... related vendor bills.` | yes, cancelling a billed PO | raised in `button_cancel` | unchanged |
| 9 | `You can only upload a bill for a single vendor at a time.` (Python + JS) | yes, upload on POs | raised (Python) / JS notification | unchanged |
| 10 | `Select Vendor Bill lines...`, `...same vendor.`, `Vendor Bill lines can only be added to one Purchase Order.` | yes, bill-line matching | raised in the matching wizard | unchanged |
| 11 | `This vendor bill has been created from:` / `modified from:` | yes, chatter of bills from POs | built and posted inside purchase's own method | unchanged (posted inside the method) |
| 12 | JS tour texts (`Search a vendor name...`, ...) | no: onboarding tour, never started for tenants | JavaScript | unreachable in normal use |

Five are reworded; the rest are listed with their reason. Arabic is correct for all of them.

## Developer Mode

Settings > Activate Developer Mode (the existing menu) turns on Odoo's own developer mode, which sticks only for the tenant
Owner, the platform administrator and the superuser (`ir.http._handle_debug`). The label editor does NOT need it: it
works in normal mode for whoever may edit Interface Text. Odoo's technical menus stay restricted to
`base.group_no_one` / `base.group_system`.

## NCollection's own strings (owner review 2026-11)

The purple role badges in partner dropdowns ("Customer | Vendor") were NCollection's OWN label
(`ncollection_account_core/models/res_partner.py` `_nc_role_labels`), a code string, which the terminology rule cannot
reach in English. Fixed at the source: every user-visible "Vendor" in NCollection's own Python, views and data now says
Supplier (role badges, document kinds and file names, report names, dashboard KPI, invoice charge scope, onboarding,
menus), with the Arabic re-keyed. Technical identifiers (`vendor` role key, xml ids, fields) are unchanged.
