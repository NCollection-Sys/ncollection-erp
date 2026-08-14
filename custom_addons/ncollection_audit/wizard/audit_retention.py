# -*- coding: utf-8 -*-
"""P8-T05: retention — the cron OCA ships switched off, made usable and honest.

``auditlog`` ships ``ir_cron_auditlog_autovacuum`` with ``active="False"`` and a
hardcoded ``model.autovacuum(180)``. So out of the box an audit trail grows
without limit, and the fix is a number somebody typed into a cron's code field.

Two things are wrong with just flipping it on.

**The number is a compliance decision, not a default.** How long financial audit
records must be kept is set by law and by contract, not by whichever value
upstream picked. It is therefore a **system parameter**, ``ncollection.audit.retention_days``,
readable and auditable per database, with 180 as the inherited starting point
rather than a recommendation. This module does not claim to know the right UAE
retention period — it makes the value visible and changeable instead of buried.

**Deleting logs destroys the evidence that the survivors are intact.** That is
not avoidable; it is the point of retention. What IS avoidable is a verifier
that reports routine housekeeping as an attack. So pruning marks the seals whose
range it empties, and ``ncollection.audit.seal._nc_verify`` then reports those
ranges as *pruned by retention* rather than *tampered*.

The consequence, stated plainly because it is the sharp edge of this design:
**anyone who can run the retention cron can erase audit history without the
verifier objecting.** Tamper evidence protects the trail from edits, not from
its own retention policy. Who can run that cron matters as much as the ACLs.
"""
import logging

from odoo import api, fields, models

_logger = logging.getLogger(__name__)

RETENTION_PARAM = 'ncollection.audit.retention_days'
DEFAULT_RETENTION_DAYS = 180


class AuditlogAutovacuum(models.TransientModel):
    _inherit = 'auditlog.autovacuum'

    @api.model
    def _nc_retention_days(self):
        """The configured retention, or the inherited 180.

        A non-numeric or negative value falls back rather than raising: a
        mistyped parameter must not stop the trail from being trimmed, and it
        must not be read as "keep zero days" either — which is what
        ``int()``-ing a stray value into 0 would do, and upstream's
        ``(days > 0) and int(days) or 0`` turns 0 into "delete everything".
        """
        raw = self.env['ir.config_parameter'].sudo().get_param(RETENTION_PARAM)
        try:
            days = int(raw)
        except (TypeError, ValueError):
            return DEFAULT_RETENTION_DAYS
        if days <= 0:
            _logger.warning(
                "%s is %r; refusing to treat that as 'delete everything' and "
                "using %s days instead", RETENTION_PARAM, raw,
                DEFAULT_RETENTION_DAYS)
            return DEFAULT_RETENTION_DAYS
        return days

    @api.model
    def autovacuum(self, days, chunk_size=None):
        """Mark the seals this prune will empty, then prune — ONE deadline.

        Order matters and is the whole point: the seals must be marked while
        the logs are still there to be counted. Marking afterwards would record
        a prune of zero rows and leave the range verifying as TAMPERED.

        SO DOES SHARING THE DEADLINE, and the first version did not.
        `super().autovacuum()` computes its own `datetime.now() -
        timedelta(days)` a moment later, so any row created between the two
        calls was deleted by upstream and never marked by us — and its seal
        then reported ordinary retention as tampering. That is the identical
        timing bug this module already fixed in its own test, left standing in
        the production path. The delete is reimplemented here rather than
        delegated so that both halves are driven by one value.

        `datetime.now()` vs `fields.Datetime.now()` mattered too: upstream uses
        naive local time while `create_date` is stored UTC. Only the container
        being UTC made that safe, which is not a property to depend on.
        """
        deadline = fields.Datetime.subtract(fields.Datetime.now(), days=days)
        self.env['ncollection.audit.seal'].sudo()._nc_mark_pruned(deadline)
        for model_name in ('auditlog.log', 'auditlog.http.request',
                           'auditlog.http.session'):
            records = self.env[model_name].sudo().search(
                [('create_date', '<=', deadline)],
                limit=chunk_size, order='create_date asc')
            count = len(records)
            records.unlink()
            _logger.info("AUTOVACUUM - %s '%s' records deleted",
                         count, model_name)
        return True

    @api.model
    def _nc_cron_autovacuum(self):
        """Cron entry point — reads the parameter instead of a literal."""
        return self.autovacuum(self._nc_retention_days())
