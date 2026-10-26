# System branding

(Formerly "System branding". The persisted names did not change: the model is still `ncollection.product.branding`, its table
`ncollection_product_branding`, the resolver `nc_brand()`, the QWeb value `nc_product_brand` and the session key
`nc_product_brand`. Only what people read changed, so every workspace keeps the row it already configured. See "Why the
technical names stayed" below.)

## Three identities, never one field

| | **System branding** | **Tenant branding** | **Company identity** |
|---|---|---|---|
| What | the SaaS system itself: system name, short name, logo, login logo, administration / system logo, favicon, browser title, copyright / ownership text, **system primary and secondary colour** | how one workspace looks: its primary, secondary and sidebar colour, its login background | who the company legally is: legal name, registration, address, TRN, company logo, stamp, printed identity |
| Model | `ncollection.product.branding` (one record per database) | `res.company.nc_primary_color`, `nc_secondary_color`, `nc_sidebar_color`, `nc_login_background` (also written by the platform's reseller push) | `res.company` / its partner |
| Configured in | **Workspace Administration > System Branding** (and Settings > Administration) | the company form, **Appearance** | the company form, **Company**, Document Stamps |
| Who may change it | **Platform staff only**: the `System Branding Manager` group (ACL row + a guard in the model; checked over RPC too); not the tenant Owner (see below) | company administrators | company administrators / the stamp manager group |
| Seen on | sign-in, reset and signup pages, browser tab and theme colour, error pages, offline and "workspace paused" pages, e-mail frame, administration / platform screens | inside the workspace: navbar, buttons, sidebar | printed invoices, bills, receipts, reports, the company switcher |

Isolation is asserted in both directions by tests (`test_system_branding.py`): a tenant's colours / name / logo cannot change anything the system
shows; a change to the system branding cannot change a tenant's colours or a company's identity; they are written by different models and share no
field.

## Who may change System Branding (platform staff, not the tenant Owner)

System Branding is the identity of the platform, so it is changed by platform staff. It is **not** a right of the tenant Owner, even though the Owner
role implies `base.group_system` (`role_groups.xml`): `base.group_system` keeps its global meaning and nothing here widens or narrows it.

| Who | Holds `System Branding Manager`? | Why |
|---|---|---|
| Platform staff in the platform database (`ncollection_subscription` Platform Admin; `base.group_system` implies it there, so existing operators keep their access) | yes | `Platform Admin` implies `ncollection_branding.group_system_branding_manager` (`saas_security.xml`) |
| A Platform Administrator entering a tenant (the passwordless proxy user) | yes, while inside that tenant | `ncollection_core` `_nc_proxy_platform_group_ids`, applied on first entry and refreshed on every re-entry |
| The built-in superuser (provisioning, migrations) | yes | superuser bypasses access checks |
| Tenant Owner, Tenant Administrator, any workspace role, Settings administrator of a tenant | **no** | a tenant database has no Platform Admin group (the subscription layer is not installed there), so no chain reaches the group |
| Ordinary, portal and anonymous users | no | as before |

The right is enforced at the ORM, not in a menu: the access row of `ncollection.product.branding` names only this group (read, write, create; never delete), the model's `create` / `write` /
`action_open` re-check it, and the two menus carry it. Everybody still **reads** the resolved identity (`nc_brand()`), because every page does. Tenant branding (`res.company.nc_*`) and company
identity stay the Owner's, exactly as before. Tests: `ncollection_core/tests/test_system_branding_boundary.py` (Owner and Settings administrator refused at the ORM and hidden from the menu, still
owning tenant branding and company identity, proxy holds and regains the right), `ncollection_subscription/tests/test_system_branding_staff.py` (platform staff and existing operators keep it),
`e2e/tests/product-branding.spec.ts` (the Owner is refused through the real web client; a platform proxy session edits it).

*Consequence to know about:* a tenant database now shows the system identity it was seeded or last given by platform staff; its Owner cannot rename it. How the platform's identity reaches tenants at
provisioning is unchanged and out of scope here.

### Holding the group cannot be self-served (the grant guard)

A boundary on a group is only as strong as the rule on who may JOIN it. The Owner implies `base.group_system` and therefore `base.group_erp_manager`, which Odoo lets write `res.users.group_ids` and
every relation of `res.groups`. Before the grant guard (`ncollection_branding/models/system_branding_grant.py`, pre-commit review H1) an Owner could simply add the group to their own account, assign
themselves a custom role carrying it, or make a group they hold imply it. Now any change that makes somebody NEW hold `System Branding Manager`, directly or through any implication, is refused with an
`AccessError` unless:

* it runs as the superuser itself (`uid == SUPERUSER_ID`: install, upgrade, migrations, the provisioning seed, the shell). `sudo()` is **not** enough: it keeps the acting user, and the workspace's own
  code calls `sudo()` on a tenant user's behalf (role assignment does);
* the acting user is platform staff (`ncollection_subscription`: a Platform Admin, in the platform database, may still make another operator);
* every new holder is a platform administrator's entry proxy (`ncollection_core`), which is how the entry flow grants it.

Holders are compared before and after the write, straight from `res_groups_users_rel` and the transitive closure of `res_groups_implied_rel`, so no field name, reified group field, compute inverse
or cache steps around it. Taking the right away is never restricted. A custom role can no longer contain the group either (`ncollection_core/models/role.py` `FORBIDDEN_GROUPS`). Tests: the self-grant
routes (direct, a new user, a custom role, `user_ids` / `implied_ids` / `implied_by_ids`, a new group) in `test_system_branding_boundary.py`; platform staff granting and a non-staff user manager
refused in `test_system_branding_staff.py`.

**Known limit, not redesigned:** `base.group_system` is Odoo's "Settings" administration. Its holders can still edit `ir.model.access` rows and record rules, or write server-action code, and so reach
`ncollection.product.branding` without ever holding the group. Closing that would mean narrowing what Settings administration is in Odoo (and therefore what the tenant Owner role is), which is an
architecture decision, not part of this change. What is closed is every route through users, groups and roles.

## One source of truth

`ncollection.product.branding._nc_record()` is the single row; `nc_brand()` returns the resolved identity as a plain dict (`name`, `short_name`, `title`,
`copyright`, `primary_color`, `secondary_color`, `logo_url`, `login_logo_url`, `admin_logo_url`, `favicon_url`, `has`, `version`), cached per version. Every
surface reads it:

* **Server templates** call `nc_product_brand()`. `ir.qweb._prepare_environment` adds it to every render (`models/ir_qweb.py`), so it also works for templates
  rendered without an HTTP request (error layouts, e-mails, tests). The browser title, favicon link, theme colour, login page and its copyright line, error pages,
  offline page, subscription-paused page and e-mail frames use it.
* **The web client** receives `session_info['nc_product_brand']` (`models/ir_http.py`); `white_label.js` uses `title` for the tab title fallback.
* **Images** are served by `/ncollection/brand/<logo|login|admin|favicon>?v=<version>` (`controllers/brand.py`): public on purpose (the sign-in page needs them), a fixed list of
  four kinds, no id and no model name accepted, `X-Content-Type-Options: nosniff`, `Cache-Control: immutable` when a version is in the URL (the version is the record's write date, so a
  changed image has a **new URL**; a favicon is never served stale), `no-cache` without one.

Fallbacks: login logo → system logo → the file shipped with the module; administration logo → system logo → shipped file; favicon → the real multi-size `favicon.ico`
shipped with the module; browser title → system name; copyright → "© <year> <system name>. All rights reserved."; colours → the NCollection palette (`#1F5F8F` / `#2D7AB7`).

## System colours use the existing token vocabulary

There is **one** CSS custom-property namespace, `--nc-color-*` (the design-token layer, `static/src/scss/tokens.scss`). System branding does not add another:
`views/branding_theme_templates.xml` emits the system's `--nc-color-primary` / `--nc-color-secondary` in a `<style id="nc_system_theme">` block **before** the tenant's
`<style id="nc_tenant_theme">`. Consequences, each covered by a test:

* inside a workspace the tenant's colours override (they come later at equal specificity), and a workspace that chose none **inherits the system's**;
* the sign-in, reset and signup pages carry `body.o_nc_system_surface`; the system block re-asserts its colours at that scope, which wins for everything on the page, so a
  **tenant's colours can never repaint a system surface**;
* error pages, the e-mail frame and the browser's `theme-color` meta read the system colour (they hard-coded `#1F5F8F` / `#17375E` before);
* both colours are strict 6-digit hex, enforced by an ORM constraint on every write path (UI, RPC, import, data file) and again by a validated fallback in `nc_brand()`,
  because the value is written into a `<style>` block: nothing that could break out of a CSS declaration can be stored or emitted. The block uses no `left` / `right` /
  margin / padding, so right-to-left pages are unaffected.

## Why the technical names stayed

Renaming the model, table, `nc_brand()`, the QWeb value or the session key would need an `ir.model` / `ir.model.data` / ACL / template / JS migration on every database, for no change a
person can see, and it would put the persisted row (which holds a workspace's configured name and images) at risk. The user-facing concept is "System Branding"; the persisted identifiers are
unchanged. The 19.0.1.11.0 migration adds the new columns (the ORM does that) and `_nc_adopt_system_defaults()` fills a missing / invalid colour with the palette; it never touches the
name, short name or any image, never overwrites a colour somebody chose, runs any number of times, and contains no schema SQL and no `-u all`.

## What "Balance Cloud" is

It is a **configured value** (the system name stored in a workspace's branding row), not text in the code. A repository scan finds no runtime file naming it, and
`TestNoHardcodedSystemIdentity` fails if any runtime file ever does or if the default product name is typed anywhere outside the three places that define the shipped default.

## Validation

Logos: PNG, JPEG or WebP. Favicon: PNG or ICO. Checked **by content** (magic bytes), never by file name; **SVG is refused** (it can carry script); at most 2 MB and 4096 px a side.
A renamed script, a text file or an executable is refused on write.

**How each format is proven readable.** PNG and JPEG are opened and verified with Pillow. **WebP is never decoded on the server**: Odoo initialises Pillow with only the BMP / GIF / ICO / JPEG / PNG / PPM decoders (`odoo/tools/image.py`: `Image.preinit()`, `_initialized = 2`), and its own `image_process` leaves WebP alone - which is why every WebP upload used to fail with "System logo is not a readable image" while the message promised WebP. A WebP is now checked the way Odoo reads one: the RIFF length matches the file, and `odoo.tools.image.get_webp_size` reads a non-zero size from its VP8 / VP8L / VP8X header; the browser decodes it. Enabling Pillow's WebP decoder process-wide was rejected: it widens the decoder surface of the whole server (libwebp has had remote-code-execution CVEs) for a check a header read answers.
**Messages say what is wrong**: "System logo could not be read as a PNG image: the file is damaged or incomplete.", or "... must be a PNG, JPEG or WebP image; this file is GIF" (GIF, BMP, TIFF, PDF, SVG / XML / HTML, AVIF / HEIC and non-WebP RIFF are named).

**The sign-in logo IS the System Branding logo.** The brand panel and the mobile card show the login logo (else the system logo), served versioned by `/ncollection/brand/login?v=<write stamp in ms>` - a replaced logo gets a new URL, so no browser or CDN keeps showing the old one. Nothing configured: the system name alone. There is no hard-coded mark (the inline SVG "N" that stood in for an unconfigured logo is gone) and the page never reads `/web/binary/company_logo` (the company identity). Proven by uploading logo A, then B, then a WebP C through the real form (`e2e/tests/product-branding.spec.ts`) and by `test_the_sign_in_logo_is_the_system_logo_a_then_b_never_the_company_s`.

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
| Shipped group and digest names that said "Odoo" | the system name is substituted once |
| The `(?)` documentation links of Settings | not rendered (JS template extension) |
| Web-client strings that NAME the product (`Install Odoo` in Discuss, the notification-permission messages, the session-expired dialog, `Odoo needs your authorization first.`) | `ir.http._get_translations_for_webclient` (`models/ir_http.py`, `WEB_IDENTITY_TERMS`): Odoo's own translation channel answers them in the System Branding name, in every language (the Arabic "أودو" too); an explicit list, never every string that mentions Odoo |
| Preferences > Notification "In Odoo" (and its help) | `res.users.notification_type` keeps its values (`email` / `inbox`); its labels come from a selection METHOD resolved on every read: "In %(system)s" / "في %(system)s", so a renamed system shows at once |
| The company's logo on its documents | the vendor marks (Odoo's default company logo, the NCollection mark seeded until 19.0.1.15.0) are removed, exact bytes only; the letterhead shows the company's own logo or its name |

`web.base.title` and `database.show_name` are system parameters that hold the default product name as DATA. Nothing in the product or in the Odoo
core of this version reads either (verified by search; `database.show_name` only belongs to the dev database manager, `list_db = False` in production), so
neither is user-visible in any supported flow and neither is a second source of truth. They are left alone, and a test fails if product code ever starts reading
them (they would not follow a configured name).

Residual technical references kept on purpose: the `/odoo/...` URL prefix inside links, `X-Odoo-Database`, the `odoo` package and model / table names, the RPC
error envelope (`"message": "Odoo Server Error"` in a JSON response, not shown as such: the web client's dialogs are retitled), LGPL attribution, comments and tests.

## Where the pieces are

* Model, validation, resolver: `ncollection_branding/models/product_branding.py` (the persisted name); seeded record `data/product_branding_data.xml`; form `views/product_branding_views.xml`; theme tokens `views/branding_theme_templates.xml`.
* Admin logo surfaces: the Workspace Overview and the platform (SaaS) dashboard header, and the platform-mode bar (`ncollection_core/static/src/platform/`).
* Tests: `ncollection_branding/tests/test_system_branding.py` (model, colours, security, three-way isolation, surfaces, no hard-coded identity, existing-data migration), `test_product_branding.py` (fallbacks, image validation, versioned URLs, no vendor name), `test_webclient_branding.py`, `test_translation_coverage.py`;
  Playwright `e2e/tests/product-branding.spec.ts`.

## Not in scope / limits

* The Odoo database manager page (`/web/database/selector`) is core and only reachable when `list_db = True` (dev); production sets it False.
* Third-party OCA / core modules that are installed but not part of the product's screens may carry their own wording; the audit above covers the screens the product ships.
* **The write check accepts the REAL superuser only** (`uid == SUPERUSER_ID`: install, migrations), never `env.su`: a tenant Owner holds `base.group_system` and can run `.sudo()` from a server action, which keeps the acting user. The proxy flags the System Branding exemption rests on (`nc_platform_proxy` / `nc_platform_ref`) follow the same rule (security review 2026-10-24).
* **Known limit, stated: a tenant Owner is still a system administrator of their own database.** With `base.group_system` they can edit `ir.model.data` (re-point the `group_system_branding_manager` xmlid to a group they hold, then add an access row), or create an `ir.cron` that runs as the superuser. Both change only their own database's System Branding (sign-in, error and mail surfaces of that one workspace), never another tenant's or the platform's. Closing it needs the Owner to stop being `base.group_system`, an architecture decision outside this change.
