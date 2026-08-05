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
* ``panels`` — optional (#56/#57), cross-domain rows:
  ``[{key, label, type, rows, drilldown:{model, field}}]``.

This shape is the stable API downstream dashboards consume; keep it additive.

Department dashboards (#57 / P4-T04)
====================================

Sales, HR and Warehouse — **non-financial**, added into this module (extends the
shared base; no new module). Each dashboard's headline KPI (incl. target) comes
from the P4-T02 ``ncollection.kpi`` service and its panels from the P4-T01
``ncollection.aggregation.engine`` — the same "services only, zero computation"
rule as the financial dashboards, enforced by ``tests/test_boundary.py`` (which
also forbids any direct ``sale``/``crm``/``hr``/``stock`` access). Each is
gated to its role (``group_role_sales`` / ``_hr`` / ``_warehouse``) under a
separate **Department Dashboards** menu, mirrored at the data layer (KPI/engine
reads run under the user's own rights, no ``sudo`` — a cross-role call returns
empty, never another role's data). Apps that a plan does not install degrade to
an omitted KPI/panel (never a misleading zero).
