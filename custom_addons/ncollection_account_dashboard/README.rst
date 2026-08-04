==============================
NCollection Account Dashboard
==============================

Financial dashboards (F3-T01) — **presentation only**.

Provides the Finance, Accountant and Cash dashboards as OWL client actions,
rendered with the UI-T02 component library (``ncollection_branding``) and fed
by a thin orchestration service that consumes the F2-T08 executive report
services (``ncollection_account_reports``).

Boundary rule (FPA §7 — enforced by ``tests/test_boundary.py``)
==============================================================

This module performs **zero financial computation**. It never reads
``account.move`` / ``account.move.line``, runs no SQL, and does no balance /
P&L / Balance-Sheet arithmetic. Every figure comes from
``ncollection.account.report.*._nc_service_figures()``. The report and the
dashboard therefore can never disagree.

Extension point
===============

``ncollection.account.dashboard.service`` and the ``NcFinancialDashboard`` OWL
base component are the shared infrastructure that later dashboard issues
(#56 CEO, #57 department dashboards) build on — they add their own client
action + payload builder using the same base, without re-implementing the
fetch/chart/trend plumbing.

Public payload contract
=======================

The service returns ``{kpis, charts, meta}``:

* ``kpis``  — ``[{key, label, value, previous, unit}]`` (trend is derived in the
  view from ``value`` vs ``previous``; the service does no arithmetic).
* ``charts`` — ``[{key, label, type, labels, series:[{name, data}]}]``.
* ``meta``  — ``{currency, period:{from, to}, as_of}``.

This shape is the stable API downstream dashboards consume; keep it additive.
