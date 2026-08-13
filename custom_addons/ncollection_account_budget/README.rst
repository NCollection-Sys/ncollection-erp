==========================
NCollection Account Budget
==========================

Budget planning, revision, approval and Budget-vs-Actual reporting (F4-T02,
issue #121).

``FINANCIAL_PLATFORM_ARCHITECTURE.md`` §7 assigns *Budget Planning · Revision ·
Approval · Monitoring · Budget vs Actual* to this module. Unlike
``ncollection_account_analytics``, its entry carries no *Must Never Own:
Reports*, so the Budget-vs-Actual report ships here beside its data.

Why native
==========

Odoo 19 Community ships **no budgeting at all** — ``account_budget`` moved to
Enterprise. OCA's ``account_budget_oca`` models a budget line with a single
``analytic_account_id``, which cannot express the *Department AND Cost Center*
filtering FPA specifies, nor compose with the ``analytic_distribution`` model
``ncollection_account_analytics`` established in #120.
``mis_builder_budget`` is compatible but AGPL-3, installed nowhere, and inside
the #117 sunset — a design reference for pro-rata accumulation, not a
dependency.

What it provides
================

Lifecycle
---------

``draft → approved → revised``, tracked through ``mail.thread`` — Odoo already
records who changed what and when, so there is no bespoke revision log.

Approval is a state, not a label: an **approved budget refuses line edits**. To
change one, revise it — which *copies* the budget into a new draft and marks the
original ``revised``, keeping it. A budget that was approved and acted on is a
record of what was decided.

Only **approved** budgets are reported. A draft is a proposal; a revised one is
history. Counting either would compare actuals against a plan nobody agreed to.

Budget lines
------------

A GL account + an ``analytic_distribution`` + an amount — keyed exactly as the
actuals are, so the variance compares like with like without a translation layer
between them.

The period-mismatch rule
------------------------

A budget covers a span; a report asks about a different one. FPA is silent on
partial overlap, so this **pro-rates by day overlap**, inclusive at both ends: a
365-day budget of 36,500 contributes 3,100 to a 31-day month. Declared once, in
``_nc_overlap_ratio``.

Budget vs Actual
----------------

Columns **Budget · Actual · Variance · Variance %**, per FPA. The report is
built on the F2 engine, so ``current`` is Actual and ``previous`` is Budget —
which makes an overspend on a cost account read as a *positive* variance, what a
budget holder means by "over". It ships its own list view so the on-screen
labels match the PDF and the workbook.

Interfaces
==========

``_nc_variance_payload(date_from, date_to)`` satisfies the contract
``ncollection_account_analytics`` has called since #120. When no approved budget
covers the window it answers ``{'available': False, 'reason': ...}`` — never a
zero variance, because "no budget" and "exactly on budget" are opposite
statements about a business.

Security
========

The report **wizard** is scoped to its creator (``create_uid``), like every
sibling report run. The **budgets** deliberately are not: a budget is shared
tenant data that a finance team reads and a manager edits, and scoping it to its
creator would hide the company's own plan from everyone but whoever typed it.

License
=======

LGPL-3.
