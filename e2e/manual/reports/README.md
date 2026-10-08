# Reports hub — manual browser checks

Real-browser journeys for the Reports hub, the report viewer and the language
switch. They are **not in CI** (they need the platform database with accounting
data and a Chrome install) — run them by hand after touching
`ncollection_account_reports`, the launcher or the language-switch code.

    cd e2e && node manual/reports/dd1.js        # drill-down: account, entry, item, payment, partner
    node manual/reports/all1.js                 # every report in the hub opens without an error
    node manual/reports/lang_rep.js             # EN<->AR from each report keeps report + filters
    node manual/reports/qa1.js                  # no overflow / clipping at 1366, 1920, 1024, 768, 480 (EN + AR)

Defaults: `http://ncollection.localhost:8069`, login `admin`/`admin` (the dev
platform database; see CLAUDE.md). The stack must be the routing + SaaS overlay
(`db_filter=^%d$`). Chrome is expected at the Windows default path — edit
`executablePath` in `lib.js` on other hosts.
