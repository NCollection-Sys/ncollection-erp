# -*- coding: utf-8 -*-
"""KPI threshold bands (P4-T02).

Answers "is this number good?" — the third of the three things the task
requires of every KPI (computation method, period comparison, target/threshold
configuration).

The shape (name + min + max + a good/warning/bad state) is borrowed from
``OCA/reporting-engine``'s ``kpi_threshold`` / ``kpi_threshold_range``, which is
a clean model for this. The DEPENDENCY was deliberately not taken: that module
is AGPL-3, and its computation side is free-text SQL / ``safe_eval`` Python
entered on the record — the wrong shape for three fixed formulas that must match
hand-calculated fixtures exactly, and an admin-authored-code surface a
self-service tenant should not have. See the PR for the full survey.
"""

from odoo import api, fields, models

BAND_STATES = [
    ('good', 'Good'),
    ('warning', 'Warning'),
    ('bad', 'Bad'),
]


class NCollectionKpiThreshold(models.Model):
    """One band of a KPI's target range, e.g. "0-5% turnover is good"."""

    _name = 'ncollection.kpi.threshold'
    _description = 'NCollection KPI Threshold Band'
    _order = 'kpi_id, sequence, id'

    kpi_id = fields.Many2one(
        'ncollection.kpi', required=True, ondelete='cascade', index=True)
    sequence = fields.Integer(default=10)
    name = fields.Char(required=True)
    state = fields.Selection(BAND_STATES, required=True, default='good')

    # Bounds are half-open [value_min, value_max) so adjacent bands cannot both
    # claim the same number. Either bound may be left empty to mean unbounded,
    # which is what makes "anything above 20% is bad" expressible.
    value_min = fields.Float(help="Lower bound, inclusive. Empty = unbounded.")
    value_max = fields.Float(help="Upper bound, exclusive. Empty = unbounded.")
    has_min = fields.Boolean(default=True)
    has_max = fields.Boolean(default=True)

    @api.model
    def _match(self, bands, value):
        """Return the band containing ``value``, or None.

        Bands are evaluated in ``sequence`` order and the first match wins, so
        an ambiguous configuration resolves deterministically rather than
        depending on database order.
        """
        if value is None:
            return None
        for band in bands.sorted(lambda b: (b.sequence, b.id)):
            if band.has_min and value < band.value_min:
                continue
            if band.has_max and value >= band.value_max:
                continue
            return band
        return None
