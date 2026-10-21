# Product branding

Two different brands live in this product and they never share a field:

| | **Product brand** | **Company brand** |
|---|---|---|
| What | the application: name, short name, favicon, product logo, login logo, administration logo | one tenant company: legal name, company logo, colours, stamp, printed identity |
| Where it is configured | **Workspace Administration > Product Branding** (`ncollection.product.branding`, one record) | the company form, **Appearance**, **Company**, Document Stamps |
| Who may change it | Settings administrators only (ACL + `write` guard in the model) | company administrators / the stamp manager group |
| Where it shows | sign-in and password pages, browser tab, error and access-denied pages, the offline page, e-mail footers, administration screens, platform (SaaS) screens | printed invoices, bills, receipts, quotations, reports, the company switcher |

The product brand is **not** the company brand: setting a product logo never changes a company logo, a printed document, or a stamp (a test asserts it).

## One source of truth

`ncollection.product.branding._nc_record()` is the single row; `nc_brand()` returns the resolved identity as a plain dict (`name`, `short_name`, `logo_url`,
`login_logo_url`, `admin_logo_url`, `favicon_url`, `has`, `version`), cached per version. Every surface reads it:

* **Server templates** call `nc_product_brand()`. `ir.qweb._prepare_environment` adds it to every render (`models/ir_qweb.py`), so it also works for templates rendered
  without an HTTP request (error layouts, e-mails, tests). The tab title, favicon link, login page, error pages, offline page, subscription-paused page and e-mail layouts use it.
* **The web client** receives `session_info['nc_product_brand']` (`models/ir_http.py`); `white_label.js` uses it for the tab title fallback.
* **Images** are served by `/ncollection/brand/<logo|login|admin|favicon>?v=<version>` (`controllers/brand.py`): public on purpose (the sign-in page needs them), a fixed list of four
  kinds, no id and no model name accepted, `X-Content-Type-Options: nosniff`, `Cache-Control: immutable` when a version is in the URL (the version is the record's write date, so a changed
  image has a **new URL**; a favicon is never served stale), `no-cache` without one.

Fallbacks: login logo → product logo → the file shipped with the module; administration logo → product logo → shipped file; favicon → the real multi-size `favicon.ico` shipped with the module.

## Validation

Logos: PNG, JPEG or WebP. Favicon: PNG or ICO. Checked **by content** (magic bytes), never by file name; **SVG is refused** (it can carry script); at most 2 MB and 4096 px a side; the image must decode.
A renamed script, a text file or an executable is refused on write.

## "Odoo" in the user interface

Classification of the occurrences (repository scan + a real-browser audit of login, password reset, backend shell, launcher, Settings, error pages, platform screens):

| Class | Action |
|---|---|
| User-visible product name / brand (title, login, error pages, offline page, e-mail footer, Settings "About", user-menu account link, dialog titles, `Powered by`) | replaced by the central resolver |
| User-visible wording that named the upstream product (field help, wizard messages, Settings API-keys / IAP labels, a note on the accounting defaults page) | reworded (and the Arabic catalogue kept in step) |
| The `(?)` documentation links of Settings widgets that point at the upstream vendor's website | not rendered (`DocumentationLink` template extension); links to other sites are untouched |
| Technical and legal: the `odoo` Python package / module framework, `X-Odoo-Database` header, `/odoo` URL prefix, `<odoo>` XML root, `ir.*` model names, LGPL attribution (`docs/LEGAL.md`), the dev-only database manager (`list_db = False` in production), comments, docstrings, tests, manifests | intentionally kept |

### How the remaining vendor wording is handled

| Where | Mechanism |
|---|---|
| Server-rendered pages and e-mails of a module we depend on (reset-password e-mail, portal sidebar, digest e-mail, two-factor pages, the sale / purchase portal "connect your software" dialog) | a view inheritance in the module that depends on the module that owns the page (`ncollection_branding`, `ncollection_account_core`, `ncollection_auth`, `ncollection_sale_documents`, `ncollection_purchase_documents`), using the same resolver |
| Module-authored mail templates (invitation, sign-up, portal set-password, e-invoice) and the empty-state help of actions (vendor "sample" / documentation buttons) | `ncollection.vendor.content._nc_unbrand` (`models/vendor_content.py`): an explicit, idempotent rule list applied only to records a MODULE shipped (never to a user's own template), run on install / upgrade and when the registry loads, so a module installed later is covered |
| The system user's name (`OdooBot`) | renamed to `<short name> Bot`, and kept in step when the short name changes, unless an administrator gave it another name |
| Shipped group and digest names that said "Odoo" | the product name is substituted once |
| The `(?)` documentation links of Settings | not rendered (JS template extension) |

`web.base.title` and `database.show_name` are system parameters that hold the default product name as DATA. Nothing in the product or in the Odoo
core of this version reads either (verified by search; `database.show_name` only belongs to the dev database manager, `list_db = False` in production), so
neither is user-visible in any supported flow and neither is a second source of truth. They are left alone, and a test fails if product code ever starts reading
them (they would not follow a configured name).

Residual technical references kept on purpose: the `/odoo/...` URL prefix inside links, `X-Odoo-Database`, the `odoo` package and model / table names, the RPC
error envelope (`"message": "Odoo Server Error"` in a JSON response, not shown as such: the web client's dialogs are retitled), LGPL attribution, comments and tests.

## Where the pieces are

* Model, validation, resolver: `ncollection_branding/models/product_branding.py`; seeded record `data/product_branding_data.xml`; form `views/product_branding_views.xml`.
* Admin logo surfaces: the Workspace Overview and the platform (SaaS) dashboard header, and the platform-mode bar (`ncollection_core/static/src/platform/`).
* Tests: `ncollection_branding/tests/test_product_branding.py` (model, fallbacks, validation, versioned URLs, sign-in page, no vendor name), `test_webclient_branding.py`, `test_translation_coverage.py`;
  Playwright `e2e/tests/product-branding.spec.ts`.

## Not in scope / limits

* The Odoo database manager page (`/web/database/selector`) is core and only reachable when `list_db = True` (dev); production sets it False.
* Third-party OCA / core modules that are installed but not part of the product's screens may carry their own wording; the audit above covers the screens the product ships.
