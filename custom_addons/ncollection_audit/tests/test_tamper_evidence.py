# -*- coding: utf-8 -*-
"""P8-T05: tamper evidence — what it catches, and what it admits it does not.

`auditlog` has none: grepping the module for hash/checksum/chain/tamper returns
zero hits, and its own manager group holds `perm_unlink=1` on `auditlog.log`.
An audit trail a compromised admin can silently edit records what the attacker
wanted recorded.

Each test below breaks exactly one thing and asserts the verifier names it —
including the LIMITS, which are asserted as deliberately as the guarantees. A
tamper check that quietly cannot see a class of attack is worse than none,
because it is believed.
"""
from odoo import fields
from odoo.exceptions import AccessError
from odoo.tests import tagged

from .common import AuditCommon


@tagged('post_install', '-at_install')
class TestTamperEvidence(AuditCommon):

    def setUp(self):
        super().setUp()
        self._noise('Seal Fixture')
        self.seal = self.Seal._nc_seal_pending()
        self.assertTrue(self.seal, "nothing was sealed, so every assertion "
                                   "below would pass vacuously")

    # ---- the guarantees --------------------------------------------------

    def test_a_clean_trail_verifies(self):
        self.assertEqual(self.Seal._nc_verify(), [])

    def test_an_EDITED_log_row_is_caught_by_its_own_digest(self):
        """The immediate check — no seal needed."""
        log = self.Log.search([('id', '>=', self.seal.from_log_id)], limit=1)
        self.env.cr.execute(
            "UPDATE auditlog_log SET name = %s WHERE id = %s",
            ('doctored', log.id))
        log.invalidate_recordset()
        _ok, tampered = log._nc_verify_content()
        self.assertEqual(tampered, log, "an edited row still verifies")

    def test_an_EDITED_log_row_also_breaks_its_seal(self):
        log = self.Log.search([('id', '>=', self.seal.from_log_id)], limit=1)
        self.env.cr.execute(
            "UPDATE auditlog_log SET nc_content_hash = %s WHERE id = %s",
            ('f' * 64, log.id))
        self.env.invalidate_all()
        kinds = [f['kind'] for f in self.Seal._nc_verify()]
        self.assertIn('tampered', kinds)

    def test_a_DELETED_log_row_is_caught_by_the_seal(self):
        """The case a per-row digest alone cannot see: the row is simply gone,
        so there is nothing left to re-derive."""
        log = self.Log.search([('id', '>=', self.seal.from_log_id)], limit=1)
        self.env.cr.execute("DELETE FROM auditlog_log WHERE id = %s", (log.id,))
        self.env.invalidate_all()
        findings = self.Seal._nc_verify()
        self.assertEqual([f['kind'] for f in findings], ['tampered'])
        self.assertIn('of %s rows present' % self.seal.log_count,
                      findings[0]['detail'])

    def test_an_EDITED_SEAL_breaks_the_chain(self):
        """Covering the obvious counter-attack: re-seal the doctored range."""
        self._noise('Second range')
        second = self.Seal._nc_seal_pending()
        self.assertTrue(second)
        self.env.cr.execute(
            "UPDATE ncollection_audit_seal SET digest = %s WHERE id = %s",
            ('a' * 64, self.seal.id))
        self.env.invalidate_all()
        kinds = [f['kind'] for f in self.Seal._nc_verify()]
        self.assertIn('chain_broken', kinds,
                      "a rewritten seal did not break the chain")

    def test_a_manager_cannot_delete_audit_rows(self):
        """OCA grants perm_unlink=1 on auditlog.log to its manager group. This
        module overrides that row to 0 — otherwise the tamper evidence above
        protects against editing and not against erasing, which is the easier
        attack."""
        manager = self.env['res.users'].create({
            'name': 'Audit Manager', 'login': 'nc_audit_mgr',
            'group_ids': [(6, 0, [
                self.env.ref('auditlog.group_auditlog_manager').id,
                self.env.ref('base.group_user').id])],
        })
        log = self.Log.search([], limit=1)
        with self.assertRaises(AccessError):
            log.with_user(manager).unlink()

    def test_a_manager_cannot_forge_a_seal(self):
        manager = self.env['res.users'].create({
            'name': 'Audit Manager 2', 'login': 'nc_audit_mgr2',
            'group_ids': [(6, 0, [
                self.env.ref('auditlog.group_auditlog_manager').id,
                self.env.ref('base.group_user').id])],
        })
        with self.assertRaises(AccessError):
            self.Seal.with_user(manager).create({
                'from_log_id': 1, 'to_log_id': 2, 'log_count': 2,
                'digest': 'b' * 64, 'prev_digest': 'c' * 64})

    # ---- idempotency (Standing Rule 12) ----------------------------------

    def test_sealing_twice_is_a_no_op(self):
        """Rule 12 asks for idempotency to be PROVED, not claimed in an echo."""
        before = self.Seal.search_count([])
        self.assertFalse(self.Seal._nc_seal_pending(),
                         "a second seal ran with nothing new to cover")
        self.assertEqual(self.Seal.search_count([]), before)

    # ---- the admitted limits ---------------------------------------------

    def test_an_UNSEALED_row_is_still_content_checked_and_says_so(self):
        """The window this design does not close, asserted rather than hidden:
        rows written since the last seal have only their own digest."""
        self._noise('After the seal')
        fresh = self.Log.search([('id', '>', self.seal.to_log_id)], limit=1)
        self.assertTrue(fresh, "nothing was written after the seal")
        self.env.cr.execute(
            "UPDATE auditlog_log SET nc_content_hash = %s WHERE id = %s",
            ('e' * 64, fresh.id))
        self.env.invalidate_all()
        kinds = [f['kind'] for f in self.Seal._nc_verify()]
        self.assertIn('unsealed_tampered', kinds)


@tagged('post_install', '-at_install')
class TestPartialPrune(AuditCommon):
    """Retention vs tamper evidence — deliberately WITHOUT the pre-seal that
    `TestTamperEvidence.setUp` creates.

    Two seals plus a prune that spans them produced a fixture harder to reason
    about than the code it guards, and three successive attempts to make it
    both green and discriminating each failed for a different reason. One seal,
    two dated batches, arithmetic a reader can check by eye.
    """

    def _age(self, logs, hours=1):
        """Move rows back in time, then RE-DERIVE their digests.

        `create_date` is part of the content digest — deliberately, because a
        row whose timestamp was moved IS tampered with. A fixture that only
        rewrote the timestamp would be indistinguishable from an attack.
        """
        older = fields.Datetime.subtract(fields.Datetime.now(), hours=hours)
        self.env.cr.execute(
            "UPDATE auditlog_log SET create_date = %s WHERE id IN %s",
            (older, tuple(logs.ids)))
        self.env.invalidate_all()
        for log in logs:
            log.nc_content_hash = log._nc_row_digest()
        return older

    def _straddling_seal(self):
        """One seal covering an OLD batch and a NEW batch.

        The ageing is necessary, not decorative: `create_date` comes from the
        TRANSACTION timestamp, so every row created inside one test shares it
        to the microsecond. An earlier fixture assumed two separate flushes
        would differ and asserted it — the assertion fired, which is how the
        assumption was caught rather than silently producing a test that could
        never model a straddling prune.
        """
        self.Log.search([]).unlink()
        self._noise('Old batch')
        old = self.Log.search([], order='id')
        self.assertTrue(old, "the old batch produced no rows")
        cutoff = self._age(old, hours=1)
        self._noise('New batch')
        new = self.Log.search([('id', '>', max(old.ids))], order='id')
        self.assertTrue(new, "the new batch produced no rows")
        seal = self.Seal._nc_seal_pending()
        self.assertTrue(seal)
        self.assertEqual(seal.log_count, len(old) + len(new))
        return seal, old, new, cutoff

    def test_retention_reports_PRUNED_rather_than_TAMPERED(self):
        """Routine housekeeping must not read as an attack."""
        seal, old, _new, cutoff = self._straddling_seal()
        self.Seal._nc_mark_pruned(cutoff)
        self.assertEqual(seal.pruned_count, len(old))
        self.env.cr.execute("DELETE FROM auditlog_log WHERE id IN %s",
                            (tuple(old.ids),))
        self.env.invalidate_all()
        self.assertEqual([f['kind'] for f in self.Seal._nc_verify()], ['pruned'])

    def test_a_PARTIAL_prune_does_not_mask_tampering_of_a_SURVIVOR(self):
        """THE finding from review.

        Deciding pruned-vs-tampered on `bool(seal.pruned_at)` alone meant a
        seal whose range merely STRADDLED the deadline was marked pruned — and
        from then on any loss of a row that SURVIVED the prune reported as
        routine housekeeping, permanently. Seals batch hourly, so on a quiet
        tenant one seal spans days and the first retention run almost always
        straddles.

        The survivor is DELETED, not doctored: a doctored row trips the per-row
        content check, which reports 'tampered' on its own and would mask
        whether the count arithmetic works at all. That is what the first
        version of this assertion did, and its RED proof came back green.
        """
        seal, old, new, cutoff = self._straddling_seal()
        self.Seal._nc_mark_pruned(cutoff)
        self.env.cr.execute("DELETE FROM auditlog_log WHERE id IN %s",
                            (tuple(old.ids),))
        self.env.invalidate_all()
        self.assertEqual([f['kind'] for f in self.Seal._nc_verify()], ['pruned'],
                         "the prune alone should verify as pruned")

        self.env.cr.execute("DELETE FROM auditlog_log WHERE id = %s",
                            (new[0].id,))
        self.env.invalidate_all()
        kinds = [f['kind'] for f in self.Seal._nc_verify()]
        self.assertIn(
            'tampered', kinds,
            "a surviving row was deleted after a partial prune and the "
            "verifier still reported 'pruned' — retention permanently masks "
            "tampering for the seal's whole range")
        self.assertNotIn('pruned', kinds)
        self.assertTrue(seal.pruned_at, "the fixture did not actually prune")
