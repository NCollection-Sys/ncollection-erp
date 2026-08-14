======================
NCollection Audit Trail
======================

Audit-trail **infrastructure** for the NCollection platform (P8-T05, issue #81):
who changed what, when, from where — and evidence that the record itself has not
been altered.

This is plumbing, not product. The issue is explicit that financial-audit UX
(timeline, approval history on ``account.move``, compliance logs) belongs to
``ncollection_account_audit`` (#124), and that the "changing an invoice amount"
acceptance example is an **infrastructure test**.

Built on OCA ``auditlog``
=========================

No ``repos.yml`` change. ``auditlog`` was pinned *for this ticket* at P1-T04 —
the pin comment reads "Consumed: auditlog — audit trail, wired at P8-T05" — and
``DELIVERABLE_1_SYSTEM_DESIGN.md`` names it for P8-T05. The build-vs-buy question
was settled before this branch existed.

``auditlog`` supplies old/new values, the acting user, a timestamp, and
registry-level ORM hooking. This module is the gap list.

What it adds
============

**The client IP.** The acceptance criterion names it; ``auditlog`` has no IP
field anywhere (grep for ``remote_addr``/``ip_address``: zero hits). Captured
from ``request.httprequest.remote_addr``, guarded the way upstream guards
``request`` — crons, queue jobs and the shell write with no request at all, and
for those the IP is **empty**, which is the honest answer rather than a
fabricated ``127.0.0.1``.

**Rules as data.** ``auditlog.rule`` is UI-only upstream: nothing seeds it. A
fleet cannot be configured by clicking. Seeding here is idempotent, re-run on
every registry load, and public so provisioning can call it — because a
``post_init_hook`` alone seeds what exists at install time and then never looks
again, which is REGRESSIONS.md **R-014**, twice.

**Retention.** OCA's autovacuum cron ships ``active="False"`` with 180 days
hardcoded in its code field. The period is now
``ncollection.audit.retention_days``, because how long financial audit records
must be kept is set by law and contract, not by an upstream default. Automatic
deletion stays **off** until someone arms it deliberately.

**Tamper evidence.** ``auditlog`` has none, and its own manager group holds
``perm_unlink=1`` on ``auditlog.log``. Each row carries a digest of its own
content; ``ncollection.audit.seal`` chains ranges of those digests hourly.

====================  ====================================
row edited            its content digest stops matching
row deleted           its covering seal stops recomputing
seal edited           the seal chain breaks
====================  ====================================

Managers lose ``perm_unlink`` on ``auditlog.log``; the retention cron runs as
root and prunes regardless.

Two measured facts that shaped this
===================================

**A rule on ``account.move`` alone cannot satisfy the acceptance criterion.**
Probed on a scratch database before any of this was written::

    PROBE amount_total before: 100.0
    PROBE amount_total after : 250.0
    PROBE move logs before/after: 4 4

``amount_total`` is a stored computed field; editing a line moves it through
recompute-flush, which never reaches the ``write()`` ``auditlog`` patches. So
``account.move.line`` is seeded too — it is **not** in the ticket's model list,
and the ticket's list cannot meet the ticket's acceptance.

**``log_type='fast'`` fabricates old values.** ``write_fast`` sets
``old_vals2 = dict.fromkeys(vals2.keys(), False)``, so every "old value" is a
hardcoded ``False``. Every seeded rule uses ``full``, and a test pins it.

What this does NOT cover
========================

* **A window before each seal.** Rows written since the last seal carry only
  their own digest; an attacker who inserts *and* deletes inside that hour
  leaves nothing. Closing it needs a per-write chain, which would add a locked
  read to a path already paying two full reads per audited write. Asserted by a
  test rather than hidden.
* **Retention is trusted.** Pruning destroys the evidence it protected. Anyone
  who can run that cron can erase history without the verifier objecting.
* **The invoice-amount acceptance criterion is NOT met.** It needs
  ``account.move.line`` (``amount_total`` is a stored computed field, so a rule
  on ``account.move`` alone logs nothing), and auditing that model overflows
  Python's recursion limit — 79 nested ``write_full`` frames. Both financial
  models are withheld. **#81 was rescoped to infrastructure-only rather than
  reworded.**
* **``res.users`` is NOT audited**, and the ticket lists it. Auditing it
  disables the plan seat limit: ``ncollection_core`` enforces ``max_users`` by
  overriding ``res.users.create``, ``auditlog`` enforces auditing by
  monkeypatching ``create`` onto the model class, and the licence check loses.
  Measured by bisection — with it seeded, ``ncollection_core`` fails
  ``test_limit_blocks_raw_orm_create``. So **user creation, deactivation and
  role changes are not in the trail.** Tracked as its own issue; an audit trail
  must not punch a hole in billing enforcement to get itself installed.
* **Group-restricted fields are never logged.** ``auditlog`` reads every stored
  field and ignores ``groups=``, while ``auditlog.log.line`` is readable by
  ``auditlog.group_auditlog_user`` — so blanket auditing would have published
  ``ncollection.tenant.checkout_token``, a live signup bearer credential, to
  every audit viewer. Excluded generically rather than by name, because a
  per-field list protects today's secret and exposes the next one.
* **Deleting an unsealed row leaves no finding at all.** The seal cron runs
  hourly and simply seals what exists, so a gap where a row used to be is never
  noticed. The docstring's narrower claim (insert-and-delete) understated this.
* **Reads are not logged.** Highest volume, lowest signal; a read trail is a
  separate decision with a separate cost.
* **No financial audit UX** — that is #124.

Testing
=======

``make test m=ncollection_audit`` — 24 tests. The invoice acceptance tests need
``account``; on a database without it they skip, and ``scripts/ci/check_skips.py``
fails CI on an unexpected skip rather than counting it as a pass.
