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

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

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
    name = fields.Char(required=True, translate=True)
    state = fields.Selection(BAND_STATES, required=True, default='good')

    # Bounds are half-open [value_min, value_max) so adjacent bands cannot both
    # claim the same number. Either bound may be left empty to mean unbounded,
    # which is what makes "anything above 20% is bad" expressible.
    value_min = fields.Float(help="Lower bound, inclusive. Empty = unbounded.")
    value_max = fields.Float(help="Upper bound, exclusive. Empty = unbounded.")
    has_min = fields.Boolean(default=True)
    has_max = fields.Boolean(default=True)

    @api.constrains('has_min', 'has_max', 'value_min', 'value_max')
    def _check_band_is_reachable(self):
        """A band that can never match is a silent hole in a dashboard.

        Both bounds default to 0.0 with has_min/has_max True, so a record
        created without setting them is the empty interval [0.0, 0.0) — it
        matches no value at all, `_match` returns None, and the KPI renders
        without a band and without any error. #56/#57 will expose these for
        tenants to edit, so the guard goes in before the UI does.
        """
        for band in self:
            if (band.has_min and band.has_max
                    and band.value_min >= band.value_max):
                raise ValidationError(_(
                    "Threshold band %(name)s covers no values: its minimum "
                    "(%(minimum)s) is not below its maximum (%(maximum)s). "
                    "Leave a bound unset to make the band open-ended.",
                    name=band.name or '?',
                    minimum=band.value_min, maximum=band.value_max))

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
