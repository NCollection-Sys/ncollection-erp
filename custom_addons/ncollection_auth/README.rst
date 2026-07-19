NCollection Auth Hardening
==========================

Authentication hardening for every NCollection database (P1-T19):
installed in the admin DB **and** all tenant DBs (see the §2.5 matrix in
``DELIVERABLE_1_SYSTEM_DESIGN.md``).

What it does
------------

* **Auth audit log** (``ncollection.auth.log``): login success/failure,
  logout, password-reset request/completion — with IP, user agent and
  database. Logging only; it never participates in auth decisions.
  Readable by Settings (``base.group_system``) users only; writable by no
  one at the ORM (hooks write via sudo) — Rule 4 mirroring.
* **Brute-force lockout defaults**: arms Odoo **core's** login cooldown —
  ``base.login_cooldown_after = 5`` failures, ``base.login_cooldown_duration
  = 300`` seconds. Feature flag: set ``_after`` to ``0`` to disable.
* **Session timeout**: depends on OCA ``auth_session_timeout`` (pinned in
  ``repos.yml``); default ``inactive_session_time_out_delay = 7200`` (2 h),
  per-tenant configurable via ``ir.config_parameter``.

Decision record (Rule 2 — OCA-first)
------------------------------------

* ``auth_session_timeout``: OCA 19.0 module used **as-is**.
* ``auth_brute_force``: **does not exist on any OCA branch ≥ 12.0** (an
  Odoo ≤ 11-era module). Porting decade-old auth monkey-patches would be
  custom security code of the worst kind. The documented minimal
  equivalent is Odoo core's native login cooldown (verified live:
  ``res.users._on_login_cooldown``), combined with the independent Nginx
  edge rate limit on ``/web/login`` (P1-T03) — the two layers
  ARCHITECTURE_SECURITY.md §6 requires.

Failure-path detail
-------------------

Failed-login rows are written through a **separate cursor**
(``_capture_isolated``) because the ``AccessDenied`` rolls back the request
transaction — a same-cursor row would vanish with it.
