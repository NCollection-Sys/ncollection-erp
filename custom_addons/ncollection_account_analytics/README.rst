==============================
NCollection Account Analytics
==============================

Financial analytics for the NCollection platform (F4-T01, issue #120):
cost/profit centres, the three financial KPIs, budget variance and trend
extrapolation.

``FINANCIAL_PLATFORM_ARCHITECTURE.md`` §7 assigns *Cost Centers, Profit Centers,
Financial KPIs, Forecasting, Variance Analysis* to this module, and lists
**Reports, Journal Entries and Accounting Configuration** under *Must Never
Own*. ADR #15 names it, alongside ``ncollection_account_reports``, as the native
owner of financial computation once the OCA reporting bootstrap is retired
(#117).

What it provides
================

Dimensions — on Odoo's own analytic plans
-----------------------------------------

Three **root** ``account.analytic.plan`` records are seeded: Departments, Cost
Centers, Profit Centers. This module defines no dimension model of its own,
because Odoo 19 already is one: ``analytic_mixin._validate_distribution()``
enforces the 100% rule *independently per root plan*, so a single journal item
can carry a split across all three at once, each validated separately.

``ncollection.account.analytics.dimension`` exposes ``_nc_breakdown()`` and
``_nc_total()`` per dimension. Both read ``account.analytic.line.amount``, which
Odoo writes **already apportioned** by the distribution percentage — summing
``account.move.line.balance`` grouped by a distribution key would double-count
every split item, and only on tenants that actually use splits.

.. note::

   Odoo stores an analytic line's account in ``account_id`` **only** for the
   "project plan"; every other plan gets a runtime-created ``x_plan<id>_id``
   column (``account.analytic.plan._strict_column_name``). Always ask the plan
   for ``_column_name()`` — grouping by ``account_id`` silently returns nothing
   for any plan but the first.

Financial KPIs
--------------

``ncollection.account.analytics.kpi`` — Revenue Growth %, Gross Margin %, and
Days Sales Outstanding. These are the three ``ncollection_core`` deliberately
declined (see its ``models/kpi/kpi.py`` docstring and the 2026-07-19 split); the
payload shape matches ``ncollection.kpi`` exactly so a dashboard renders either
with one code path.

Each formula is written out in its method docstring with the reason for the
variant chosen — DSO uses the simple single-period form rather than countback,
because countback needs a look-back depth and a dashboard figure that depends on
a hidden constant cannot be reproduced by the person reading it.

``value is None`` means *cannot be computed on this tenant*, never zero.

Variance and trend
------------------

``ncollection.account.analytics.forecast`` provides budget variance and an
ordinary-least-squares trend over closed periods.

Variance is a **soft dependency** on ``ncollection_account_budget`` (F4-T02,
#121). While that module is absent the result is
``{'available': False, 'reason': ...}`` — never ``0.00``, which would read as
"exactly on budget".

The trend needs at least three closed periods and returns the method string
attached to the number. It models no seasonality and offers no confidence
interval; both are modelling choices that need a finance owner rather than a
default.

Dependencies
============

``account`` and ``ncollection_account_core`` only. **No OCA dependency**: the
OCA survey for this ticket found ``OCA/account-analytic``'s 19.0 branch to be
document-integration glue with no cost- or profit-centre concept, and nothing in
OCA providing financial KPI computation, variance or forecasting. The closest
matches are the ``mis_builder`` family, which #117 plans to retire and which this
module is meant to replace.

``analytic`` arrives as a direct dependency of ``account``, so the dimension
mechanism costs no new pin.

License
=======

LGPL-3.
