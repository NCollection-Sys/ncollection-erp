============================
NCollection Approval Workflows
============================

**Task:** P3-T07

Configurable approval workflows layered onto Odoo's own ``sale`` / ``purchase`` /
``crm`` models via ``_inherit`` — **Odoo core is never modified** (Standing Rule
1). The gate is a ``mail.activity`` + a state field, per the architecture
(DELIVERABLE_1 P3-T07). ``oca-scout`` confirmed **BUILD** over OCA
``tier-validation`` (a days-old 19.0 port, not vendored; the architecture
prescribes this custom shape).

Sale-order threshold approval (the acceptance)
==============================================

A sale order whose total exceeds ``res.company.nc_sale_approval_threshold``
(0 = disabled) cannot be confirmed until a **Sales Manager** approves it:

- ``action_request_approval`` schedules a to-do activity for a Sales Manager and
  moves the order to ``to_approve``.
- ``action_approve`` / ``action_reject`` complete the activity and flip the state.
  Both are gated on ``has_group('sales_team.group_sale_manager')`` **at the ORM
  layer** — not only via the button's ``groups=`` (Standing Rule 4/7; the exact
  bug class fixed in #228: a UI restriction must be mirrored server-side).
- ``action_confirm`` is overridden to block until ``approval_state == 'approved'``.

Purchase two-level approval
===========================

A purchase order above ``nc_purchase_approval_threshold`` needs **department**
then **finance** approval (``pending_department`` → ``pending_finance`` →
``approved``) before ``button_confirm`` will confirm it. The two approver roles
are the module's own groups (``group_approval_purchase_department`` /
``…_finance``); each approve action is ORM-gated on its group.

.. note::
   Odoo core also ships a native purchase double-validation
   (*Settings → Purchase → Purchase Order Approval*, ``po_double_validation`` /
   ``po_double_validation_amount``). This module's gate runs **before**
   ``super().button_confirm()``, so enabling both stacks two independent
   approvals (the native one lands the PO in Odoo's own ``to approve`` state
   afterwards). Pick one — either this module's two-level flow **or** the native
   one — not both.

Guard against stale approvals
=============================

An approval only covers the amount it was granted for. If order lines are edited
after approval so the total grows past the approved amount, the approval is
**voided** (state reset, an audit note posted) and must be requested again —
approving a small order then inflating it cannot slip past the gate. Every
approve/reject/confirm gate is enforced at the ORM layer, not only via the
button ``groups=`` (Rule 4/7; the #228 state-write lesson): ``approval_state``
itself is guarded on ``write``/``create`` so it cannot be forged over RPC.

CRM territory-based lead assignment
===================================

``ncollection.lead.territory`` maps a set of states/countries to a salesperson
(and team). A lead created **without** an explicit salesperson is auto-assigned
to the first matching territory (state before country; rules ordered by
sequence) and the assignee gets a notification activity. An explicit salesperson
is never overridden. A rule that constrains **states** is matched via the state
tier only — it never becomes a country-wide catch-all for other states. The
assignment write runs under ``sudo`` (system routing), so a low-privileged
creator cannot block it. Odoo 19 core also ships native domain-based assignment
on ``crm.team.member``; this is the lighter, ticket-prescribed custom mechanism.

.. note::
   Territory rules are **not** company-scoped. In a tenant database configured
   with multiple ``res.company`` records, a rule applies across all companies in
   that DB. This assumes the common single-company-per-tenant setup; per-company
   territories are a tracked follow-up.

Configuration
=============

- Thresholds: *Settings → Companies →* the company form (Approval Thresholds).
- Territories: *CRM → Configuration → Lead Territories*.
- Approvers: assign users to the *Sales Manager* group (SO) and the *Purchase
  Approver: Department / Finance* groups (PO).

Dependencies
============

``sale`` · ``purchase`` · ``crm`` · ``mail`` — all Odoo core. No new OCA
dependency (BUILD, per ``oca-scout`` + the architecture).
