# -*- coding: utf-8 -*-
"""P8-T05: tamper evidence — the part the ticket marked "if feasible".

WHY SEALS AND NOT A PER-ROW CHAIN. The obvious design is to chain each
``auditlog.log`` row to the one before it at insert time. It is also the wrong
one here, for two independent reasons:

* **Cost.** ``auditlog`` in ``full`` mode already pays, on every audited write,
  two complete reads of every stored field on the record, a flush, one insert
  per changed field, and an uncached search on ``auditlog.http.session``. A
  chain needs the previous row's digest, so it adds a query — and, to be
  correct, a lock — to that path.
* **Correctness under concurrency.** Without the lock, two transactions read the
  same predecessor and the chain forks. A verifier then reports a fork, which
  looks exactly like tampering. A tamper check whose normal operation produces
  false alarms gets ignored, and then deleted.

So: each row carries a digest of **its own content** (see
``models/auditlog_log.py``, no query, no lock), and the chaining happens here,
periodically, over ranges. What each mechanism catches:

    row edited      -> its content digest stops matching     (immediate)
    row deleted     -> the covering seal stops recomputing   (at verify)
    seal edited     -> the seal chain breaks                 (at verify)

WHAT THIS DOES NOT CLAIM. Rows written since the last seal are covered only by
their own content digest — an attacker who inserts *and* deletes inside that
window, before a seal runs, leaves nothing behind. Shortening the cron interval
shortens the window; it cannot close it. Closing it needs the per-write chain
this deliberately rejected, and that is a trade to revisit with real numbers,
not a gap to hide.

RETENTION IS A TRUSTED OPERATION, AND THAT IS A REAL LIMITATION. Pruning old
logs necessarily destroys the evidence that they were untouched. Rather than
pretend otherwise, the pruner marks the covering seals with ``pruned_at``, and
verification reports those ranges as *pruned by retention* instead of as
tampered. Anyone who can run the retention cron can therefore erase history
without the verifier crying foul — so that cron's ownership matters as much as
the ACLs here.
"""
import hashlib
import json

from odoo import api, fields, models

GENESIS = '0' * 64


class NcollectionAuditSeal(models.Model):
    _name = 'ncollection.audit.seal'
    _description = 'NCollection Audit Seal'
    _order = 'id'

    from_log_id = fields.Integer(required=True, readonly=True, index=True)
    to_log_id = fields.Integer(required=True, readonly=True, index=True)
    log_count = fields.Integer(readonly=True)
    digest = fields.Char(required=True, readonly=True, index=True)
    prev_digest = fields.Char(required=True, readonly=True)
    sealed_at = fields.Datetime(
        readonly=True, default=fields.Datetime.now, index=True)
    pruned_at = fields.Datetime(
        readonly=True,
        help="Set when the retention policy removed logs this seal covers. "
             "The range then verifies as PRUNED rather than TAMPERED — "
             "retention is a trusted operation and destroys its own evidence.")
    pruned_count = fields.Integer(readonly=True)

    # ---- sealing ---------------------------------------------------------

    @api.model
    def _nc_range_digest(self, prev_digest, logs):
        """Digest over an ordered range of rows, anchored to the previous seal.

        Built from each row's own content digest rather than re-deriving the
        content here: one definition of "what this row says" (``_nc_row_digest``),
        used by the row check, by this, and by the verifier.
        """
        payload = {
            'prev': prev_digest,
            'rows': [(log.id, log.nc_content_hash or '') for log in logs],
        }
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, default=str).encode('utf-8')
        ).hexdigest()

    @api.model
    def _nc_seal_pending(self, limit=None):
        """Seal every log written since the last seal. Returns the new seal.

        Idempotent by construction: a second run with nothing new to cover
        creates nothing and returns an empty recordset (Standing Rule 12 — the
        second run must be a no-op, and a test proves it rather than an echo
        claiming it).
        """
        last = self.search([], order='id desc', limit=1)
        floor_id = last.to_log_id if last else 0
        logs = self.env['auditlog.log'].sudo().search(
            [('id', '>', floor_id)], order='id', limit=limit)
        if not logs:
            return self.browse()
        return self.sudo().create({
            'from_log_id': logs[0].id,
            'to_log_id': logs[-1].id,
            'log_count': len(logs),
            'prev_digest': last.digest if last else GENESIS,
            'digest': self._nc_range_digest(
                last.digest if last else GENESIS, logs),
        })

    # Bounded on purpose — see data/ir_cron.xml. Production runs ONE cron
    # thread for every tenant database on the node, so an unbounded seal on a
    # busy tenant would delay everyone else's crons (#310). Sealing resumes
    # from the last sealed id, so a partial run is simply continued next hour.
    SEAL_BATCH = 5000

    @api.model
    def _nc_cron_seal(self):
        """Cron entry point. Sealing is cheap and must not block anything."""
        self._nc_seal_pending(limit=self.SEAL_BATCH)
        return True

    # ---- verification ----------------------------------------------------

    @api.model
    def _nc_verify(self):
        """Walk the whole chain. Returns a list of finding dicts.

        An EMPTY list means verified. Each finding names the seal, the range and
        what is wrong, because "the audit trail is broken" is not an actionable
        sentence.
        """
        findings = []
        previous = None
        # pylint: disable=no-search-all
        # A chain verification walks the WHOLE chain by definition — a limit
        # here would report "verified" over the portion it happened to read,
        # which is the one answer this method must never give.
        for seal in self.search([], order='id'):
            expected_prev = previous.digest if previous else GENESIS
            if seal.prev_digest != expected_prev:
                findings.append({
                    'seal_id': seal.id, 'kind': 'chain_broken',
                    'detail': "seal %s expects predecessor %s but the chain "
                              "says %s — a seal was altered, removed or "
                              "inserted" % (seal.id, seal.prev_digest,
                                            expected_prev),
                })
            logs = self.env['auditlog.log'].sudo().search(
                [('id', '>=', seal.from_log_id), ('id', '<=', seal.to_log_id)],
                order='id')
            # Content-check the sealed rows too. WITHOUT THIS THE SEAL IS
            # CIRCULAR: it is computed FROM the rows' own digests, so a trail
            # where every digest is missing or blank re-derives to exactly the
            # seal that was stored and "verifies" perfectly. Found by a RED
            # proof that came back GREEN — deleting the digest computation
            # entirely broke nothing.
            _ok, tampered = logs._nc_verify_content()
            for record in tampered:
                findings.append({
                    'seal_id': seal.id, 'kind': 'tampered',
                    'detail': "log %s has no valid content digest of its own, "
                              "so its seal attests to nothing" % record.id,
                })
            recomputed = self._nc_range_digest(seal.prev_digest, logs)
            # A prune is only an ACCEPTABLE explanation if the number of
            # survivors is exactly what that prune should have left. Deciding
            # on `bool(seal.pruned_at)` alone meant a seal whose range merely
            # STRADDLED the retention deadline was marked pruned, and from then
            # on any genuine edit to a SURVIVING row in that range reported as
            # routine housekeeping — permanently. Seals batch hourly, so on a
            # quiet tenant one seal spans days and the first retention run
            # almost always straddles. Found in review; the original test could
            # not see it because it never tampered with a survivor.
            explained_by_pruning = (
                seal.pruned_at
                and len(logs) == max(seal.log_count - seal.pruned_count, 0))
            if recomputed != seal.digest:
                findings.append({
                    'seal_id': seal.id,
                    'kind': 'pruned' if explained_by_pruning else 'tampered',
                    'detail': (
                        "logs %s-%s no longer match their seal (%s of %s rows "
                        "present)%s" % (
                            seal.from_log_id, seal.to_log_id, len(logs),
                            seal.log_count,
                            "; pruned by retention on %s" % seal.pruned_at
                            if seal.pruned_at else "")),
                })
            previous = seal
        # Rows written since the last seal carry only their own digest. Say so
        # rather than letting an empty finding list imply they are covered.
        _ok, tampered = self.env['auditlog.log'].sudo().search(
            [('id', '>', previous.to_log_id if previous else 0)]
        )._nc_verify_content()
        for record in tampered:
            findings.append({
                'seal_id': False, 'kind': 'unsealed_tampered',
                'detail': "log %s is not yet sealed and its own content digest "
                          "does not match" % record.id,
            })
        return findings

    @api.model
    def _nc_mark_pruned(self, before_datetime):
        """Flag the seals whose range the retention policy is about to empty."""
        # `<=`, NOT `<`. Upstream's autovacuum deletes with
        # `("create_date", "<=", deadline)`; marking with a strictly-earlier
        # comparison leaves the boundary row deleted and its seal unmarked, so
        # ordinary retention reports itself as TAMPERING. Caught by
        # test_retention_reports_PRUNED_rather_than_TAMPERED, which failed with
        # ['tampered'] != ['pruned'] — the two comparisons must be the same
        # comparison, because they describe the same set of rows.
        logs = self.env['auditlog.log'].sudo().search(
            [('create_date', '<=', before_datetime)], order='id')
        if not logs:
            return self.browse()
        doomed = set(logs.ids)
        seals = self.search([('from_log_id', '<=', logs[-1].id),
                             ('pruned_at', '=', False)])
        now = fields.Datetime.now()
        for seal in seals:
            # PER SEAL, not the global count. Writing len(logs) — every row in
            # the database past the deadline — onto each seal made the number
            # meaningless, which is why nothing could safely compare against
            # it. The count must describe THIS seal's range or the check in
            # _nc_verify cannot distinguish a prune from a deletion.
            # NOT RED-PROVED, and said so rather than implied. Replacing this
            # with the global `len(doomed)` does not fail any test: doing so
            # only diverges when one prune spans TWO seals, and a fixture that
            # builds that state ended up harder to reason about than the four
            # lines it guards — three attempts at it were each green-but-wrong
            # for a different reason. The COMPARISON that uses this number is
            # proved (see test_a_PARTIAL_prune_does_not_mask_tampering_of_a_
            # SURVIVOR); the per-seal attribution rests on review and on the
            # plain argument that a count describing every doomed row in the
            # database says nothing about any particular seal's range.
            covered = sum(1 for log_id in doomed
                          if seal.from_log_id <= log_id <= seal.to_log_id)
            if covered:
                seal.sudo().write({'pruned_at': now, 'pruned_count': covered})
        return seals
