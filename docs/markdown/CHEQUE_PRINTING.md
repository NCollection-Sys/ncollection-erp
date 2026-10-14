# Cheque printing (`ncollection_cheque_print`)

Prints the **variable data** of an Odoo payment onto a **pre-printed** bank cheque.

```
account.payment ─► Print Cheque (wizard) ─► Cheque Template ─► Cheque Print Job (snapshot + audit)
                                                              └► exact-size PDF overlay ─► physical cheque
```

The payment stays the only financial source of truth. Printing a cheque never changes the payment (state, amount, memo).

## Where to find it (and why there is no Home tile)

Cheque printing is **inside the Accounting application**, not an application of its own:

| Entry point | Where | Who |
|---|---|---|
| **Accounting > Cheque Printing > Print Jobs** (`ncollection_cheque_print.menu_nc_cheque_job`) | the print history: who printed, reprinted or voided what, and why | accounting read-only and above |
| **… > Cheque Templates** (`menu_nc_cheque_template`) | the bank layouts; the **Designer** is a tab of the template form, with *Design preview / Print preview / Calibration sheet* buttons | read-only reads; accountant edits; manager deletes |
| **… > Printer Profiles** (`menu_nc_cheque_profile`) | per-printer offset and scale, filled in from the calibration sheet | same |
| **Print Cheque** button on a vendor payment (`account.payment` form header, view `view_account_payment_form_nc_cheque`) | opens the wizard: template, printer profile, **print language**, cheque number, previews, Print | accountant (`account.group_account_user`) |

Arabic names: طباعة الشيكات > مهام الطباعة / قوالب الشيكات / ملفات تعريف الطابعات. The folder menu
(`menu_nc_cheque_root`) is a child of `account.menu_finance`, so it inherits the Accounting application's rights and
licence.

**Why no Home tile.** A Home tile is a root menu, and the existing convention (see `ncollection_core/models/ir_ui_menu.py`)
is that a native feature gets one only when it is a workflow of its own (Barcode is a scanner screen). Cheque printing
is an *action on a payment* plus the layouts it needs; budget, assets and analytics nest under Accounting the same way.
The Settings > Applications board deliberately lists only non-native applications, so it does not show it either. A
second front door would be a duplicate of the Accounting entry, and would need its own licence-following rule.

### It is invisible until it is INSTALLED in the tenant database

This was the actual reason it "could not be found": a new addon is unknown to a database until its Apps list is
refreshed, and unused until installed.

```bash
docker compose exec odoo odoo -d <tenant database> -i ncollection_cheque_print --stop-after-init \
    --db_host=db --db_user=odoo --db_password=<db password>      # (or: make install m=ncollection_cheque_print db=<db>)
```

Installing refreshes the Apps list and seeds the three provisional templates and one printer profile. The running
server picks the new module up on the next request (restart it only after a later CODE change: a long-running server
keeps the Python it already imported). You only install it by hand on a database that was NOT provisioned: a tenant
provisioned from a plan **gets it automatically whenever its entitlement includes Accounting** (see "Licensing" below).

## What is and is not printed

| Printed (variable text) | Never generated here |
|---|---|
| payee, date, amount, currency, amount in words, reference, cheque number, account number, bank name, static text | bank logo, security background, MICR line, QR code, signature |

The reference image of a template is a **designer background only**. It is printed only if a template explicitly ticks
*Print the reference image* (a copy on plain paper).

## Architecture

| Piece | Where | Notes |
|---|---|---|
| Template + fields | `ncollection.cheque.template`, `…template.field` | Data. Millimetres from the top-left corner. A field must stay inside the page. One of each registry key per template (static text may repeat). |
| Field registry | `models/field_registry.py` | The controlled list of printable keys, with default boxes and designer samples. The only Python a new *field kind* needs. |
| Amount in words | `ncollection_account_core` → `ncollection.amount.words.convert()` | Shared service (EN + AR, currency forms, fractions). Not in the template, not in QWeb. |
| Renderer | `models/renderer.py` | `reportlab`, page = `width_mm × height_mm` exactly (no A4/Letter wrapper, no browser). Deterministic output. |
| Arabic | `models/text_shaping.py` | Letter shaping from `unicodedata` presentation forms + a small UBA bidi; DejaVu fonts. No new pip package. |
| Print job | `ncollection.cheque.print.job` | Snapshot of what was printed, state machine, `…print.event` append-only history. |
| Printer profile | `ncollection.cheque.printer.profile` | Horizontal / vertical offset (mm) and scale (%) of one printer. Applied to the printed overlay only. |
| Designer | `static/src/designer/*` (OWL view widget `nc_cheque_designer`) | Drag, resize, exact X/Y/W/H in mm, font/size/alignment/direction. Edits save as they happen to the field records. |
| Routes | `controllers/main.py` | `/ncollection/cheque/preview` (read-only, watermarked print preview) and `/ncollection/cheque/download/<event>` (the delivery of one audited print). |

### Adding a fourth bank (no code)

Cheque Printing → Cheque Templates → New: name, bank, physical width/height, upload the reference image, open
*Designer*, add the fields, drag them into place, *Calibration sheet*, measure, correct, **Confirm dimensions**.

### Two views in the designer

*Design view* (reference image + boxes; **not printed**) vs the real PDFs behind *Print preview* (overlay only,
watermarked as a preview) and *Calibration sheet* (10 mm crosses + a ruler, to measure a printer).

## Print job lifecycle and audit

`draft → printed → reprinted … → void` (or `draft → cancelled`).

* First print: `action_print()`. Records user, time, a SHA-256 of the PDF, the printer profile used, any layout warnings.
* **Reprint** and **void** need a reason (≥ 5 characters); both are events with user and time.
* The job is a **snapshot** (payee, amount, words EN/AR, date, reference, profile, whether the template was confirmed).
  A later payment edit does not change what was printed. Snapshot and lifecycle fields cannot be written directly.
* One open cheque per payment; a second needs a void first. Events cannot be edited or deleted; jobs cannot be deleted.
* The download link belongs to the user who printed, expires after 15 minutes and re-renders from the snapshot, refusing
  if the bytes no longer match the recorded fingerprint.

## Print language (English / Arabic)

The language a cheque is **printed in** is chosen in the Print Cheque wizard (*Print language*). It is **not** the
language of the screen: an Arabic user can print an English cheque and the other way round. The wizard only *offers*
the user's own language as a default.

* It is snapshotted on the job (`output_lang`) and cannot be edited afterwards, so a reprint reproduces the same
  cheque.
* It decides the **month name** of a date (`dd mmm yyyy`: `05 Oct 2026` / `05 أكتوبر 2026`) through Odoo's own
  localisation (babel, via `odoo.tools.misc.babel_locale_parse`; no second month table) and an **automatic** amount in
  words. A field may still pin `English` or `Arabic` for a bank that wants one language only. Numeric date formats do
  not change; the field's *Digits* option turns western digits into Arabic-Indic.
* Alignment *Automatic* puts an Arabic line against the right edge of its box and an English line against the left.
* The words on the design preview, preview watermark and calibration sheet are translated into the print language
  (`ncollection.cheque.values.labels`), and drawn with the Arabic-capable font when they are Arabic.

Shaping scope (`models/text_shaping.py`): letters are shaped from Unicode's own presentation-form data, lam + alef is the
only ligature (DejaVu has none of the optional Forms-A ligatures; using them printed an empty box, a regression now
covered by a glyph-coverage test), and **Arabic diacritics (harakat, e.g. the tanween in an amount in words) are not
drawn** because there is no layout engine to place them. The text stays correct and legible without them.

## Access, companies, currencies, licensing

* Read: accounting read-only. Print / prepare: accountant (`account.group_account_user`). Template and profile
  management: accountant create/edit, manager delete. Reprint/void: accountant.
* Multi-company record rules on template, field, profile, job, event. A template with no company is shared.
* A template may pin a **cheque currency** (paper printed "Dirhams"); a payment in another currency is refused.

### Licensing: it FOLLOWS Accounting (a companion module; no entitlement of its own)

Source of truth: `ncollection.tenant._nc_effective_module_list()` in `ncollection_subscription`, the one authority that
provisioning (`-i` at database creation), the module-install job, config sync (the tenant's licence) and the tenant
display field all read. Its inputs are the plan's `allowed_module_names`, the country package, and the per-tenant
overrides. Cheque printing adds **no input**: `ncollection_subscription/models/companions.py` holds one table,
`{'ncollection_cheque_print': 'account'}`, and the authority adds a companion exactly when its host is in the result.

| Plan (as shipped, `ncollection_subscription/data/demo_data.xml`) | Accounting | Cheque printing |
|---|---|---|
| STARTER (no module list: core only) | no | no |
| GROWTH (no module list: core only) | no | no |
| ENTERPRISE (`account`, reports, dashboards, budget, OCA reports) | **yes** | **yes**, derived |

A platform may have edited its plans (the dev platform's GROWTH is `crm,stock` and its ENTERPRISE a longer list); the
rule is the same for every plan, shipped or made later: **cheque printing is entitled exactly when `account` is.**

* **New tenants:** provisioned with the module in the same `-i` as `account`; the three provisional templates are seeded.
* **Existing tenants:** a one-off migration (`ncollection_saas` 19.0.6.16.0) queues the EXISTING install and config-sync
  jobs for the ready tenants that gain it (the 19.0.6.8.0 / #467 pattern). Installing a module that is already installed
  is a no-op (test11 and the dev platform keep their manual installs). A tenant without Accounting is not touched.
* **Never standalone:** a plan line naming it is ignored; a per-tenant override naming it is refused; the plan picker
  does not offer it. (A standalone line would license `account` through the dependency closure Ring 1 expands: an
  accidentally widened plan.) To withhold it, withhold Accounting.
* **Losing Accounting** hides it with its host (Ring 1/2). Revocation never uninstalls (customer data).

The three Ring layers still apply on top, unchanged:

| Layer | What decides | For this module |
|---|---|---|
| Installed at all (Ring 3) | the effective module list above | follows Accounting |
| Menus (Ring 1) | the licence of the root menu the module sits under | the menus live under the **Accounting** root, so a tenant that does not license `account` loses them |
| ORM / RPC (Ring 2) | the licence of the model's module | the only business record it touches is `account.payment`; an unlicensed tenant is refused at the payment, so no cheque can be prepared |

Pinned by `ncollection_subscription/tests/test_companion_modules.py` (the rule, both directions, no standalone path),
`ncollection_saas/tests/test_companion_provisioning.py` (provisioning, install job, config sync, plan-save trigger, the
migration), `tests/test_licensing.py` here (Ring 1/2 and the module record), and by `verify_provisioning.sh`, which
provisions real databases: `provclient` (plan `crm, account`) must have the module, `provnoacct` (plan `crm`) must have
neither it nor `account`.

## The three shipped templates

Mashreq NeoBiz, ADCB, FAB, shipped as records (`data/cheque_template_data.xml`, generated from one layout table by
`scripts/dev/gen_cheque_templates.py`). Layout fractions were read visually from three photographs; **no real data from
those photographs is stored** (the images in `static/src/img/` are schematic). Sizes are **provisional** and every
template ships `dimensions_confirmed = False` with a visible banner:

| Template | Provisional size (mm) | Needs |
|---|---|---|
| Mashreq NeoBiz | 203 × 94 | measure a physical cheque |
| ADCB | 203 × 99 | measure a physical cheque |
| FAB | 203 × 106 | measure a physical cheque |

## Needs physical validation (cannot be proven by software)

1. Real paper size of each cheque and the exact position of each printable area.
2. The printer's feed offset and scale (use the calibration sheet; enter the numbers in a printer profile).
3. Whether each bank accepts the printed font/size; Arabic glyph appearance on the actual printer.
4. Whether a bank requires cheque-number or account-number to be printed (both are available fields, off by default).

## Tests

`custom_addons/ncollection_cheque_print/tests` (renderer, shaping, templates, flow, routes) and
`ncollection_account_core/tests/test_amount_words.py`.
