# Trial Balance pilot — NCollection engine vs OCA `account_financial_report`

Decision: **A — KEEP THE CURRENT NCOLLECTION IMPLEMENTATION as the default and canonical Trial
Balance.** The OCA-backed adapter works, is correct, and is kept as an opt-in, off-by-default option;
it does not clearly outperform the current implementation, so no second pilot (General Ledger) is
started. Rationale and evidence below.

## 1. Architecture of the pilot

```
menu ─► report_viewer (unchanged) ─► ncollection.report.service.nc_report_run('…trial.balance')
                                         │  _nc_resolve_source(): param ncollection_account_reports.source.trial_balance
                                         ▼                         'native' (default) | 'oca'  — anything else ⇒ native
              ncollection.account.report.trial.balance     ncollection.account.report.tb.oca   (module
              (native, reads account.move.line)            ncollection_account_reports_oca)
                                                              │ _nc_oca_values(): create OCA wizard → run
                                                              │ report.account_financial_report.trial_balance
                                                              │ → map to the NCollection row contract → delete wizard row
```

* **The adapter is an engine, not a screen.** It inherits the NCollection report base and implements the
  same two methods every report implements (`_nc_columns`, `_nc_compute_lines`). The viewer, state in the
  URL, RTL/LTR, drill-down, `_nc_export_payload` (PDF + XLSX), the per-run privacy rule and the
  single-fiscal-year guard are therefore the *same code* for both sources. No OCA wizard form, template or
  menu is shown.
* **Normalised:** columns (Account / Opening / Debit / Credit / Closing — same keys and labels), rows
  (`account_id` = the real account, so the viewer links it; OCA group rows get no link; hierarchy in
  `level`), a Total row, filters (company, dates, target moves, journals, accounts, partners; plus
  `hide_zero` and `show_hierarchy` as ordinary viewer filters), currency/company, print payload.
  **Not mapped (stated):** OCA's foreign-currency columns and analytic "grouped by" mode — the viewer has
  no column contract for them yet.
* **Licensing/boundary:** `ncollection_account_reports` still has no OCA dependency. The adapter lives in
  its own module (`ncollection_account_reports_oca`, LGPL-3, depends on the AGPL OCA module through its
  public report model) and is plan-licensable (`application: True`).
* **Rollback:** set the parameter back to `native` (instant), or uninstall the adapter module; nothing
  else references it. The default never changes without an explicit parameter.

## 2. Direct comparison (same dataset, same filters)

Dataset: scratch database, generic chart (42 used accounts), 100 → 1,100 → 4,100 journal entries
(200 / 2,200 / 8,200 lines), 25 % in the prior fiscal year, ~8 % left in draft, 150 partners, 2 journals.
Filters: default FY, sub-period, all entries (incl. draft), one journal, one partner, an account subset,
the prior fiscal year.

| | CURRENT (native) | OCA via adapter | Evidence |
|---|---|---|---|
| **Correctness** | reference | **identical** — 0 mismatched accounts in 7 filter sets × 3 sizes, totals equal | `bench_tb` output; unit tests `test_the_two_engines_agree_*` |
| Correctness finding | with an **account filter** it also showed the *Current Year Earnings* roll-up row the user had excluded, so totals differed from OCA | excludes it | pilot found a native deviation → **fixed in native** (`_nc_opening_balances`) |
| **Backend run** (s, median of 3) | 0.008 / 0.036 / 0.014 | 0.047 / 0.054 / 0.060 | small / medium / large; OCA ≈ 1.5–6× slower, always < 0.07 s |
| Print payload (s) | 0.015 / 0.023 / 0.027 | 0.056 / 0.064 / 0.080 | |
| **XLSX build** (s) | 0.018 / 0.029 / 0.050 | 0.062 / 0.069 / 0.081 | same builder, different rows |
| HTML render (s) | 0.125† / 0.033 / 0.041 | 0.103† / 0.078 / 0.085 | †first run, cold |
| **PDF (wkhtmltopdf)** (s, large) | 2.25 | 2.29 | dominated by wkhtmltopdf |
| **Browser load → table** (ms, large, 5 runs) | median 396–471 | median 501–512 | ≈ +80 ms |
| Filters | company, dates, moves, journals, accounts, partners | the same + **hide-zero**, **account-group hierarchy** | viewer shows them as ordinary filters |
| Grouping / hierarchy | flat | **account-group hierarchy** (verified: group rows above their accounts, totals not double-counted) | `test_the_hierarchy_has_group_rows…` |
| Multi-company | per company, record rules apply | per company, **caller's rights**; found: asking for a company the user cannot use made OCA create that company's earnings account as the system → **refused before OCA runs** (both engines now) | `test_it_never_returns_another_companys_figures` |
| Multi-currency | company currency | company currency in the pilot; OCA's foreign-currency columns exist but are **not mapped** | |
| PDF / XLSX | NCollection payload | **same payload and template** | identical shape test |
| Drill-down | account → form | account → form (groups unlinked) | browser A5 |
| RTL / language | yes | yes (adapter module has its own Arabic file) | browser A7–A10 |
| Security | own-run rule, ACL accounting-readonly | **same rule and ACL on the adapter model**, OCA runs as the caller (no `sudo`), OCA's wizard row deleted after use | tests + browser section C |
| Maintainability | 76 lines + shared helpers, no dependency | + 1 module (~110 lines + 27 tests); tracks an upstream AGPL module pinned in `repos.yml`; OCA's own wizard ACL is `base.group_user` (unchanged, no data in it) | |
| Dependencies | none | `account_financial_report`, `date_range`, `report_xlsx` (already installed where OCA reports are) | |

## 3. Verification run for the pilot

* Python: 27 adapter tests (correctness under every filter, totals, hierarchy, single-FY policy, wizard
  not left behind, source switch + fallbacks, export uses the wizard's own model, XLSX numbers/frozen
  header/RTL, print payload shape, HTML + real PDF in EN/AR + pagination, ACL, own-run privacy, foreign
  company refused with no side effect, no `sudo`, performance sanity).
* Browser (`e2e/manual/reports/pilot_trial_balance.js` + the tenant section run by hand with temporary
  users): OCA-backed report inside the NCollection viewer; totals equal independent SQL; drill-down to the
  exact account; filters in the URL; EN→AR→EN with no F5 keeping report + dates; hard reload restores
  filters; XLSX download; PDF fetched from Odoo's own report route; platform database without the adapter
  stays native; two tenants × (owner, accounting user, sales user): own-database figures only, no
  platform/other-tenant data, cross-database authentication refused, restricted user refused.
* Honest limits: the tenants used have 0 and 2 posted lines, so their isolation checks prove the
  boundary, not volume (the data-rich scratch database proves volume). The toolbar's **Print** click goes
  through Odoo's client download helper, which fails the same way for a *native* Odoo report in this
  headless harness (control run), so the PDF was verified through the server route plus real-PDF HttpCase
  tests, not the click.

## 4. Decision gate

OCA is **correct** (it matches the native engine everywhere) but **not clearly better**: it is slower
(small in absolute terms), adds a dependency and a module, and its two genuine advantages — account-group
hierarchy and a hide-zero option — are small features the native engine can add directly. Its larger
advantages (foreign currency, analytic grouping) are exactly the part this pilot did **not** map.

* **A — KEEP CURRENT** is the recommendation. Native stays canonical and default.
* The adapter stays in the repository, off by default, as a documented option for a tenant that wants the
  OCA hierarchy today. It can be deleted without touching anything else.
* **No second pilot.** General Ledger / Aged are not migrated. Revisit only if a concrete requirement
  (foreign-currency trial balance, analytic grouping) appears that is cheaper to adopt than to build — the
  adapter pattern would make that a bounded task.
* The pilot's real yield: one native correctness bug found and fixed, a company-isolation hardening that
  applies to **every** report, and a proven, reversible adapter pattern.
