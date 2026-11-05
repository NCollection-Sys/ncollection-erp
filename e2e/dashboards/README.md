# Dashboard browser acceptance (headless Playwright)

Drives a **disposable** Odoo (database `uxbrowse`, port 8169, built by `fixtures/run_browse.sh`) with headless Edge/Chromium, so it
is not throttled the way a hidden browser pane is. It never touches the shared stack.

* `acceptance.js` - 41 checks: Sales / Invoicing analysis against deterministic AED fixtures (EN / AR, desktop / 375 px),
  drill-down, tooltips, custom dates, empty and EUR companies, two companies ticked, PDF / Excel downloads **opened and inspected**
  (`inspect_file.py`), Share + Export on the native Spreadsheet Dashboards, sample-data banner, and the English invoice filters /
  sorting, product image, chatter, Document Layout, Arabic Duplicate.
* `survey.js` - screenshots + overflow metrics of every NCollection dashboard (`node survey.js ar_001 375x812 tag_`).
* Host assumptions: Playwright in `D:/Projects/ncollection-erp/e2e/node_modules`, Edge (`PW_CHANNEL` to change), Docker for the
  file inspector. `lib.js` holds the Playwright path; `fixtures/*.sh` derive the repo from their own location and the db container via `docker compose ps -q db` (set `OCA_DIR` / `COMPOSE_DIR` in a worktree).
