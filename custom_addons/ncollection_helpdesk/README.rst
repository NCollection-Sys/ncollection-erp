====================
NCollection Helpdesk
====================

SLA timers for support tickets (P6-T03 / #67).

What this is — and is not
=========================

This module is **small on purpose**. The ticket model, teams, stages, the portal
submit/track pages and the CSAT rating all come from OCA (`helpdesk_mgmt`,
`helpdesk_mgmt_rating`, pinned in ``repos.yml``). This module adds the one thing
OCA does not ship on 19.0: **SLA response and resolution timers**.

``helpdesk_mgmt_sla`` is not on the 19.0 branch — its migration PR #1012 is open
and unmerged, and two earlier attempts closed without landing. Grafting an
unmerged branch is the mistake ``auth_brute_force`` taught this repo, so the
timers are native. If #1012 ever merges, this module should be re-evaluated for
retirement; the decision record is in ``docs/markdown/OCA_DEPENDENCIES.md``.

How the SLA works
=================

A **policy** answers one question: given a ticket's team and priority, how long
until a response is due, and how long until resolution is due. Team-specific
policies beat the team-less fallback; a unique constraint on
``(company, team, priority)`` means the match is never a coin flip between equals.

Each ticket then carries:

``nc_sla_response_deadline`` / ``nc_sla_resolution_deadline``
    Derived from ``create_date`` + the policy. Facts about the record, so they
    are stored and computed normally.

``nc_sla_first_response``
    Stamped once, when an agent takes the ticket or moves its stage. An event,
    not a computation — recomputing it would let a later edit rewrite history.

``nc_sla_state``
    ``on_track`` / ``due_soon`` / ``breached`` / ``met`` / ``none``.

The one subtlety worth knowing
==============================

A deadline is a fact about the record. A **breach is a fact about the clock**,
and no ORM recompute fires because time passed. So ``nc_sla_state`` is stored
and refreshed by an hourly cron (``_cron_nc_sla_scan``) — the same reasoning as
the P2-T14 lifecycle sweep. Storing it is what lets the list view filter and
group by it; the cron is what stops it being a lie between writes.

Stated rather than hidden: **between two cron runs the stored state can be stale
by up to an hour.** ``_nc_sla_state_now()`` computes the honest answer on demand
for anything that must not wait — the tests use it to prove breach transitions
without sleeping.

Hourly, not daily, because the tightest shipped promise is a 1-hour Urgent
response; a daily scan would report that breach up to a day late.

Deliberate limits
=================

* **Elapsed hours, not business hours.** A real "8 working hours" SLA would use
  ``resource.calendar``; that pulls the whole resource/leave surface in and makes
  every test depend on a calendar fixture. Elapsed hours is the honest v1 — it is
  what the field name says. Business-hours SLAs are a follow-up, not a silent gap.
* **No custom portal rule.** ``helpdesk_mgmt`` already scopes portal users with
  ``partner_id child_of user.commercial_partner_id``, which is exactly the
  convention P6-T02 measured as this repo's majority pattern. Adding a second
  rule would only create two places to get it wrong. Instead
  ``tests/test_ticket_portal_isolation.py`` **pins** that domain, so an upstream
  bump that weakens it fails a test rather than quietly widening access.

Testing
=======

``tests/test_sla.py`` — policy matching, constraints, deadlines, every state
transition, and the cron (clock injected, never slept on).

``tests/test_ticket_portal_isolation.py`` — the handoff P6-T02 recorded for this
issue: tickets did not exist when portal isolation was proven, so proving them is
this ticket's job. Follows #66's house style — every isolation assertion paired
with a control that would fail on an empty fixture, and the IDOR shape (direct
read of a known id) asserted separately from search filtering.
