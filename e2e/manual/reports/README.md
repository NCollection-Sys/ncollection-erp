# Reports hub — manual browser checks

Real-browser journeys for the Reports hub, the report viewer and the language
switch. They are **not in CI** (they need the platform database with accounting
data and a Chrome install) — run them by hand after touching
`ncollection_account_reports`, the launcher or the language-switch code.

    cd e2e && node manual/reports/dd1.js        # drill-down: account, entry, item, payment, partner
    node manual/reports/all1.js                 # every report in the hub opens without an error
    node manual/reports/lang_rep.js             # EN<->AR from each report keeps report + filters
    node manual/reports/reach.js [480x800,1366x768]  # every page: nothing clipped, header not overlapping, no hidden overflow (EN + AR)
    node manual/reports/search.js               # partner search, clear, pick, date, language, reload, back/forward
    node manual/reports/pilot_trial_balance.js  # Trial Balance pilot (adapter installed): viewer, totals vs SQL, drill-down, EN<->AR, exports
    NC_DB=<db> node manual/reports/tax_analysis.js  # Tax Analysis (data-rich tenant): every view, totals vs SQL, drill-down, EN<->AR, Excel
    node manual/reports/pdf_quality.js <db> en <prefix>   # 12 reports -> real PDFs inspected (pages, size, text, RTL); also: ar
    node manual/reports/xlsx_quality.js <db> en           # real workbooks unzipped: numeric cells, frozen header, RTL
    node manual/reports/qa1.js                  # no overflow / clipping at 1366, 1920, 1024, 768, 480 (EN + AR)

Defaults: `http://ncollection.localhost:8069`, login `admin`/`admin` (the dev
platform database; see CLAUDE.md). The stack must be the routing + SaaS overlay
(`db_filter=^%d$`). Chrome is expected at the Windows default path — edit
`executablePath` in `lib.js` on other hosts.

**PDF on a developer machine.** Two things make a PDF look broken without anything being wrong in Odoo:

* the odoo *container* cannot resolve `<tenant>.localhost`, so wkhtmltopdf cannot load the stylesheet/letterhead and
  Odoo returns an **unstyled** PDF with exit 0 (`make pdf-hosts` fixes it; `make doctor` warns; re-run after a container restart);
* a download manager such as **Internet Download Manager** answers Odoo's PDF download with an empty `204 Intercepted by
  the IDM Advanced Integration`, which Odoo's client shows as "Arbitrary Uncaught Python Exception" although the server
  returned a valid PDF. Exclude `localhost` in the download manager. The viewer now explains it and opens the PDF in a tab.

The scripts above that need many rows run against a scratch tenant, not the shared fixtures (see CLAUDE.md fixture table).
