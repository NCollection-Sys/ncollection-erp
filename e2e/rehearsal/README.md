# Release rehearsal (three databases): browser verification on upgraded COPIES

The harness that builds the isolated stack lives with the recovery scripts (`reh.sh`: its own Postgres on an `--internal` Docker network, restore from the
verified backups, neutralised crons / mail / queue jobs, release tree + release `odoo.conf` + the two pinned OCA repos). Odoo itself has no route out; a
dumb nginx proxy publishes `127.0.0.1:8169`.

* `bank/acceptance.js` on a copy: seed `bank/fixtures/bank_fixture_rehearsal.py` first (a NEW company "Rehearsal Bank Co", so nothing in the copy is changed), then
  `BANK_COMPANY=<company id> node bank/acceptance.js` (17 checks, English / Arabic, desktop / 390 px, match, undo, import, permissions).
* `verify_copy.js <db> [--ai]` (`REH_ADMIN=<login of uid 2>`, `BANK_COMPANY`): the two premium dashboards on the copy's REAL company in English and Arabic with PDF and
  Excel export over the real session, invoice + overpayment, CEO read-only, Owner is not a technical administrator and cannot read another company, one database on the
  server, and (with `--ai`) the chat widget streaming in English and Arabic.
* `worker_probe.js`: evidence for the streaming review. Needs `--workers=2` and a slow mock gateway; it measures how long an ordinary request waits while streams run.
