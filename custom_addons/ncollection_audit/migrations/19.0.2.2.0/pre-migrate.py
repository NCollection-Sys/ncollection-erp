"""19.0.2.2.0: the one-Undo-per-event rule becomes a PARTIAL unique index (a version may be restored again), so the
former table constraint is dropped here; the index is created by the model. No row is changed."""


def migrate(cr, version):
    if not version:
        return
    cr.execute("ALTER TABLE ncollection_audit_undo DROP CONSTRAINT IF EXISTS ncollection_audit_undo_original_unique")
