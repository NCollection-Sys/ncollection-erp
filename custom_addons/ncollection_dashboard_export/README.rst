============================
NCollection Dashboard Export
============================

An **Export** button (Excel and PDF) immediately beside **Share** on the Dashboards page (``/atlas/dashboards``, Odoo's
own Spreadsheet Dashboards).

* The browser sends a snapshot of what the page shows (evaluated cells, scorecards, chart values, filters).
* The server validates and bounds the snapshot, reads the dashboard as the signed-in user (Odoo's access rules decide
  whether it may be exported) and builds both files from that one snapshot: numbers stay numbers, text never becomes a
  formula, Arabic is right-to-left.
* No model, no access rights and no data of its own; one authenticated POST route
  (``/ncollection/dashboards/export/<xlsx|pdf>``).
* ``auto_install`` with ``spreadsheet_dashboard``. On an existing database it must be installed explicitly (``-i``).
